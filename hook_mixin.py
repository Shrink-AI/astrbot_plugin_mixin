"""hook_mixin —— 按名字挂钩子：调用前 / 返回时 / 调用后 / 出错时。

子类只写钩子方法，不碰目标类的任何值：

    class Audit(HookMixin):
        def on_before_store(self, ctx): ...   # 调用前
        def on_return_store(self, ctx): ...   # 返回时（ctx["result"] 是返回值）
        def on_after_store(self, ctx): ...    # 调用后（成功失败都会跑）
        def on_error_store(self, ctx): ...    # 出错时（ctx["error"]）

    bound = Audit.bind(MemoryService)         # 为「有钩子的方法」生成包装器
    traced = type("TracedMemoryService", (bound, MemoryService), {})

钩子的职责是**读**：参数、返回值、耗时都在 ctx 里，能不能改是另一回事——
这里不提供改值通道，包装器永远返回原实现的返回值。

钩子抛异常不会影响目标方法：内部捕获后记一条 `[mixin:hook]`，继续执行。
"""

import inspect
import logging
import time

import memtrace

_LOG = memtrace._DEFAULT_LOG
PREFIXES = ("before", "return", "after", "error")


class HookMixin:
    """把 `on_<阶段>_<方法名>` 变成包装器；没写钩子的方法不动。"""

    params = {"log": None}

    @classmethod
    def configure(cls, params):
        cls.params = {**cls.params, **(params or {})}

    @classmethod
    def _hooked_methods(cls, base_cls):
        names = set()
        for attr in dir(cls):
            for prefix in PREFIXES:
                marker = f"on_{prefix}_"
                if attr.startswith(marker):
                    name = attr[len(marker):]
                    if name and callable(getattr(base_cls, name, None)):
                        names.add(name)
                    break
        return sorted(names)

    @classmethod
    def bind(cls, base_cls):
        """返回一个带包装器的子类；没有匹配到任何钩子时原样返回 cls。"""
        names = cls._hooked_methods(base_cls)
        if not names:
            return cls
        cell = {}
        namespace = {}
        for name in names:
            is_async = inspect.iscoroutinefunction(getattr(base_cls, name))
            namespace[name] = _make_wrapper(name, is_async, cell)
        bound = type(f"Bound{cls.__name__}", (cls,), namespace)
        cell["cls"] = bound
        return bound


def _fire(self, prefix, name, ctx):
    hook = getattr(self, f"on_{prefix}_{name}", None)
    if hook is None:
        return
    try:
        hook(ctx)
    except Exception as exc:
        log = getattr(type(self), "params", {}).get("log") or _LOG
        memtrace._emit(log, logging.ERROR, "[mixin:hook] on_%s_%s failed: %s: %s",
                       prefix, name, type(exc).__name__, exc)


def _ctx(self, name, args, kwargs):
    return {"method": name, "self": self, "args": args, "kwargs": kwargs}


def _make_wrapper(name, is_async, cell):
    if is_async:
        async def wrapper(self, *args, **kwargs):
            ctx = _ctx(self, name, args, kwargs)
            started = time.perf_counter()
            _fire(self, "before", name, ctx)
            try:
                out = await getattr(super(cell["cls"], self), name)(*args, **kwargs)
            except Exception as exc:
                ctx["error"] = exc
                ctx["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
                _fire(self, "error", name, ctx)
                raise
            else:
                ctx["result"] = out
                ctx["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
                _fire(self, "return", name, ctx)
                return out
            finally:
                ctx.setdefault("elapsed_ms", (time.perf_counter() - started) * 1000.0)
                _fire(self, "after", name, ctx)
    else:
        def wrapper(self, *args, **kwargs):
            ctx = _ctx(self, name, args, kwargs)
            started = time.perf_counter()
            _fire(self, "before", name, ctx)
            try:
                out = getattr(super(cell["cls"], self), name)(*args, **kwargs)
            except Exception as exc:
                ctx["error"] = exc
                ctx["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
                _fire(self, "error", name, ctx)
                raise
            else:
                ctx["result"] = out
                ctx["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
                _fire(self, "return", name, ctx)
                return out
            finally:
                ctx.setdefault("elapsed_ms", (time.perf_counter() - started) * 1000.0)
                _fire(self, "after", name, ctx)
    wrapper.__name__ = name
    wrapper.__qualname__ = f"HookMixin.{name}"
    return wrapper