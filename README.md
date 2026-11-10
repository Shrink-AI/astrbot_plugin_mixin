# astrbot_plugin_mixin

不改目标插件**一行源码**，在运行时给它套 mixin 或给方法加日志。

- 目标插件的文件、导入路径、加载方式都不动；
- 卸载时把类引用和原函数换回去；
- 配置放在部署根目录，找不到会自动生成一份，改完保存即热更新。

## 两条路，按需要选

| | 方法级补丁（`memtrace.py`） | mixin（`trace_mixin.py`） |
|---|---|---|
| 做法 | 把类上的方法换成包装器 | 动态重建子类 + 替换类引用 + 迁移旧实例 |
| 类身份 | 不变，`type(x) is C` 仍成立 | 变成子类，`isinstance` 成立 |
| `super()` 链 | 不行 | 可以，适合"在原有行为上再加一层" |
| 已有实例 | 不用管 | 要迁移（`__class__`），布局不兼容会失败 |
| 卸载 | 换回原函数对象 | 换回原类 + 实例迁回 |
| 风险 | 低 | 类名变化、MRO 变动 |

只观察就选前者；要叠加/改写行为（重排、过滤、审计）选后者。

## 安装

把仓库放进 AstrBot 的 `data/plugins/`：

```
AstrBot/
  data/
    plugins/
      astrbot_plugin_mixin/
        main.py
        metadata.yaml
        _conf_schema.json
        memtrace.py
        trace_mixin.py
        mixin_config.py
```

重启，或在 WebUI 插件管理里重载。

## 配置

默认什么都不用配：启动时会在**部署根目录**生成 `mixins.json`（与 `data/` 同级），并把路径打进日志。

查找与生成顺序：

```
1. WebUI 的 config_file
2. 环境变量 ASTRBOT_MIXIN_CONFIG
3. <AstrBot>/mixins.json          ← 默认生成在这里
4. 插件目录/mixins.json
5. 当前工作目录/mixins.json
6. data/config/astrbot_plugin_mixin/mixins.json
```

已有任意一份就用它，一份都没有才创建；部署根只读时依次降级，不会让插件起不来。

```json
{
  "version": 1,
  "targets": [
    {
      "module": "yourpkg.memory",
      "class": "MemoryService",
      "enabled": true,
      "params": {"level": "DEBUG"},
      "mixins": [
        {"path": "trace_mixin:TraceMixin", "enabled": true,
         "params": {"level": "DEBUG"}}
      ]
    }
  ]
}
```

- `mixins` 的顺序就是 MRO 顺序，写在前面的优先级更高；也可以用 `priority`（大的在前）；
- `path` 指向可导入的模块：类（`"trace_mixin:TraceMixin"`），`file` 指向一个 `.py` 文件（相对配置文件目录）；
- `params` 两级合并：`targets[].params` → `mixins[].params`，后者覆盖前者；
- `level` 支持 `"DEBUG"`/`"INFO"`，`INFO` 只留 `store`/`recall` 摘要，不刷逐条打分；
- `log` 写字符串按 logger 名字解析（`"log": "my.memtrace"`），写 `null` 表示不接管、回落到模块 logger，不写则由管理器注入它自己的 logger；
- 单条配置写错只跳过那一条，其他目标照常。

## 钩子：在你需要的位置做信息处理

不想只打日志、而是想在**调用前 / 返回时 / 调用后**做自己的处理（统计、采样、落一份审计、判断要不要告警……），写一个 `HookMixin` 子类即可：

```python
from hook_mixin import HookMixin

class Audit(HookMixin):
    def on_before_remember(self, ctx):
        print("进入", ctx["method"], "参数", ctx["args"], ctx["kwargs"])

    def on_return_remember(self, ctx):
        print("返回", ctx["result"], "耗时", ctx["elapsed_ms"])

    def on_after_remember(self, ctx):
        print("结束，是否异常：", "error" in ctx)

    def on_error_remember(self, ctx):
        print("出错了", ctx["error"])
```

放进 `mixins/audit.py`，配置里写文件路径：

```json
{
  "module": "yourpkg.memory",
  "class": "MemoryService",
  "mixins": [{"file": "mixins/audit.py", "class": "Audit", "priority": 1000}]
}
```

执行顺序：

| 阶段 | 钩子 | 触发时机 | `ctx` 里有 |
|---|---|---|---|
| 调用前 | `on_before_<m>` | 方法体执行之前 | `method` `args` `kwargs` `self` |
| 返回时 | `on_return_<m>` | 正常返回、**拿到返回值之后** | 上列 + `result` `elapsed_ms` |
| 调用后 | `on_after_<m>` | 无论成功失败都会跑（相当于 finally） | 上列 + `error`（失败时） |
| 出错时 | `on_error_<m>` | 方法抛出异常时 | 上列 + `error` |

顺序是 `before → 方法体 → return → after`，失败时是 `before → 方法体(raises) → error → after`，然后异常原样抛出。

三条约定：

- **钩子只读**：不提供改值通道，包装器永远返回原实现的返回值；想改行为请用普通 mixin 覆盖方法。
- **钩子不能弄坏目标**：钩子里抛异常会被捕获并记一条 `[mixin:hook]`，方法继续正常执行。
- **一次返回算一次**：方法有多个 `return` 语句时，`on_return` 仍然只触发一次（在真正返回给调用方时）。要精确到"每个 return 语句"，那需要 `sys.monitoring` 级别的行级钩子，目前没做。

方法名不用登记：`bind()` 会扫描你写的 `on_<阶段>_<方法>`，为目标类上真实存在的方法生成包装器，其余方法原样不动。

钩子想写自己的日志，用 `ctx["log"]`（和 `self.hook_log` 是同一个对象）：

```python
def on_before_store(self, ctx):
    ctx["log"].debug("store 进来：%s", ctx["args"][0].get("id"))
```

它取的就是配置里那个 `log`：写名字 → 按名字 `getLogger`；写 `null` → 回落到 `memtrace.mixin`；不写 → 管理器注入的 logger。

## 日志长什么样

```
[memtrace:mixin] store kind=topic subject=u1 origin=digest run=7 -> created id=m1 ms=0.2
[memtrace:mixin] recall_for src=g:1 speaker=u1 qchars=12 chosen=1 chars=3 ms=1.4
[memtrace:mixin] _score id=m1 kind=topic -> 0.500 ms=0.0
[memtrace:mixin] store raised ValueError: bad item (0.1ms)
```

只记录 id、类型、长度、分数、耗时——**不记录正文**。

## 边界

1. 只能看到方法边界。方法内部的分支（比如打分为什么是 0）包装器看不到，要那种粒度得改源码。
2. 目标插件热重载后类对象被替换，需要重新安装；配置文件热更新会自动重装，但类被换掉的情况仍需重载本插件。
3. 配置能指定任意 `module:Class` 并触发 import，等价于代码执行入口——别把它放在 Web 可访问目录，也别提交进仓库。
4. mixin 方案会让 `type(x) is C` 失效、类名变化；目标插件里若有这种恒等比较会走错分支。

## 测试

```
python tests/run_all.py
```

测试用自造的假目标类，不包含任何第三方插件代码。
## 许可证

AGPL-3.0，见 [LICENSE](LICENSE)。
