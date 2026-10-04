# 01 · 过滤器的加载次序、短路时机与“换序结果不同”

## 1. 链序是在哪里被定死的

过滤器注册表是一个**手写顺序**的列表：

- `logdissect/filters/__init__.py:23-25`

```python
__filters__ = ['last', 'range', 'grep', 'rgrep', 'source', 'rsource',
        'shost', 'rshost', 'dhost', 'rdhost', 'process', 'rprocess',
        'protocol', 'rprotocol']
```

但加载时对它做了 `sorted(...)`：

- `logdissect/core.py:197-202`（load_filters）

```python
for f in sorted(logdissect.filters.__filters__):
    self.filter_modules[f] = \
        __import__('logdissect.filters.' + f, ...).FilterModule(args=self.filter_args)
```

`self.filter_modules` 是普通 dict（`core.py:43`）。CPython 3.7 起 dict 保证按
**首次插入序**迭代，而执行阶段正是直接迭代这个 dict：

- `logdissect/core.py:97-105`（run_filters）

```python
def run_filters(self):
    for m in self.filter_modules:
        ourfilter = self.filter_modules[m]
        ourlog = ourfilter.filter_data(
                self.data_set['finalized_data'],
                args=self.args)
        self.data_set['finalized_data'] = ourlog   # 上一个的输出喂给下一个
```

结论：**每个过滤器的输出 dict 成为下一个过滤器的输入**（core.py:103），而执行
次序只取决于字母序，与 argv 书写顺序无关。

排序后的真实执行次序（# 即链上槽位）：

| # | 模块键 | CLI 选项 | argparse dest | 读的 entry 字段 |
|---|--------|----------|---------------|-----------------|
| 1 | dhost     | --dhost     | dhost     | dest_host |
| 2 | grep      | --grep      | pattern   | raw_text |
| 3 | last      | --last      | last      | numeric_date_stamp_utc |
| 4 | process   | --process   | process   | source_process |
| 5 | protocol  | --protocol  | protocol  | protocol |
| 6 | range     | --range/--utc | range, utc | numeric_date_stamp(_utc) |
| 7 | rdhost    | --rdhost    | rdhost    | dest_host |
| 8 | rgrep     | --rgrep     | rpattern  | raw_text |
| 9 | rprocess  | --rprocess  | rprocess  | source_process |
|10 | rprotocol | --rprotocol | rprotocol | protocol |
|11 | rshost    | --rshost    | rshost    | source_host |
|12 | rsource   | --rsource   | rsource   | log_source（代码有缺陷，见 §4） |
|13 | shost     | --shost     | shost     | source_host |
|14 | source    | --source    | source    | log_source |

注意一个反直觉点：`rdhost` 的内部 `self.name` 被写成 `"rdest"`
（`logdissect/filters/rdhost.py:28`），但注册/执行用的是**模块文件名** `rdhost`，
所以这个笔误不影响链序，只影响人类可读名字。

## 2. 正反向对的“入链次序”

把三对被点名的过滤器和另外四对一起按槽位列出来：

| 正（保留匹配） | 槽位 | 反（剔除匹配） | 槽位 | 同对内谁先执行 |
|----------------|------|----------------|------|----------------|
| grep     | 2  | rgrep     | 8  | 正先反后 |
| dhost    | 1  | rdhost    | 7  | 正先反后 |
| process  | 4  | rprocess  | 9  | 正先反后 |
| protocol | 5  | rprotocol | 10 | 正先反后 |
| shost    | 13 | rshost    | 11 | **反先正后** |
| source   | 14 | rsource   | 12 | **反先正后** |

没有任何机制保证正反成对相邻。之所以 grep/dhost/process/protocol 的正向排在前，
仅仅因为它们的模块名少一个 `r` 前缀、字母序更靠前；shost/source 这一对，
`rshost`/`rsource` 同样以 r 开头却排在前面，是因为**反向键的整体字母序**落在了
`shost`/`source` 之前（`rs...` < `s...`，而 `rd...`/`rg...` 要和不带 r 的
`d...`/`g...` 比，后者更小）。这是“按模块名排序”这一实现选择的直接副作用。

## 3. 短路时机：每个过滤器是一道 AND 闸门

每个过滤器 `filter_data` 的第一段都是同一个短路模式（以 grep 为例）：

