"""测试公共件：路径、日志捕获、行为基线。"""

import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(HERE), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SECRET = "不该出现在日志里的正文-9f3a"
HOOK_EVENTS = []   # 钩子记录用：放在 support 里，避免 __main__ 与 import 出两份类


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass

    def clear(self):
        self.messages.clear()

    @property
    def text(self):
        return "\n".join(self.messages)


def make_logger(name="mixin.test"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    capture = Capture()
    logger.addHandler(capture)
    return logger, capture


def play(service):
    """跑一串固定调用，用做行为基线比对。"""
    import asyncio

    from fake_plugin import FakeQuery

    return [
        asyncio.run(service.vector("hello")),
        asyncio.run(service.store(
            {"id": "m1", "kind": "topic", "subject": "u1", "text": SECRET}, "digest", 7)),
        asyncio.run(service.remember({"id": "m2"}, "正文")),
        asyncio.run(service.recall_for(FakeQuery(text=SECRET))),
        asyncio.run(service._index("m1")),
        service._score({"id": "m1", "kind": "topic"}),
        service._score({"id": "m9", "kind": "event"}),
        service._similarity([1.0, 0.0], "m3"),
        service._nearest([{"id": "a"}, {"id": "b"}], 1),
        service._duplicate({"id": "a"}, {"id": "a"}),
        service.render([{"id": "a"}, {"id": "b"}]),
        service.lessons(),
        service.known_for("u1"),
    ]