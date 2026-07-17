"""Post-stitch: ghép các tile đã dịch lại thành ảnh dài hoàn chỉnh.

Đọc metadata từ meta.json (do pre_split tạo) + ảnh tile đã dịch trong work/translated,
ghép lại thành ảnh dài đúng kích thước gốc, loại bỏ overlap.
"""

import json
import os
from pathlib import Path

from PIL import Image

# Dùng chung natural sort + tắt giới hạn decompression-bomb cho ảnh dài
from pre_split import natural_key


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
    candidates = [
        f
        for f in os.listdir(translated_dir)
        if f.startswith(tile_basename)
        and Path(f).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    if not candidates:
        return None
    # Ưu tiên file khớp chính xác, sau đó file ngắn nhất (ít suffix nhất)
    exact = tile_basename + ".png"
    if exact in candidates:
        return os.path.join(translated_dir, exact)
    candidates.sort(key=len)
    return os.path.join(translated_dir, candidates[0])


SLICE_HEIGHT = 2400
SLICE_QUALITY = 85


def save_output(result: Image.Image, out_path: str, sliced: bool) -> None:
    """Lưu ảnh chương đã ghép.

    sliced=True: out_path là THƯ MỤC, ảnh cắt thành lát ~2400px JPEG q85
    (nhẹ hơn 60-85% so với 1 file, không đụng giới hạn JPEG 65500px,
    web đọc từng lát nhanh). sliced=False: 1 file như cũ.
    """
    if not sliced:
        result.save(out_path, quality=95)
        return
    os.makedirs(out_path, exist_ok=True)
    w, h = result.size
    for i, y in enumerate(range(0, h, SLICE_HEIGHT)):
        piece = result.crop((0, y, w, min(y + SLICE_HEIGHT, h)))
        piece.save(
            os.path.join(out_path, f"{i:03d}.jpg"),
            quality=SLICE_QUALITY, progressive=True, optimize=True,
        )


def stitch_chapter(
    meta_path: str, translated_dir: str, out_path: str, sliced: bool = False
) -> bool:
    """Ghép các tile đã dịch thành 1 chương hoàn chỉnh.

    Args:
        meta_path: Đường dẫn meta.json (do pre_split tạo).
        translated_dir: Thư mục chứa tiles đã dịch.
        out_path: File ảnh (sliced=False) hoặc thư mục lát (sliced=True).
        sliced: Lưu dạng thư mục lát JPEG thay vì 1 file.

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
        img = Image.open(tile_translated).convert("RGB")
        # Resize về kích thước gốc nếu cần
        if img.size != (orig_w, orig_h):
            img = img.resize((orig_w, orig_h), Image.LANCZOS)
        save_output(img, out_path, sliced)
        print(f"  [stitch] 1 tile -> {out_path}")
        return True

    # Ghép nhiều tile: đường nối đặt ở GIỮA vùng overlap.
    # Tile i giữ [y_start + overlap_top/2, y_end - overlap_bottom/2] theo tọa độ gốc,
    # nên các phần ghép liền mạch nhau và mỗi tile có nửa overlap làm lề an toàn.
    result = Image.new("RGB", (orig_w, orig_h), (255, 255, 255))

    for tile_info in tiles:
        stem = Path(tile_info["file"]).stem
        tile_translated = find_translated_tile(translated_dir, stem)
        if tile_translated is None:
            print(f"  [stitch] Thiếu tile: {stem}")
            return False

        tile_img = Image.open(tile_translated).convert("RGB")

        # Kích thước tile gốc theo meta (engine có thể resize output)
        y_start = tile_info["y_start"]
        y_end = tile_info["y_end"]
        expected_h = y_end - y_start
        if tile_img.size != (orig_w, expected_h):
            tile_img = tile_img.resize((orig_w, expected_h), Image.LANCZOS)

        crop_top = tile_info["overlap_top"] // 2
        crop_bottom = expected_h - tile_info["overlap_bottom"] // 2
        if crop_top >= crop_bottom:
            print(f"  [stitch] Tile {stem} crop không hợp lệ")
            return False

        cropped = tile_img.crop((0, crop_top, orig_w, crop_bottom))
        result.paste(cropped, (0, y_start + crop_top))

    save_output(result, out_path, sliced)
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
    chapters.sort(key=natural_key)

    print(f"[stitch] Tìm thấy {len(chapters)} chương cần ghép")
    outputs = []
    for ch in chapters:
        meta_path = os.path.join(tiles_root, f"{ch}.meta.json")
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