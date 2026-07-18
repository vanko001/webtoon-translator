"""Orchestrator chính: chạy pipeline end-to-end từ input/ đến output/.

Pipeline:
  1. pre_split: cắt ảnh webtoon dài thành tiles ~6000px (overlap 200px)
  2. translate: chạy manga-image-translator per-tile folder qua Ollama Cloud
     - config JSON (--config-file) khai báo translator/target_lang/inpainter
     - --context-size: truyền N tile trước làm context
     - --save-text-file: lưu text đã dịch để build glossary
     - glossary tích lũy xuyên suốt series (OPENAI_GLOSSARY_PATH)
  3. glossary: extract proper nouns từ text đã dịch, tích lũy
  4. post_stitch: ghép tiles lại thành ảnh dài hoàn chỉnh
  5. checkpoint: ghi progress.json để resume nếu crash

Cách chạy:
  python run_pipeline.py
  python run_pipeline.py --resume          # bỏ qua chương đã xong
  python run_pipeline.py --chapter ch005   # chỉ chạy 1 chương
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from pre_split import split_chapter, DEFAULT_TILE_HEIGHT, DEFAULT_OVERLAP
from post_stitch import stitch_chapter
from glossary import Glossary

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Config mặc định
DEFAULT_CONFIG = {
    # Đường dẫn
    "input_dir": "input",
    "output_dir": "output",
    "work_dir": "work",
    "fonts_dir": "fonts",
    "glossary_file": "glossary.json",
    "progress_file": "work/progress.json",
    # Ollama Cloud
    "ollama_base_url": "https://ollama.com/v1",
    "ollama_model": "qwen3.5",
    # manga-image-translator
    # "chatgpt" = LLM qua OpenAI-compatible API (Ollama Cloud). Đây là translator DUY NHẤT
    # được engine nối context (--context-size) và glossary (OPENAI_GLOSSARY_PATH).
    # "custom_openai" cũng gọi được Ollama nhưng KHÔNG có context/glossary.
    # "original" giữ nguyên text, để smoke-test không cần API key.
    "translator": "chatgpt",
    "target_lang": "VIN",  # mã ngôn ngữ của manga-image-translator (VIN = Vietnamese)
    # Series mode: gộp tiles của TẤT CẢ chương vào 1 lần chạy engine
    # → context dịch chảy liền mạch xuyên chương (cần model context dài).
    # False = chạy engine riêng từng chương (context chỉ trong chương).
    "series_mode": False,
    "context_size": 3,
    "batch_size": 3,
    "use_gpu": True,  # MPS trên Apple Silicon, CUDA trên NVIDIA
    "attempts": 2,  # retry khi lỗi mạng/API
    "overwrite": False,  # True = dịch lại cả tile đã có output (khi đổi model/config)
    "detection_size": 2048,
    "inpainter": "lama_large",
    "inpainting_size": 2048,
    "inpainting_precision": "bf16",
    "ocr": "48px",
    "font_size_offset": 0,
    "tile_height": DEFAULT_TILE_HEIGHT,
    "overlap": DEFAULT_OVERLAP,
    # glossary
    "glossary_extract_llm": True,  # Dùng LLM extract proper nouns
    "glossary_export_mit": True,  # Export sang format MIT
    # Character sheet: LLM duy trì hồ sơ xưng hô + giọng điệu từng nhân vật,
    # tiêm vào system prompt dịch → giữ cách xưng hô nhất quán xuyên series
    # (chỉ hoạt động với translator "chatgpt").
    "character_sheet": True,
    # File chứa system prompt cho translator (chỉnh sửa tự do, không cần sửa code).
    # Không có file → dùng template mặc định của engine + quy tắc xưng hô.
    "system_prompt_file": "prompts/vietnamese-adult.txt",
    # Temperature của model dịch: cao hơn = văn bay hơn nhưng dễ lệch format.
    # None = dùng mặc định engine (0.5).
    "gpt_temperature": None,
    # Xóa tiles/translated trong work/ sau khi stitch xong (giữ texts + meta +
    # glossary). Bật khi dịch nhiều bộ liên tiếp để không tràn disk.
    "clean_work": False,
    # Xóa thư mục ảnh RAW (input_dir) sau khi dịch xong TOÀN BỘ và verify
    # từng file output. KHÔNG hoàn tác được — chỉ bật khi output là bản lưu chính.
    "delete_raw": False,
    # "slices" = mỗi chương là THƯ MỤC lát JPEG ~2400px (nhẹ hơn 60-85%,
    # web đọc nhanh, không đụng giới hạn JPEG 65500px). "single" = 1 file như cũ.
    "output_format": "slices",
    # Series mode xử lý theo nhóm N chương (split→dịch→stitch→dọn từng nhóm)
    # → bộ hàng trăm chương không làm đầy ổ. Context liền mạch trong nhóm;
    # giữa các nhóm có glossary + character sheet giữ nhất quán.
    "batch_chapters": 20,
}


def chapter_out_path(cfg: dict, chapter_file: str) -> str:
    """Đường output của 1 chương theo output_format."""
    stem = Path(chapter_file).stem
    if cfg.get("output_format", "slices") == "slices":
        return os.path.join(cfg["output_dir"], stem)
    return os.path.join(cfg["output_dir"], f"{stem}{Path(chapter_file).suffix}")


def chapter_output_ok(cfg: dict, chapter_file: str) -> bool:
    """Verify output của 1 chương tồn tại thật (file khác rỗng / thư mục có lát)."""
    p = Path(chapter_out_path(cfg, chapter_file))
    if cfg.get("output_format", "slices") == "slices":
        return p.is_dir() and any(
            f.suffix == ".jpg" and f.stat().st_size > 0 for f in p.iterdir()
        )
    return p.is_file() and p.stat().st_size > 0

# Few-shot sample dạy văn phong: xưng hô nhất quán + giọng truyền cảm + SFX Việt.
# Engine tiêm cặp này làm ví dụ user/assistant trước khi dịch thật.
VI_CHAT_SAMPLE = [
    (
        "<|1|>하아… 기다렸어…\n"
        "<|2|>오빠… 더 가까이 와…\n"
        "<|3|>쿵\n"
        "<|4|>야! 너 뭐하는 거야?!\n"
        "<|5|>그날 밤, 나는 잠들 수 없었다."
    ),
    (
        "<|1|>Haa... em đợi anh mãi...\n"
        "<|2|>Anh... lại gần em thêm chút nữa đi...\n"
        "<|3|>Thịch\n"
        "<|4|>Này! Mày đang làm cái gì đấy hả?!\n"
        "<|5|>Đêm hôm ấy, tôi trằn trọc không sao chợp mắt nổi."
    ),
]

# Quy tắc bổ sung cho system prompt của translator (append vào template gốc engine).
# Lưu ý: template được engine .format(to_lang=...) nên không được chứa { } lạ.
VI_EXTRA_RULES = """
## Vietnamese Translation Rules
- Xung ho (toi/anh/em/chi/chu/may/tao/cau...) MUST be consistent for each character pair across the whole story.
- Match each character's established speech style (formal/informal, rude/polite, childish...).
- Keep sound effects and interjections natural in Vietnamese (vd: "Gau!", "Am am", "Hic").
"""

CHARACTER_SHEET_HEADER = """
## Character Sheet (MUST follow for pronouns and tone)
"""


def natural_key(name: str):
    """Sort key tự nhiên: ch2 < ch10 (thay vì sort chữ cái ch10 < ch2)."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def load_config(config_path: str = "config.yaml") -> dict:
    """Load config từ file yaml nếu có, fallback sang default."""
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(config_path):
        try:
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
            cfg.update(user_cfg)
        except ImportError:
            print("[config] PyYAML chưa cài, dùng config mặc định")
    return cfg


