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

- `mixins` 的顺序就是 MRO 顺序，写在前面的优先级更高；
- `params` 两级合并：`targets[].params` → `mixins[].params`，后者覆盖前者；
- `level` 支持 `"DEBUG"`/`"INFO"`，`INFO` 只留 `store`/`recall` 摘要，不刷逐条打分；
- 单条配置写错只跳过那一条，其他目标照常。

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