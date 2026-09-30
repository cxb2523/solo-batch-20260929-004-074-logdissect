# 03 · 取舍二：就地 mutate 与浅/深拷贝的边界

## 1. 一张图说清边界

```
parse_file()                         merge_logs()                run_filters() 链式
┌───────────────────────┐   entry    ┌──────────────────┐       ┌────────────────────────────┐
│ entry = {}            │◀── 就地 ──│ ourlog['entries'] │◀─────│ newdata = {}               │
│ entry['year'] = ...   │   写入     │  = a + b          │ 同一  │ newdata['entries'] = []    │
│ entry['numeric_...']  │            │  .sort(in-place)  │ 批    │ newdata['entries'].append( │
│ entry['tzone'] = ...  │            │  utils.py:208-210 │ 对象  │     entry)   ← 原对象，不拷 │
│ parsers/type.py       │            └──────────────────┘       │ grep.py:57-58 等           │
└───────────────────────┘                                     └────────────────────────────┘
   深/就地 mutate 区：这里会改 entry                           浅拷贝区：新建外壳，entry 仍是同一对象
```

边界一句话：**entry dict 只在 parse 阶段被就地创建与改写；merge 与全部 filter
都只新建「容器」（外层 dict、entries list），从不复制 entry，也不向 entry 写键。**

## 2. 哪些东西是新建的（浅拷贝发生处）

每个被启用的过滤器都遵循同一模板（以 grep 为例）：

```python
newdata = {}                                   # grep.py:42  新外层 dict
if 'parser' in data.keys():                    # grep.py:43  单文件 header 分支
    newdata['parser'] = data['parser']         # grep.py:44-48  逐键搬 header 标量
newdata['entries'] = []                        # grep.py:49  新空列表
for entry in data['entries']:
    if <命中>:
        newdata['entries'].append(entry)       # grep.py:57-58  append 的是原 entry
return newdata
```

同样的外壳重建见 `rgrep.py:42-49`、`source.py:41-48`、`rsource.py:41-48`、
`dhost.py:41-48`、`rdhost.py:41-48`、`shost/rshost/process/rprocess/
protocol/rprotocol`、以及 `last.py:63-70`、`range.py:47-54`。

所以「拷贝」只是浅拷贝，且分两层：

- **外层 tsdata dict**：每过一个被启用的过滤器就是一个新对象（`newdata`）。
- **entries 列表**：每个被启用的过滤器都是一个新 list（元素是挑选后的原 entry）。
- **entry dict 本身**：**从不复制**。列表里 `append(entry)` 放进去的是入参列表里
  的同一个 dict 对象。

一个易被忽略的死分支：`if 'parser' in data.keys()`（grep.py:43 等）只在
**单文件未 merge** 的 tsdata 上成立；真实链路上游是 `merge_logs()` 的结果，
而 merge 只保留 `entries`（`utils.py:205-208`），所以这个 header 搬运分支
在 CLI 路径里**永远不执行**。它只对把过滤器当库函数、直接喂单文件 LogData
的调用方有意义（文档 `docs/README-MODULE.md` 描述了这种用法）。

## 3. 哪些字段是共享引用、别名何时产生

别名（aliasing）在三个时刻产生：

1. **merge 拼接**：`utils.py:208` 的列表相加把各文件 `entries` 里的同一批 dict
   对象搬进 `ourlog['entries']`；`utils.py:210` 的 `.sort()` 是**就地排序**，
   只换列表槽位，不换对象。
2. **过滤器挑选**：每个 `append(entry)`（如 `grep.py:58`、`dhost.py:53`、
   `rdhost.py:53,55`、`last.py:79`、`range.py:70,80`）把同一个 entry 对象
   放进新列表。
3. **短路返回**：未启用的过滤器 `return data`（如 `grep.py:40`），连外壳都不换，
   调用方与被调用方直接握有同一个 tsdata。

共享的具体内容：

- **entry dict 跨阶段共享**：parse 产物、merge 结果、每个过滤器的输出，引用的是
  同一批 entry 对象。过滤器 A 输出列表与过滤器 B 输出列表里的同一个 entry
  `is` 相等。
- **entry 内的标量字段**（`raw_text`、`log_source`、`dest_host`、
  `numeric_date_stamp_utc` 等）都是不可变对象；过滤器只读不写，所以目前不会
  观察到标量被串改。但共享的是**同一个 dict 容器**——任何代码一旦
  `entry['x'] = ...`，所有视图立刻同时可见（测试
  `test_filters_share_entry_dicts_no_copy` 用一次手工 mutate 演示了这一点）。
- **entry 级来源字段**：`entry['source_path']`（`type.py:112`）与
  `entry['parser']`（`type.py:137`）在 parse 时写死进每个 entry，随 entry 一起
  被各阶段共享；它们不是从 tsdata header 查的。
- **header 标量**：仅在单文件库调用路径里被逐键搬到 `newdata`
  （`grep.py:44-48` 等），属于不可变值的重新绑定，无共享可变状态。

实测（entry 对象身份贯穿两个过滤器，改一处处处可见）：

```bash
python -W ignore -c "
import logdissect.filters.source as S, logdissect.filters.dhost as D
e={'raw_text':'x','log_source':'h','dest_host':'d'}; es=[e]
A=type('A',(),{'source':['h']})(); B=type('A',(),{'dhost':['d']})()
r1=S.FilterModule().filter_data({'entries':es},args=A)
r2=D.FilterModule().filter_data({'entries':es},args=B)
print(r1['entries'][0] is r2['entries'][0] is e)
r1['entries'][0]['raw_text']='MUTATED'; print(r2['entries'][0]['raw_text'])"
# True
# MUTATED
```