def load_progress(path: str) -> dict:
    """Load progress.json để resume."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "current": None}


def save_progress(progress: dict, path: str) -> None:
    """Lưu progress.json."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def find_font(fonts_dir: str) -> str | None:
    """Tìm font .ttf/.otf đầu tiên trong fonts_dir."""
    if not os.path.isdir(fonts_dir):
        return None
    for f in sorted(os.listdir(fonts_dir)):
        if Path(f).suffix.lower() in {".ttf", ".otf", ".ttc"}:
            return os.path.join(fonts_dir, f)
    return None


def build_gpt_config(cfg: dict, glossary) -> str | None:
    """Sinh file gpt_config.yaml cho translator chatgpt.

    Nội dung: system prompt = template gốc của engine + quy tắc xưng hô tiếng Việt
    + hồ sơ nhân vật (nếu có) → model dịch tuân theo cách xưng hô đã thiết lập.
    """
    if cfg["translator"] != "chatgpt":
        return None

    # Template: ưu tiên file người dùng chỉnh được; fallback = template gốc engine
    # + quy tắc xưng hô (giữ nguyên quy tắc format <|number|> của engine).
    prompt_file = cfg.get("system_prompt_file") or ""
    if prompt_file and os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        from manga_translator.translators.config_gpt import ConfigGPT

        template = ConfigGPT._CHAT_SYSTEM_TEMPLATE + VI_EXTRA_RULES

    if cfg.get("character_sheet", True):
        sheet = glossary.to_character_sheet_prompt()
        if sheet:
            # Template bị engine .format() nên phải khử ngoặc nhọn trong nội dung sheet
            safe_sheet = sheet.replace("{", "(").replace("}", ")")
            template += CHARACTER_SHEET_HEADER + safe_sheet + "\n"

    data = {
        "chat_system_template": template,
        # Few-shot sample văn phong tiếng Việt (engine match theo target lang)
        "chat_sample": {"Vietnamese": list(VI_CHAT_SAMPLE)},
    }
    if cfg.get("gpt_temperature") is not None:
        data["temperature"] = cfg["gpt_temperature"]

    import yaml

    path = os.path.join(cfg["work_dir"], "gpt_config.yaml")
    os.makedirs(cfg["work_dir"], exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, width=10**6)
    return path


