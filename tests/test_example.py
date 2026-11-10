"""示例 mixin（examples/mem_diag.py）能在真实形状上跑通并打出关键行。"""

import asyncio

import support
import mixin_config
import fake_plugin


def main():
    logger, cap = support.make_logger("mem.diag")
    results = mixin_config.apply({
        "targets": [{
            "module": "fake_plugin", "class": "FakeMemoryService",
            "params": {"log": "mem.diag", "level": "DEBUG"},
            "mixins": [{
                "path": "examples.mem_diag:MemoryDiag",
                "params": {"clip": 20, "stack": True},
            }],
        }],
    }, log=logger)
    assert results[0]["status"] == "ok", results

    service = fake_plugin.FakeMemoryService()
    asyncio.run(service.store({"id": "m1", "kind": "topic", "subject": "u1"}, "digest", 1))
    asyncio.run(service.recall_for(fake_plugin.FakeQuery("今晚吃什么")))

    text = cap.text
    assert "[diag] recall 入参" in text, text
    assert "[diag] recall 结果" in text, text
    assert "[diag] recall_for 调用链" in text, text
    assert "[diag] store kind=" in text, text

    mixin_config.uninstall()
    print("OK 示例 mixin：入参 / 结果 / 调用链 / store 都能打出来")


if __name__ == "__main__":
    main()