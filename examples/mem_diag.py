"""放在 <AstrBot>/mixins/ 下的诊断 mixin：把记忆召回的入参、产出、写入结果打出来。

配置（<AstrBot>/mixins.json）：

    {
      "targets": [{
        "module": "shrink.services.memory",
        "class": "MemoryService",
        "params": {"level": "DEBUG", "log": "mem.diag"},
        "mixins": [{
          "file": "mixins/mem_diag.py",
          "class": "MemoryDiag",
          "params": {"clip": 60, "full": false, "stack": true}
        }]
      }]
    }

`clip` 是正文截断字数，`full=true` 打全文；`stack=true` 额外打一条调用链，
用来确认这次召回是被人格/表达流程调的，还是被别的路径绕过去了。
"""

import traceback

from hook_mixin import HookMixin

ROW_FIELDS = ("kind", "subject", "importance", "confidence", "status", "source", "recalls")


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class MemoryDiag(HookMixin):
    def _opt(self, key, default=None):
        return getattr(type(self), "params", {}).get(key, default)

    def _clip(self, text):
        if self._opt("full", False):
            return text
        limit = int(self._opt("clip", 60) or 60)
        text = text or ""
        return text if len(text) <= limit else text[:limit] + "…"

    def _stack(self, log, tag):
        if not self._opt("stack", False):
            return
        frames = traceback.extract_stack(limit=10)[:-1]
        chain = " <- ".join(f"{f.name}:{f.lineno}" for f in reversed(frames[-5:]))
        log.debug("[diag] %s 调用链: %s", tag, chain)

    def on_before_recall_for(self, ctx):
        query = ctx["args"][0]
        log = ctx["log"]
        log.info(
            "[diag] recall 入参 speaker=%s mention=%s window=%s src=%s budget=%s limit=%s text=%r",
            _get(query, "speaker"), _get(query, "mentioned"), _get(query, "window"),
            _get(query, "source"), ctx["kwargs"].get("budget"), ctx["kwargs"].get("limit"),
            self._clip(_get(query, "text", "")),
        )
        self._stack(log, "recall_for")

    def on_return_recall_for(self, ctx):
        log = ctx["log"]
        result = ctx["result"]
        if isinstance(result, tuple) and len(result) == 2:
            block, chosen = result
        else:
            block, chosen = result, []
        chosen = list(chosen or [])
        log.info(
            "[diag] recall 结果 命中=%d 注入=%d字 耗时=%.1fms",
            len(chosen), len(block or ""), ctx.get("elapsed_ms", 0.0),
        )
        for row in chosen:
            log.info(
                "[diag]   - id=%s kind=%s subject=%s imp=%s conf=%s src=%s recalls=%s text=%r",
                _get(row, "id"), _get(row, "kind"), _get(row, "subject"),
                _get(row, "importance"), _get(row, "confidence"), _get(row, "source"),
                _get(row, "recalls"), self._clip(_get(row, "text", "")),
            )
        if not chosen:
            log.warning("[diag] 一条都没召回：这次回复拿到的记忆块是空的")
        if block:
            log.debug("[diag] 注入的记忆块:\n%s", self._clip(block))

    def on_return_render(self, ctx):
        log = ctx["log"]
        rows = ctx["args"][0] if ctx["args"] else []
        log.debug("[diag] render rows=%d -> %d字: %r",
                  len(rows or []), len(ctx["result"] or ""), self._clip(ctx["result"]))

    def on_return_store(self, ctx):
        log = ctx["log"]
        result = ctx["result"]
        if isinstance(result, tuple) and len(result) == 2:
            memory_id, outcome = result
        else:
            memory_id, outcome = result, "?"
        item = ctx["args"][0] if ctx["args"] else {}
        log.info("[diag] store kind=%s subject=%s -> %s id=%s",
                 _get(item, "kind"), _get(item, "subject"), outcome, memory_id)

    def on_return_lessons(self, ctx):
        ctx["log"].debug("[diag] lessons 产出 %d字: %r",
                         len(ctx["result"] or ""), self._clip(ctx["result"]))

    def on_return_known_for(self, ctx):
        log = ctx["log"]
        result = ctx["result"]
        refs = result[0] if isinstance(result, tuple) and len(result) == 2 else {}
        log.info("[diag] known_for 参考 %d 条", len(refs or {}))

    def on_error_recall_for(self, ctx):
        exc = ctx.get("error")
        ctx["log"].error("[diag] recall 抛异常: %s: %s", type(exc).__name__, exc)