def build_mit_config(cfg: dict, gpt_config_path: str | None = None) -> str:
    """Sinh file config JSON cho manga-image-translator.

    CLI mới của manga-image-translator không nhận --translator/--target-lang
    trực tiếp nữa mà đọc từ --config-file.
    """
    mit_cfg = {
        "translator": {
            "translator": cfg["translator"],
            "target_lang": cfg["target_lang"],
            "gpt_config": gpt_config_path,
        },
        "detector": {
            "detector": "default",
            "detection_size": cfg["detection_size"],
        },
        "inpainter": {
            "inpainter": cfg["inpainter"],
            "inpainting_size": cfg["inpainting_size"],
            "inpainting_precision": cfg["inpainting_precision"],
        },
        "ocr": {
            "ocr": cfg["ocr"],
        },
        "render": {
            "font_size_offset": cfg["font_size_offset"],
        },
    }
    path = os.path.join(cfg["work_dir"], "mit_config.json")
    os.makedirs(cfg["work_dir"], exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mit_cfg, f, ensure_ascii=False, indent=2)
    return path


def _mit_cmd_env(
    tiles_dir: str,
    out_dir: str,
    text_file: str,
    cfg: dict,
    glossary_path: str | None = None,
    gpt_config_path: str | None = None,
) -> tuple[list[str], dict] | None:
    """Dựng (cmd, env) để chạy manga-image-translator. None nếu thiếu API key."""
    env = os.environ.copy()
    if cfg["translator"] in ("chatgpt", "custom_openai"):
        if "OLLAMA_API_KEY" not in env:
            print("[pipeline] CẢNH BÁO: OLLAMA_API_KEY chưa set!")
            return None
        # chatgpt translator (context + glossary) đọc OPENAI_*
        env["OPENAI_API_BASE"] = cfg["ollama_base_url"]
        env["OPENAI_MODEL"] = cfg["ollama_model"]
        env["OPENAI_API_KEY"] = env["OLLAMA_API_KEY"]
        # custom_openai translator đọc CUSTOM_OPENAI_*
        env["CUSTOM_OPENAI_API_BASE"] = cfg["ollama_base_url"]
        env["CUSTOM_OPENAI_MODEL"] = cfg["ollama_model"]
        env["CUSTOM_OPENAI_API_KEY"] = env["OLLAMA_API_KEY"]
    # MPS: một số op chưa hỗ trợ → fallback CPU thay vì crash
    env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    if glossary_path:
        env["OPENAI_GLOSSARY_PATH"] = os.path.abspath(glossary_path)

    # --save-text-file append vào file cũ → xóa trước để không bị trùng
    if os.path.exists(text_file):
        os.remove(text_file)
    os.makedirs(os.path.dirname(text_file) or ".", exist_ok=True)

    # Lưu ý: các flag chung (--use-gpu, --font-path, --context-size...) phải đặt
    # TRƯỚC subcommand "local"; các flag riêng (-i, -o, --config-file...) đặt sau.
    cmd = [
        sys.executable,
        "-m",
        "manga_translator",
        "--context-size",
        str(cfg["context_size"]),
        "--batch-size",
        str(cfg["batch_size"]),
        "--attempts",
        str(cfg["attempts"]),
    ]
    if cfg["use_gpu"]:
        cmd.append("--use-gpu")
    font = find_font(cfg["fonts_dir"])
    if font:
        cmd.extend(["--font-path", font])
    else:
        print(f"[mit] CẢNH BÁO: không có font trong {cfg['fonts_dir']}/ — chữ tiếng Việt có thể vỡ dấu")
    cmd.extend(
        [
            "local",
            "-i",
            tiles_dir,
            "-o",
            out_dir,
            "-f",
            "png",
            "--config-file",
            build_mit_config(cfg, gpt_config_path),
            "--save-text-file",
            text_file,
        ]
    )
    # Mặc định KHÔNG --overwrite: tile đã dịch trong lần chạy trước được giữ lại
    # (resume ở mức tile). Set overwrite: true trong config nếu đổi model/config.
    if cfg.get("overwrite"):
        cmd.append("--overwrite")
    return cmd, env


