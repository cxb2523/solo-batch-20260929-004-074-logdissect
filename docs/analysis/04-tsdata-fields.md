# 04 · 各过滤器对 tsdata 字段的读写

tsdata 形状：`{'parser', 'source_path', 'source_file', 'source_file_mtime',
'source_file_year', 'entries': [entry, ...]}`（merge 后只剩 `entries`）。

entry 的标准字段（以 syslog 为例，见 `parsers/syslog.py:32-33` 与
`parsers/type.py`）：

| 字段 | 来源 |
|------|------|
| `raw_text` | 原始行，`type.py:111,136` 必写 |
| `parser` | parser 名，`type.py:137` |
| `source_path` | 绝对路径，`type.py:112` |
| `date_stamp` / `message` / `source_pid` | 正则捕获字段 |
| `log_source` | syslog 主机名（`source`/`rsource` 用） |
| `source_host` / `dest_host` / `dest_port` / `source_port` | tcpdump 等（`parsers/tcpdump.py:42-55`） |
| `source_process` | `process`/`rprocess` 用 |
| `protocol` | `protocol`/`rprotocol` 用 |
| `year/month/day/tstamp` | 日期转换工具写入 |
| `numeric_date_stamp` | 本地数字戳，`type.py:102` 等 |
| `numeric_date_stamp_utc` | UTC 数字戳，`utils.py:148,166`；merge 排序键 |
| `tzone` | 时区串，`type.py:107-109` |

「写」一列：所有过滤器对 entry **均不写**；它们只写自己新建的
`newdata['entries']` 列表（append 原 entry）。下表的「写」指对外层 tsdata
容器的写。

## 字段读写矩阵

| # | 过滤器 | CLI / dest | 读 entry 字段 | 读 args | 对容器的写 | 关键行 |
|---|--------|-----------|--------------|---------|-----------|--------|
| 1 | `dhost` | `--dhost` / `dhost` (append) | `dest_host` | `args.dhost` | 新建 dict+list，append 命中 entry | `dhost.py:39,41-48,50-53` |
| 2 | `grep` | `--grep` / `pattern` (append) | `raw_text` | `args.pattern` | 同上；多值之间 OR（`break`） | `grep.py:39-40,53,55-59` |
| 3 | `last` | `--last` / `last` | `numeric_date_stamp_utc` | `args.last`；阈值 `utcnow()` 现算 | 同上；`dstamp>=ourstart` | `last.py:39-41,48-59,72-79` |
| 4 | `process` | `--process` / `process` | `source_process` | `args.process` | 同 dhost 模板 | `process.py:39-40,51-53` |
| 5 | `protocol` | `--protocol` / `protocol` | `protocol` | `args.protocol` | 同 dhost 模板 | `protocol.py:39-40,51-53` |
| 6 | `range` | `--range` / `range`；`--utc` / `utc` | 默认 `numeric_date_stamp`；`--utc` 时 `numeric_date_stamp_utc` | `args.range,args.utc` | 新建容器；窗口 `ljust(14,'0')` | `range.py:39-43,55-56,58-79` |
| 7 | `rdhost` | `--rdhost` / `rdhost` | `dest_host` | `args.rdhost` | 反向：不命中或字段缺失才 append | `rdhost.py:39,50-55` |
| 8 | `rgrep` | `--rgrep` / `rpattern` | `raw_text` | `args.rpattern`（**缺陷：整 list 被 format**） | 反向；任一命中即剔除 | `rgrep.py:39-40,53,56-62` |
| 9 | `rprocess` | `--rprocess` / `rprocess` | `source_process` | `args.rprocess` | 反向 | `rprocess.py:39,51-55` |
| 10 | `rprotocol` | `--rprotocol` / `rprotocol` | `protocol` | `args.rprotocol` | 反向 | `rprotocol.py:39,51-55` |
| 11 | `rshost` | `--rshost` / `rshost` | `source_host` | `args.rshost` | 反向 | `rshost.py:39,50-53` |
| 12 | `rsource` | `--rsource` / `rsource` | （意图读 `log_source`） | `args.rsource` | **启用即崩**：`entry.keys` 漏括号 | `rsource.py:51` |
| 13 | `shost` | `--shost` / `shost` | `source_host` | `args.shost` | 正向 | `shost.py:39,50-52` |
| 14 | `source` | `--source` / `source` | `log_source` | `args.source` | 正向 | `source.py:39,50-52` |

所有过滤器在对应 dest 为空值（None）时走守卫短路，原样返回 `data`，
不读任何 entry 字段（见 [02 §3](02-ordering-and-short-circuit.md)）。

## 正反向语义差异（字段缺失时）

| 字段是否存在 | 正向过滤器（grep/source/dhost/…） | 反向过滤器（rgrep/rsource/rdhost/…） |
|-------------|----------------------------------|--------------------------------------|
| 存在且命中值集 | 保留 | 剔除 |
| 存在但不命中 | 剔除 | 保留 |
| 字段不存在 | 剔除（不进 append 分支） | 保留（反向各模块的 `else` 分支） |

特例：grep/rgrep 只依赖恒存在的 `raw_text`，所以没有「字段缺失」这一行；
`last`/`range` 要求时间戳字段存在，缺失即静默丢弃。

## 手工复现

```bash
# 正向 dhost / 反向 rdhost 需要 tcpdump 风格字段，可用库方式构造
python -W ignore -c "
import importlib
F={n:importlib.import_module('logdissect.filters.'+n).FilterModule() for n in ['dhost','rdhost','source','shost']}
a=type('A',(),{'dhost':['10.0.0.2'],'rdhost':None,'source':None,'shost':None})()
d={'entries':[{'raw_text':'x','dest_host':'10.0.0.2'},{'raw_text':'y','dest_host':'10.0.0.9'}]}
print([e['raw_text'] for e in F['dhost'].filter_data(d,args=a)['entries']])"
# ['x']

# grep 多值 OR
python -W ignore logdissect.py --grep dbus --grep CRON tests/files/exsyslog
```
