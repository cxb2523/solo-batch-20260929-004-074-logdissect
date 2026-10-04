# 02 · 就地 mutate、浅拷贝边界、共享引用与时间戳重写

本篇回答四个问题：过滤器会不会改 entry？哪些字段是共享引用？别名在什么时刻产生？
时间戳在哪一步、被谁重写？

## 1. entry / tsdata 到底长什么样

以默认 syslog parser 解析 tests/files/exsyslog 为例，每条 entry 是一个 dict，键为：

```
date_stamp, day, log_source, message, month, numeric_date_stamp,
numeric_date_stamp_utc, parser, raw_text, source_path, source_pid,
source_process, tstamp, tzone, year
```

不同 parser 的字段来自各自 `self.fields`（如
`logdissect/parsers/syslog.py:34-36`），tcpdump/webaccess 等还会产出
source_host、dest_host、protocol 等。容器结构是两层：

```
data (dict)
  parser/source_path/source_file/source_file_mtime/source_file_year   # 每文件元数据
  entries (list[dict])  <- 每个元素就是一条 tsdata/entry
```

多文件时 `utils.merge_logs` 再把各文件的 entries 拼成一个顶层 dict（只剩
`entries` 一个键，元数据不进入合并结果），即 `finalized_data`。

## 2. 时间戳是在“解析期”被重写的，过滤器阶段一律只读

过滤器链拿到数据之前，entry 的所有时间字段已在解析阶段定型，**14 个过滤器没有
一个写入任何 entry 字段**（见 03 篇矩阵，全部只读）。时间字段的产生/重写路径：

1. `logdissect/parsers/type.py:111-115` parse_line 用正则把 `date_stamp` 等原始
   片段塞进 entry。
2. type.py:117-133 根据 `datestamp_type` 调 utils 的转换函数，**就地写入**
   year/month/day/tstamp：
   - standard → `utils.convert_standard_datestamp`（utils.py，写 year=None、
     month、day、tstamp、tzone=None）；
   - iso → `convert_iso_datestamp`；webaccess/unix/nodate 各自分支。
3. `logdissect/parsers/type.py:67-87` parse_file 对 standard 类型按“倒序解析”
   做跨年回退，并**重写**两个字段：
   - type.py:73-79：若 `entry['month'] > currentmonth` 则 `entryyear -= 1`，
     随后 `entry['numeric_date_stamp'] = str(entryyear)+month+day+tstamp`、
     `entry['year'] = str(entryyear)`（type.py:75-78）——numeric_date_stamp 在
     这里被第二次赋值（第一次在 convert_* 里没有它，standard 路径只在此处生成）。
   - type.py:80-83：`entry['tzone']` 被设置为 `-t` 传入值或本机时区。
   - type.py:84：`entry = logdissect.utils.get_utc_date(entry)` 就地补写
     `numeric_date_stamp_utc`（utils.py get_utc_date，对 entry 赋值后 return 同
     一个 dict）。
   - type.py:85-86：`entry['raw_text']`、`entry['source_path']` 再次赋值（
     raw_text 在 parse_line 里先存过原始行，这里用 rstrip 后的 ourline 覆盖；
     source_path 用绝对路径）。
4. `logdissect/utils.py` `merge_logs` 结尾：当**所有** entry 都带
   numeric_date_stamp_utc 时，`ourlog['entries'].sort(key=...)` 就地重排列表
   （不改 entry 内容，只改顺序）。

一句话：**字符串形态的时间戳（date_stamp/month/day/tstamp/tzone）在 parse_line
与 convert_* 里写；可排序的 numeric_date_stamp 在 parse_file 跨年逻辑里写；
numeric_date_stamp_utc 在 get_utc_date 里写；过滤器阶段只读取
numeric_date_stamp / numeric_date_stamp_utc 来比较（last/range），从不重写。**

> 附注：`convert_now_datestamp`（utils.py）是唯一会**覆盖 entry['date_stamp']**
> 的转换（datestamp_type == 'now' 时），用于没有时间戳的 parser，会把
> date_stamp 改成当前时间。默认 syslog 不走这条分支。

## 3. 过滤器做的是“新外壳 + 共享内芯”：边界划在哪

每个启用的过滤器都执行同一个模板（以 grep 为例）：

- `logdissect/filters/grep.py:42-49`：`newdata = {}`，把顶层**不可变/标量**元数据
  逐个复制（parser/source_path/source_file/source_file_mtime/
  source_file_year），再 `newdata['entries'] = []` 建一个**新列表**。
- `logdissect/filters/grep.py:55-58`：遍历**旧列表**，命中时
  `newdata['entries'].append(entry)` —— append 进去的是**同一个 entry dict 对象**，
  没有 `dict(entry)`、没有 `copy.copy/copy.deepcopy`。

因此拷贝语义是**逐字段浅拷贝（shallow copy）**，分三层看：

