"""Web đọc truyện + điều khiển pipeline dịch từ xa.

Chạy trên Mac mini, truy cập qua Tailscale (không expose internet công khai).

    venv/bin/uvicorn webapp.app:app --host 0.0.0.0 --port 8765

Tính năng:
- Thư viện truyện từ output/ (tên giữ nguyên theo thư mục manhwa/)
- Reader: ảnh chương tự cắt lát ~2400px (mobile không decode nổi ảnh 90k px)
- Điều khiển: xem trạng thái dịch từng bộ, bấm dịch từ xa (hàng đợi tuần tự,
  1 job một lúc vì chỉ có 1 GPU), stream log realtime qua SSE
"""

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

REPO = Path(__file__).resolve().parent.parent
MANHWA = REPO.parent / "manhwa"
OUTPUT = REPO / "output"
CACHE = REPO / "webapp" / "cache"
LOGS = REPO / "work" / "webapp-logs"
PY = REPO / "venv" / "bin" / "python"

SLICE_HEIGHT = 2400
SLICE_QUALITY = 82
CACHE_LIMIT_GB = 1.2
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Google Drive qua rclone: ảnh gốc có thể nằm trên Drive (private) thay vì local.
# Đọc chương chưa có local → tự tải từ Drive về, cắt lát, chỉ giữ lát trong cache.
RCLONE = shutil.which("rclone") or "/opt/homebrew/bin/rclone"
REMOTE = "gdrive:webtoon-output"


def rclone_ready() -> bool:
    try:
        out = subprocess.run([RCLONE, "listremotes"], capture_output=True, text=True, timeout=10)
        return "gdrive:" in out.stdout
    except Exception:
        return False


_remote_cache: dict = {"t": 0.0, "data": {}}


def remote_chapters() -> dict[str, list[str]]:
    """Danh sách chương trên Drive theo series (cache 5 phút)."""
    if time.time() - _remote_cache["t"] < 300:
        return _remote_cache["data"]
    data: dict[str, list[str]] = {}
    if rclone_ready():
        try:
            out = subprocess.run(
                [RCLONE, "lsjson", REMOTE, "--recursive", "--files-only"],
                capture_output=True, text=True, timeout=60,
            )
            if out.returncode == 0:
                for e in json.loads(out.stdout):
                    p = e.get("Path", "")
                    if "/" in p:
                        s, f = p.split("/", 1)
                        if "/" not in f and Path(f).suffix.lower() in IMG_EXTS:
                            data.setdefault(s, []).append(f)
                for s in data:
                    data[s].sort(key=natural_key)
        except Exception:
            data = _remote_cache["data"]
    _remote_cache.update(t=time.time(), data=data)
    return _remote_cache["data"]


def merged_chapters(series: str) -> list[str]:
    """Chương của 1 bộ: local ∪ Drive."""
    merged = set(list_images(OUTPUT / series)) | set(remote_chapters().get(series, []))
    return sorted(merged, key=natural_key)


def ensure_original(series: str, chapter: str) -> tuple[Path, bool]:
    """Trả về (đường dẫn ảnh gốc, có phải file tạm tải từ Drive không)."""
    local = OUTPUT / series / chapter
    if local.is_file():
        return local, False
    if chapter not in remote_chapters().get(series, []):
        raise HTTPException(404, "Không có chương này (cả local lẫn Drive)")
    tmp = CACHE / "_dl" / series / chapter
    tmp.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [RCLONE, "copyto", f"{REMOTE}/{series}/{chapter}", str(tmp)],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0 or not tmp.is_file():
        raise HTTPException(502, f"Tải từ Drive lỗi: {r.stderr[-200:]}")
    return tmp, True

load_dotenv(REPO / ".env")

app = FastAPI(title="Webtoon Reader")
app.mount("/static", StaticFiles(directory=REPO / "webapp" / "static"), name="static")
jinja = Environment(loader=FileSystemLoader(REPO / "webapp" / "templates"))

SETTINGS_FILE = REPO / "webapp" / "settings.json"
TITLES_FILE = REPO / "titles.json"
KNOWN_MODELS = ["glm-5.2", "deepseek-v4-pro", "deepseek-v4-flash", "qwen3.5",
                "kimi-k2.6", "minimax-m3", "gpt-oss"]
DEFAULT_SETTINGS = {"model": "glm-5.2", "auto_offload": True}


def load_settings() -> dict:
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1))


def load_titles() -> dict:
    try:
        return json.loads(TITLES_FILE.read_text())
    except Exception:
        return {}


