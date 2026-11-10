"""配置扫描与生成：优先落在部署根目录，缺失才创建，写不进去就降级。"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import support
import mixin_config


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mixinscan-"))
    saved_root = os.environ.pop("ASTRBOT_ROOT", None)
    try:
        # 1. 没有部署根目录（本地调试）时退到插件目录
        plugin_a = tmp / "plugin_a"
        plugin_a.mkdir()
        path, created = mixin_config.scan_config(plugin_dir=plugin_a)
        assert created and path == plugin_a / "mixins.json", (path, created)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1 and data["targets"] == [] and data["_readme"]
        assert mixin_config.scan_config(plugin_dir=plugin_a) == (path, False)

        # 2. 有部署根目录时生成在 <AstrBot>/mixins.json
        bot_root = tmp / "AstrBot"
        (bot_root / "data" / "plugins").mkdir(parents=True)
        os.environ["ASTRBOT_ROOT"] = str(bot_root)
        assert mixin_config.deployment_root() == bot_root

        plugin_b = tmp / "plugin_b"
        plugin_b.mkdir()
        path_b, created_b = mixin_config.scan_config(plugin_dir=plugin_b)
        assert created_b and path_b == bot_root / "mixins.json", (path_b, created_b)
        assert mixin_config.scan_config(plugin_dir=plugin_b) == (path_b, False)

        # 3. 显式路径优先（换一套干净的根目录，避免已被占用）
        root2 = tmp / "AstrBot2"
        (root2 / "data" / "plugins").mkdir(parents=True)
        os.environ["ASTRBOT_ROOT"] = str(root2)
        plugin_c = tmp / "plugin_c"
        plugin_c.mkdir()
        explicit = tmp / "custom" / "m.json"
        path_c, created_c = mixin_config.scan_config(plugin_dir=plugin_c, explicit=explicit)
        assert created_c and path_c == explicit, (path_c, created_c)

        # 4. 已有配置时不会为显式路径另起一份空文件
        os.environ["ASTRBOT_ROOT"] = str(bot_root)
        path_d, created_d = mixin_config.scan_config(
            plugin_dir=plugin_b, explicit=tmp / "custom" / "missing.json",
        )
        assert path_d == path_b and not created_d, (path_d, created_d)

        # 5. 生成的空配置能加载、能应用（0 个目标）
        assert mixin_config.load(path_b)["targets"] == []
        assert mixin_config.apply_file(path_b) == []

        # 6. 写不进去要降级，而不是抛异常
        blocker = tmp / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        fallback, created_e = mixin_config.scan_config(
            plugin_dir=plugin_b, explicit=blocker / "mixins.json",
        )
        assert fallback == path_b and not created_e, (fallback, created_e)

        print("OK 配置扫描：兜底/部署根目录优先/显式优先/可应用/降级 全部通过")
    finally:
        os.environ.pop("ASTRBOT_ROOT", None)
        if saved_root is not None:
            os.environ["ASTRBOT_ROOT"] = saved_root
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()