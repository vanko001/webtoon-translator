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
| **Giữ context xuyên chương** | Truyền 3 tile trước làm context khi dịch (`--context-size 3`) → AI hiểu mạch truyện |
| **Glossary tích lũy** | Tự extract tên nhân vật/địa danh, tích lũy xuyên suốt 200 chương → dịch consistent toàn series |
| **Xử lý ảnh dài** | Tự cắt webtoon strip dài >8000px thành tiles, dịch xong ghép lại |
| **Resume được** | Checkpoint `progress.json`, crash không mất công |
| **Ollama Cloud** | Dịch qua `qwen3.5` / `deepseek-v4-pro` trên Ollama Cloud (không cần GPU local) |
| **Apple Silicon** | Tận dụng MPS acceleration trên Mac M1-M4 |

---

## 📐 Kiến trúc pipeline

```
input/                           ← Bỏ ảnh raw (.jpeg, 1 ảnh = 1 chương)
  │
  ▼
[1. pre_split]  Cắt ảnh >8000px thành tiles ~6000px
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
    └─ Render text          → Chèn text Vietnamese vào bubble
  │
  ▼
[3. glossary]   Extract proper nouns từ text đã dịch
                → Tích lũy vào glossary.json (xuyên suốt series)
  │
  ▼
[4. post_stitch]  Ghép tiles lại thành ảnh dài hoàn chỉnh
  │
  ▼
output/                          ← Ảnh đã chèn text Vietnamese
```

---

## 💻 Yêu cầu hệ thống

| Yêu cầu | Tối thiểu | Khuyến nghị |
|---------|-----------|-------------|
| **OS** | macOS 12+ / Linux / Windows 10+ | macOS Apple Silicon (M1-M4) |
| **Python** | 3.10+ | 3.12+ |
| **RAM** | 8 GB | 16 GB+ |
| **GPU** | Không bắt buộc (dùng Ollama Cloud) | Apple Silicon MPS hoặc NVIDIA CUDA |
| **Ollama Cloud** | API key (miễn phí) | Pro plan cho tốc độ cao hơn |
| **Disk** | 2 GB (cho manga-image-translator models) | 5 GB+ |

---

## 📦 Cài đặt

### Bước 1: Clone repo

```bash
git clone https://github.com/vanko001/webtoon-translator.git
cd webtoon-translator
```

### Bước 2: Tạo virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate          # Windows
```

### Bước 3: Cài Python dependencies

```bash
pip install -r requirements.txt
```

### Bước 4: Cài manga-image-translator (engine chính)

**Cách A — pip (nhanh):**
```bash
pip install manga-image-translator
```

**Cách B — từ source (khuyến nghị, để có GPU support):**
```bash
git clone https://github.com/zyddnys/manga-image-translator
cd manga-image-translator
pip install -r requirements.txt
pip install -e .
cd ..
```

### Bước 5: Lấy Ollama Cloud API key

1. Vào **https://ollama.com/settings**
2. Tạo API key
3. Set environment variable:

```bash
# macOS / Linux — thêm vào ~/.bashrc hoặc ~/.zshrc
export OLLAMA_API_KEY=oll-your-key-here

# Verify
echo $OLLAMA_API_KEY
```

### Bước 6: Chuẩn bị font Vietnamese

Bỏ font `.ttf` hỗ trợ tiếng Việt vào thư mục `fonts/`:

```bash
# Tải Noto Sans (miễn phí, hỗ trợ đầy đủ Vietnamese)
# Hoặc dùng font có sẵn:
cp /Library/Fonts/Arial\ Unicode.ttf fonts/
```

Font khuyến nghị:
- **Be Vietnam Pro** — thiết kế cho Vietnamese, đẹp
- **Noto Sans** — đầy đủ Unicode
- **Arial Unicode** — phổ biến

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

Output:
```
🚀 Pipeline webtoon translator
   Input:    input/ (50 chương)
   Output:   output/
   Model:    qwen3.5 @ https://ollama.com/v1
   Target:   Vietnamese
   Context:  3 tiles
   Glossary: 0 entries

============================================================
[pipeline] Xử lý chương: ch001.jpg
============================================================
  [split] ch001: 3 tiles
  [mit] Running: ... (tiles: work/tiles/ch001)
  [mit] OK -> work/translated/ch001
  [glossary] +12 entries mới (total: 12)
  [stitch] 3 tiles -> output/ch001.jpg
  [done] ch001 -> output/ch001.jpg

============================================================
✅ Hoàn thành: 50/50 chương
   Glossary: 347 entries
   Output:   output/
============================================================
```

### Resume (bỏ qua chương đã xong)

Nếu pipeline bị crash hoặc dừng, chạy lại với `--resume`:

```bash
python run_pipeline.py --resume
```

→ Bỏ qua các chương đã xong, chỉ chạy chương chưa hoàn thành.

### Chỉ chạy 1 chương

```bash
python run_pipeline.py --chapter ch005
```

### Đổi model dịch

Chỉnh `config.yaml`:

```yaml
# Nhanh, đa ngôn ngữ (default)
ollama_model: "qwen3.5"

# Chất lượng cao, chậm hơn
ollama_model: "deepseek-v4-pro"

# Nhanh nhất, rẻ
ollama_model: "deepseek-v4-flash"

# Tốt cho Asian languages
ollama_model: "glm-5.2"
```

---

## ⚙️ Cấu hình

File `config.yaml` chứa toàn bộ config. Các thông số quan trọng:

### Kích thước tile (cắt ảnh)

```yaml
# Chiều cao mỗi tile (px) - dưới 10240px an toàn với manga-image-translator
tile_height: 6000

