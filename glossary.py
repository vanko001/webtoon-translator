"""Glossary: quản lý terminology (tên nhân vật/địa danh) xuyên suốt toàn series.

Glossary được tích lũy qua từng chương, đảm bảo tên nhân vật/địa danh được dịch
consistent từ chương 1 đến chương 200. Format tương thích với manga-image-translator
(OPENAI_GLOSSARY_PATH) và cũng dùng được để inject vào prompt khi dịch.
"""

import json
import os
import re
from pathlib import Path

GLOSSARY_FILE = "glossary.json"

# Prompt template để nhờ LLM extract proper nouns từ text đã dịch.
EXTRACT_PROMPT = """Bạn là trợ lý trích xuất thuật ngữ. Từ đoạn truyện đã dịch dưới đây,
hãy trích xuất các danh từ riêng (tên nhân vật, địa danh, tổ chức, vũ khí, kỹ năng)
kèm bản dịch tiếng Việt đang dùng. Chỉ trả về JSON array, mỗi phần tử có:
  - "source": tên gốc tiếng Hàn
  - "target": bản dịch tiếng Việt
  - "desc": mô tả ngắn (vd: "tên nhân vật nữ", "tên thành phố")

Ví dụ: [{"source": "김도현", "target": "Kim Do-hyun", "desc": "tên nhân vật nam chính"}]

Đoạn truyện:
{text}

Trả về JSON array, không kèm giải thích thêm:"""

# Prompt cập nhật hồ sơ nhân vật (xưng hô + giọng điệu) từ thoại đã dịch.
CHARACTER_PROMPT = """Bạn là trợ lý biên tập truyện dịch. Dưới đây là (1) hồ sơ nhân vật hiện tại
và (2) các lời thoại mới đã dịch (dạng "gốc => bản dịch").

Nhiệm vụ: CẬP NHẬT hồ sơ nhân vật. Với mỗi nhân vật xác định được, ghi lại:
- Cách xưng hô với từng nhân vật khác. BẮT BUỘC chọn đúng MỘT cặp đại từ duy nhất
  cho mỗi quan hệ (vd: "xưng 'tôi' gọi 'cậu' với Sarang" — KHÔNG được liệt kê nhiều
  lựa chọn kiểu 'tôi/tớ/mình'). Nếu thoại cũ dao động, chọn cách xuất hiện nhiều nhất.
- Cách xưng khi độc thoại nội tâm/narration (thường là 'tôi').
- Giọng điệu đặc trưng (vd: "cộc lốc, hay chửi thề", "lễ phép, nói kiểu trẻ con").
Giữ nguyên thông tin cũ còn đúng, chỉ bổ sung/sửa khi có bằng chứng mới trong thoại.
Chỉ đưa nhân vật có tên rõ ràng, bỏ qua người qua đường.

Hồ sơ hiện tại (JSON):
{sheet}

Thoại mới:
{text}

Trả về TOÀN BỘ hồ sơ đã cập nhật, chỉ là JSON array dạng:
[{"name": "Kim Do-hyun", "notes": "xưng 'anh' gọi 'em' với Soo-bin; giọng trầm, ít nói"}]
Không kèm giải thích:"""


