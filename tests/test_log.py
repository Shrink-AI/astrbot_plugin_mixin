"""logger 来源：配置里写名字、写 null 回落默认、钩子拿到的 ctx["log"]。"""

import asyncio
import logging

import support
import mixin_config
import fake_plugin
from hook_mixin import HookMixin


class HookAudit(HookMixin):
    def on_before_store(self, ctx):
        support.HOOK_EVENTS.append((ctx["log"].name, self.hook_log.name, ctx["method"]))


class Boom(HookMixin):
    def on_before_remember(self, ctx):
        raise RuntimeError("boom")


def cfg(mixins, **params):
    return {"targets": [{
        "module": "fake_plugin", "class": "FakeMemoryService",
        "params": params, "mixins": mixins,
    }]}


def main():
    # 1. 配置里写 logger 名字：日志进这个 logger
    sink, cap = support.make_logger("mixin.test.sink")
    results = mixin_config.apply(
        cfg([{"path": "trace_mixin:TraceMixin"}], log="mixin.test.sink", level="DEBUG"),
    )
    assert results[0]["status"] == "ok", results
    import trace_mixin
    assert trace_mixin.TraceMixin.params["log"] is sink, trace_mixin.TraceMixin.params
    asyncio.run(fake_plugin.FakeMemoryService().store({"id": "m1"}, "digest", 1))
    assert "[memtrace:mixin] store" in cap.text, cap.text

    # 2. 钩子里的 ctx["log"] 与 self.hook_log 指向同一个对象
    hook_sink, hook_cap = support.make_logger("mixin.test.hook")
    results = mixin_config.apply(
        cfg([{"path": "test_log:HookAudit"}], log="mixin.test.hook"),
    )
    assert results[0]["status"] == "ok", results
    support.HOOK_EVENTS.clear()
    asyncio.run(fake_plugin.FakeMemoryService().store({"id": "m1"}, "digest", 1))
    assert support.HOOK_EVENTS == [
        ("mixin.test.hook", "mixin.test.hook", "store"),
    ], support.HOOK_EVENTS

    # 3. 钩子抛异常时，[mixin:hook] 也写到配置指定的 logger
    mixin_config.apply(cfg([{"path": "test_log:Boom"}], log="mixin.test.hook"))
    asyncio.run(fake_plugin.FakeMemoryService().remember({"id": "m1"}, "正文"))
    assert "[mixin:hook] on_before_remember failed" in hook_cap.text, hook_cap.text

    # 4. 显式写 null：不接管，回落到模块 logger memtrace.mixin
    default_sink, default_cap = support.make_logger("memtrace.mixin")
    mixin_config.apply(cfg([{"path": "trace_mixin:TraceMixin"}], log=None, level="DEBUG"))
    assert trace_mixin.TraceMixin.params["log"] is None
    asyncio.run(fake_plugin.FakeMemoryService().store({"id": "m2"}, "digest", 1))
    assert "[memtrace:mixin] store" in default_cap.text, default_cap.text
    assert default_sink.name == "memtrace.mixin"

    # 5. 名字前后空白会被去掉
    mixin_config.apply(cfg([{"path": "trace_mixin:TraceMixin"}], log="  mixin.test.sink  "))
    assert trace_mixin.TraceMixin.params["log"] is sink

    mixin_config.uninstall()

    # 直接调 install() 一样可以传对象
    import memtrace
    memtrace.install("fake_plugin", "FakeMemoryService", log=sink, level=logging.DEBUG)
    asyncio.run(fake_plugin.FakeMemoryService().store({"id": "m3"}, "digest", 1))
    assert "[memtrace] store" in cap.text, cap.text
    memtrace.uninstall("memtrace")

    print("OK logger：配置写名字 / null 回落 / 钩子 ctx[log] / install 传对象 全部通过")


if __name__ == "__main__":
    main()