| 层 | 是否新建 | 后果 |
|----|----------|------|
| 外壳 `newdata` dict | 新建（`{}`） | 给外壳加/改键不会回流到上一节点的外壳 |
| `newdata['entries']` 列表 | 新建（`[]`） | append/重排只影响新列表，旧列表顺序/成员不变 |
| 每个 entry dict | **不复制，共享同一对象** | 对 entry 任一字段的写入，所有持有它的视图同时可见 |
| 元数据标量(str/int/float) | 复制的是不可变值 | 本来也无法就地改 |

被丢弃的 entry 只是不在新列表里，原 dict 在其它视图（例如 merge 前的单文件数据、
或库调用方自己持有的列表）里仍存活——过滤器只做“选择”，不做“销毁/深拷贝”。

实测（库直接调用，证明 entry 身份与别名回流）：

```sh
python -c "from logdissect.parsers.syslog import ParseModule as P; from logdissect.filters.grep import FilterModule as G; import types; a=types.SimpleNamespace(pattern=['dbus'],rpattern=None,source=None,rsource=None,shost=None,rshost=None,dhost=None,rdhost=None,process=None,rprocess=None,protocol=None,rprotocol=None,last=None,range=None,utc=False); d=P().parse_file(r'tests/files/exsyslog'); o=G().filter_data(d,args=a); print(len(o['entries']), o is not d, all(x in d['entries'] for x in o['entries']))"
# 3 True True  —— 3 条命中；外壳是新 dict；命中 entry 全部是原对象
```

别名回流的最小复现（改输出视图里的 entry，输入视图同步变化）：

```sh
python - <<'PY'
from logdissect.parsers.syslog import ParseModule
from logdissect.filters.grep import FilterModule
import types
args = types.SimpleNamespace(pattern=['dbus'], rpattern=None, source=None,
    rsource=None, shost=None, rshost=None, dhost=None, rdhost=None,
    process=None, rprocess=None, protocol=None, rprotocol=None,
    last=None, range=None, utc=False)
data = ParseModule().parse_file('tests/files/exsyslog')
out = FilterModule().filter_data(data, args=args)
e = out['entries'][0]
e['raw_text'] = 'ALIASED'
idx = data['entries'].index(e)
print(data['entries'][idx]['raw_text'])   # ALIASED —— 同一对象，就地改动双向可见
PY
```

当前代码里没有过滤器真的去改 entry，所以链上**暂时观察不到**别名引发的串扰；
但共享是事实——任何下游（输出模块、库使用者）就地改 entry，都会反映到
finalized_data 以及 merge 前的单文件数据上。唯一对列表本身的就地变动是
merge_logs 的 `.sort()`（改顺序不改内容）。

## 4. 未启用过滤器的“零拷贝”短路

- `logdissect/filters/grep.py:39-40`（及其余 13 个同构函数）在选项为 None 时
  `return data`：连外壳都不新建，**入参与出参是同一个 dict**。

所以在 CLI 链路上，只有真正启用的过滤器会把外壳换成新 dict；未启用的槽位是
透传同一对象。core.py:103 每次都做 `self.data_set['finalized_data'] = ourlog`，
启用过滤器后该槽位指向新外壳，旧外壳只被局部变量短暂持有后在
`del(ourlog)`（core.py:104）处释放——但旧外壳里那些**未被选中**的 entry 若还有
别处引用就不会释放。

## 5. 一个在 CLI 路径上永不进入的分支

每个过滤器都有这段“复制单文件元数据”的代码：

- 例如 grep.py:43-48、rsource.py:42-47（`if 'parser' in data.keys(): ...`）。

它只在**直接对 parser.parse_file 的返回值**调用过滤器（库用法）时成立；CLI 路径
上过滤器的输入是 `merge_logs` 的结果，而 merge_logs 的返回 dict 只有 `entries`
一个键：

```sh
python -c "import logdissect.utils as u; from logdissect.parsers.syslog import ParseModule as P; d=P().parse_file(r'tests/files/exsyslog'); print(sorted(u.merge_logs([d]).keys()))"
# ['entries']
```

因此 CLI 链路上该 `if` 恒为 False，元数据复制代码不执行，finalized_data 从头到尾
只有 entries。这也意味着 §3 表里“外壳标量复制”一行在纯 CLI 场景其实不发生，
外壳始终是 `{'entries': [...]}`。

## 6. 结论

- 深拷贝：全代码路径**不存在**；最“深”只到新建外壳 dict 与 entries list。
- 共享引用：所有跨过滤器存活的 entry dict 都是同一对象；merge 时
  `ourlog['entries'] = ourlog['entries'] + d['entries']`（utils.py merge_logs）
  产生新列表但元素仍是各文件原 entry。
- 别名产生时刻：parse_file 产出 entry 的那一刻起，后续 merge、每个启用过滤器的
  append、输出模块遍历，复用的都是这批对象。
- 时间戳重写时刻：集中在解析期（convert_*、parse_file 跨年与 get_utc_date），
- 过滤器只比较 numeric_date_stamp(_utc)，不写。