def run_mit(
    tiles_dir: str,
    out_dir: str,
    text_file: str,
    cfg: dict,
    glossary_path: str | None = None,
    gpt_config_path: str | None = None,
) -> bool:
    """Chạy manga-image-translator cho 1 folder tiles (blocking).

    Returns:
        True nếu thành công.
    """
    cmd_env = _mit_cmd_env(tiles_dir, out_dir, text_file, cfg, glossary_path, gpt_config_path)
    if cmd_env is None:
        return False
    cmd, env = cmd_env

    print(f"[mit] Dịch {tiles_dir} -> {out_dir}")
    # Không capture output để thấy progress trực tiếp
    result = subprocess.run(cmd, env=env)

    if result.returncode != 0:
        print(f"[mit] LỖI (exit {result.returncode})")
        return False

    if not os.path.exists(out_dir) or not any(
        Path(f).suffix.lower() in SUPPORTED_EXTS for f in os.listdir(out_dir)
    ):
        print(f"[mit] Output rỗng: {out_dir}")
        return False

    print(f"[mit] OK -> {out_dir}")
    return True


def process_chapter(
    chapter_file: str,
    cfg: dict,
    glossary: Glossary,
) -> bool:
    """Xử lý 1 chương: split → translate → glossary → stitch.

    Args:
        chapter_file: Tên file ảnh gốc (vd: "ch001.jpg").
        cfg: Config dict.
        glossary: Glossary instance.

    Returns:
        True nếu thành công.
    """
    stem = Path(chapter_file).stem
    input_path = os.path.join(cfg["input_dir"], chapter_file)

    print(f"\n{'='*60}")
    print(f"[pipeline] Xử lý chương: {chapter_file}")
    print(f"{'='*60}")

    # 1. Pre-split
    tiles_root = os.path.join(cfg["work_dir"], "tiles")
    meta_path = split_chapter(
        input_path, tiles_root, cfg["tile_height"], cfg["overlap"]
    )
    tiles_dir = os.path.join(tiles_root, stem)

    # 2. Export glossary sang MIT format (chỉ entries có source gốc)
    mit_glossary = None
    if cfg["glossary_export_mit"] and glossary.entries:
        mit_glossary = os.path.join(cfg["work_dir"], "glossary_mit.txt")
        glossary.to_mit_format(mit_glossary)

    # 3. Translate qua manga-image-translator
    translated_dir = os.path.join(cfg["work_dir"], "translated", stem)
    os.makedirs(translated_dir, exist_ok=True)
    text_file = os.path.join(cfg["work_dir"], "texts", f"{stem}.txt")
    gpt_config = build_gpt_config(cfg, glossary)
    if not run_mit(tiles_dir, translated_dir, text_file, cfg, mit_glossary, gpt_config):
        print(f"  [fail] Dịch thất bại: {stem}")
        return False

    # 4. Update glossary từ text đã dịch
    if cfg["glossary_extract_llm"]:
        try:
            update_glossary_from_text_file(text_file, cfg, glossary)
        except Exception as e:
            print(f"  [glossary] Skip extract (lỗi: {e})")

    # 5. Post-stitch
    out_path = chapter_out_path(cfg, chapter_file)
    os.makedirs(cfg["output_dir"], exist_ok=True)
    sliced = cfg.get("output_format", "slices") == "slices"
    if not stitch_chapter(meta_path, translated_dir, out_path, sliced=sliced):
        print(f"  [fail] Stitch thất bại: {stem}")
        return False

    print(f"  [done] {stem} -> {out_path}")
    return True


