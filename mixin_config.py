"""mixin_config —— 用配置文件管理“给哪个类套哪些 mixin”。

配置文件（JSON；装了 PyYAML 也可以用 YAML）：

    {
      "version": 1,
      "targets": [
        {
          "module": "shrink.services.memory",
          "class": "MemoryService",
          "enabled": true,
          "params": {"level": "INFO"},
          "mixins": [
            {"path": "trace_mixin:TraceMixin", "enabled": true,
             "params": {"level": "DEBUG"}}
          ]
        }
      ]
    }

约定：
- `mixins` 的顺序就是 MRO 顺序，写在前面的优先级更高；
- `params` 会合并（target 级 → mixin 级，后者覆盖前者），再交给 mixin 的 `configure()`；
- 任何一条配置出错只跳过那一条，不影响其他目标，也不让宿主进程崩；
- `apply()` 会先 `uninstall()` 上一次的结果，所以可以反复调用来热更新。
"""

import gc
import hashlib
import importlib
import json
import logging
import os
import sys
import time
import types
from pathlib import Path

_LOG = logging.getLogger("mixin.config")
_APPLIED = []  # [{module, class, base, traced, aliases, mixins}]


class MixinConfigError(Exception):
    pass


# ---- 读取 -------------------------------------------------------------------------------------------
DEFAULT_NAME = "mixins.json"

TEMPLATE = {
    "_readme": (
        "把要插桩的目标写进 targets；每项形如 "
        "{\"module\": \"包路径\", \"class\": \"类名\", \"mixins\": [{\"path\": \"模块:类\"}]}。"
        "改完保存即热加载，不需要重启。"
    ),
    "version": 1,
    "targets": [],
}


def load(path):
    text = Path(path).read_text(encoding="utf-8")
    if str(path).lower().endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as exc:
            raise MixinConfigError("需要 PyYAML 才能读 YAML 配置，或改用 JSON") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise MixinConfigError("配置根节点必须是对象")
    return data


def _as_level(value, default=logging.DEBUG):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return getattr(logging, value.upper(), default)
    return default


# ---- 解析 -------------------------------------------------------------------------------------------
def _resolve_class(module_path, class_name):
    module = importlib.import_module(module_path)
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise MixinConfigError(f"{module_path} 里没有 {class_name}") from exc


def _resolve_mixin(spec):
    if ":" in spec:
        module_path, _, attr = spec.partition(":")
    else:
        module_path, _, attr = spec.rpartition(".")
    if not module_path or not attr:
        raise MixinConfigError(f"mixin 路径要写成 'module:Class' 或 'module.Class'，收到 {spec!r}")
    module = importlib.import_module(module_path)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise MixinConfigError(f"{module_path} 里没有 {attr}") from exc


