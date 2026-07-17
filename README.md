# 🎨 Webtoon Translator Pipeline

Pipeline tự động dịch **webtoon / manhwa (Korean → Vietnamese)**, giữ ngữ cảnh xuyên suốt toàn bộ truyện, render text vào ảnh đúng vùng bubble. Chạy end-to-end từ folder ảnh raw đến ảnh đã chèn text Vietnamese.

![Pipeline](https://img.shields.io/badge/Pipeline-end--to--end-blue) ![Language](https://img.shields.io/badge/Translate-Korean--%3EVietnamese-green) ![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 📖 Mục lục

- [Tính năng](#-tính-năng)
- [Kiến trúc pipeline](#-kiến-trúc-pipeline)
- [Yêu cầu hệ thống](#-yêu-cầu-hệ-thống)
- [Cài đặt](#-cài-đặt)
- [Cách chạy](#-cách-chạy)
- [Cấu hình](#️-cấu-hình)
- [Cấu trúc thư mục](#-cấu-trúc-thư-mục)
- [Troubleshooting](#-troubleshooting)
- [FAQ](#-faq)

---

## ✨ Tính năng

| Tính năng | Mô tả |
|-----------|-------|
| **End-to-end** | Bỏ ảnh raw vào `input/` → chạy 1 lệnh → nhận ảnh đã dịch trong `output/` |
| **Giữ context xuyên chương** | `series_mode: true` dịch cả bộ trong 1 lần chạy engine + `context_size` tile trước làm context → AI hiểu mạch truyện xuyên suốt series |
| **Glossary tích lũy** | Tự extract tên nhân vật/địa danh, tích lũy xuyên suốt series → dịch consistent |
| **Xử lý ảnh dài** | Tự cắt webtoon strip dài thành tiles, dịch xong ghép lại **chính xác từng pixel** |
| **Cắt thông minh** | Đường cắt tự né text bubble (tìm vùng trống giữa panel) → không bao giờ chữ chồng chữ tại seam |
| **Resume được** | Checkpoint `progress.json`, crash không mất công |
| **Ollama Cloud** | Dịch qua `qwen3.5` / `deepseek-v4-pro` trên Ollama Cloud (không cần GPU local) |
| **Apple Silicon** | Tận dụng MPS acceleration trên Mac M1-M4 (tự bật fallback CPU cho op chưa hỗ trợ) |
| **Smoke-test không cần API key** | `translator: "original"` chạy full pipeline ảnh để test máy trước khi mua key |

---

## 📐 Kiến trúc pipeline

```
input/                           ← Bỏ ảnh raw (.jpeg/.png/.webp, 1 ảnh = 1 chương)
  │
  ▼
[1. pre_split]  Cắt ảnh >6000px thành tiles ~6000px
                Overlap 200px để không cắt đôi text bubble
  │
  ▼
[2. manga-image-translator]  Xử lý từng tile:
    ├─ Text Detection       → Tìm vùng có text (GPU MPS)
    ├─ OCR Korean            → Đọc text gốc
    ├─ Dịch qua Ollama Cloud → Korean → Vietnamese
    │   ├─ Context 3 tiles trước (giữ mạch truyện)
    │   └─ Glossary (giữ tên nhân vật consistent)
    ├─ Inpaint              → Xóa text raw khỏi ảnh
    └─ Render text          → Chèn text Vietnamese vào bubble (font trong fonts/)
  │
  ▼
[3. glossary]   Extract proper nouns từ text đã dịch
                → Tích lũy vào glossary.json (xuyên suốt series)
  │
  ▼
[4. post_stitch]  Ghép tiles lại thành ảnh dài hoàn chỉnh
                  (đường nối đặt giữa vùng overlap, khớp pixel tuyệt đối)
  │
  ▼
output/                          ← Ảnh đã chèn text Vietnamese
```

---

## 💻 Yêu cầu hệ thống

| Yêu cầu | Tối thiểu | Khuyến nghị |
|---------|-----------|-------------|
| **OS** | macOS 12+ / Linux / Windows 10+ | macOS Apple Silicon (M1-M4) |
| **Python** | **3.10 – 3.12** | 3.12 (đã test) |
| **RAM** | 8 GB | 16 GB+ |
| **GPU** | Không bắt buộc (dịch qua Ollama Cloud) | Apple Silicon MPS hoặc NVIDIA CUDA |
| **Ollama Cloud** | API key (có free tier) | Pro plan cho tốc độ cao hơn |
| **Disk** | ~8 GB (Python deps + models của manga-image-translator) | 10 GB+ |

---

## 📦 Cài đặt

### Bước 1: Clone 2 repo (pipeline + engine)

> ⚠️ `manga-image-translator` **không có trên PyPI** — phải cài từ source.

```bash
git clone https://github.com/vanko001/webtoon-translator.git
git clone --depth 1 https://github.com/zyddnys/manga-image-translator.git
cd webtoon-translator
```

### Bước 2: Cài Python 3.12 (nếu chưa có)

```bash
# macOS
brew install python@3.12
```

### Bước 3: Tạo virtual environment + cài dependencies

```bash
python3.12 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate          # Windows

pip install -U pip wheel setuptools
pip install -r requirements.txt
pip install -r ../manga-image-translator/requirements.txt
pip install -e ../manga-image-translator --no-deps --ignore-requires-python
```

> `--ignore-requires-python` vì engine khai báo `<3.12` nhưng chạy tốt trên 3.12 (đã test trên Mac M4).

**Vá 2 bug `--save-text-file` của engine** (bắt buộc — nếu không glossary/character sheet không có dữ liệu):

```bash
cd ../manga-image-translator
git apply ../webtoon-translator/patches/manga-image-translator-save-text-fix.patch
```

### Bước 4: Lấy Ollama Cloud API key

1. Vào **https://ollama.com/settings**
2. Tạo API key
3. Set environment variable:

```bash
# macOS / Linux — thêm vào ~/.zshrc hoặc ~/.bashrc
export OLLAMA_API_KEY=your-key-here

# Verify
echo $OLLAMA_API_KEY
```

### Bước 5: Font Vietnamese

Repo đã kèm sẵn **Noto Sans** (`fonts/NotoSans-Regular.ttf`, hỗ trợ đầy đủ tiếng Việt).
Muốn đổi font: bỏ file `.ttf` khác vào `fonts/` (pipeline tự lấy font đầu tiên theo alphabet).

Font gợi ý: **Be Vietnam Pro**, **Noto Sans**, **Arial Unicode**.

### Bước 6 (khuyến nghị): Smoke-test không cần API key

```bash
python run_pipeline.py --config test_e2e/config.yaml
```

Chạy full pipeline (detect → OCR → inpaint → render → stitch) với `translator: "original"` trên ảnh test — lần đầu sẽ tải models (~1-2 GB). Nếu ra ảnh trong `test_e2e/output/` là máy đã sẵn sàng.

---

## 🚀 Cách chạy

### Chạy toàn bộ pipeline

```bash
# 1. Kích hoạt venv
source venv/bin/activate

# 2. Bỏ ảnh raw vào input/ (1 ảnh = 1 chương)
cp /path/to/your/webtoon/*.jpeg input/

# 3. Chạy pipeline
python run_pipeline.py
```

### Resume (bỏ qua chương đã xong)

```bash
python run_pipeline.py --resume
```

### Chỉ chạy 1 chương

```bash
python run_pipeline.py --chapter ch005
```

### Đổi model dịch

Chỉnh `config.yaml`:

```yaml
ollama_model: "qwen3.5"           # nhanh, đa ngôn ngữ (default)
# ollama_model: "deepseek-v4-pro"  # chất lượng cao, chậm hơn
# ollama_model: "deepseek-v4-flash" # nhanh nhất, rẻ
# ollama_model: "glm-5.2"          # tốt cho Asian languages
```

---

## ⚙️ Cấu hình

File `config.yaml` chứa toàn bộ config. Các thông số quan trọng:

### Ngôn ngữ đích

```yaml
# Dùng MÃ ngôn ngữ của manga-image-translator (không phải tên đầy đủ):
# VIN=Vietnamese, ENG=English, JPN=Japanese, THA=Thai, IND=Indonesian...
target_lang: "VIN"
```

Ngôn ngữ **nguồn** không cần khai báo — OCR tự nhận diện (Korean/Japanese/Chinese đều được).

### Kích thước tile (cắt ảnh)

```yaml
tile_height: 6000   # Chiều cao mỗi tile (px); 8500 = nhanh hơn (ít tile hơn), vẫn an toàn
overlap: 200        # Overlap giữa tiles (px)
```

- Đường cắt tự động né text bubble: quét ±800px quanh vị trí cắt lý tưởng, chọn dải ngang "yên tĩnh" nhất (khoảng trống giữa panel)
- Bị OOM khi inpaint → giảm `tile_height` xuống `4000-5000`

### Context size (giữ mạch truyện)

```yaml
context_size: 3   # Số tile trước truyền làm context khi dịch
```

- Tăng lên `5` nếu muốn AI hiểu mạch truyện sâu hơn (chậm hơn + tốn token hơn)
- Giảm xuống `0-1` nếu muốn chạy nhanh

### Engine tuning (Mac M-series)

```yaml
use_gpu: true                  # MPS tự bật trên Apple Silicon
inpainter: "lama_large"        # đổi "lama_mpe" nếu máy 8GB RAM
inpainting_precision: "bf16"   # bf16 chạy tốt trên MPS (macOS 14+); "fp32" nếu lỗi
detection_size: 2048           # tăng 2560 nếu chữ nhỏ khó detect
batch_size: 3                  # số tile dịch chung 1 batch
```

### Glossary (terminology xuyên series)

```yaml
glossary_extract_llm: true   # Dùng LLM extract proper nouns (tốn thêm API call)
glossary_export_mit: true    # Đưa glossary vào prompt dịch của engine
```

---

## 📁 Cấu trúc thư mục

```
webtoon-translator/
├── run_pipeline.py          # Orchestrator chính — chạy pipeline end-to-end
├── pre_split.py              # Cắt ảnh dài thành tiles (Pillow)
├── post_stitch.py            # Ghép tiles lại thành ảnh dài
├── glossary.py               # Quản lý terminology xuyên suốt series
├── config.yaml               # Config (model, paths, tile size, context)
├── requirements.txt           # Python dependencies
├── README.md                 # File này
│
├── input/                    # ← Bỏ ảnh raw vào đây (1 ảnh = 1 chương)
├── output/                   # ← Ảnh đã dịch xuất ra đây
├── fonts/                    # Font Vietnamese (đã kèm NotoSans-Regular.ttf)
├── test_e2e/                 # Smoke-test không cần API key
│
├── work/                     # Working directory (tự sinh)
│   ├── tiles/                #   Tiles sau khi cắt (+ meta.json từng chương)
│   ├── translated/           #   Tiles đã dịch
│   ├── texts/                #   Text gốc + bản dịch từng chương (sửa tay được)
│   ├── mit_config.json       #   Config sinh tự động cho engine
│   └── progress.json         #   Checkpoint (cho --resume)
│
└── glossary.json             # Terminology tích lũy (tự sinh)
```

---

## 🔧 Troubleshooting

### `OLLAMA_API_KEY chưa set`

```bash
export OLLAMA_API_KEY=your-key-here
echo $OLLAMA_API_KEY
```

### `manga-image-translator chưa cài trong môi trường Python này`

```bash
git clone --depth 1 https://github.com/zyddnys/manga-image-translator.git ../manga-image-translator
pip install -r ../manga-image-translator/requirements.txt
pip install -e ../manga-image-translator --no-deps --ignore-requires-python
```

### OOM (out of memory) khi inpaint

Giảm trong `config.yaml`:

```yaml
tile_height: 4000        # giảm từ 6000
inpainter: "lama_mpe"    # nhẹ hơn lama_large
batch_size: 1
```

### Text render lỗi / vỡ dấu tiếng Việt

Pipeline tự lấy font đầu tiên trong `fonts/`. Kiểm tra folder có file `.ttf` hỗ trợ tiếng Việt (repo kèm sẵn Noto Sans). Log lúc chạy in ra dòng `Font: ...` — nếu ghi `KHÔNG CÓ` thì bỏ font vào `fonts/`.

### Lỗi MPS trên Mac (op không hỗ trợ)

Pipeline tự set `PYTORCH_ENABLE_MPS_FALLBACK=1` (op chưa hỗ trợ tự chạy CPU). Nếu vẫn crash: set `use_gpu: false` trong config.

### Pipeline crash giữa chừng

```bash
python run_pipeline.py --resume
```

→ Tự bỏ qua chương đã xong (dựa trên `work/progress.json`). Trong 1 chương, tile đã dịch xong cũng không dịch lại.

---

## ❓ FAQ

**Q: Pipeline có cần GPU không?**
A: Không bắt buộc. Dịch chạy trên Ollama Cloud; GPU chỉ dùng cho OCR + inpaint. Trên Mac M1-M4, MPS tự bật. Máy không GPU: set `use_gpu: false`.

**Q: Test máy trước khi mua API key được không?**
A: Được: `python run_pipeline.py --config test_e2e/config.yaml` — chạy full pipeline ảnh với translator `original` (giữ nguyên text, không gọi API).

**Q: Có dịch được Japanese/Chinese không?**
A: Có. OCR tự nhận diện ngôn ngữ nguồn, chỉ cần giữ `target_lang: "VIN"`.

**Q: Chi phí Ollama Cloud bao nhiêu?**
A: Xem https://ollama.com/cloud — có free tier đủ để test.

**Q: Có chỉnh sửa text sau khi dịch không?**
A: Text đã dịch lưu trong `work/texts/<chapter>.txt` (kèm tọa độ từng bubble). Chỉnh translation rồi chạy lại chương đó, hoặc dùng editor của manga-image-translator.

**Q: Glossary.json có share giữa project được không?**
A: Có. Copy `glossary.json` sang project khác → tên nhân vật giữ consistent.

---

## 📝 Ghi chú

- Pipeline **không** dịch lại chương đã xong khi `--resume`
- Glossary giữ **bản dịch đầu tiên** của mỗi tên riêng để consistent xuyên chương
- Đường nối giữa 2 tile đặt ở **giữa vùng overlap** → mỗi tile có 100px lề an toàn, ghép lại khớp pixel tuyệt đối
- Output giữ nguyên extension file input (PNG/JPG)
- Repo này dùng `manga-image-translator` làm engine backend ([github.com/zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator))

## 📄 License

MIT License — tự do sử dụng, sửa đổi, phân phối.

## 🙏 Credits

- [manga-image-translator](https://github.com/zyddnys/manga-image-translator) — engine OCR + inpaint + render
- [Ollama Cloud](https://ollama.com/cloud) — LLM backend
- [Pillow](https://python-pillow.org/) — image processing
