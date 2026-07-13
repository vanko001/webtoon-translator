"""Pre-split: cắt ảnh webtoon strip dài thành tiles để tránh OOM khi inpaint.

manga-image-translator bị lỗi inpainting với ảnh >10240px (issue #953).
Module này cắt ảnh dài thành tiles ~6000px với overlap 200px để không cắt đôi bubble.
"""

import json
import os
from pathlib import Path

from PIL import Image

# Kích thước tile mặc định (px). Dưới 10240px là an toàn với manga-image-translator.
DEFAULT_TILE_HEIGHT = 6000
# Overlap giữa các tile để tránh cắt đôi text bubble.
DEFAULT_OVERLAP = 200
# Định dạng ảnh output.
SUPPORTED_INPUT_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


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
        tile_path = os.path.join(out_dir, f"{stem}_tile_001.png")
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

    # Cắt thành tiles có overlap
    tiles = []
    step = tile_height - overlap  # khoảng cách giữa 2 tile liên tiếp
    y = 0
    index = 0
    while y < height:
        y_end = min(y + tile_height, height)
        tile = img.crop((0, y, width, y_end))
        tile_path = os.path.join(out_dir, f"{stem}_tile_{index:03d}.png")
        tile.save(tile_path)

        # Overlap trên/dưới để post_stitch biết vùng nào cần cắt bỏ
        overlap_top = overlap if index > 0 else 0
        overlap_bottom = overlap if y_end < height else 0

        tiles.append(
            {
                "file": tile_path,
                "index": index,
                "y_start": y,
                "y_end": y_end,
                "overlap_top": overlap_top,
                "overlap_bottom": overlap_bottom,
            }
        )
        index += 1
        y += step

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
    meta_path = os.path.join(tiles_dir, "meta.json")
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
        f
        for f in os.listdir(input_dir)
        if Path(f).suffix.lower() in SUPPORTED_INPUT_EXTS
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