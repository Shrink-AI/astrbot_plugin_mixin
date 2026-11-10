"""astrbot_plugin_memtrace —— 给别的插件的方法插日志桩，不改它的源码。

它不是“改插件”，而是进程内替换方法对象：目标插件的文件、加载方式、导入路径都不动。
卸载/重载时 `memtrace.uninstall()` 把原函数换回去。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    import memtrace
except ImportError:  # 以插件目录形式部署时，memtrace.py 与 main.py 同级
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import memtrace


class MemTracePlugin(Star):
    def __init__(self, context: Context, config=None):
        try:
            super().__init__(context)
        except TypeError:
            super().__init__()
        self.config = config or {}
        self.target = str(self.config.get("target") or "").strip()
        self.class_name = str(self.config.get("class") or "MemoryService").strip()
        self.level = getattr(
            logging, str(self.config.get("level") or "DEBUG").upper(), logging.DEBUG
        )
        self.slow_ms = int(self.config.get("slow_ms") or 0)
        self.config_file = str(self.config.get("config_file") or "").strip()
        self.reload_s = max(1.0, float(self.config.get("reload_interval_s") or 3))
        self._installed = False
        self._task = None
        self._watcher = None
        mode = (
            f"配置文件 {self.config_file}" if self.config_file
            else f"单目标 {self.target or '<未配置>'}.{self.class_name}" if self.target
            else "自动扫描/生成 mixins.json"
        )
        logger.info("[mixin] 模式: %s", mode)
        self._ensure_task()

    def _config_path(self):
        if not self.config_file:
            return None
        path = Path(self.config_file)
        return path if path.is_absolute() else Path(__file__).resolve().parent / path

    def _start_watcher(self, path):
        from mixin_config import watch

        self._installed = True
        self._watcher = asyncio.create_task(
            watch(path, interval=self.reload_s, log=logger)
        )

    async def _install_from_config(self):
        """配置文件模式：扫描/生成 mixins.json，应用后监听变更。"""
        from mixin_config import apply_file, load, scan_config

        path, created = scan_config(
            plugin_dir=Path(__file__).resolve().parent,
            explicit=self._config_path(),
            log=logger,
        )
        if path is None:
            logger.error(
                "[mixin] 插件目录、工作目录、data/config 都不可写，无法生成 mixin 配置"
            )
            return
        if created:
            logger.info("[mixin] 已生成默认配置 %s；填好 targets 保存即可生效", path)
            self._start_watcher(path)
            return
        try:
            data = load(path)
        except Exception as exc:
            logger.error("[mixin] 配置解析失败 %s: %s: %s",
                         path, type(exc).__name__, exc)
            return
        if not data.get("targets"):
            logger.info("[mixin] %s 里还没有目标；编辑保存后会自动生效", path)
            self._start_watcher(path)
            return
        for attempt in range(1, 31):
            try:
                results = apply_file(path, log=logger)
            except Exception as exc:
                logger.error("[mixin] 应用配置失败 %s: %s: %s",
                             path, type(exc).__name__, exc)
                return
            ok = [r for r in results if r.get("status") == "ok"]
            if ok:
                logger.info("[mixin] 已按配置插桩 %d 个目标: %s",
                            len(ok), [r.get("target") for r in ok])
                self._start_watcher(path)
                return
            if attempt == 30:
                logger.warning("[mixin] 配置里暂时没有可应用的目标，继续监听 %s", path)
                self._start_watcher(path)
                return
            await asyncio.sleep(1)

    async def _install_until_ready(self):
        """插件加载顺序不确定：目标模块可能比本插件晚导入，所以重试。"""
        if self.config_file or not self.target:
            await self._install_from_config()
            return
        if not self.target:
            logger.warning("[mixin] 未配置 target，不插桩")
            return
        for attempt in range(1, 31):
            try:
                names = memtrace.install(
                    self.target, self.class_name, log=logger,
                    level=self.level, slow_ms=self.slow_ms,
                )
            except Exception as exc:
                if attempt == 30:
                    logger.error(
                        "[mixin] 30 次仍未能安装 %s.%s: %s: %s",
                        self.target, self.class_name, type(exc).__name__, exc,
                    )
                    return
                await asyncio.sleep(1)
                continue
            self._installed = True
            logger.info(
                "[mixin] 已插桩 %s.%s，共 %d 个方法：%s",
                self.target, self.class_name, len(names), ",".join(names),
            )
            return

    def _ensure_task(self):
        if self._task is not None or self._installed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环时由第一条消息兜底
        self._task = loop.create_task(self._install_until_ready())

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _kick(self, event: AstrMessageEvent):
        """兜底触发：__init__ 里没有事件循环时，用第一条消息补上。"""
        self._ensure_task()

    async def terminate(self):
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        watcher, self._watcher = self._watcher, None
        if watcher is not None and not watcher.done():
            watcher.cancel()
        try:
            import mixin_config
            mixin_config.uninstall(log=logger)
        except ImportError:
            pass
        memtrace.uninstall()
        self._installed = False
        logger.info("[mixin] 已还原目标方法")