def process_series(
    files: list[str],
    cfg: dict,
    glossary: Glossary,
    progress: dict,
    done_set: set,
) -> int:
    """Series mode: dịch cả bộ theo TỪNG NHÓM chương (batch_chapters, mặc định 20).

    Trong mỗi nhóm, tiles gom vào 1 thư mục phẳng → context dịch liền mạch;
    giữa các nhóm, glossary + hồ sơ nhân vật được cập nhật rồi tiêm cho nhóm sau.
    Chia nhóm để bộ hàng trăm chương không làm đầy ổ đĩa (chỉ tiles của 1 nhóm
    tồn tại trên disk tại một thời điểm).

    Returns:
        Số chương thành công.
    """
    pending = [f for f in files if Path(f).stem not in done_set]
    skipped = len(files) - len(pending)
    if skipped:
        print(f"  [skip] {skipped} chương đã xong từ trước")
    if not pending:
        return skipped

    group_size = max(1, int(cfg.get("batch_chapters", 20)))
    groups = [pending[i:i + group_size] for i in range(0, len(pending), group_size)]
    n_ok_total = 0
    for gi, group in enumerate(groups):
        if len(groups) > 1:
            print(f"\n[series] ── Nhóm {gi + 1}/{len(groups)} ({len(group)} chương) ──")
        free_gb = shutil.disk_usage("/").free / 1e9
        if free_gb < 1.2:
            print(f"[series] DỪNG: disk chỉ còn {free_gb:.1f}GB — dọn bớt rồi chạy lại với --resume")
            break
        n_ok = _run_series_group(group, cfg, glossary, progress, f"group-{gi:03d}")
        n_ok_total += n_ok
        if n_ok < len(group):
            print(f"[series] Nhóm {gi + 1} chỉ xong {n_ok}/{len(group)} — vẫn tiếp tục nhóm sau")
    return skipped + n_ok_total


