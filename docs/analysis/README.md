# logdissect 过滤器链分析

本目录分析从 `logdissect.py` 入口、`argparse` 参数解析，到
`LogDissectCore` 按 `args` 组装并执行过滤器链的完整路径。配套产物：

- [01-chain-assembly.md](01-chain-assembly.md) —— 参数解析到过滤器链的组装与执行
- [02-ordering-and-short-circuit.md](02-ordering-and-short-circuit.md) —— 取舍一：按名加载、入链次序与短路时机
- [03-mutation-and-aliasing.md](03-mutation-and-aliasing.md) —— 取舍二：就地 mutate 与浅/深拷贝边界
- [04-tsdata-fields.md](04-tsdata-fields.md) —— 每个过滤器对 tsdata 字段的读写表
- [chain.html](chain.html) —— 浏览器直接打开的交互式链路图（点节点看字段读写）

所有结论标注到「文件:行号」，每条关键结论附一条可手工执行的复现命令。
结论同时被 `tests/test_filter_chain.py` 中的 28 条 pytest 用例锁定。

## 一句话结论

过滤器的**执行顺序由模块名的字母序决定，与命令行上参数出现的先后无关**
（`logdissect/core.py:199` 的 `sorted(...)`）；每个过滤器要么在对应参数为空时
**短路并把入参对象原样返回**（如 `logdissect/filters/grep.py:39-40`），要么构造
一个**新的外层 dict 和新的 entries 列表，但列表里装的仍是同一批 entry dict 对象**
（浅拷贝）。因此链上不存在对 entry 的深拷贝，别名贯穿 parse → merge → filter 全程；
entry 上的时间戳只在 parse 阶段被写入/重写，过滤器只读不写。

## 环境说明

分析与复现基于 Python 3.12（仓库代码为 Python 2 时代写法，3.12 下 parser 正则会打
`SyntaxWarning`，复现命令统一加 `-W ignore` 保持输出干净；这不影响逻辑结论）。

```bash
python -m pytest -q          # 28 passed
```

打开链路图：直接双击 `docs/analysis/chain.html`，或

```bash
start docs/analysis/chain.html      # Windows
xdg-open docs/analysis/chain.html   # Linux
```
