"""memtrace —— 给“别的插件”的类做运行时日志插桩，不改它一行源码。

原理：插件进程里，类方法就是一个可替换的属性。我们在**运行时**把目标类上的方法
换成带日志的包装器，卸载时把原函数换回去。目标插件的文件、加载方式、导入路径都不动。

用法（框架无关）：

    import memtrace
    memtrace.install("yourpkg.memory", "MemoryService")   # 或 "yourpkg.memory.MemoryService"
    ...
    memtrace.uninstall()                                  # 插件卸载/重载时调用

边界（必须知道）：
- 只能看到**方法边界**：入参、返回值、耗时、异常。
- `_score` 里“为什么返回 0”（status/过期/陈旧/跨场/私聊）在方法内部，包装器看不到，
  只能看到 `_score -> 0.000`；想知道具体哪道闸门，还是得在源码里加钩子。
- 目标插件若被热重载，类对象会被替换，需要重新 install（AstrBot 外壳里有看门狗）。
"""

import contextvars
import functools
import importlib
import inspect
import logging
import time

__all__ = ["install", "uninstall", "is_patched", "patched"]

_DEFAULT_LOG = logging.getLogger("memtrace")
_DEPTH = contextvars.ContextVar("memtrace_depth", default=0)
_PATCHED = []  # [(cls, name, original, tag)]


def _ms(started):
    return (time.perf_counter() - started) * 1000.0


def _dict_get(row, key, default="-"):
    try:
        return row.get(key, default) if row.get(key) not in (None, "") else default
    except Exception:
        return default


def _d_store(args, kwargs, out, ms):
    item = args[0] if args else {}
    origin = args[1] if len(args) > 1 else kwargs.get("origin", "-")
    run = args[2] if len(args) > 2 else kwargs.get("run_id", "-")
    mid, outcome = out if isinstance(out, tuple) and len(out) == 2 else ("-", "-")
    return (
        f"kind={_dict_get(item, 'kind')} subject={_dict_get(item, 'subject')} "
        f"origin={origin} run={run} -> {outcome} id={mid} ms={ms:.1f}"
    )


def _d_remember(args, kwargs, out, ms):
    mid, status = out if isinstance(out, tuple) and len(out) == 2 else ("-", "-")
    chars = len(args[1]) if len(args) > 1 and isinstance(args[1], str) else 0
    return f"id={mid} status={status} chars={chars} ms={ms:.1f}"


def _d_recall(args, kwargs, out, ms):
    query = args[0] if args else None
    block, chosen = out if isinstance(out, tuple) and len(out) == 2 else ("", [])
    return (
        f"src={getattr(query, 'source', '-')} speaker={getattr(query, 'speaker', '') or '-'} "
        f"qchars={len(getattr(query, 'text', '') or '')} chosen={len(chosen)} "
        f"chars={len(block or '')} ms={ms:.1f}"
    )


def _d_score(args, kwargs, out, ms):
    row = args[0] if args else {}
    score = out if isinstance(out, (int, float)) else 0.0
    return f"id={_dict_get(row, 'id')} kind={_dict_get(row, 'kind')} -> {score:.3f} ms={ms:.1f}"


def _d_dup(args, kwargs, out, ms):
    left = args[0] if args else {}
    right = args[1] if len(args) > 1 else {}
    return f"{_dict_get(left, 'id')}~{_dict_get(right, 'id')} -> {bool(out)}"


def _d_similarity(args, kwargs, out, ms):
    memory_id = args[1] if len(args) > 1 else "-"
    cosine = out if isinstance(out, (int, float)) else 0.0
    return f"id={memory_id} cos={cosine:.3f} ms={ms:.1f}"


def _d_nearest(args, kwargs, out, ms):
    return f"kept={len(out) if out else 0} ms={ms:.1f}"


def _d_render(args, kwargs, out, ms):
    rows = args[0] if args else []
    return f"rows={len(rows)} chars={len(out or '')} ms={ms:.1f}"


def _d_lessons(args, kwargs, out, ms):
    return f"lines={len((out or '').splitlines())} ms={ms:.1f}"


def _d_known(args, kwargs, out, ms):
    refs = out[0] if isinstance(out, tuple) and len(out) == 2 else {}
    return f"refs={len(refs)} ms={ms:.1f}"


def _d_index(args, kwargs, out, ms):
    return f"id={args[0] if args else '-'} ms={ms:.1f}"