def _run_series_group(
    pending: list[str],
    cfg: dict,
    glossary: Glossary,
    progress: dict,
    group_tag: str,
) -> int:
    """Dịch 1 nhóm chương: split → engine 1 lần → stitch tăng dần → dọn."""
    import time

    tiles_root = os.path.join(cfg["work_dir"], "tiles")
    combined_dir = os.path.join(cfg["work_dir"], "tiles_all")
    os.makedirs(combined_dir, exist_ok=True)
    os.makedirs(cfg["output_dir"], exist_ok=True)

    # 1. Split các chương của nhóm, gom tiles vào combined_dir (hardlink)
    metas = {}
    chapter_tile_stems: dict[str, list[str]] = {}
    for f in pending:
        if shutil.disk_usage("/").free / 1e9 < 1.0:
            print("[series] DỪNG split: disk còn dưới 1GB")
            return 0
        stem = Path(f).stem
        meta_path = split_chapter(
            os.path.join(cfg["input_dir"], f),
            tiles_root,
            cfg["tile_height"],
            cfg["overlap"],
        )
        metas[stem] = meta_path
        with open(meta_path, "r", encoding="utf-8") as mf:
            meta = json.load(mf)
        chapter_tile_stems[stem] = [Path(t["file"]).stem for t in meta["tiles"]]
        chapter_tiles = os.path.join(tiles_root, stem)
        for t in os.listdir(chapter_tiles):
            if not t.endswith((".png", ".jpg")):
                continue
            src = os.path.join(chapter_tiles, t)
            dst = os.path.join(combined_dir, t)
            if not os.path.exists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy(src, dst)

    # 2. Export glossary + hồ sơ nhân vật MỚI NHẤT (nhóm trước vừa bổ sung)
    mit_glossary = None
    if cfg["glossary_export_mit"] and glossary.entries:
        mit_glossary = os.path.join(cfg["work_dir"], "glossary_mit.txt")
        glossary.to_mit_format(mit_glossary)

    # 3. Chạy engine cho nhóm, STITCH TĂNG DẦN: chương nào đủ tiles là xuất ngay
    translated_dir = os.path.join(cfg["work_dir"], "translated_all")
    os.makedirs(translated_dir, exist_ok=True)
    text_file = os.path.join(cfg["work_dir"], "texts", f"{group_tag}.txt")
    gpt_config = build_gpt_config(cfg, glossary)
    cmd_env = _mit_cmd_env(
        combined_dir, translated_dir, text_file, cfg, mit_glossary, gpt_config
    )
    if cmd_env is None:
        return 0
    cmd, env = cmd_env
    print(f"[mit] Dịch {combined_dir} -> {translated_dir} (stitch tăng dần)")
    proc = subprocess.Popen(cmd, env=env)

    order = [Path(f).stem for f in pending]
    file_of = {Path(f).stem: f for f in pending}
    sliced = cfg.get("output_format", "slices") == "slices"
    stitched: set[str] = set()

    def tile_done(tile_stem: str, names: list[str]) -> bool:
        return any(
            n.startswith(tile_stem) and Path(n).suffix.lower() in SUPPORTED_EXTS
            for n in names
        )

    def try_stitch(engine_alive: bool) -> None:
        names = os.listdir(translated_dir) if os.path.isdir(translated_dir) else []
        for i, stem in enumerate(order):
            if stem in stitched:
                continue
            if not all(tile_done(t, names) for t in chapter_tile_stems[stem]):
                continue
            # Engine ghi file tuần tự → chương i chắc chắn ghi xong khi chương
            # i+1 đã có tile đầu tiên (hoặc engine đã thoát).
            if engine_alive:
                if i + 1 >= len(order):
                    continue
                if not tile_done(chapter_tile_stems[order[i + 1]][0], names):
                    continue
            out_path = chapter_out_path(cfg, file_of[stem])
            if stitch_chapter(metas[stem], translated_dir, out_path, sliced=sliced):
                stitched.add(stem)
                if stem not in progress["completed"]:
                    progress["completed"].append(stem)
                save_progress(progress, cfg["progress_file"])
                print(f"  [done] {stem} -> {out_path}", flush=True)
                if cfg.get("clean_work"):
                    # Dọn ngay tiles + translated của chương này → disk peak thấp
                    shutil.rmtree(os.path.join(tiles_root, stem), ignore_errors=True)
                    for t in chapter_tile_stems[stem]:
                        for n in list(names):
                            if n.startswith(t):
                                for d in (translated_dir, combined_dir):
                                    try:
                                        os.remove(os.path.join(d, n))
                                    except FileNotFoundError:
                                        pass
                        for ext in (".png", ".jpg"):
                            try:
                                os.remove(os.path.join(combined_dir, f"{t}{ext}"))
                            except FileNotFoundError:
                                pass
            else:
                print(f"  ⚠️  Stitch thất bại: {stem}")
                stitched.add(stem)  # tránh retry vô hạn

    while proc.poll() is None:
        try_stitch(engine_alive=True)
        time.sleep(5)
    try_stitch(engine_alive=False)

    if proc.returncode != 0:
        print(f"[mit] LỖI (exit {proc.returncode}) — chương đã stitch vẫn giữ nguyên")

    # 4. Update glossary + hồ sơ nhân vật từ text nhóm này (nhóm sau hưởng ngay)
    if cfg["glossary_extract_llm"]:
        try:
            update_glossary_from_text_file(text_file, cfg, glossary)
        except Exception as e:
            print(f"  [glossary] Skip extract (lỗi: {e})")

    # 5. Dọn phần còn lại của work nếu nhóm trọn vẹn
    n_ok = len([s for s in stitched if s in progress["completed"]])
    if cfg.get("clean_work") and n_ok == len(pending):
        for d in (combined_dir, translated_dir):
            shutil.rmtree(d, ignore_errors=True)
    return n_ok


