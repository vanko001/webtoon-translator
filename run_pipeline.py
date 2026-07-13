"""Orchestrator chính: chạy pipeline end-to-end từ input/ đến output/.

Pipeline:
  1. pre_split: cắt ảnh webtoon dài thành tiles ~6000px (overlap 200px)
  2. translate: chạy manga-image-translator per-tile folder qua Ollama Cloud
     - --context-size 3: truyền 3 tile trước làm context
     - --save-text: lưu text để resume
     - glossary tích lũy xuyên suốt series
  3. glossary: extract proper nouns từ text đã dịch, tích lũy
  4. post_stitch: ghép tiles lại thành ảnh dài hoàn chỉnh
  5. checkpoint: ghi progress.json để resume nếu crash

Cách chạy:
  python run_pipeline.py
  python run_pipeline.py --resume        # bỏ qua chương đã xong
  python run_pipeline.py --chapter 5     # chỉ chạy chương 5
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from pre_split import split_chapter, DEFAULT_TILE_HEIGHT, DEFAULT_OVERLAP
from post_stitch import stitch_chapter
from glossary import Glossary

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
    "target_lang": "Vietnamese",
    "source_lang": "Korean",
    "context_size": 3,
    "batch_size": 3,
    "use_gpu": True,
    "tile_height": DEFAULT_TILE_HEIGHT,
    "overlap": DEFAULT_OVERLAP,
    # glossary
    "glossary_extract_llm": True,  # Dùng LLM extract proper nouns
    "glossary_export_mit": True,  # Export sang format MIT
}


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


def run_mit(
    tiles_dir: str,
    out_dir: str,
    cfg: dict,
    glossary_path: str | None = None,
) -> bool:
    """Chạy manga-image-translator cho 1 folder tiles.

    Args:
        tiles_dir: Thư mục chứa tiles cần dịch.
        out_dir: Thư mục output cho tiles đã dịch.
        cfg: Config dict.
        glossary_path: Đường dẫn glossary file (MIT format) nếu có.

    Returns:
        True nếu thành công.
    """
    # Build env cho subprocess
    env = os.environ.copy()
    env["CUSTOM_OPENAI_API_BASE"] = cfg["ollama_base_url"]
    env["CUSTOM_OPENAI_MODEL"] = cfg["ollama_model"]
    # API key phải set từ env OLLAMA_API_KEY
    if "OLLAMA_API_KEY" not in env:
        print("[pipeline] CẢNH BÁO: OLLAMA_API_KEY chưa set!")
        return False
    env["CUSTOM_OPENAI_API_KEY"] = env["OLLAMA_API_KEY"]

    if glossary_path:
        env["OPENAI_GLOSSARY_PATH"] = glossary_path

    # Build command
    cmd = [
        sys.executable,
        "-m",
        "manga_translator",
        "local",
        "-i",
        tiles_dir,
        "-o",
        out_dir,
        "-f",
        "png",
        "--translator",
        "custom_openai",
        "--target-lang",
        cfg["target_lang"],
        "--context-size",
        str(cfg["context_size"]),
        "--batch-size",
        str(cfg["batch_size"]),
        "--save-text",
    ]
    if cfg["use_gpu"]:
        cmd.append("--use-gpu")
    if cfg.get("source_lang"):
        cmd.extend(["--source-lang", cfg["source_lang"]])

    print(f"[mit] Running: {' '.join(cmd[:6])}... (tiles: {tiles_dir})")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[mit] LỖI (exit {result.returncode}):")
        print(result.stderr[-2000:] if result.stderr else "(no stderr)")
        return False

    # Kiểm tra output có file không
    if not os.path.exists(out_dir) or not os.listdir(out_dir):
        print(f"[mit] Output rỗng: {out_dir}")
        return False

    print(f"[mit] OK -> {out_dir}")
    return True


def process_chapter(
    chapter_file: str,
    cfg: dict,
    glossary: Glossary,
    skip_done: bool = False,
) -> bool:
    """Xử lý 1 chương: split → translate → glossary → stitch.

    Args:
        chapter_file: Tên file ảnh gốc (vd: "ch001.jpg").
        cfg: Config dict.
        glossary: Glossary instance.
        skip_done: Bỏ qua nếu đã xong.

    Returns:
        True nếu thành công.
    """
    stem = Path(chapter_file).stem
    input_path = os.path.join(cfg["input_dir"], chapter_file)

    print(f"\n{'='*60}")
    print(f"[pipeline] Xử lý chương: {chapter_file}")
    print(f"{'='*60}")

    # Skip nếu đã done
    if skip_done and stem in cfg.get("_done_set", set()):
        print(f"  [skip] {stem} đã xong")
        return True

    # 1. Pre-split
    tiles_root = os.path.join(cfg["work_dir"], "tiles")
    meta_path = split_chapter(
        input_path, tiles_root, cfg["tile_height"], cfg["overlap"]
    )
    tiles_dir = os.path.join(tiles_root, stem)

    # 2. Export glossary sang MIT format
    mit_glossary = None
    if cfg["glossary_export_mit"] and glossary.entries:
        mit_glossary = os.path.join(cfg["work_dir"], "glossary_mit.txt")
        glossary.to_mit_format(mit_glossary)

    # 3. Translate qua manga-image-translator
    translated_dir = os.path.join(cfg["work_dir"], "translated", stem)
    os.makedirs(translated_dir, exist_ok=True)
    success = run_mit(tiles_dir, translated_dir, cfg, mit_glossary)
    if not success:
        print(f"  [fail] Dịch thất bại: {stem}")
        return False

    # 4. Update glossary từ text đã dịch
    if cfg["glossary_extract_llm"]:
        try:
            update_glossary_from_chapter(translated_dir, cfg, glossary)
        except Exception as e:
            print(f"  [glossary] Skip extract (lỗi: {e})")

    # 5. Post-stitch
    out_path = os.path.join(cfg["output_dir"], f"{stem}{Path(chapter_file).suffix}")
    os.makedirs(cfg["output_dir"], exist_ok=True)
    if not stitch_chapter(meta_path, translated_dir, out_path):
        print(f"  [fail] Stitch thất bại: {stem}")
        return False

    print(f"  [done] {stem} -> {out_path}")
    return True


def update_glossary_from_chapter(
    translated_dir: str, cfg: dict, glossary: Glossary
) -> None:
    """Extract proper nouns từ text đã dịch của 1 chương, update glossary."""
    # Tìm file _translated.txt hoặc .txt do --save-text tạo ra
    text_content = ""
    for f in os.listdir(translated_dir):
        if f.endswith("_translated.txt") or f.endswith(".txt"):
            with open(os.path.join(translated_dir, f), "r", encoding="utf-8") as fh:
                text_content += fh.read() + "\n"

    if not text_content.strip():
        return

    # Dùng LLM extract nếu có OpenAI client
    if cfg["glossary_extract_llm"]:
        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=cfg["ollama_base_url"],
                api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
            )
            from glossary import extract_proper_nouns_llm

            entries = extract_proper_nouns_llm(text_content, client, cfg["ollama_model"])
            if entries:
                glossary.merge(entries)
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

    # Validate OLLAMA_API_KEY
    if not os.environ.get("OLLAMA_API_KEY"):
        print("LỖI: Set OLLAMA_API_KEY trước khi chạy!")
        print("  export OLLAMA_API_KEY=your_key_here")
        sys.exit(1)

    # Load progress
    progress = load_progress(cfg["progress_file"])
    cfg["_done_set"] = set(progress["completed"]) if args.resume else set()

    # Load glossary
    glossary = Glossary(cfg["glossary_file"])

    # List chương cần xử lý
    input_dir = cfg["input_dir"]
    if not os.path.exists(input_dir):
        print(f"LỖI: Thư mục input không tồn tại: {input_dir}")
        sys.exit(1)

    files = sorted(
        f
        for f in os.listdir(input_dir)
        if Path(f).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if args.chapter:
        files = [f for f in files if Path(f).stem == args.chapter or f == args.chapter]
        if not files:
            print(f"Không tìm thấy chương: {args.chapter}")
            sys.exit(1)

    print(f"\n🚀 Pipeline webtoon translator")
    print(f"   Input:    {input_dir} ({len(files)} chương)")
    print(f"   Output:   {cfg['output_dir']}")
    print(f"   Model:    {cfg['ollama_model']} @ {cfg['ollama_base_url']}")
    print(f"   Target:   {cfg['target_lang']}")
    print(f"   Context:  {cfg['context_size']} tiles")
    print(f"   Glossary: {len(glossary.entries)} entries")
    print()

    # Chạy từng chương
    success_count = 0
    for f in files:
        stem = Path(f).stem
        ok = process_chapter(f, cfg, glossary, skip_done=args.resume)
        if ok:
            success_count += 1
            if stem not in progress["completed"]:
                progress["completed"].append(stem)
            save_progress(progress, cfg["progress_file"])
        else:
            print(f"  ⚠️  Chương {stem} thất bại, tiếp tục chương sau")

    print(f"\n{'='*60}")
    print(f"✅ Hoàn thành: {success_count}/{len(files)} chương")
    print(f"   Glossary: {len(glossary.entries)} entries")
    print(f"   Output:   {cfg['output_dir']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()