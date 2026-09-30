# 02 · 取舍一：按名加载的入链次序与短路时机

## 1. 两份「顺序」互相打架

`logdissect/filters/__init__.py:23-25` 用一个列表声明全部过滤器（**定义序**）：

```python
__filters__ = ['last', 'range', 'grep', 'rgrep', 'source', 'rsource',
        'shost', 'rshost', 'dhost', 'rdhost', 'process', 'rprocess',
        'protocol', 'rprotocol']
```

紧接着 `__init__.py:27-40` 按这个顺序 `import` 各子模块。读者很容易以为过滤器链
就按这个顺序跑。**但不是**——装载器 `LogDissectCore.load_filters()`
（`logdissect/core.py:197-202`）写的是：

```python
for f in sorted(logdissect.filters.__filters__):
    self.filter_modules[f] = __import__('logdissect.filters.' + f, ...) \
                             .FilterModule(args=self.filter_args)
```

`sorted(...)` 把顺序重排成**字母序**，而 `run_filters()`（`core.py:97-105`）
按 dict 插入序迭代。最终生效的入链次序是：

| # | 模块 | 读取的字段 | 作用 |
|---|------|-----------|------|
| 1 | `dhost` | `dest_host` | 正向：保留目标主机命中的 entry |
| 2 | `grep` | `raw_text` | 正向：保留正则命中的 entry |
| 3 | `last` | `numeric_date_stamp_utc` | 正向：保留最近 N(s/m/h/d) 的 entry |
| 4 | `process` | `source_process` | 正向 |
| 5 | `protocol` | `protocol` | 正向 |
| 6 | `range` | `numeric_date_stamp` 或 `numeric_date_stamp_utc` | 正向：时间窗 |
| 7 | `rdhost` | `dest_host` | 反向：剔除目标主机命中 |
| 8 | `rgrep` | `raw_text` | 反向：剔除正则命中（**有缺陷，见 §5**） |
| 9 | `rprocess` | `source_process` | 反向 |
| 10 | `rprotocol` | `protocol` | 反向 |
| 11 | `rshost` | `source_host` | 反向 |
| 12 | `rsource` | `log_source` | 反向（**有缺陷，启用即崩，见 §5**） |
| 13 | `shost` | `source_host` | 正向 |
| 14 | `source` | `log_source` | 正向 |

三对正反向过滤器在这条字母序链上的相对位置：

- `grep`(2) → … → `rgrep`(8)：**先正后反**
- `dhost`(1) → … → `rdhost`(7)：**先正后反**
- `rsource`(12) → `source`(14)：**先反后正**（唯一的例外，因为 `r` 使名字变大）

这就是「按名加载」与 `__filters__` 手写顺序两处互相牵制的取舍：维护者在
`__filters__` 里调换名字顺序对执行结果**毫无影响**；真正的次序由字母表隐式决定。

复现命令：

```bash
python -W ignore -c "import logdissect.filters as f; print(list(enumerate(sorted(f.__filters__),1)))"
```

## 2. 命令行参数换序不会改变任何事

argparse 只负责把标志收集成 `args`（多值项用 `action='append'`，同名标志按出现顺序
追加进同一个 list；不同名标志落到不同 dest）。链的执行序由模块名字典序决定，
因此：

```bash
# 下面两条内部执行链完全相同：grep 永远在 rgrep 前面跑
python -W ignore logdissect.py --grep dbus --rgrep x tests/files/exsyslog
python -W ignore logdissect.py --rgrep x --grep dbus tests/files/exsyslog
```

「同一组参数换序后结果不同」在本代码里**不是由 argparse 触发的**。它只可能来自下面
两类机制，必须区分清楚：

## 3. 短路时机：未启用的过滤器原样返回入参对象

每个过滤器的 `filter_data()` 开头都是同一段守卫（以 grep 为例，
`logdissect/filters/grep.py:37-40`）：

```python
if args:
    if not args.pattern:
        return data          # ← 短路：返回的是调用方传进来的同一个对象
```

同样的守卫见 `rgrep.py:37-40`、`source.py:36-38`、`rsource.py:36-38`、
`dhost.py:36-38`、`rdhost.py:36-38`、`shost/rshost/process/rprocess/
protocol/rprotocol`、以及 `last.py:38-40`、`range.py:38-40`。

这个短路是「按名加载、全量实例化」架构的必然配套：14 个过滤器无条件全部入链，
没被命令行选中的过滤器必须变成 no-op。它选择的是 **`return data`（恒等）**而不是
「复制一份再返回」，所以未启用的过滤器不会切断对象身份（identity），下游拿到的
就是上游那个 dict。这一点与 [03](03-mutation-and-aliasing.md) 的别名问题直接咬合。

复现命令：

```bash
python -W ignore -c "import logdissect.filters.grep as G; d={'entries':[]}; a=type('A',(),{'pattern':None})(); print(G.FilterModule().filter_data(d,args=a) is d)"
# 输出 True
```

