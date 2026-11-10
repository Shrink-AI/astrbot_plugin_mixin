"""mixin 方案：重建子类、迁移旧实例、行为不变、可还原。"""

import support
import trace_mixin
import fake_plugin
from fake_plugin import FakeMemoryService


def main():
    logger, capture = support.make_logger()

    base_cls = FakeMemoryService
    old_instance = FakeMemoryService()          # 安装前就存在，应该被 __class__ 迁移
    baseline = support.play(old_instance)

    info = trace_mixin.install("fake_plugin", "FakeMemoryService", log=logger)
    traced = info["traced"]
    assert traced.__mro__[1] is trace_mixin.TraceMixin, traced.__mro__
    assert traced.__mro__[2] is base_cls, traced.__mro__
    assert fake_plugin.FakeMemoryService is traced, "模块里的类引用没被替换"
    assert type(old_instance) is traced, "旧实例没有被迁移"
    assert isinstance(old_instance, base_cls), "isinstance 应该仍然成立"

    assert support.play(old_instance) == baseline, "mixin 版行为变了"
    assert "[memtrace:mixin] store" in capture.text, "mixin 版没有日志"
    assert support.SECRET not in capture.text, "日志泄漏了正文"

    trace_mixin.uninstall()
    assert fake_plugin.FakeMemoryService is base_cls, "卸载没有还原类引用"
    assert type(old_instance) is base_cls, "卸载没有把实例迁回去"
    print(f"OK mixin：aliases={info['aliases']} instances={info['instances']} "
          f"日志 {len(capture.messages)} 行")


if __name__ == "__main__":
    main()