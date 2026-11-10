"""钩子：调用前 / 返回时 / 调用后 / 出错时，且不改变目标行为。"""

import asyncio
import json
import tempfile
from pathlib import Path

import support
import mixin_config
import fake_plugin
from hook_mixin import HookMixin


class Audit(HookMixin):
    events = []

    def on_before_store(self, ctx):
        Audit.events.append(("before", ctx["method"], ctx["args"][0].get("id")))

    def on_return_store(self, ctx):
        Audit.events.append(("return", ctx["method"], ctx["result"]))

    def on_after_store(self, ctx):
        Audit.events.append(("after", ctx["method"], "error" in ctx))

    def on_error_store(self, ctx):
        Audit.events.append(("error", ctx["method"], type(ctx["error"]).__name__))


class Noisy(Audit):
    def on_before_remember(self, ctx):
        raise RuntimeError("hook 自己也炸")


def config(mixins):
    return {"targets": [{
        "module": "fake_plugin", "class": "FakeMemoryService", "mixins": mixins,
    }]}


def main():
    logger, capture = support.make_logger("mixin.hooks")
    baseline = support.play(fake_plugin.FakeMemoryService())

    results = mixin_config.apply(
        config([{"path": "test_hooks:Audit", "params": {"level": "DEBUG"}}]),
        log=logger,
    )
    assert results[0]["status"] == "ok", results

    Audit.events.clear()
    service = fake_plugin.FakeMemoryService()
    assert support.play(service) == baseline, "加了钩子之后行为变了"

    # 正常路径：before → return → after
    events = list(Audit.events)
    assert events[0][0] == "before" and events[0][2] == "m1", events
    assert [e[0] for e in events].count("return") == 1, events
    assert events[-1][0] == "after" and events[-1][2] is False, events
    assert ("return", "store", ("m1", "created")) in events, events

    # 异常路径：before → error → after，异常照旧抛出
    Audit.events.clear()
    raised = False
    try:
        asyncio.run(service.store({"id": "boom"}, "digest", 1))
    except ValueError:
        raised = True
    assert raised, "异常没有透传"
    assert [e[0] for e in Audit.events] == ["before", "error", "after"], Audit.events
    assert Audit.events[1][2] == "ValueError"

    # 钩子自己抛异常不能影响目标方法
    mixin_config.apply(config([{"path": "test_hooks:Noisy"}]), log=logger)
    service = fake_plugin.FakeMemoryService()
    assert asyncio.run(service.remember({"id": "m9"}, "正文")) == ("m9", "created")
    assert "[mixin:hook] on_before_remember failed" in capture.text

    # priority 大的排在 MRO 更前面
    mixin_config.apply(config([
        {"path": "trace_mixin:TraceMixin", "priority": 1},
        {"path": "test_hooks:Audit", "priority": 10},
    ]), log=logger)
    traced = fake_plugin.FakeMemoryService
    assert traced.__mro__[1].__name__ == "BoundAudit", traced.__mro__

    # 配置文件里的 file 来源（相对配置文件目录），改文件后重新应用能生效
    tmp = Path(tempfile.mkdtemp(prefix="mixinhook-"))
    (tmp / "mixins").mkdir()
    mixin_py = tmp / "mixins" / "audit.py"
    mixin_py.write_text(
        "from hook_mixin import HookMixin\n"
        "class FileAudit(HookMixin):\n"
        "    events = []\n"
        "    def on_before_remember(self, ctx):\n"
        "        FileAudit.events.append(('v1', ctx['method']))\n",
        encoding="utf-8",
    )
    conf = tmp / "mixins.json"
    conf.write_text(json.dumps({"targets": [{
        "module": "fake_plugin", "class": "FakeMemoryService",
        "mixins": [{"file": "mixins/audit.py"}],
    }]}), encoding="utf-8")

    results = mixin_config.apply_file(conf, log=logger)
    assert results[0]["status"] == "ok", results
    service = fake_plugin.FakeMemoryService()
    asyncio.run(service.remember({"id": "m1"}, "正文"))
    file_audit = results[0]["mixins"][0]
    assert file_audit == "mixins/audit.py", results

    # 改文件、重新 apply：新版本生效
    mixin_py.write_text(
        "from hook_mixin import HookMixin\n"
        "class FileAudit(HookMixin):\n"
        "    events = []\n"
        "    def on_before_remember(self, ctx):\n"
        "        FileAudit.events.append(('v2', ctx['method']))\n",
        encoding="utf-8",
    )
    mixin_config.apply_file(conf, log=logger)
    service = fake_plugin.FakeMemoryService()
    asyncio.run(service.remember({"id": "m1"}, "正文"))
    module = __import__("_mixin_" + __import__("hashlib").sha1(
        str(mixin_py.resolve()).encode()).hexdigest()[:10], fromlist=["FileAudit"])
    assert module.FileAudit.events[0] == ("v2", "remember"), module.FileAudit.events

    mixin_config.uninstall(log=logger)
    assert fake_plugin.FakeMemoryService.__name__ == "FakeMemoryService"
    print("OK 钩子：before/return/after/error、钩子容错、priority、file 加载与重载 全部通过")


if __name__ == "__main__":
    main()