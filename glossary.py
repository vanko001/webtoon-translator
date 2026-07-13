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


class Glossary:
    """Quản lý glossary tích lũy xuyên suốt series."""

    def __init__(self, path: str = GLOSSARY_FILE):
        self.path = path
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Load glossary từ file JSON."""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.entries = json.load(f)
            print(f"[glossary] Loaded {len(self.entries)} entries từ {self.path}")

    def save(self) -> None:
        """Lưu glossary ra file."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)
        print(f"[glossary] Saved {len(self.entries)} entries -> {self.path}")

    def add(self, source: str, target: str, desc: str = "") -> None:
        """Thêm 1 entry nếu chưa có (tránh trùng)."""
        # Check trùng theo source
        for e in self.entries:
            if e["source"] == source:
                # Update target nếu thay đổi
                if target and e["target"] != target:
                    e["target"] = target
                if desc and not e.get("desc"):
                    e["desc"] = desc
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
    prompt = EXTRACT_PROMPT.format(text=snippet)
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
            return json.loads(match.group(0))
    except (json.JSONDecodeError, Exception) as e:
        print(f"[glossary] LLM extract failed: {e}")
    return []


if __name__ == "__main__":
    # Test nhanh
    g = Glossary(GLOSSARY_FILE)
    g.add("김도현", "Kim Do-hyun", "tên nhân vật nam chính")
    g.add("박수빈", "Park Soo-bin", "tên nhân vật nữ chính")
    g.add("서울", "Seoul", "tên thành phố")
    g.save()
    print(g.to_prompt_context())