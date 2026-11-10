"""方法级插桩：行为不变、签名不变、可还原、日志不泄漏正文。"""

import asyncio
import inspect

import support
import memtrace
from fake_plugin import FakeMemoryService


def main():
    logger, capture = support.make_logger()
    baseline = support.play(FakeMemoryService())

    original = FakeMemoryService.__dict__["store"]
    signature = str(inspect.signature(FakeMemoryService.store))

    names = memtrace.install("fake_plugin", "FakeMemoryService", log=logger)
    assert "store" in names and "recall_for" in names and "_score" in names, names
    assert memtrace.is_patched(FakeMemoryService), "没装上"
    assert str(inspect.signature(FakeMemoryService.store)) == signature, "签名被改了"

    patched = FakeMemoryService()
    assert support.play(patched) == baseline, "打桩后行为变了"

    joined = capture.text
    for needle in ("[memtrace] store", "[memtrace] recall_for", "[memtrace] _score"):
        assert needle in joined, f"缺少日志 {needle}"
    assert support.SECRET not in joined, "日志泄漏了正文"

    # 异常原样透传，同时记一条 raised
    raised = False
    try:
        asyncio.run(patched.store({"id": "boom"}, "digest", 1))
    except ValueError:
        raised = True
    assert raised, "异常没有被抛出"
    assert "raised ValueError" in capture.text, "异常没有被记录"

    memtrace.uninstall()
    assert not memtrace.is_patched(FakeMemoryService), "卸载后仍是补丁"
    assert FakeMemoryService.__dict__["store"] is original, "卸载没有还原原函数"
    assert memtrace.patched() == [], memtrace.patched()
    print(f"OK 方法插桩：{len(names)} 个方法，日志 {len(capture.messages)} 行")


if __name__ == "__main__":
    main()