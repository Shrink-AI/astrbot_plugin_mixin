"""trace_mixin —— 用 mixin 的方式给目标类加日志：动态重建一个子类，替换类引用。

和 memtrace（方法级补丁）的区别：这里走的是 **MRO**，所以 `super()` 可以链式调用，
mixin 里也能加自己的方法和状态；代价是类身份变了，已经存在的实例要迁过去。

    import trace_mixin
    trace_mixin.install("yourpkg.memory", "MemoryService")
    ...
    trace_mixin.uninstall()
"""

import gc
import logging
import time

import memtrace

_LOG = logging.getLogger("memtrace.mixin")

_state = {
    "base": None,
    "traced": None,
    "aliases": [],
    "log": None,
    "level": logging.DEBUG,
}


def _report(name, describe, level, started, args, kwargs, out, exc=None):
    log = TraceMixin.params.get("log") or _LOG
    elapsed = (time.perf_counter() - started) * 1000.0
    if exc is not None:
        memtrace._emit(log, logging.ERROR,
                       "[memtrace:mixin] %s raised %s: %s (%.1fms)",
                       name, type(exc).__name__, exc, elapsed)
        return
    threshold = max(memtrace._log_level(log), TraceMixin.params.get("level", logging.DEBUG))
    if level < threshold:
        return
    try:
        detail = describe(args, kwargs, out, elapsed)
    except Exception:
        detail = "<describe failed>"
    memtrace._emit(log, level, "[memtrace:mixin] %s %s", name, detail)


def _make_method(name, describe, level, is_async):
    if is_async:
        async def wrapper(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                out = await getattr(super(TraceMixin, self), name)(*args, **kwargs)
            except Exception as exc:
                _report(name, describe, level, started, args, kwargs, None, exc)
                raise
            _report(name, describe, level, started, args, kwargs, out)
            return out
    else:
        def wrapper(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                out = getattr(super(TraceMixin, self), name)(*args, **kwargs)
            except Exception as exc:
                _report(name, describe, level, started, args, kwargs, None, exc)
                raise
            _report(name, describe, level, started, args, kwargs, out)
            return out
    wrapper.__name__ = name
    wrapper.__qualname__ = f"TraceMixin.{name}"
    return wrapper


class TraceMixin:
    """方法体在 install 时按 memtrace.SPECS 注入；这里只做 `super()` 转发。"""

    params = {"log": None, "level": logging.DEBUG}

    @classmethod
    def configure(cls, params):
        """配置文件里的 params 在这里生效（level/log 等）。"""
        cls.params = {**cls.params, **(params or {})}


for _name, (_level, _describe) in memtrace.SPECS.items():
    _async = _name in ("vector", "index_memory", "store", "remember", "recall_for", "_index")
    setattr(TraceMixin, _name, _make_method(_name, _describe, _level, _async))


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


def install(spec, class_name=None, log=None, level=logging.DEBUG):
    """重建子类、替换所有引用了目标类的模块属性、迁移已有实例。"""
    base = memtrace._resolve(spec, class_name)
    TraceMixin.configure({"log": log, "level": level})
    traced = type(
        f"Traced{base.__name__}",
        (TraceMixin, base),
        {"__module__": base.__module__, "__doc__": base.__doc__},
    )
    _state.update({"base": base, "traced": traced, "log": log, "level": level, "aliases": []})

    import sys
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
                _state["aliases"].append((module, name))

    moved = _migrate(base, traced)
    memtrace._emit(log or _LOG, logging.INFO,
                   "[memtrace:mixin] %s -> %s aliases=%d instances=%d",
                   base.__name__, traced.__name__, len(_state["aliases"]), moved)
    return {"traced": traced, "aliases": len(_state["aliases"]), "instances": moved}


def uninstall():
    base, traced = _state["base"], _state["traced"]
    if base is None:
        return 0
    for module, name in _state["aliases"]:
        try:
            setattr(module, name, base)
        except Exception:
            pass
    moved = _migrate(traced, base)
    _state.update({"base": None, "traced": None, "aliases": []})
    memtrace._emit(_state["log"] or _LOG, logging.INFO,
                   "[memtrace:mixin] restored %s instances=%d", base.__name__, moved)
    return moved