merge 不复制对象的实测：

```bash
python -W ignore -c "
from logdissect.parsers.syslog import ParseModule
import logdissect.utils as U
d=ParseModule().parse_file('tests/files/exsyslog')
m=U.merge_logs([d], sort=True)
ids={id(x) for x in d['entries']}
print(all(id(x) in ids for x in m['entries']))"
# True
```

## 4. 时间戳何时被写入 / 重写

**所有时间戳写入都发生在 parse 阶段，过滤器链上没有任何过滤器写时间戳。**

按执行顺序（`logdissect/parsers/type.py` 与 `logdissect/utils.py`）：

1. `parse_line()`（`type.py:125-167`）正则命中后：
   - `entry['raw_text']`、`entry['parser']`：`type.py:136-137`
   - 按 `fields` 写入命名字段：`type.py:139-141`
   - 调日期转换工具（`type.py:143-161`），由各工具**就地**写
     `year/month/day/tstamp/numeric_date_stamp`：
     - 标准 syslog：`utils.convert_standard_datestamp()` 写
       `year=None, month, day, tstamp, tzone=None`（`utils.py:46-50`）
     - ISO：`convert_iso_datestamp()`（`utils.py:89-95`）
     - 无日期（tcpdump）：`convert_nodate_datestamp()`（`utils.py:69-74`）
     - webaccess / unix / now：`utils.py:104-140`，其中
       `convert_now_datestamp()` 还会**重写 `entry['date_stamp']`**
       （`utils.py:134`）。
2. 回到 `parse_file()` 主循环（`type.py:94-115`）：
   - 跨年回拨：`type.py:98-105` 用逆序解析推断年份，重写
     `entry['numeric_date_stamp']` 与 `entry['year']`；
   - `entry['tzone']`：`type.py:106-109`（`-t` 参数优先，否则用
     `utils.get_local_tzone()`）；
   - **UTC 戳唯一写入点**：`entry = utils.get_utc_date(entry)`（`type.py:110`），
     该函数就地添加 `entry['numeric_date_stamp_utc']`（`utils.py:145-168`，
     赋值在 `utils.py:148` 与 `utils.py:166`）；
   - `entry['raw_text'] = ourline` 与 `entry['source_path']`：
     `type.py:111-112`。

过滤器侧对时间戳只读：

- `last` 读 `numeric_date_stamp_utc`（`last.py:72-77`），阈值用
  `datetime.utcnow()` 现算（`last.py:48-59`）；
- `range` 默认读本地 `numeric_date_stamp`（`range.py:71-79`），
  加 `--utc` 时读 `numeric_date_stamp_utc`（`range.py:58-69`）。

复现「过滤器不改写时间戳」：

```bash
python -W ignore -c "
from logdissect.parsers.syslog import ParseModule
import logdissect.filters.grep as G
es=ParseModule().parse_file('tests/files/exsyslog')['entries']
before=es[0]['numeric_date_stamp_utc']
G.FilterModule().filter_data({'entries':list(es)},args=type('A',(),{'pattern':['dbus']})())
print(es[0]['numeric_date_stamp_utc']==before)"
# True
```

## 5. 一个会污染 UTC 戳的上游缺陷（与别名叠加放大）

`utils.get_local_tzone()`（`utils.py:171-200`）在本机（Asia/Shanghai, UTC+8）
产出的时区串是 `'+-800'`：符号与 `rjust/ljust` 拼接在 3.12 下得到非法形式。
`get_utc_date()`（`utils.py:158-164`）取 `entry['tzone'][0]=='+'` 走减法分支、
再解析 `[1:3]='-8'`、`[3:5]='00'`，于是本地 `21:30` 被算成 UTC `+8h` 的
次日 `05:30`（方向反了）。由于该错误值**就地写进每个 entry 的
`numeric_date_stamp_utc`**，而 merge（`utils.py:209-210`）与 `last`/`range --utc`
全都读这个字段，别名机制让一处错误同时污染排序、`--last`、`--range --utc`。

```bash
python -W ignore -c "
from logdissect.parsers.syslog import ParseModule
e=ParseModule().parse_file('tests/files/exsyslog')['entries'][0]
print(e['tzone'], e['numeric_date_stamp'], e['numeric_date_stamp_utc'])"
# +-800 20260227213046 20260228053046.000000

python -W ignore logdissect.py -s --utc --range 202602272130-202602272131 tests/files/exsyslog
python -W ignore logdissect.py -s --utc --range 202602280530-202602280531 tests/files/exsyslog
# 第一条 0 行（本地窗口查不到），第二条 8 行（被迫使用偏移后的 UTC 窗口）
```

## 6. 小结：拷贝/别名/重写速查表

| 阶段 | 外层 tsdata | entries 列表 | entry dict | 时间戳字段 |
|------|-------------|--------------|-----------|-----------|
| parse_file | 新建（`type.py:54`） | 新建并 `.reverse()`（`type.py:121`） | 新建并多次就地写 | 写/重写（见 §4） |
| merge_logs | 新建（`utils.py:205`） | 拼接新建 + 就地 sort（`utils.py:208-210`） | 原对象共享 | 只读 |
| 过滤器（启用） | 新建 `newdata` | 新建，append 原 entry | 原对象共享，不写 | 只读 |
| 过滤器（未启用） | 原样返回 `data` | 同一列表 | 同一批对象 | 只读 |