class Glossary:
    """Quản lý glossary tích lũy xuyên suốt series."""

    def __init__(self, path: str = GLOSSARY_FILE):
        self.path = path
        self.entries: list[dict] = []  # thuật ngữ: {source, target, desc}
        self.characters: list[dict] = []  # hồ sơ nhân vật: {name, notes}
        self._load()

    def _load(self) -> None:
        """Load glossary từ file JSON.

        Hỗ trợ 2 schema: list thuần (bản cũ, chỉ terms) và dict
        {"terms": [...], "characters": [...]}.
        """
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            self.entries = data
        else:
            self.entries = data.get("terms", [])
            self.characters = data.get("characters", [])
        print(
            f"[glossary] Loaded {len(self.entries)} terms, "
            f"{len(self.characters)} nhân vật từ {self.path}"
        )

    def save(self) -> None:
        """Lưu glossary ra file."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {"terms": self.entries, "characters": self.characters},
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(
            f"[glossary] Saved {len(self.entries)} terms, "
            f"{len(self.characters)} nhân vật -> {self.path}"
        )

    def update_characters(self, new_sheet: list[dict]) -> None:
        """Thay hồ sơ nhân vật bằng bản LLM đã cập nhật (merge theo name).

        LLM nhận hồ sơ cũ + thoại mới và trả về TOÀN BỘ hồ sơ mới, nên ở đây
        chỉ cần validate và giữ lại nhân vật cũ nếu bản mới lỡ bỏ sót.
        """
        cleaned = [
            {"name": c["name"].strip(), "notes": str(c.get("notes", "")).strip()}
            for c in new_sheet
            if isinstance(c, dict) and c.get("name", "").strip()
        ]
        if not cleaned:
            return
        new_names = {c["name"] for c in cleaned}
        kept = [c for c in self.characters if c["name"] not in new_names]
        self.characters = cleaned + kept
        print(f"[glossary] Hồ sơ nhân vật: {len(self.characters)} nhân vật")

    def to_character_sheet_prompt(self, max_chars: int = 4000) -> str:
        """Format hồ sơ nhân vật để tiêm vào system prompt của translator."""
        if not self.characters:
            return ""
        lines = []
        total = 0
        for c in self.characters:
            line = f"- {c['name']}: {c['notes']}"
            total += len(line)
            if total > max_chars:
                break
            lines.append(line)
        return "\n".join(lines)

    def add(self, source: str, target: str, desc: str = "") -> None:
        """Thêm 1 entry nếu chưa có (tránh trùng).

        Dedup theo source; entry không có source (từ regex fallback) dedup theo target.
        """
        if not source and not target:
            return
        for e in self.entries:
            if source:
                if e["source"] != source:
                    continue
                # Đã có source này → giữ bản dịch ĐẦU TIÊN để consistent xuyên chương,
                # chỉ bổ sung desc nếu còn thiếu
                if desc and not e.get("desc"):
                    e["desc"] = desc
                return
            if e["target"] == target:
                return
        self.entries.append({"source": source, "target": target, "desc": desc})

    def merge(self, new_entries: list[dict]) -> int:
        """Merge danh sách entries mới, trả về số entries thực sự thêm mới."""
        before = len(self.entries)
        for e in new_entries:
            self.add(e.get("source", ""), e.get("target", ""), e.get("desc", ""))
        added = len(self.entries) - before
        if added > 0:
            print(f"[glossary] +{added} entries mới (total: {len(self.entries)})")
        return added

    def to_mit_format(self, out_path: str) -> None:
        """Xuất glossary sang format manga-image-translator hiểu được.

        manga-image-translator đọc glossary dạng text, mỗi dòng:
            source -> target
        hoặc JSON tùy cấu hình. Format phổ biến nhất là TSV/JSON.
        """
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for e in self.entries:
                # Entry không có source (regex fallback) vô dụng với engine → bỏ qua
                if e["source"] and e["target"]:
                    f.write(f"{e['source']}\t{e['target']}\n")
        print(f"[glossary] Exported MIT format -> {out_path}")

    def to_prompt_context(self, max_entries: int = 50) -> str:
        """Tạo đoạn context glossary để inject vào prompt dịch.

        Chỉ lấy top entries (vì glossary có thể rất dài sau 200 chương).
        """
        if not self.entries:
            return ""
        subset = self.entries[-max_entries:]  # lấy entries mới nhất
        lines = [f"- {e['source']}: {e['target']}" for e in subset]
        return "Bảng thuật ngữ (giữ nhất quán khi dịch):\n" + "\n".join(lines)


def extract_proper_nouns_regex(text: str) -> list[dict]:
    """Extract nhanh proper nouns bằng regex (dùng khi không có LLM).

    Heuristic đơn giản: tìm các cụm tiếng Việt bắt đầu bằng chữ hoa liên tiếp.
    Đây là fallback, không chính xác bằng LLM extraction.
    """
    # Tìm các từ/cụm tiếng Việt viết hoa (tên riêng)
    pattern = r"\b(?:[A-ZĐ][a-zà-ỹ]+)(?:\s+[A-ZĐ][a-zà-ỹ]+)*\b"
    matches = re.findall(pattern, text)
    seen = set()
    entries = []
    for m in matches:
        if m not in seen and len(m) > 2:
            seen.add(m)
            entries.append({"source": "", "target": m, "desc": "auto-extracted"})
    return entries


def extract_proper_nouns_llm(
    text: str, client, model: str
) -> list[dict]:
    """Dùng LLM (qua OpenAI-compatible client) để extract proper nouns.

    Args:
        text: Đoạn text đã dịch (Vietnamese).
        client: OpenAI client (đã cấu hình base_url Ollama Cloud).
        model: Tên model (vd: "qwen3.5").

    Returns:
        Danh sách entries [{"source":..., "target":..., "desc":...}].
    """
    # Giới hạn text để không vượt context
    snippet = text[:3000]
    prompt = EXTRACT_PROMPT.replace("{text}", snippet)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            stream=False,
        )
        content = resp.choices[0].message.content.strip()
        # Tìm JSON array trong response (LLM có thể kèm markdown)
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            entries = json.loads(match.group(0))
            # Chỉ giữ entries đúng cấu trúc
            return [
                e
                for e in entries
                if isinstance(e, dict) and (e.get("source") or e.get("target"))
            ]
    except Exception as e:
        print(f"[glossary] LLM extract failed: {e}")
    return []


def extract_character_sheet_llm(
    text: str, current_sheet: list[dict], client, model: str
) -> list[dict]:
    """Dùng LLM cập nhật hồ sơ nhân vật (xưng hô + giọng điệu) từ thoại đã dịch.

    Args:
        text: Các cặp thoại "gốc => dịch" của phần vừa dịch.
        current_sheet: Hồ sơ nhân vật hiện tại [{"name":..., "notes":...}].
        client: OpenAI client (đã cấu hình base_url Ollama Cloud).
        model: Tên model.

    Returns:
        Hồ sơ nhân vật đã cập nhật (toàn bộ), hoặc [] nếu extract thất bại.
    """
    # Model context dài — gửi nhiều thoại để nắm đủ quan hệ (cắt ở 30k ký tự)
    snippet = text[-30000:]
    sheet_json = json.dumps(current_sheet, ensure_ascii=False)
    prompt = CHARACTER_PROMPT.replace("{sheet}", sheet_json).replace("{text}", snippet)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            stream=False,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            sheet = json.loads(match.group(0))
            if isinstance(sheet, list):
                return sheet
    except Exception as e:
        print(f"[glossary] Character sheet extract failed: {e}")
    return []


if __name__ == "__main__":
    # Test nhanh
    g = Glossary(GLOSSARY_FILE)
    g.add("김도현", "Kim Do-hyun", "tên nhân vật nam chính")
    g.add("박수빈", "Park Soo-bin", "tên nhân vật nữ chính")
    g.add("서울", "Seoul", "tên thành phố")
    g.save()
    print(g.to_prompt_context())