# logdissect 参数 → 过滤器链 路径分析

本目录把 logdissect.py 的命令行参数，经由 core.LogDissectCore 组装成过滤器链
（filter chain）这一路径完整拆开。所有结论都落到具体函数与行号，并附一条可手工
执行的复现命令；行号对应仓库当前代码（logdissect/__init__.py 版本 3.1.2）。

## 文件索引

- [01-chain-ordering.md](01-chain-ordering.md) —— 过滤器的加载/入链次序、正反向
  对（grep↔rgrep、source↔rsource、dhost↔rdhost 等）的相对位置、短路时机，以及
  “同一组参数换序后结果不同”的真实来源。
- [02-mutation-and-copy.md](02-mutation-and-copy.md) —— 就地 mutate 与浅/深拷贝的
  边界：哪些字段是共享引用、别名何时产生、时间戳在何时被重写。
- [03-field-matrix.md](03-field-matrix.md) —— 每个过滤器对 tsdata（entry dict）
  各字段的读/写矩阵，与交互式链路图 filter-chain.html 的点击面板一一对应。
- [filter-chain.html](filter-chain.html) —— 浏览器直接打开的链路图。按真实执行
  次序绘制 14 个过滤器节点与箭头；点击任一过滤器，右侧显示它读哪些 entry 字段、
  写哪些字段、短路/返回逻辑、缺陷与复现命令。

## 一张总图

```
argv
  -> logdissect.py:25-28            LogDissectCore().run_job()
     -> core.py run_job()  L58-80
        -> load_parsers()         L190-195   sorted(parsers.__all__)
        -> load_filters()         L197-202   sorted(filters.__filters__)  <- 链序在此决定
        -> load_outputs()         L204-209
        -> config_args()          L124-156   argparse 解析 argv 到 self.args
        -> load_inputs()          L161-179
        -> run_parse()            L82-95     每文件 parser.parse_file()
        -> utils.merge_logs()     utils.py merge_logs   生成 finalized_data
        -> run_filters()          L97-105    按字典插入次序串行套过滤器   <- 本文焦点
        -> run_output()           L107-120
```

## 关键事实速览

1. **过滤器链是固定的，与命令行书写顺序无关。**
   load_filters()（logdissect/core.py:197-202）对
   logdissect.filters.__filters__（logdissect/filters/__init__.py:23-25）做
   sorted(...) 后逐个塞进 dict；run_filters()（core.py:97-105）按 dict 插入序
   （CPython 3.7+ 保序）执行。CLI 上把 `--grep a --process b` 写成
   `--process b --grep a` 得到同一条链、同一份结果（实测均为 0 行交集）。

2. **实际执行次序（sorted 后）：**
   dhost, grep, last, process, protocol, range, rdhost, rgrep, rprocess,
   rprotocol, rshost, rsource, shost, source。
   正反向对因此不是相邻执行的，而且谁先谁后取决于字母序：grep 在 rgrep 之前、
   dhost 在 rdhost 之前；但 rsource 在 source 之前、rshost 在 shost 之前。
   详见 01 篇。

3. **每个未启用的过滤器返回“同一个 data 对象”（短路）；启用的过滤器返回全新
   外壳 dict，但 entries 里每个 entry dict 是共享引用，不做拷贝。**
   见 grep.py:39-40（短路 return data）、grep.py:42-58（新外壳 + append(entry)
   原对象）。别名与拷贝边界见 02 篇。

4. **链上有两个会破坏“正反向互补”直觉的现存缺陷：**
   - logdissect/filters/rgrep.py:53 用
     `re.compile(r".*({}).*".format(args.rpattern))` 把整个 list 而不是当前
     pattern 塞进格式串：单参数与多参数都会退化成字符类 `['dbus']` /
     `['dbus', 'x']`（命中任意含这些字母/引号/逗号/空格的行），把日志几乎全剔掉；
     只有全大写之类的罕见 pattern 会漏过部分行。
   - logdissect/filters/rsource.py:51 写成 `'log_source' in entry.keys`
     （绑定方法，不是 entry.keys()），一旦启用 --rsource 必抛 TypeError，整条
     作业在此节点崩溃。

## 统一复现素材

以下命令均以仓库自带的 tests/files/exsyslog（10 行 syslog，时间戳 Feb 27
21:30–21:35）为输入，在仓库根目录执行：

```sh
# 正向 grep：3 行含 dbus
python logdissect.py --grep dbus tests/files/exsyslog

# 反向 rgrep：期望 7 行（补集），实际 0 行（缺陷，见 01 篇）
python logdissect.py --rgrep dbus tests/files/exsyslog

# rsource 启用即崩在 rsource.py:51
python logdissect.py --rsource shade tests/files/exsyslog
```