def _rebind(base, traced):
    aliases = []
    for module in list(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if not namespace:
            continue
        for name, value in list(namespace.items()):
            if value is base:
                try:
                    setattr(module, name, traced)
                except Exception:
                    continue
                aliases.append((module, name))
    return aliases


def _pick_mixin_class(module, entry):
    name = entry.get("class")
    if name:
        cls = getattr(module, name, None)
        if not isinstance(cls, type):
            raise MixinConfigError(f"{module.__name__} 里没有类 {name}")
        return cls
    explicit = getattr(module, "MIXIN", None)
    if isinstance(explicit, type):
        return explicit
    candidates = [
        value for value in vars(module).values()
        if isinstance(value, type) and value.__module__ == module.__name__
        and (hasattr(value, "bind") or value.__name__.endswith("Mixin"))
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise MixinConfigError("无法确定 mixin 类，请在配置里写 \"class\"")


def _load_mixin_file(path_text, base_dir, entry, log):
    """加载 mixin 文件；相对路径以配置文件所在目录为基准。

    每次都直接编译源码，不经过 __pycache__：同一秒内改两次文件时，
    字节码缓存的 mtime 粒度会让它读到旧版本。
    """
    path = Path(path_text)
    if not path.is_absolute():
        path = (Path(base_dir or Path.cwd()) / path).resolve()
    if not path.is_file():
        raise MixinConfigError(f"mixin 文件不存在: {path}")
    name = f"_mixin_{hashlib.sha1(str(path).encode()).hexdigest()[:10]}"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise MixinConfigError(f"加载 {path} 失败: {type(exc).__name__}: {exc}") from exc
    if log is not None:
        log.debug("[mixin.config] 已加载 mixin 文件 %s -> %s", path, name)
    return _pick_mixin_class(module, entry)


def _migrate(old, new):
    moved = 0
    for obj in gc.get_objects():
        if type(obj) is old:
            try:
                obj.__class__ = new
            except TypeError:
                continue
            moved += 1
    return moved


# ---- 扫描 / 生成 ------------------------------------------------------------------------------------
def _data_path():
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(get_astrbot_data_path())
    except Exception:
        return None


def _data_config_path():
    """AstrBot 的数据目录：data/config/astrbot_plugin_mixin/mixins.json。"""
    data = _data_path()
    return None if data is None else data / "config" / "astrbot_plugin_mixin" / DEFAULT_NAME


def deployment_root():
    """AstrBot 的部署根目录，也就是与 data/ 同级的那一层。"""
    for key in ("ASTRBOT_ROOT", "ASTRBOT_DIR"):
        env = os.environ.get(key)
        if env and Path(env).is_dir():
            return Path(env)

    data = _data_path()
    if data is not None:
        try:
            if data.is_dir():
                return data.parent
        except OSError:
            pass

    # 没有 astrbot 包时（本地调试），看当前目录像不像部署根目录
    for base in (Path.cwd(), Path(__file__).resolve().parent):
        try:
            if (base / "data" / "plugins").is_dir():
                return base
        except OSError:
            continue
    return None


def candidate_paths(plugin_dir=None, explicit=None):
    """顺序即优先级，也即生成顺序：部署根目录优先。"""
    paths = []
    if explicit:
        paths.append(Path(explicit))
    env = os.environ.get("ASTRBOT_MIXIN_CONFIG")
    if env:
        paths.append(Path(env))
    root = deployment_root()
    if root is not None:
        paths.append(root / DEFAULT_NAME)
    if plugin_dir:
        paths.append(Path(plugin_dir) / DEFAULT_NAME)
    paths.append(Path.cwd() / DEFAULT_NAME)
    data = _data_config_path()
    if data is not None:
        paths.append(data)

    seen, unique = set(), []
    for path in paths:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def write_template(path):
    """原子写入默认配置；目标不可写时抛 OSError 交给上层降级。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(TEMPLATE, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        path.write_text(text, encoding="utf-8")
    return path


def scan_config(plugin_dir=None, explicit=None, log=None):
    """找到就用现成的，一个都没有就在第一个可写位置生成。返回 (path|None, created)。"""
    candidates = candidate_paths(plugin_dir, explicit)
    for path in candidates:
        if path.is_file():
            return path, False
    for path in candidates:
        try:
            return write_template(path), True
        except OSError as exc:
            if log is not None:
                log.debug("[mixin.config] 无法写入 %s: %s", path, exc)
            continue
    return None, False


# ---- 应用 / 还原 ------------------------------------------------------------------------------------
def apply(config, log=None, base_dir=None):
    """按配置重建子类。返回每个目标的执行摘要；单条失败不影响其他条目。"""
    log = log or _LOG
    uninstall(log=log)
    results = []
    for index, item in enumerate(config.get("targets") or []):
        label = f"targets[{index}]"
        if not item.get("enabled", True):
            results.append({"label": label, "status": "skipped"})
            continue
        module_path = item.get("module")
        class_name = item.get("class") or "MemoryService"
        if not module_path:
            log.warning("[mixin.config] %s 缺少 module，跳过", label)
            results.append({"label": label, "status": "invalid", "reason": "missing module"})
            continue
        try:
            base = _resolve_class(module_path, class_name)
        except Exception as exc:
            log.warning("[mixin.config] %s 解析目标失败 (%s.%s): %s",
                        label, module_path, class_name, exc)
            results.append({"label": label, "status": "missing", "reason": str(exc)})
            continue

        entries = [e for e in item.get("mixins") or [] if e.get("enabled", True)]
        try:
            entries.sort(key=lambda e: -int(e.get("priority") or 0))
        except (TypeError, ValueError):
            log.warning("[mixin.config] %s 的 priority 不是整数，保持原顺序", label)

        bases, applied = [], []
        for entry in entries:
            spec = entry.get("file") or entry.get("path") or ""
            try:
                if entry.get("file"):
                    mixin = _load_mixin_file(entry["file"], base_dir, entry, log)
                else:
                    mixin = _resolve_mixin(entry.get("path") or "")
            except Exception as exc:
                log.warning("[mixin.config] %s 的 mixin %s 不可用: %s", label, spec, exc)
                continue
            params = {**(item.get("params") or {}), **(entry.get("params") or {})}
            params.setdefault("log", log)  # 配置里没法写对象，默认用管理器的 logger
            if "level" in params:
                params["level"] = _as_level(params["level"])
            configure = getattr(mixin, "configure", None)
            if params and callable(configure):
                try:
                    configure(params)
                except Exception as exc:
                    log.warning("[mixin.config] %s 的 mixin %s configure 失败: %s", label, spec, exc)
                    continue
            binder = getattr(mixin, "bind", None)   # HookMixin 之类的按目标绑定
            if callable(binder):
                try:
                    mixin = binder(base)
                except Exception as exc:
                    log.warning("[mixin.config] %s 的 mixin %s bind 失败: %s", label, spec, exc)
                    continue
            bases.append(mixin)
            applied.append(spec or getattr(mixin, "__name__", "?"))
        if not bases:
            results.append({"label": label, "status": "no_mixin"})
            continue

        try:
            traced = type(
                f"Traced{base.__name__}",
                tuple(bases) + (base,),
                {"__module__": base.__module__, "__doc__": base.__doc__},
            )
        except Exception as exc:
            log.error("[mixin.config] %s 构建子类失败: %s", label, exc)
            results.append({"label": label, "status": "failed", "reason": str(exc)})
            continue

        aliases = _rebind(base, traced)
        moved = _migrate(base, traced)
        _APPLIED.append({
            "module": module_path, "class": class_name,
            "base": base, "traced": traced, "aliases": aliases,
        })
        log.info(
            "[mixin.config] %s.%s <- %s (aliases=%d instances=%d)",
            module_path, class_name, " + ".join(applied), len(aliases), moved,
        )
        results.append({
            "label": label, "status": "ok", "target": f"{module_path}.{class_name}",
            "mixins": applied, "aliases": len(aliases), "instances": moved,
        })
    return results


def apply_file(path, log=None):
    return apply(load(path), log=log, base_dir=Path(path).resolve().parent)


def uninstall(log=None):
    log = log or _LOG
    while _APPLIED:
        item = _APPLIED.pop()
        for module, name in item["aliases"]:
            try:
                setattr(module, name, item["base"])
            except Exception:
                pass
        _migrate(item["traced"], item["base"])
        log.info("[mixin.config] restored %s.%s", item["module"], item["class"])


def status():
    return [
        {"target": f"{i['module']}.{i['class']}", "instances_target": i["traced"].__name__}
        for i in _APPLIED
    ]


# ---- 热更新 -----------------------------------------------------------------------------------------
async def watch(path, interval=3.0, log=None):
    """文件 mtime 变化就重新 apply；配置写错时保留上一份，不影响运行。"""
    import asyncio

    log = log or _LOG
    last = None
    while True:
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            await asyncio.sleep(interval)
            continue
        if last is not None and mtime != last:
            try:
                results = apply_file(path, log=log)
                log.info("[mixin.config] 配置已热更新: %s", results)
            except Exception as exc:
                log.error("[mixin.config] 热更新失败，保留旧配置: %s", exc)
        last = mtime
        await asyncio.sleep(interval)