- `logdissect/filters/grep.py:38-40`

```python
if args:
    if not args.pattern:
        return data
```

14 个过滤器的对应短路行：

- dhost.py:37-39 / rdhost.py:37-39
- grep.py:38-40 / rgrep.py:38-40
- last.py:39-41 / range.py:39-41
- process.py:37-39 / rprocess.py:38-40
- protocol.py:37-39 / rprotocol.py:38-40
- shost.py:37-39 / rshost.py:38-40
- source.py:37-39 / rsource.py:37-39

含义：**没给该选项（dest 为 None）时，原封不动返回输入对象本身**（同一个 dict，
连外壳都不新建）。给了选项才构建 `newdata` 外壳并逐行挑选。因此 14 个槽位始终
全部执行，但只有被启用的槽位真正收缩集合——整条链等价于“所有启用过滤器谓词的
逻辑与（AND）”。

### 3.1 条目级“短路”：只存在于 grep 的 break

- `logdissect/filters/grep.py:55-59`：对每条 entry 遍历该选项的多个 pattern，
  **命中任意一个就 `break`**（OR 语义，且只 append 一次）。

```python
for entry in data['entries']:
    for repat in repatterns:
        if re.match(repatterns[repat], entry['raw_text']):
            newdata['entries'].append(entry)
            break
```

- `logdissect/filters/rgrep.py:55-62`：反向版没有 break，对每条 entry 要把
  `args.rpattern` 全部试一遍，只要任一命中就把 `match` 置 True。功能上
  “存在命中即剔除”本不需要 break（结果等价），但它多做了无用功，且正好踩中
  第 4 节的编译缺陷。
- 其余字段类过滤器（source/shost/dhost/process/protocol 及其反向）没有内层
  pattern 循环，只有一次 `in values` 成员判断，无所谓短路。

复现（多 pattern 的 OR）：

```sh
python logdissect.py --grep dbus --grep CRON tests/files/exsyslog
# 命中 3 条 dbus 或 1 条 CRON，共 4 行
```

## 4. 为什么“同一组参数换序后结果不同”

需要分清三个层面，前两个层面**不会**因换序改变结果，差异全部来自第 3 层。

### 4.1 CLI argv 换序：链不变，结果不变

链序由 `sorted(__filters__)` 决定，argv 顺序只影响 argparse 收集值，不影响
run_filters 的迭代序。实测：

```sh
python logdissect.py --grep dbus --process CRON tests/files/exsyslog   # 0 行
python logdissect.py --process CRON --grep dbus tests/files/exsyslog   # 0 行
```

两条命令在槽位 2、4 上做的都是集合交（dbus 行 ∩ CRON 行 = ∅），中间产物的行数
不同（先 grep 得 3 行再过 process；先 process 得 1 行再过 grep），但**最终集相同**。
纯集合筛选谓词满足交换律，这也是为什么在“无缺陷、字段齐全”的理想模型里换序
不该影响答案。

### 4.2 正/反向互补本应也可交换（在字段齐全时）

对同一条目集合，正向 `保留匹配 V` 与反向 `剔除匹配 V` 是互补操作。字段存在时，
先正后反或先反后正，最终都只剩“与 V 无关”的行，最终集相同。实测健康的一对：

```sh
python logdissect.py --process CRON tests/files/exsyslog    # 1 行（正向）
python logdissect.py --rprocess CRON tests/files/exsyslog   # 9 行（反向补集）
```

### 4.3 真正的换序/成对差异，来自这三处实现偏差

**(A) rgrep 不是 grep 的补集 —— rgrep.py:53 把 list 当 pattern。**

- `logdissect/filters/rgrep.py:51-53`

```python
repatterns = {}
for rpat in values:
    repatterns[rpat] = re.compile(r".*({}).*".format(args.rpattern))  # 应为 rpat
```

- 单参数 `--rgrep dbus`：`args.rpattern == ['dbus']`，`str(['dbus'])` 是
  `"['dbus']"`，正则变成 `"*(['dbus'])*"`，括号内是**字符类**，等价于
  “行里含 d/b/u/s/[ /] /' 任意一个字符”。syslog 几乎每行都有字母，于是
  全部被当命中而剔除。
