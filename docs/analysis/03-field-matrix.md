# 03 · 过滤器 × tsdata 字段 读/写矩阵

本篇是 filter-chain.html 点击面板的文字版。约定：

- **R** = 读取该字段做判断；**W** = 写入/重写该字段；**S** = 新建外壳并复制该顶层
  标量（仅库路径，CLI 合并后无此键，见 02 篇 §5）；空白 = 不接触。
- 所有过滤器**都不 W 任何 entry 字段**。所谓“写”只发生在解析期（见 02 篇 §2），
  表末单列时间戳字段的产生者。
- “缺字段行”列说明 entry 缺该判断字段时该行的去向（丢弃/保留）。

## 1. 读字段与缺字段行为

| 槽位 | 过滤器 | 判断读取字段 (R) | 多值语义 | 命中后 | 缺该字段的行 | 关键行 |
|------|--------|------------------|----------|--------|--------------|--------|
| 1 | dhost | dest_host | OR：值 ∈ values | append | 丢弃（无 else） | dhost.py:50-53 |
| 2 | grep | raw_text | OR：任一 pattern 命中即 break | append | raw_text 总存在 | grep.py:55-59 |
| 3 | last | numeric_date_stamp_utc | 单值；dstamp ≥ ourstart | append | 缺则跳过该行 | last.py:72-79 |
| 4 | process | source_process | OR：∈ values | append | 丢弃 | process.py:50-53 |
| 5 | protocol | protocol | OR：∈ values | append | 丢弃 | protocol.py:50-53 |
| 6 | range | numeric_date_stamp（默认）/ numeric_date_stamp_utc（--utc） | 闭区间 first..last | append | 缺则跳过 | range.py:58-80 |
| 7 | rdhost | dest_host | NOT IN values 才保留 | append | **保留**（else） | rdhost.py:50-55 |
| 8 | rgrep | raw_text | AND 扫描：任一命中→剔除（实现有缺陷） | 不命中才 append | raw_text 总存在 | rgrep.py:55-62 |
| 9 | rprocess | source_process | NOT IN values | append | 保留（else） | rprocess.py:50-55 |
|10 | rprotocol | protocol | NOT IN values | append | 保留（else） | rprotocol.py:50-55 |
|11 | rshost | source_host | NOT IN values | append | 保留（else） | rshost.py:50-54 |
|12 | rsource | log_source（代码写成 entry.keys，崩溃） | NOT IN args.rsource | append | 设计为保留，实际崩溃 | rsource.py:50-54 |
|13 | shost | source_host | ∈ values | append | 丢弃 | shost.py:50-53 |
|14 | source | log_source | ∈ values | append | 丢弃 | source.py:50-53 |

外层结构上，每个启用过滤器：新建 `newdata` 外壳、按需 S 复制 parser/
source_path/source_file/source_file_mtime/source_file_year、新建 entries 列表
（模板见 grep.py:42-49）。未启用时直接 `return data`（短路，见各文件开头）。

## 2. 顶层标量字段（外壳）接触矩阵

| 字段 | dhost/grep/last/process/protocol/range | rdhost/rgrep/rprocess/rprotocol/rshost/rsource/shost/source |
|------|----------------------------------------|-------------------------------------------------------------|
| parser | S（仅 if 'parser' in data，库路径） | S 同左 |
| source_path / source_file / source_file_mtime / source_file_year | S 同左 | S 同左 |
| entries | 新建列表，append 共享 entry | 同左 |

CLI 路径下 data 只有 entries（merge_logs 所致），S 列实际不发生。

## 3. entry 时间戳字段：谁写、过滤器谁读

| entry 字段 | 写入者（解析期，W） | 过滤器读取者 (R) |
|------------|---------------------|------------------|
| date_stamp | 正则捕获（type.py:111-115）；now 类型被 convert_now_datestamp 覆盖 | 无 |
| year/month/day/tstamp | convert_* 系列（utils.py）；standard 的 year/numeric_date_stamp 在 type.py:73-78 重写 | 无直接读取 |
| numeric_date_stamp | standard：type.py:75-77；iso/webaccess/unix/nodate：convert_* | range（默认分支 range.py:72-80） |
| tzone | type.py:80-83（-t 或本机时区） | 无过滤器读取（仅 get_utc_date 用） |
| numeric_date_stamp_utc | utils.get_utc_date（type.py:84 调用） | last（last.py:73-78）、range --utc（range.py:60-70） |
| raw_text | parse_line 赋值后在 type.py:85 被 rstrip 后的 ourline 覆盖 | grep、rgrep |
| log_source | 正则捕获（parser.fields） | source、rsource |
| source_host / dest_host / protocol | tcpdump/webaccess 等 parser 捕获 | shost/rshost、dhost/rdhost、protocol/rprotocol |
| source_process / source_pid / message | 正则捕获 | process/rprocess 读 source_process；其余仅随输出打印 |
| parser / source_path（entry 级） | type.py:113、type.py:86 | 无过滤器读取 |

## 4. 三条手工复现命令（对应本篇与链路图面板）

```sh
# 读 raw_text 的正反向对比（rgrep 缺陷，03 表槽位 2 vs 8）
python logdissect.py --grep dbus tests/files/exsyslog    # 3
python logdissect.py --rgrep dbus tests/files/exsyslog   # 0（应为 7）

# 读 dest_host 的缺字段不对称（槽位 1 vs 7）：syslog 无 dest_host
python logdissect.py --dhost x tests/files/exsyslog      # 0（缺字段行被丢）
python logdissect.py --rdhost x tests/files/exsyslog     # 10（缺字段行被留）

# 读时间戳字段（槽位 3/6）
python logdissect.py --last 10000d tests/files/exsyslog                       # 10
python logdissect.py --range 202602272130-202602272131 tests/files/exsyslog   # 8
```
