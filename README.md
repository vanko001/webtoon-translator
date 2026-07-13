# 🎨 Webtoon Translator Pipeline

Pipeline tự động dịch **webtoon/manhwa Korean → Vietnamese**, giữ ngữ cảnh xuyên suốt toàn bộ truyện, render text vào ảnh đúng vùng bubble.

## ✨ Tính năng

- **End-to-end**: Bỏ folder ảnh raw vào `input/` → chạy 1 lệnh → nhận ảnh đã dịch trong `output/`
- **Giữ context xuyên chương**: Truyền 3 tile trước làm context khi dịch (`--context-size 3`)
- **Glossary tích lũy**: Tự extract tên nhân vật/địa danh, tích lũy xuyên suốt 200 chương → dịch consistent
- **Xử lý ảnh dài**: Tự cắt webtoon strip dài >8000px thành tiles, dịch xong rồi ghép lại
- **Resume được**: Checkpoint `progress.json`, crash không mất công
- **Ollama Cloud**: Dịch qua `qwen3.5` / `deepseek-v4-pro` trên Ollama Cloud (không cần GPU local)
- **Apple Silicon**: Tận dụng MPS acceleration trên Mac M1-M4

## 📦 Cài đặt

### 1. Clone repo

```bash
git clone <repo-url>
cd webtoon-translator
```

### 2. Cài dependencies

```bash
# Tạo virtual env
python3 -m venv venv
source venv/bin/activate

# Cài Python deps
pip install -r requirements.txt
```

### 3. Cài manga-image-translator (engine chính)

```bash
# Cách 1: pip
pip install manga-image-translator

# Cách 2: từ source (recommend để có GPU support)
git clone https://github.com/zyddnys/manga-image-translator
cd manga-image-translator
pip install -r requirements.txt
pip install -e .
cd ..
```

### 4. Set Ollama Cloud API key

```bash
# Lấy key từ: https://ollama.com/settings
export OLLAMA_API_KEY=your_key_here
```

### 5. Chuẩn bị font Vietnamese

Bỏ font `.ttf` hỗ trợ tiếng Việt vào thư mục `fonts/`:
- Arial Unicode
- Noto Sans
- Be Vietnam Pro

## 🚀 Sử dụng

### Chạy toàn bộ pipeline

```bash
# Bỏ ảnh raw vào input/ (1 ảnh = 1 chương)
cp /path/to/your/webtoon/*.jpeg input/

# Chạy
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

### Đổi model

Chỉnh `config.yaml`:
```yaml
ollama_model: "deepseek-v4-pro"   # chất lượng cao hơn
# hoặc
ollama_model: "deepseek-v4-flash" # nhanh hơn
```

## 📐 Kiến trúc pipeline

```
input/ (ảnh raw Korean)
  ↓
[1. pre_split] Cắt ảnh >8000px thành tiles ~6000px (overlap 200px)
  ↓
[2. manga-image-translator] Per-tile:
    ├─ Text Detection (GPU MPS)
    ├─ OCR Korean
    ├─ Dịch qua Ollama Cloud (context 3 tiles + glossary)
    ├─ Inpaint (xóa text raw)
    └─ Render text Vietnamese
  ↓
[3. glossary] Extract proper nouns → tích lũy glossary.json
  ↓
[4. post_stitch] Ghép tiles lại thành ảnh dài
  ↓
output/ (ảnh đã chèn text Vietnamese)
```

## 📁 Cấu trúc thư mục

```
webtoon-translator/
├── run_pipeline.py          # Orchestrator chính
├── pre_split.py              # Cắt ảnh dài thành tiles
├── post_stitch.py            # Ghép tiles lại
├── glossary.py               # Quản lý terminology xuyên series
├── config.yaml               # Config (model, paths, tile size)
├── requirements.txt          # Python dependencies
├── input/                    # Bỏ ảnh raw vào đây
├── output/                   # Ảnh đã dịch xuất ra đây
├── work/                     # Working directory (không cần quan tâm)
│   ├── tiles/                # Tiles sau khi cắt
│   ├── translated/           # Tiles đã dịch
│   └── progress.json         # Checkpoint
├── fonts/                    # Font Vietnamese
└── glossary.json             # Terminology tích lũy xuyên suốt
```

## ⚙️ Tùy chỉnh

### Kích thước tile

Nếu ảnh webtoon rất dài (>15000px) hoặc bị OOM:
```yaml
# config.yaml
tile_height: 4000    # nhỏ hơn
overlap: 150         # giảm overlap
```

### Context size

Tăng nếu muốn AI hiểu mạch truyện sâu hơn (nhưng chậm hơn):
```yaml
context_size: 5      # truyền 5 tile trước làm context
```

### Batch size

Tăng nếu Mac mạnh (RAM 16GB+):
```yaml
batch_size: 5        # xử lý 5 ảnh song song
```

## 🔧 Troubleshooting

### Lỗi "OLLAMA_API_KEY chưa set"
```bash
export OLLAMA_API_KEY=your_key_here
```

### Lỗi OOM khi inpaint
Giảm `tile_height` trong config.yaml xuống 4000-5000.

### Text render lỗi font
Bỏ font hỗ trợ Vietnamese vào `fonts/` folder.

### manga-image-translator không tìm thấy
```bash
pip install -e manga-image-translator/
```

## 📝 Ghi chú

- Pipeline này **không** dịch lại chương đã xong khi `--resume`
- Glossary tích lũy tự động — càng dịch nhiều chương, consistency càng cao
- Overlap 200px giữa tiles đảm bảo không cắt đôi text bubble
- Output giữ nguyên format gốc (PNG/JPG) theo extension file input