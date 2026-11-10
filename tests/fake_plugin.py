"""测试用假目标：方法形状对齐 memtrace 的 describe 约定，不含任何第三方代码。"""


class FakeQuery:
    def __init__(self, text="", source="g:1:group", speaker="u1"):
        self.text = text
        self.source = source
        self.speaker = speaker


class FakeMemoryService:
    def __init__(self):
        self.rows = [{"id": "m1", "kind": "topic", "text": "内部正文"}]
        self.seen = []

    async def vector(self, text):
        return [float(len(text)), 1.0]

    async def index_memory(self, memory_id, text=""):
        return memory_id

    async def store(self, item, origin="digest", run_id=0):
        self.seen.append(item.get("id"))
        if item.get("id") == "boom":
            raise ValueError("bad item")
        return item.get("id"), "created"

    async def remember(self, item, text):
        return item.get("id"), "created"

    async def recall_for(self, query):
        chosen = [r for r in self.rows if r["kind"] == "topic"]
        return "渲染块", chosen

    def _score(self, row):
        return 0.5 if row.get("kind") == "topic" else 0.0

    def _similarity(self, vec, memory_id):
        return 0.9

    def _nearest(self, rows, limit=3):
        return rows[:limit]

    def _duplicate(self, left, right):
        if left.get("id") == "raise":
            raise ValueError("dup boom")
        return left.get("id") == right.get("id")

    async def _index(self, memory_id):
        return None

    def render(self, rows):
        return "".join(r.get("id", "") for r in rows)

    def lessons(self):
        return "第一行\n第二行"

    def known_for(self, person):
        return {"m1": 1}, person