def display_name(slug: str) -> str:
    return load_titles().get(slug, slug)


def translate_title(slug: str) -> str | None:
    """Dịch tên truyện (slug -> tiếng Việt) bằng model đang chọn, lưu titles.json."""
    key = os.environ.get("OLLAMA_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(base_url="https://ollama.com/v1", api_key=key)
        raw = slug.replace("-", " ").strip()
        resp = client.chat.completions.create(
            model=load_settings()["model"],
            temperature=0.3,
            messages=[{"role": "user", "content":
                f"Dịch tên truyện manhwa sau sang tiếng Việt tự nhiên, ngắn gọn, hấp dẫn "
                f"(giữ tên riêng nhân vật nếu có). CHỈ trả về tên đã dịch, không giải thích:\n{raw}"}],
        )
        title = resp.choices[0].message.content.strip().strip('"“”').splitlines()[0][:120]
        if title:
            titles = load_titles()
            titles[slug] = title
            TITLES_FILE.write_text(json.dumps(titles, ensure_ascii=False, indent=1))
            return title
    except Exception:
        pass
    return None


CONFIG_TMPL = """# Auto-generated by webapp
input_dir: "{input_dir}"
output_dir: "output/{name}"
work_dir: "work/{name}"
fonts_dir: "fonts"
glossary_file: "glossary-{name}.json"
progress_file: "work/{name}/progress.json"
ollama_base_url: "https://ollama.com/v1"
ollama_model: "{model}"
translator: "chatgpt"
series_mode: true
target_lang: "VIN"
context_size: 10
batch_size: 3
use_gpu: true
attempts: 3
system_prompt_file: "prompts/vietnamese-adult.txt"
gpt_temperature: 0.55
character_sheet: true
inpainter: "lama_mpe"
inpainting_size: 1536
tile_height: 8500
overlap: 200
glossary_extract_llm: true
glossary_export_mit: true
clean_work: true
delete_raw: true
"""


def natural_key(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def list_images(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(
        (f for f in os.listdir(d) if Path(f).suffix.lower() in IMG_EXTS),
        key=natural_key,
    )


# ---------------------------------------------------------------- job manager
class JobManager:
    """Hàng đợi dịch tuần tự: 1 job chạy, các job sau xếp hàng."""

    def __init__(self):
        self.q: queue.Queue[str] = queue.Queue()
        self.queued: list[str] = []
        self.current: str | None = None
        self.current_log: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.history: list[dict] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    def enqueue(self, series: str, kind: str = "translate") -> str:
        label = f"{kind}:{series}"
        with self.lock:
            if label == self.current or label in self.queued:
                return "đã có trong hàng đợi"
            self.queued.append(label)
        self.q.put(label)
        return "đã xếp hàng"

    def stop_current(self) -> str:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                return f"đã dừng {self.current}"
        return "không có job đang chạy"

    def _build_cmd(self, kind: str, series: str) -> list[str]:
        if kind == "offload":
            # move = upload có verify checksum rồi XÓA file local
            return [RCLONE, "move", str(OUTPUT / series), f"{REMOTE}/{series}",
                    "--checksum", "--transfers", "4", "-v"]
        cfg_dir = REPO / "work" / "batch-configs"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / f"{series}.yaml"
        cfg_path.write_text(
            CONFIG_TMPL.format(
                input_dir=str(MANHWA / series), name=series,
                model=load_settings()["model"],
            ),
            encoding="utf-8",
        )
        return [str(PY), "run_pipeline.py", "--config", str(cfg_path), "--resume"]

    def _worker(self):
        while True:
            label = self.q.get()
            kind, series = label.split(":", 1)
            with self.lock:
                self.queued.remove(label)
                self.current = label
            LOGS.mkdir(parents=True, exist_ok=True)
            log_path = LOGS / f"{kind}-{series}-{int(time.time())}.log"
            self.current_log = log_path
            t0 = time.time()
            with open(log_path, "w") as log:
                self.proc = subprocess.Popen(
                    self._build_cmd(kind, series),
                    cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                )
                rc = self.proc.wait()
            if kind == "offload":
                _remote_cache["t"] = 0  # bust cache danh sách Drive
                d = OUTPUT / series
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            if kind == "translate" and rc == 0:
                if series not in load_titles():
                    translate_title(series)  # dịch tên truyện sau khi dịch xong bộ
                # Tự động đẩy output lên Drive rồi xóa local (nếu bật + đã kết nối)
                if (
                    load_settings().get("auto_offload")
                    and rclone_ready()
                    and list_images(OUTPUT / series)
                ):
                    self.enqueue(series, kind="offload")
            with self.lock:
                self.history.insert(0, {
                    "series": label, "exit": rc,
                    "minutes": round((time.time() - t0) / 60, 1),
                    "at": time.strftime("%H:%M %d/%m"),
                })
                self.history = self.history[:20]
                self.current = None
                self.proc = None


jobs = JobManager()


# ---------------------------------------------------------------- slicing
def chapter_slices(series: str, chapter: str) -> list[str]:
    """Trả về danh sách file lát của 1 chương, cắt nếu chưa có trong cache.

    Ảnh gốc lấy từ local, không có thì tự tải từ Drive (xóa file tạm sau khi cắt).
    """
    cdir = CACHE / series / Path(chapter).stem
    done_marker = cdir / ".done"
    if done_marker.exists():
        os.utime(cdir)  # đánh dấu mới dùng (cho LRU)
        return sorted(
            (f for f in os.listdir(cdir) if f.endswith(".jpg")), key=natural_key
        )
    src, is_tmp = ensure_original(series, chapter)
    cdir.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGB")
    w, h = img.size
    names = []
    for i, y in enumerate(range(0, h, SLICE_HEIGHT)):
        piece = img.crop((0, y, w, min(y + SLICE_HEIGHT, h)))
        name = f"{i:03d}.jpg"
        piece.save(cdir / name, quality=SLICE_QUALITY, progressive=True)
        names.append(name)
    done_marker.touch()
    if is_tmp:
        img.close()
        src.unlink(missing_ok=True)
    _evict_cache()
    return names


def _evict_cache():
    """Giữ cache dưới giới hạn: xóa chương lâu không đọc nhất."""
    entries = []
    total = 0
    for sdir in CACHE.iterdir() if CACHE.is_dir() else []:
        if not sdir.is_dir():
            continue
        for cdir in sdir.iterdir():
            size = sum(f.stat().st_size for f in cdir.glob("*.jpg"))
            total += size
            entries.append((cdir.stat().st_mtime, size, cdir))
    limit = CACHE_LIMIT_GB * 1e9
    if total <= limit:
        return
    for _, size, cdir in sorted(entries):
        shutil.rmtree(cdir, ignore_errors=True)
        total -= size
        if total <= limit:
            break


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def library():
    # Mọi thư mục output có ảnh đều là truyện (raw trong manhwa/ có thể đã xóa
    # sau khi dịch xong — không được lọc theo manhwa/)
    local = {
        d for d in (os.listdir(OUTPUT) if OUTPUT.is_dir() else [])
        if not d.startswith("model-test-") and list_images(OUTPUT / d)
    }
    names = sorted(local | set(remote_chapters().keys()), key=natural_key)
    titles = load_titles()
    series = []
    for name in names:
        chapters = merged_chapters(name)
        if chapters:
            series.append({"name": name, "title": titles.get(name, name),
                           "n": len(chapters), "first": Path(chapters[0]).stem})
    return jinja.get_template("library.html").render(series=series)


@app.get("/doc/{series}", response_class=HTMLResponse)
def series_page(series: str):
    chapters = merged_chapters(series)
    if not chapters:
        raise HTTPException(404)
    chs = [{"file": c, "stem": Path(c).stem} for c in chapters]
    return jinja.get_template("series.html").render(
        series=series, title=display_name(series), chapters=chs
    )


@app.get("/doc/{series}/{stem}", response_class=HTMLResponse)
def reader(series: str, stem: str):
    chapters = merged_chapters(series)
    stems = [Path(c).stem for c in chapters]
    if stem not in stems:
        raise HTTPException(404)
    chapter = chapters[stems.index(stem)]
    slices = chapter_slices(series, chapter)
    i = stems.index(stem)
    prev_ch = stems[i - 1] if i > 0 else None
    next_ch = stems[i + 1] if i + 1 < len(stems) else None
    return jinja.get_template("reader.html").render(
        series=series, title=display_name(series), stem=stem,
        slices=slices, prev=prev_ch, next=next_ch,
    )


@app.get("/cover/{series}")
def cover(series: str):
    """Thumbnail bìa: phần đầu chương 1, rộng 480px."""
    p = CACHE / series / "_cover.jpg"
    if not p.is_file():
        chapters = merged_chapters(series)
        if not chapters:
            raise HTTPException(404)
        src, is_tmp = ensure_original(series, chapters[0])
        img = Image.open(src).convert("RGB")
        w, h = img.size
        img = img.crop((0, 0, w, min(h, int(w * 4 / 3))))
        img.thumbnail((480, 640))
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p, quality=80)
        if is_tmp:
            src.unlink(missing_ok=True)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/img/{series}/{stem}/{name}")
def slice_img(series: str, stem: str, name: str):
    p = CACHE / series / stem / name
    if not re.fullmatch(r"\d{3}\.jpg", name) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/control", response_class=HTMLResponse)
def control_page():
    return jinja.get_template("control.html").render()


# ---------------------------------------------------------------- api
@app.get("/api/status")
def api_status():
    rows = []
    titles = load_titles()
    for name in sorted(os.listdir(MANHWA) if MANHWA.is_dir() else [], key=natural_key):
        d = MANHWA / name
        if not d.is_dir():
            continue
        n_in = len(list_images(d))
        n_local = len(list_images(OUTPUT / name))
        n_drive = len(remote_chapters().get(name, []))
        n_out = max(n_local, n_drive)
        if jobs.current == f"translate:{name}":
            state = "đang dịch"
        elif jobs.current == f"offload:{name}":
            state = "đang đẩy Drive"
        elif any(q.endswith(f":{name}") for q in jobs.queued):
            state = "xếp hàng"
        elif n_out == 0:
            state = "chưa dịch"
        elif n_out < n_in:
            state = "dở dang"
        else:
            state = "hoàn thành"
        size_mb = round(sum(f.stat().st_size for f in d.iterdir() if f.is_file()) / 1e6)
        rows.append({"name": name, "title": titles.get(name, ""), "in": n_in,
                     "out": n_out, "local": n_local, "drive": n_drive,
                     "state": state, "mb": size_mb})
    disk_free_gb = round(shutil.disk_usage("/").free / 1e9, 1)
    return {"series": rows, "current": jobs.current, "queued": jobs.queued,
            "history": jobs.history, "disk_free_gb": disk_free_gb,
            "drive_ready": rclone_ready()}


@app.get("/api/settings")
def api_settings_get():
    return {**load_settings(), "known_models": KNOWN_MODELS}


@app.post("/api/settings")
def api_settings_set(payload: dict):
    s = load_settings()
    model = str(payload.get("model", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,60}", model):
        raise HTTPException(400, "Tên model không hợp lệ")
    s["model"] = model
    if "auto_offload" in payload:
        s["auto_offload"] = bool(payload["auto_offload"])
    save_settings(s)
    return {"result": f"Đã lưu (model: {model}, tự đẩy Drive: {'bật' if s['auto_offload'] else 'tắt'})"}


@app.post("/api/title/{series}")
def api_title(series: str):
    title = translate_title(series)
    if not title:
        raise HTTPException(502, "Dịch tên thất bại (kiểm tra OLLAMA_API_KEY)")
    return {"title": title}


@app.post("/api/offload/{series}")
def api_offload(series: str):
    """Đẩy ảnh gốc của 1 bộ lên Drive (verify checksum xong mới xóa local)."""
    if not rclone_ready():
        raise HTTPException(400, "Chưa kết nối Google Drive — chạy: rclone config create gdrive drive")
    if not list_images(OUTPUT / series):
        raise HTTPException(404, "Bộ này không có ảnh local để đẩy")
    return {"result": jobs.enqueue(series, kind="offload")}


@app.post("/api/translate/{series}")
def api_translate(series: str):
    if not (MANHWA / series).is_dir():
        raise HTTPException(404, "Không có bộ này trong manhwa/")
    if shutil.disk_usage("/").free < 1.5e9:
        raise HTTPException(507, "Disk còn dưới 1.5GB — dọn bớt trước khi dịch")
    return {"result": jobs.enqueue(series)}


@app.post("/api/stop")
def api_stop():
    return {"result": jobs.stop_current()}


@app.get("/api/logs/stream")
def logs_stream():
    """SSE: tail log của job đang chạy."""

    def gen():
        sent_path = None
        pos = 0
        idle = 0.0
        while idle < 3600:
            log = jobs.current_log
            if log and log.exists():
                if sent_path != log:
                    sent_path, pos = log, 0
                    yield f"data: ===== {log.name} =====\n\n"
                with open(log, "r", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    idle = 0.0
                    for line in chunk.splitlines():
                        yield f"data: {line}\n\n"
            time.sleep(1)
            idle += 1
            yield ": keepalive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