## 4. 被启用时：纯集合选择，数学上可交换

被启用的过滤器全部是「保留/剔除满足条件的 entry」，不改写任何 entry 内容：

- 正向：`S_A(X) = { e ∈ X : field(e) ∈ A }`（字段缺失视为不命中，直接丢弃）
- 反向：`R_B(X) = { e ∈ X : field(e) ∉ B }`（字段缺失视为「不在 B 中」，**保留**）

对同一字段，`S_A ∘ R_B = S_A ∩ (X \\ R_B 补)` 之类的组合只是集合交并，**顺序无关**。
实测三对同字段正反组合都交换：

```bash
python -W ignore -c "
import importlib
F={n:importlib.import_module('logdissect.filters.'+n).FilterModule() for n in ['dhost','rdhost']}
a=type('A',(),{'dhost':['da'],'rdhost':['db']})()
es=[{'raw_text':'a','dest_host':'da'},{'raw_text':'b','dest_host':'db'},{'raw_text':'c','dest_host':'dc'}]
def run(fs):
    d={'entries':list(es)}
    for f in fs: d=F[f].filter_data(d,args=a)
    return [e['raw_text'] for e in d['entries']]
print(run(['dhost','rdhost']), run(['rdhost','dhost']))"
# ['a'] ['a']
```

真正会让「换序」产生差异的，只有正向/反向对**字段缺失**的非对称处理：
正向过滤器把无字段 entry 丢掉（如 `dhost.py:50-53` 只在 `'dest_host' in entry.keys()`
时 append），反向过滤器把无字段 entry 留下（`rdhost.py:53-54` 的 `else` 分支）。
一旦链条里出现「要求字段存在的正向过滤器」，字段缺失的 entry 能否活到后面就取决于
它排在正向过滤器的上游还是下游。grep 是例外中的例外：它只看 `raw_text`，
而 `raw_text` 对每条 entry 恒存在（`parsers/type.py:111,136` 必写）。

## 5. 两个会破坏上述推理的实现缺陷

这两个缺陷都是「按名加载、全链执行」与具体实现相互牵制后暴露的——只要相关参数
被使用，缺陷节点一定会在链上跑到，与参数写法无关。

### 5.1 `--rsource` 启用即崩（`logdissect/filters/rsource.py:51`）

```python
if 'log_source' in entry.keys:     # 漏了括号，entry.keys 是绑定方法
```

Python 3 下 `in <builtin_function_or_method>` 抛
`TypeError: argument of type 'builtin_function_or_method' is not iterable`。
同目录其它过滤器都正确写成 `entry.keys()`（如 `source.py:51`、`rdhost.py:51`），
这是孤例笔误。由于 `rsource` 在链上排第 12、`source` 排第 14，任何带 `--rsource`
的作业都会在到达 `source` 之前整体崩溃。

```bash
python -W ignore logdissect.py -s --rsource shade tests/files/exsyslog
# TypeError: ... 'builtin_function_or_method' is not iterable (rsource.py:51)
```

### 5.2 `--rgrep` 的正则被格式化成字符类（`logdissect/filters/rgrep.py:53`）

```python
for rpat in values:
    repatterns[rpat] = re.compile(r".*({}).*".format(args.rpattern))  # 用了整个 list
```

正向 grep 用的是循环变量 `pat`（`grep.py:53`，正确），rgrep 却把 **list**
`args.rpattern` 塞进 `format`。`['CRON']` 被转成字符串 `"['CRON']"`，
正则于是 `.*(['CRON']).*` —— 圆括号里是字符类，命中任何含 `'`、`C`、`R`、`O`、`N`
的行，而不是单词 `CRON`。结果是剔除面被严重放大（单值时几乎剔掉所有行）。
因为 rgrep 在链上排第 8，`--rgrep` 与 `--grep` 联用时这一步通常先把集合清空，
表面上看就像「加了 rgrep 之后 grep 失效了」。

```bash
python -W ignore logdissect.py --rgrep systemd tests/files/exsyslog   # 0 行（期望剔除 3 行留下 7 行）
python -W ignore logdissect.py --rgrep CRON   tests/files/exsyslog   # 仅留不含 'CRON 字母的 3 行
```

## 6. 链上短路时机速查

- 未设置对应参数：守卫命中，`return data`（恒等、同对象），见上表各模块
  `filter_data()` 前 3 行。
- 设置了参数：构造 `newdata`（新 dict、新 `entries` list），对入参 `data['entries']`
  逐条判定后 append 原 entry 对象，返回 `newdata`。
- `last`/`range` 的时间阈值在**每次调用时现算**：`last.py:48-59` 用
  `datetime.utcnow()` 动态生成下界；`range.py:55-56` 每次 `ljust`/`int` 解析窗口。
  二者都不改写 entry 的时间戳字段，只读。
