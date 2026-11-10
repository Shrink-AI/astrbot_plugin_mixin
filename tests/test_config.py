"""配置文件驱动：应用、参数、热更新、开关、容错、文件加载。"""

import json
import tempfile
from pathlib import Path

import support
import mixin_config
import fake_plugin
from fake_plugin import FakeMemoryService


def config(level="DEBUG", enabled=True):
    return {"targets": [{
        "module": "fake_plugin",
        "class": "FakeMemoryService",
        "enabled": enabled,
        "params": {"level": level},
        "mixins": [{"path": "trace_mixin:TraceMixin"}],
    }]}


def main():
    logger, capture = support.make_logger("mixin.config")
    baseline = support.play(FakeMemoryService())

    results = mixin_config.apply(config(), log=logger)
    assert results[0]["status"] == "ok", results
    assert fake_plugin.FakeMemoryService.__name__ == "TracedFakeMemoryService"
    assert support.play(fake_plugin.FakeMemoryService()) == baseline
    joined = capture.text
    assert "[memtrace:mixin] store" in joined and "[memtrace:mixin] _score" in joined

    # 热更新成 INFO：摘要保留，逐条打分消失
    capture.clear()
    results = mixin_config.apply(config("INFO"), log=logger)
    assert results[0]["status"] == "ok", results
    capture.clear()
    assert support.play(fake_plugin.FakeMemoryService()) == baseline
    joined = capture.text
    assert "[memtrace:mixin] store" in joined, "INFO 级别应保留摘要"
    assert "[memtrace:mixin] _score" not in joined, "INFO 级别不该输出逐条打分"

    # 开关关闭
    results = mixin_config.apply(config("DEBUG", enabled=False), log=logger)
    assert results[0]["status"] == "skipped", results
    assert fake_plugin.FakeMemoryService.__name__ == "FakeMemoryService"

    # 坏目标只影响自己
    broken = {"targets": [
        {"module": "no.such.module", "class": "X",
         "mixins": [{"path": "trace_mixin:TraceMixin"}]},
        {"module": "fake_plugin", "class": "FakeMemoryService",
         "mixins": [{"path": "trace_mixin:TraceMixin"}]},
    ]}
    capture.clear()
    results = mixin_config.apply(broken, log=logger)
    assert [r["status"] for r in results] == ["missing", "ok"], results
    mixin_config.uninstall(log=logger)

    # 从文件加载
    tmp = Path(tempfile.mkdtemp(prefix="mixincfg-"))
    path = tmp / "mixins.json"
    path.write_text(json.dumps(config("DEBUG"), ensure_ascii=False), encoding="utf-8")
    try:
        results = mixin_config.apply_file(path, log=logger)
        assert results[0]["status"] == "ok", results
        assert support.play(fake_plugin.FakeMemoryService()) == baseline
    finally:
        mixin_config.uninstall(log=logger)

    assert fake_plugin.FakeMemoryService.__name__ == "FakeMemoryService"
    print("OK 配置驱动：应用/参数/热更新/开关/容错/文件加载 全部通过")


if __name__ == "__main__":
    main()