def parse_translations(text_file: str) -> str:
    """Đọc file --save-text-file của MIT, ghép các cặp text gốc + bản dịch.

    Format file: các block "text:  <gốc>" / "trans: <dịch>" per region.
    """
    if not os.path.exists(text_file):
        return ""
    pairs = []
    src = None
    with open(text_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("text:"):
                src = line[len("text:"):].strip()
            elif line.startswith("trans:"):
                trans = line[len("trans:"):].strip()
                if trans:
                    pairs.append(f"{src or ''} => {trans}")
                src = None
    return "\n".join(pairs)


def update_glossary_from_text_file(
    text_file: str, cfg: dict, glossary: Glossary
) -> None:
    """Extract proper nouns + hồ sơ nhân vật từ text đã dịch, update glossary."""
    text_content = parse_translations(text_file)
    if not text_content.strip():
        return

    if cfg["glossary_extract_llm"]:
        try:
            from openai import OpenAI

            from glossary import extract_character_sheet_llm, extract_proper_nouns_llm

            client = OpenAI(
                base_url=cfg["ollama_base_url"],
                api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
            )
            entries = extract_proper_nouns_llm(
                text_content, client, cfg["ollama_model"]
            )
            if entries:
                glossary.merge(entries)

            # Hồ sơ nhân vật: xưng hô + giọng điệu (tiêm vào system prompt lần sau)
            sheet = []
            if cfg.get("character_sheet", True):
                sheet = extract_character_sheet_llm(
                    text_content, glossary.characters, client, cfg["ollama_model"]
                )
                if sheet:
                    glossary.update_characters(sheet)

            if entries or sheet:
                glossary.save()
                return
        except ImportError:
            print("  [glossary] openai lib chưa cài, fallback sang regex")

    # Fallback: regex extract
    from glossary import extract_proper_nouns_regex

    entries = extract_proper_nouns_regex(text_content)
    if entries:
        glossary.merge(entries)
        glossary.save()


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline dịch webtoon Korean → Vietnamese end-to-end."
    )
    parser.add_argument("--config", default="config.yaml", help="File config YAML")
    parser.add_argument("--resume", action="store_true", help="Bỏ qua chương đã xong")
    parser.add_argument("--chapter", type=str, help="Chỉ chạy 1 chương theo tên")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Validate OLLAMA_API_KEY (chỉ cần khi dịch qua Ollama Cloud)
    if cfg["translator"] in ("chatgpt", "custom_openai") and not os.environ.get("OLLAMA_API_KEY"):
        print("LỖI: Set OLLAMA_API_KEY trước khi chạy!")
        print("  export OLLAMA_API_KEY=your_key_here")
        sys.exit(1)

    # Validate manga_translator đã cài
    try:
        import importlib.util

        if importlib.util.find_spec("manga_translator") is None:
            raise ImportError
    except ImportError:
        print("LỖI: manga-image-translator chưa cài trong môi trường Python này.")
        print("  Xem README mục Cài đặt (cài từ source, không có trên PyPI).")
        sys.exit(1)

    # Load progress
    progress = load_progress(cfg["progress_file"])
    done_set = set(progress["completed"]) if args.resume else set()

    # Load glossary
    glossary = Glossary(cfg["glossary_file"])

    # List chương cần xử lý (natural sort: ch2 trước ch10)
    input_dir = cfg["input_dir"]
    if not os.path.exists(input_dir):
        print(f"LỖI: Thư mục input không tồn tại: {input_dir}")
        sys.exit(1)

    files = sorted(
        (
            f
            for f in os.listdir(input_dir)
            if Path(f).suffix.lower() in SUPPORTED_EXTS
        ),
        key=natural_key,
    )
    if args.chapter:
        files = [f for f in files if Path(f).stem == args.chapter or f == args.chapter]
        if not files:
            print(f"Không tìm thấy chương: {args.chapter}")
            sys.exit(1)

    font = find_font(cfg["fonts_dir"])
    print(f"\n🚀 Pipeline webtoon translator")
    print(f"   Input:    {input_dir} ({len(files)} chương)")
    print(f"   Output:   {cfg['output_dir']}")
    print(f"   Model:    {cfg['ollama_model']} @ {cfg['ollama_base_url']}")
    print(f"   Target:   {cfg['target_lang']}")
    print(f"   Font:     {font or 'KHÔNG CÓ (bỏ font .ttf vào fonts/)'}")
    print(f"   GPU:      {'bật (MPS/CUDA tự chọn)' if cfg['use_gpu'] else 'tắt'}")
    print(f"   Context:  {cfg['context_size']} tiles")
    print(f"   Glossary: {len(glossary.entries)} entries")
    print()

    if cfg["series_mode"]:
        # Dịch cả series trong 1 lần chạy engine → context xuyên chương
        print("[pipeline] Series mode: dịch toàn bộ trong 1 lần chạy (context xuyên chương)\n")
        success_count = process_series(files, cfg, glossary, progress, done_set)
    else:
        # Chạy engine riêng từng chương
        success_count = 0
        for f in files:
            stem = Path(f).stem
            if stem in done_set:
                print(f"  [skip] {stem} đã xong")
                success_count += 1
                continue
            progress["current"] = stem
            save_progress(progress, cfg["progress_file"])
            if process_chapter(f, cfg, glossary):
                success_count += 1
                if stem not in progress["completed"]:
                    progress["completed"].append(stem)
                progress["current"] = None
                save_progress(progress, cfg["progress_file"])
            else:
                print(f"  ⚠️  Chương {stem} thất bại, tiếp tục chương sau")

    print(f"\n{'='*60}")
    print(f"✅ Hoàn thành: {success_count}/{len(files)} chương")
    print(f"   Glossary: {len(glossary.entries)} entries")
    print(f"   Output:   {cfg['output_dir']}")
    print(f"{'='*60}")

    # Xóa raw sau khi dịch trọn bộ (không áp dụng khi chạy --chapter lẻ)
    if (
        cfg.get("delete_raw")
        and not args.chapter
        and files
        and success_count == len(files)
    ):
        verified = all(chapter_output_ok(cfg, f) for f in files)
        if verified:
            shutil.rmtree(input_dir)
            print(f"🗑️  Đã xóa thư mục raw: {input_dir} (output đã verify đủ {len(files)} chương)")
        else:
            print("⚠️  KHÔNG xóa raw: có file output thiếu/rỗng dù pipeline báo thành công")


if __name__ == "__main__":
    main()