- 多参数 `--rgrep dbus --rgrep systemd`：变成匹配字面串
  `"['dbus', 'systemd']"`，任何单行都不可能包含，于是 rgrep 退化为“什么都不剔”。
  （循环里 `for r in args.rpattern` 用键名去 `repatterns[r]` 取，恰好能取到对象，
  没有 KeyError——但取到的正则都是上面的坏串。）

实测：

```sh
python logdissect.py --grep dbus tests/files/exsyslog    # 3 行（健康正向）
python logdissect.py --rgrep dbus tests/files/exsyslog   # 0 行（期望补集 7，实得 0）
python logdissect.py --grep dbus --rgrep shade tests/files/exsyslog  # 0 行（坏 rgrep 全剔）
```

因此 grep↔rgrep 这对在当前代码下**根本不互补**，把同一组词在正反向之间调换，
结果当然不同；而且坏 rgrep 无论放在链上哪个槽位（槽位 8）都会把结果清空或
放过，表现“换序不同”。

**(B) rsource 启用即崩 —— rsource.py:51 对绑定方法做 in。**

- `logdissect/filters/rsource.py:50-54`

```python
for entry in data['entries']:
    if 'log_source' in entry.keys:          # entry.keys 是方法对象，应为 entry.keys()
        if entry['log_source'] not in args.rsource:
            newdata['entries'].append(entry)
    else: newdata['entries'].append(entry)
```

`in` 对内置方法对象做成员判断抛
`TypeError: argument of type 'builtin_function_or_method' is not iterable`。
崩溃点固定在链上槽位 12，因此**排在它前面的 11 个过滤器的收缩结果决定了崩溃前
已完成多少处理、后面的 shost/source（13/14）完全没机会运行**。这正是“入链次序
+ 短路/崩溃时机”与结果强相关的地方：

```sh
python logdissect.py --rsource shade tests/files/exsyslog
# Traceback ... rsource.py:51 ... TypeError ... ; exit code 1
```

**(C) 正反向对“字段缺失”的处理不对称 —— 换条件即换结果。**

正向过滤器只在字段存在且值命中时保留，**字段缺失的行被丢弃**；反向过滤器在字段
缺失时走 `else`（或 `not in`）分支，**字段缺失的行被保留**。对比：

- dhost.py:50-53：无 else，缺 `dest_host` 即丢弃。
- rdhost.py:50-55：有 else，缺 `dest_host` 照样 append。
- 同样模式：shost.py:50-53（无 else） vs rshost.py:50-54（有 else）；
  process/rprocess、protocol/rprocess 同理。

syslog parser 产出的字段里没有 `dest_host`、没有 `source_host`（见
parsers/syslog.py:34-36 的 fields），所以：

```sh
python logdissect.py --dhost foo tests/files/exsyslog    # 0 行（字段不存在，全丢）
python logdissect.py --rdhost foo tests/files/exsyslog   # 10 行（字段不存在，全留）
python logdissect.py --shost shade tests/files/exsyslog  # 0 行（syslog 用 log_source，不是 source_host）
python logdissect.py --rshost shade tests/files/exsyslog # 10 行
python logdissect.py --source shade tests/files/exsyslog # 10 行（log_source == shade 命中）
```

这不是崩溃型 bug，而是“正/反谓词对缺字段行的态度相反”的设计取舍：它让正向和
反向在字段稀疏的 parser 上天然不互补。配合槽位顺序（例如 dhost#1 最先、rdhost#7
在后），启用正向会在链最前面就把集合清空，后续所有过滤器都在空集上空转；启用
反向则在槽位 7 才遇到一个恒真谓词。中间结果与下游可见性因此明显不同。

## 5. 小结（与链路图箭头对齐）

1. 箭头方向 = `run_filters` core.py:100-103 的数据流向：**上一节点的返回 dict
   成为下一节点的 data 入参**。
2. 每个节点先过“是否启用”短路（各 filter 的 `if not args.xxx: return data`），
   未启用即原样放行；启用才做集合收缩。
3. 槽位按字母序固定（§1 表），正反向对的先后（§2 表）是排序的副产品。
4. “换序结果不同”不是 argv 顺序问题（4.1/4.2 已证其在健康情况下可交换），而是
   rgrep:53 的坏正则、rsource:51 的绑定方法崩溃、以及正反向缺字段不对称（4.3）
   三处实现偏差共同造成的。
