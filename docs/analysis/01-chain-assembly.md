# 01 · 参数解析到过滤器链的组装与执行

## 1. 入口

`logdissect.py:25-28` 只有三行有效逻辑：

```python
from logdissect.core import LogDissectCore
dissect = LogDissectCore()
dissect.run_job()
```

注意：在仓库根目录执行时，`python logdissect.py` 能正常导入 `logdissect` 包；
脚本与包目录同名但不冲突，因为脚本以 `__main__` 运行，`import logdissect.core`
解析到的是同目录下的包。

## 2. run_job 的阶段顺序

`LogDissectCore.run_job()`（`logdissect/core.py:58-80`）依次执行：

1. `load_parsers()`（`core.py:190-195`）—— 按 `sorted(logdissect.parsers.__all__)`
2. `load_filters()`（`core.py:197-202`）—— 按 `sorted(logdissect.filters.__filters__)`
3. `load_outputs()`（`core.py:204-209`）—— 按 `sorted(logdissect.output.__formats__)`
4. `config_args()`（`core.py:124-156`）—— **此时才真正 `parse_args()`**
5. `load_inputs()` → `run_parse()` → `merge_logs(sort=True)` → `run_filters()` → `run_output()`

关键时序：三个 `load_*` 都发生在 `config_args()` 之前。模块构造函数接收的是
argparse 的**参数组对象**（`self.filter_args`），由各过滤器在自己的 `__init__` 里
往同一个 group 上挂参数。例如：

- `logdissect/filters/grep.py:32-33` 注册 `--grep` → `dest='pattern'`，`action='append'`
- `logdissect/filters/rgrep.py:32-33` 注册 `--rgrep` → `dest='rpattern'`
- `logdissect/filters/source.py:31-32` 注册 `--source` → `dest='source'`
- `logdissect/filters/rsource.py:31-32` 注册 `--rsource` → `dest='rsource'`
- `logdissect/filters/dhost.py:31-32` / `rdhost.py:31-32` 注册 `--dhost` / `--rdhost`
- `logdissect/filters/last.py:33-34`（`action='store'`）与
  `logdissect/filters/range.py:32-34`（`--range`、`--utc`）

正反向过滤器挂的是**不同的 dest**（`pattern` vs `rpattern` 等），所以 `args` 命名空间里
正反两组取值彼此独立，不存在 argparse 层面的覆盖。

## 3. 数据对象 tsdata 的诞生

`run_parse()`（`core.py:82-95`）对每个输入文件调用 parser 单例的 `parse_file()`，
产出形如下面的**单文件 LogData（本文称 tsdata）**：

```python
{
  'parser': 'syslog',
  'source_path': '/abs/path/exsyslog',
  'source_file': 'exsyslog',
  'source_file_mtime': <float>,
  'source_file_year': 2026,
  'entries': [ {entry}, {entry}, ... ],
}
```

header 五个键在 `logdissect/parsers/type.py:54-63` 设置；entry 字段由
`parse_line()`（`type.py:125-167`）按 parser 的 `fields` 填充（syslog 见
`logdissect/parsers/syslog.py:32-33`：`date_stamp, log_source, source_process,
source_pid, message`），并在 `type.py:136-137,111-112` 额外写入 entry 级的
`parser` 与 `source_path`。

## 4. merge：entries 列表拼接 + 就地排序

`core.py:72-74` 调用 `logdissect.utils.merge_logs()`（`logdissect/utils.py:203-211`）：

```python
ourlog['entries'] = ourlog['entries'] + d['entries']          # utils.py:208
if sort and all('numeric_date_stamp_utc' in entry ...):        # utils.py:209
    ourlog['entries'].sort(key=lambda x: x['numeric_date_stamp_utc'])  # utils.py:210
```

注意 merge 的结果**只有 `entries` 一个键**：单文件 header（`parser`、`source_path`
等）在拼接时被丢弃；但每个 entry 自身带 `parser`/`source_path`，所以输出仍能标注来源。
这一点直接决定了过滤器里 header 拷贝分支永远不会走（见 [03](03-mutation-and-aliasing.md)）。

## 5. run_filters：字典迭代序即执行序

`run_filters()`（`core.py:97-105`）：

```python
for m in self.filter_modules:
    ourlog = ourfilter.filter_data(self.data_set['finalized_data'], args=self.args)
    self.data_set['finalized_data'] = ourlog
```

`self.filter_modules` 是普通 dict，其插入顺序就是 `load_filters()` 的插入顺序，
即 `sorted(__filters__)`。Python 3.7+ dict 保序，因此执行链固定为字母序，
与 `__filters__` 的书写顺序、以及命令行参数顺序都无关。完整 14 个节点的实际次序见
[chain.html](chain.html) 与 [02 节](02-ordering-and-short-circuit.md)。

## 复现命令

```bash
# 观察过滤器实际装载/执行顺序（sorted 覆盖 __filters__ 定义序）
python -W ignore -c "import logdissect.filters as f; print(f.__filters__); print(sorted(f.__filters__))"

# 端到端：syslog 默认解析 + grep
python -W ignore logdissect.py --grep dbus tests/files/exsyslog
```
