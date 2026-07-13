"""Post-stitch: ghép các tile đã dịch lại thành ảnh dài hoàn chỉnh.

Đọc metadata từ meta.json (do pre_split tạo) + ảnh tile đã dịch trong work/translated,
ghép lại thành ảnh dài đúng kích thước gốc, loại bỏ overlap.
"""

import json
import os
from pathlib import Path

from PIL import Image


def load_meta(meta_path: str) -> dict:
    """Load meta.json của 1 chương."""
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_translated_tile(translated_dir: str, tile_basename: str) -> str | None:
    """Tìm tile đã dịch tương ứng trong work/translated/<chapter>/.

    manga-image-translator có thể đổi extension (vd .png -> .png) hoặc thêm suffix.
    Tìm theo prefix tên file gốc.
    """
    # tile_basename ví dụ: "ch01_tile_000"
    candidates = []
    for f in os.listdir(translated_dir):
        if f.startswith(tile_basename) and not f.endswith(".json"):
            candidates.append(f)
        # manga-image-translator đôi khi thêm -translated suffix
        if f.startswith(tile_basename) and f.endswith((".png", ".jpg", ".webp")):
            candidates.append(f)

    # Ưu tiên file không có suffix extra
    for f in candidates:
        if f == tile_basename + ".png":
            return os.path.join(translated_dir, f)
    if candidates:
        # Sort để lấy file ngắn nhất (ít suffix nhất)
        candidates.sort(key=len)
        return os.path.join(translated_dir, candidates[0])
    return None


def stitch_chapter(meta_path: str, translated_dir: str, out_path: str) -> bool:
    """Ghép các tile đã dịch thành 1 ảnh dài.

    Args:
        meta_path: Đường dẫn meta.json (do pre_split tạo).
        translated_dir: Thư mục chứa tiles đã dịch.
        out_path: Đường dẫn output ảnh hoàn chỉnh.

    Returns:
        True nếu thành công.
    """
    meta = load_meta(meta_path)
    orig_w, orig_h = meta["original_size"]
    tiles = meta["tiles"]

    if len(tiles) == 1:
        # Chỉ 1 tile → copy thẳng
        stem = Path(tiles[0]["file"]).stem
        tile_translated = find_translated_tile(translated_dir, stem)
        if tile_translated is None:
            print(f"  [stitch] Thiếu tile: {stem}")
            return False
        img = Image.open(tile_translated)
        # Resize về kích thước gốc nếu cần
        if img.size != (orig_w, orig_h):
            img = img.resize((orig_w, orig_h), Image.LANCZOS)
        img.save(out_path)
        print(f"  [stitch] 1 tile -> {out_path}")
        return True

    # Ghép nhiều tile, loại bỏ overlap
    result = Image.new("RGBA", (orig_w, orig_h), (0, 0, 0, 0))
    y_cursor = 0

    for i, tile_info in enumerate(tiles):
        stem = Path(tile_info["file"]).stem
        tile_translated = find_translated_tile(translated_dir, stem)
        if tile_translated is None:
            print(f"  [stitch] Thiếu tile: {stem}")
            return False

        tile_img = Image.open(tile_translated).convert("RGBA")
        tile_w, tile_h = tile_img.size

        overlap_top = tile_info["overlap_top"]
        overlap_bottom = tile_info["overlap_bottom"]

        # Cắt bỏ overlap_top (vùng đã render ở tile trước)
        crop_top = overlap_top
        crop_bottom = tile_h - overlap_bottom
        if crop_top >= crop_bottom:
            print(f"  [stitch] Tile {stem} crop không hợp lệ")
            return False

        cropped = tile_img.crop((0, crop_top, tile_w, crop_bottom))
        crop_h = crop_bottom - crop_top

        # Paste vào vị trí tương ứng
        paste_y = y_cursor
        if paste_y + crop_h > orig_h:
            crop_h = orig_h - paste_y
            cropped = cropped.crop((0, 0, tile_w, crop_h))

        result.paste(cropped, (0, paste_y))
        y_cursor += crop_h

    # Convert sang RGB để save jpg/png (không có alpha)
    result = result.convert("RGB")
    result.save(out_path, quality=95)
    print(f"  [stitch] {len(tiles)} tiles -> {out_path}")
    return True


def stitch_all(
    work_dir: str, output_dir: str, input_dir: str
) -> list[str]:
    """Ghép toàn bộ chương đã dịch.

    Args:
        work_dir: Thư mục working (chứa tiles/ và translated/).
        output_dir: Thư mục output.
        input_dir: Thư mục input (để lấy tên file gốc).

    Returns:
        Danh sách đường dẫn output đã thành công.
    """
    tiles_root = os.path.join(work_dir, "tiles")
    translated_root = os.path.join(work_dir, "translated")
    os.makedirs(output_dir, exist_ok=True)

    # List các chương theo meta.json
    chapters = [
        d
        for d in os.listdir(tiles_root)
        if os.path.isdir(os.path.join(tiles_root, d))
    ]
    chapters.sort()

    print(f"[stitch] Tìm thấy {len(chapters)} chương cần ghép")
    outputs = []
    for ch in chapters:
        meta_path = os.path.join(tiles_root, ch, "meta.json")
        translated_dir = os.path.join(translated_root, ch)
        if not os.path.exists(meta_path) or not os.path.exists(translated_dir):
            print(f"  [stitch] Bỏ qua {ch} (thiếu meta hoặc translated)")
            continue

        # Tìm extension gốc từ input
        ext = ".png"
        for f in os.listdir(input_dir):
            if Path(f).stem == ch:
                ext = Path(f).suffix
                break
        out_path = os.path.join(output_dir, f"{ch}{ext}")
        if stitch_chapter(meta_path, translated_dir, out_path):
            outputs.append(out_path)
    return outputs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ghép tiles đã dịch thành ảnh dài.")
    parser.add_argument("--work", default="work", help="Thư mục working")
    parser.add_argument("--output", default="output", help="Thư mục output")
    parser.add_argument("--input", default="input", help="Thư mục input (lấy tên)")
    args = parser.parse_args()

    stitch_all(args.work, args.output, args.input)