# Overlap giữa tiles (px) - tránh cắt đôi text bubble
overlap: 200
```

- Ảnh webtoon rất dài (>15000px) hoặc bị OOM → giảm `tile_height` xuống `4000-5000`
- Overlap 200px đủ để không cắt bubble, không cần tăng

### Context size (giữ mạch truyện)

```yaml
# Số tile trước truyền làm context khi dịch
context_size: 3
```

- Tăng lên `5` nếu muốn AI hiểu mạch truyện sâu hơn (nhưng chậm hơn + tốn token hơn)
- Giảm xuống `1` nếu chạy nhanh, không quan tâm context

### Batch size (xử lý song song)

```yaml
# Số ảnh xử lý song song
batch_size: 3
```

- Mac M1-M4 RAM 16GB+ → tăng lên `5`
- Mac RAM 8GB → giữ `2-3` tránh OOM

### Glossary (terminology xuyên series)

```yaml
# Dùng LLM extract proper nouns (chất lượng cao, tốn thêm API call)
glossary_extract_llm: true

# Export sang format manga-image-translator
glossary_export_mit: true
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
├── input/                    # ← Bỏ ảnh raw vào đây (.jpeg/.png, 1 ảnh = 1 chương)
├── output/                   # ← Ảnh đã dịch xuất ra đây
├── fonts/                    # Font Vietnamese (.ttf)
│
├── work/                     # Working directory (tự sinh, không cần quan tâm)
│   ├── tiles/                #   Tiles sau khi cắt
│   ├── translated/           #   Tiles đã dịch
│   └── progress.json         #   Checkpoint (cho --resume)
│
└── glossary.json             # Terminology tích lũy (tự sinh, càng dịch càng phong phú)
```

---

## 🔧 Troubleshooting

### `OLLAMA_API_KEY chưa set`

```bash
# Set key
export OLLAMA_API_KEY=oll-your-key-here

# Verify
echo $OLLAMA_API_KEY
```

### `manga-image-translator` không tìm thấy

```bash
# Cài lại
pip install manga-image-translator

# Hoặc từ source
git clone https://github.com/zyddnys/manga-image-translator
cd manga-image-translator && pip install -e .
```

### OOM (out of memory) khi inpaint

Giảm `tile_height` trong `config.yaml`:

```yaml
tile_height: 4000    # giảm từ 6000 xuống 4000
overlap: 150         # giảm overlap tương ứng
```

### Text render lỗi / hiển thị方块

Thiếu font Vietnamese. Bỏ font `.ttf` vào `fonts/`:

```bash
# Download Noto Sans
wget -O fonts/NotoSans.ttf "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/full/ttf/NotoSans-Regular.ttf"
```

### Pipeline crash giữa chừng

```bash
# Chạy lại với --resume
python run_pipeline.py --resume
```

→ Tự bỏ qua chương đã xong (dựa trên `work/progress.json`).

### Inpaint không chạy với ảnh rất dài

manga-image-translator có lỗi với ảnh >10240px ([issue #953](https://github.com/zyddnys/manga-image-translator/issues/953)). Pipeline này **tự động** cắt ảnh trước nên không bị lỗi này. Nhưng nếu vẫn gặp, giảm `tile_height` xuống `4000`.

---

## ❓ FAQ

**Q: Pipeline có cần GPU không?**
A: Không bắt buộc. Dùng Ollama Cloud nên GPU chỉ cần cho manga-image-translator (OCR + inpaint). Trên Mac M1-M4, MPS tự động bật. Trên máy không GPU, set `use_gpu: false` trong config.

**Q: Bao nhiêu chương thì chạy được?**
A: Đã test với 50-200 chương. Glossary tích lũy xuyên suốt nên càng dịch nhiều, tên nhân vật càng consistent.

**Q: Có dịch được Japanese/Chinese không?**
A: Có. Đổi `source_lang` và `target_lang` trong `config.yaml`. VD: Japanese → Vietnamese:
```yaml
source_lang: "Japanese"
target_lang: "Vietnamese"
```

**Q: Chi phí Ollama Cloud bao nhiêu?**
A: Free plan có 1 concurrent model, đủ test. Pro $20/tháng cho 3 concurrent. Xem: https://ollama.com/cloud

**Q: Có chỉnh sửa text sau khi dịch không?**
A: Có. Text đã dịch lưu trong `work/translated/<chapter>/` (file `.txt`). Sửa rồi chạy lại:
```bash
python run_pipeline.py --chapter ch005
# Hoặc dùng manga-image-translator editor trực tiếp
```

**Q: Glossary.json có thể share giữa project không?**
A: Có. Copy `glossary.json` sang project khác → tên nhân vật giữ consistent xuyên suốt nhiều series.

---

## 📝 Ghi chú

- Pipeline **không** dịch lại chương đã xong khi `--resume`
- Glossary tích lũy tự động — càng dịch nhiều chương, consistency càng cao
- Overlap 200px giữa tiles đảm bảo không cắt đôi text bubble
- Output giữ nguyên format gốc (PNG/JPG) theo extension file input
- Repo này dùng `manga-image-translator` làm engine backend ([github.com/zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator))

## 📄 License

MIT License — tự do sử dụng, sửa đổi, phân phối.

## 🙏 Credits

- [manga-image-translator](https://github.com/zyddnys/manga-image-translator) — engine OCR + inpaint + render
- [Ollama Cloud](https://ollama.com/cloud) — LLM backend
- [Pillow](https://python-pillow.org/) — image processing