# name -> (level, describe)
SPECS = {
    "vector": (logging.DEBUG, lambda a, k, o, ms: (
        f"chars={len((a[0] if a else '') or '')} dims={len(o) if o else 0} ms={ms:.1f}")),
    "index_memory": (logging.DEBUG, _d_index),
    "store": (logging.INFO, _d_store),
    "remember": (logging.INFO, _d_remember),
    "recall_for": (logging.INFO, _d_recall),
    "_score": (logging.DEBUG, _d_score),
    "_similarity": (logging.DEBUG, _d_similarity),
    "_nearest": (logging.DEBUG, _d_nearest),
    "_duplicate": (logging.DEBUG, _d_dup),
    "_index": (logging.DEBUG, _d_index),
    "render": (logging.DEBUG, _d_render),
    "lessons": (logging.DEBUG, _d_lessons),
    "known_for": (logging.DEBUG, _d_known),
}


_LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


def _log_level(log):
    """兼容标准 logging.Logger 与 AstrBot 的 logger 包装（可能没有 .level/.log）。"""
    return getattr(log, "level", logging.DEBUG)


def _emit(log, level, fmt, *args):
    """日志本身永远不能把目标插件搞崩。"""
    if _DEPTH.get() > 0:
        return
    token = _DEPTH.set(1)
    try:
        if hasattr(log, "log"):
            log.log(level, fmt, *args)
        else:
            text = fmt % args if args else fmt
            getattr(log, _LEVEL_NAMES.get(level, "info"), log.info)(text)
    except Exception:
        pass
    finally:
        _DEPTH.reset(token)


def _wrap(func, name, describe, log, level, tag, slow_ms):
    is_async = inspect.iscoroutinefunction(func)

    def report(started, out, args, kwargs, exc=None):
        elapsed = _ms(started)
        if exc is not None:
            _emit(log, logging.ERROR, "[memtrace] %s raised %s: %s (%.1fms)",
                  name, type(exc).__name__, exc, elapsed)
            return
        if level < _log_level(log):
            return
        try:
            # 丢掉 self：describe 只看业务参数
            detail = describe(args[1:], kwargs, out, elapsed)
        except Exception:
            detail = "<describe failed>"
        _emit(log, level, "[memtrace] %s %s", name, detail)
        if slow_ms and elapsed >= slow_ms:
            _emit(log, logging.WARNING, "[memtrace] %s slow %.1fms >= %sms", name, elapsed, slow_ms)

    if is_async:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                out = await func(*args, **kwargs)
            except Exception as exc:
                report(started, None, args, kwargs, exc)
                raise
            report(started, out, args, kwargs)
            return out
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                out = func(*args, **kwargs)
            except Exception as exc:
                report(started, None, args, kwargs, exc)
                raise
            report(started, out, args, kwargs)
            return out

    wrapper.__memtrace__ = tag
    return wrapper


def _resolve(spec, class_name=None):
    """spec 可以是 'pkg.mod'（配 class_name），也可以是 'pkg.mod.Class'。"""
    if class_name:
        return getattr(importlib.import_module(spec), class_name)
    module_path, _, attr = spec.rpartition(".")
    if not module_path:
        raise ValueError(f"需要完整路径，例如 pkg.mod.Class，收到 {spec!r}")
    return getattr(importlib.import_module(module_path), attr)


def install(spec, class_name=None, methods=None, log=None, level=logging.DEBUG,
            slow_ms=0, tag="memtrace"):
    """给目标类打日志插桩。幂等：重复调用会先还原再重新包装。"""
    log = log or _DEFAULT_LOG
    cls = _resolve(spec, class_name)
    uninstall(tag)
    done = []
    for name, (spec_level, describe) in SPECS.items():
        if methods and name not in methods:
            continue
        original = cls.__dict__.get(name)
        if not callable(original) or getattr(original, "__memtrace__", None) == tag:
            continue
        setattr(cls, name, _wrap(original, name, describe, log, spec_level, tag, slow_ms))
        _PATCHED.append((cls, name, original, tag))
        done.append(name)
    _emit(log, logging.INFO, "[memtrace] installed %s.%s methods=%d",
          getattr(cls, "__module__", "?"), getattr(cls, "__name__", "?"), len(done))
    return done


def uninstall(tag="memtrace"):
    """还原全部被替换的方法。插件卸载/重载前必须调用。"""
    global _PATCHED
    keep = []
    for cls, name, original, patched_tag in _PATCHED:
        if patched_tag != tag:
            keep.append((cls, name, original, patched_tag))
            continue
        setattr(cls, name, original)
    _PATCHED = keep


def is_patched(cls, tag="memtrace"):
    return any(getattr(v, "__memtrace__", None) == tag for v in cls.__dict__.values())


def patched():
    return [(getattr(c, "__module__", "?"), getattr(c, "__name__", "?"), n) for c, n, _, _ in _PATCHED]