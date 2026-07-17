"""Pre-split: cắt ảnh webtoon strip dài thành tiles để tránh OOM khi inpaint.

manga-image-translator bị lỗi inpainting với ảnh >10240px (issue #953).
Module này cắt ảnh dài thành tiles ~6000px với overlap 200px để không cắt đôi bubble.
"""

import json
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image

# Webtoon strip rất dài có thể vượt giới hạn decompression-bomb mặc định của Pillow.
# Input là ảnh của chính người dùng nên tắt giới hạn này.
Image.MAX_IMAGE_PIXELS = None

# Kích thước tile mặc định (px). Dưới 10240px là an toàn với manga-image-translator.
DEFAULT_TILE_HEIGHT = 6000
# Overlap giữa các tile để tránh cắt đôi text bubble.
DEFAULT_OVERLAP = 200
# Định dạng ảnh output.
SUPPORTED_INPUT_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def natural_key(name: str):
    """Sort key tự nhiên: ch2 < ch10 (thay vì sort chữ cái ch10 < ch2)."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


# Cửa sổ tìm kiếm đường cắt "yên tĩnh" quanh vị trí cắt lý tưởng (px).
CUT_SEARCH_WINDOW = 800
# Nửa chiều cao dải ngang dùng để chấm điểm độ yên tĩnh quanh 1 đường cắt (px).
CUT_BAND = 40


def find_quiet_cuts(
    img: Image.Image,
    tile_height: int,
    overlap: int,
    window: int = CUT_SEARCH_WINDOW,
    band: int = CUT_BAND,
) -> list[int]:
    """Chọn các vị trí cắt sao cho đường cắt rơi vào vùng "yên tĩnh" của ảnh.

    Nếu cắt cứng tại bội số tile_height, đường cắt có thể xuyên qua text bubble:
    2 tile kề nhau cùng chứa bubble đó và render 2 bản dịch xuống dòng khác nhau
    → ghép lại bị chữ chồng chữ tại seam. Webtoon có rất nhiều khoảng trống giữa
    các panel, nên quanh mỗi vị trí cắt lý tưởng ta quét ±window px, chấm điểm
    từng dải ngang (độ lệch chuẩn pixel = mức chi tiết) và cắt tại dải phẳng nhất.

    Returns:
        Danh sách vị trí cắt (không gồm 0 và height), tăng dần.
    """
    width, height = img.size
    if height <= tile_height:
        return []

    # Grayscale, downsample chiều ngang cho nhanh (chi tiết dọc giữ nguyên)
    gray = np.asarray(img.convert("L").resize((min(width, 256), height)), dtype=np.float32)

    # Điểm "ồn" từng hàng: độ lệch chuẩn ngang (text/nét vẽ = cao, nền phẳng = ~0)
    row_std = gray.std(axis=1)
    row_mean = gray.mean(axis=1)

    def band_score(y: int) -> float:
        lo, hi = max(0, y - band), min(height, y + band)
        # Dải yên tĩnh = các hàng đều phẳng VÀ giống nhau (không nằm giữa 2 mảng màu)
        return float(row_std[lo:hi].mean() + row_mean[lo:hi].std())

    cuts: list[int] = []
    prev = 0
    step = tile_height - overlap
    while height - prev > tile_height:
        target = prev + step
        lo = max(prev + tile_height // 2, target - window)
        hi = min(height - overlap, target + window)
        if lo >= hi:
            cut = target
        else:
            candidates = np.arange(lo, hi, 4)
            scores = [band_score(int(y)) for y in candidates]
            cut = int(candidates[int(np.argmin(scores))])
        # Nếu phần còn lại quá ngắn thì thôi, gộp vào tile cuối
        if height - cut < overlap * 2:
            break
        cuts.append(cut)
        prev = cut
    return cuts


def split_image(
    image_path: str,
    out_dir: str,
    tile_height: int = DEFAULT_TILE_HEIGHT,
    overlap: int = DEFAULT_OVERLAP,
) -> dict:
    """Cắt 1 ảnh dài thành nhiều tile.

    Args:
        image_path: Đường dẫn ảnh gốc.
        out_dir: Thư mục chứa tiles.
        tile_height: Chiều cao mỗi tile (px).
        overlap: Độ chồng lấp giữa 2 tile liên tiếp (px).

    Returns:
        Metadata dict chứa thông tin để post_stitch ghép lại.
    """
    img = Image.open(image_path)
    width, height = img.size
    stem = Path(image_path).stem

    # Ảnh đủ ngắn → không cần cắt, copy thẳng
    if height <= tile_height:
        tile_path = os.path.join(out_dir, f"{stem}_tile_000.png")
        img.save(tile_path)
        return {
            "original": image_path,
            "original_size": [width, height],
            "tile_height": tile_height,
            "overlap": 0,
            "tiles": [
                {
                    "file": tile_path,
                    "index": 0,
                    "y_start": 0,
                    "y_end": height,
                    "overlap_top": 0,
                    "overlap_bottom": 0,
                }
            ],
        }

    # Chọn đường cắt tại vùng "yên tĩnh" (tránh cắt qua text bubble),
    # rồi nới mỗi tile nửa overlap về 2 phía quanh đường cắt.
    # → seam khi stitch (giữa vùng overlap) rơi đúng đường cắt yên tĩnh.
    cuts = find_quiet_cuts(img, tile_height, overlap)
    half = overlap // 2
    starts = [0] + [c - half for c in cuts]
    ends = [c + half for c in cuts] + [height]

    tiles = []
    last = len(starts) - 1
    for index, (y, y_end) in enumerate(zip(starts, ends)):
        tile = img.crop((0, y, width, y_end))
        tile_path = os.path.join(out_dir, f"{stem}_tile_{index:03d}.png")
        tile.save(tile_path)

        # Overlap trên/dưới để post_stitch biết vùng nào cần cắt bỏ
        tiles.append(
            {
                "file": tile_path,
                "index": index,
                "y_start": y,
                "y_end": y_end,
                "overlap_top": 2 * half if index > 0 else 0,
                "overlap_bottom": 2 * half if index < last else 0,
            }
        )

    return {
        "original": image_path,
        "original_size": [width, height],
        "tile_height": tile_height,
        "overlap": overlap,
        "tiles": tiles,
    }


def split_chapter(
    chapter_path: str,
    out_dir: str,
    tile_height: int = DEFAULT_TILE_HEIGHT,
    overlap: int = DEFAULT_OVERLAP,
) -> str:
    """Cắt 1 chương (1 ảnh) thành tiles và lưu metadata.

    Args:
        chapter_path: Đường dẫn ảnh gốc của chương.
        out_dir: Thư mục gốc chứa work/tiles.
        tile_height: Chiều cao tile.
        overlap: Độ chồng lấp.

    Returns:
        Đường dẫn file meta.json.
    """
    stem = Path(chapter_path).stem
    tiles_dir = os.path.join(out_dir, stem)
    os.makedirs(tiles_dir, exist_ok=True)

    meta = split_image(chapter_path, tiles_dir, tile_height, overlap)
    # meta.json để NGOÀI tiles_dir, tránh engine coi nó là file cần dịch
    meta_path = os.path.join(out_dir, f"{stem}.meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"  [split] {stem}: {len(meta['tiles'])} tiles")
    return meta_path


def split_all(
    input_dir: str,
    work_dir: str,
    tile_height: int = DEFAULT_TILE_HEIGHT,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Cắt toàn bộ ảnh trong input_dir thành tiles.

    Args:
        input_dir: Thư mục chứa ảnh raw.
        work_dir: Thư mục working (work/tiles).
        tile_height: Chiều cao tile.
        overlap: Độ chồng lấp.

    Returns:
        Danh sách đường dẫn meta.json của từng chương.
    """
    tiles_root = os.path.join(work_dir, "tiles")
    os.makedirs(tiles_root, exist_ok=True)

    # Sort natural order để chương 2 không nằm sau chương 10
    files = sorted(
        (
            f
            for f in os.listdir(input_dir)
            if Path(f).suffix.lower() in SUPPORTED_INPUT_EXTS
        ),
        key=natural_key,
    )
    if not files:
        print(f"[split] Không tìm thấy ảnh trong {input_dir}")
        return []

    print(f"[split] Tìm thấy {len(files)} chương cần cắt")
    metas = []
    for f in files:
        meta = split_chapter(
            os.path.join(input_dir, f), tiles_root, tile_height, overlap
        )
        metas.append(meta)
    return metas


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cắt ảnh webtoon dài thành tiles.")
    parser.add_argument("--input", default="input", help="Thư mục ảnh raw")
    parser.add_argument("--work", default="work", help="Thư mục working")
    parser.add_argument("--tile-height", type=int, default=DEFAULT_TILE_HEIGHT)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    args = parser.parse_args()

    split_all(args.input, args.work, args.tile_height, args.overlap)