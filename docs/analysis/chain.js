"use strict";

// 结论与 docs/analysis/*.md 中的文件:行号保持一致。
// 容器写语义对所有被启用的过滤器相同：新建外层 dict + 新建 entries 列表，
// append 的是原 entry 对象（浅拷贝，entry dict 全程共享）。
var COMMON_WRITE = "新建 newdata(dict) 与 newdata['entries'](list)，append 原 entry 对象；不写任何 entry 字段";

var FILTERS = [
  {
    name: "dhost", kind: "forward", cli: "--dhost", dest: "dhost (append)",
    read: ["dest_host"], args: "args.dhost",
    semantics: "保留 entry['dest_host'] 命中值集的条目；字段缺失视为不命中，直接丢弃。",
    lines: "dhost.py:39,41-48,50-53",
    short: "args.dhost 为空 → dhost.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -p tcpdump --dhost 10.0.0.2 your.log"
  },
  {
    name: "grep", kind: "forward", cli: "--grep", dest: "pattern (append)",
    read: ["raw_text"], args: "args.pattern",
    semantics: "多个 pattern 之间为 OR：命中即 append 并 break（grep.py:59）。",
    lines: "grep.py:39-40,53,55-59",
    short: "args.pattern 为空 → grep.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --grep dbus tests/files/exsyslog"
  },
  {
    name: "last", kind: "time", cli: "--last 5m|3h|2d", dest: "last (store)",
    read: ["numeric_date_stamp_utc"], args: "args.last；下界 datetime.utcnow() 每次现算",
    semantics: "dstamp >= now-N 的条目保留；无 UTC 戳的条目静默丢弃。阈值每次调用重算，不写 entry。",
    lines: "last.py:39-41,48-59,72-79",
    short: "args.last 为空 → last.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --last 3650d tests/files/exsyslog"
  },
  {
    name: "process", kind: "forward", cli: "--process", dest: "process (append)",
    read: ["source_process"], args: "args.process",
    semantics: "保留 source_process 命中的条目；字段缺失则丢弃。",
    lines: "process.py:39-40,51-53",
    short: "args.process 为空 → process.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --process systemd tests/files/exsyslog"
  },
  {
    name: "protocol", kind: "forward", cli: "--protocol", dest: "protocol (append)",
    read: ["protocol"], args: "args.protocol",
    semantics: "保留 protocol 命中的条目；字段缺失则丢弃。",
    lines: "protocol.py:39-40,51-53",
    short: "args.protocol 为空 → protocol.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -p tcpdump --protocol tcp your.log"
  },
  {
    name: "range", kind: "time", cli: "--range A-B [--utc]", dest: "range / utc",
    read: ["numeric_date_stamp", "numeric_date_stamp_utc"], args: "args.range, args.utc",
    semantics: "默认读本地 numeric_date_stamp；加 --utc 改读 numeric_date_stamp_utc。窗口边界 ljust(14,'0')。",
    lines: "range.py:39-43,55-56,58-79",
    short: "args.range 为空 → range.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --range 202602272130-202602272131 tests/files/exsyslog"
  },
  {
    name: "rdhost", kind: "reverse", cli: "--rdhost", dest: "rdhost (append)",
    read: ["dest_host"], args: "args.rdhost",
    semantics: "剔除 dest_host 命中的条目；字段缺失走 else 分支保留（与正向 dhost 非对称）。",
    lines: "rdhost.py:39,41-48,50-55",
    short: "args.rdhost 为空 → rdhost.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -p tcpdump --rdhost 10.0.0.2 your.log"
  },
  {
    name: "rgrep", kind: "reverse", cli: "--rgrep", dest: "rpattern (append)",
    read: ["raw_text"], args: "args.rpattern",
    semantics: "意图剔除正则命中的条目：任一 pattern 命中即剔除，全部不命中才保留。",
    lines: "rgrep.py:39-40,53,55-62",
    short: "args.rpattern 为空 → rgrep.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --rgrep systemd tests/files/exsyslog",
    bug: {
      title: "rgrep.py:53：把整个 list 格式化成了正则",
      body:
        "代码为 re.compile(r'.*({}).*'.format(args.rpattern))，format 用的是 list 而非循环变量 rpat。" +
        "['CRON'] 被转成字符串 \"['CRON']\"，正则于是 .*(['CRON']).* —— 圆括号里是字符类，" +
        "任何含单引号或 C/R/O/N 的行都算命中，剔除面被严重放大。对照 grep.py:53 用的是循环变量 pat（正确）。"
    }
  },
  {
    name: "rprocess", kind: "reverse", cli: "--rprocess", dest: "rprocess (append)",
    read: ["source_process"], args: "args.rprocess",
    semantics: "剔除 source_process 命中的条目；字段缺失保留。",
    lines: "rprocess.py:39,51-55",
    short: "args.rprocess 为空 → rprocess.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --rprocess systemd tests/files/exsyslog"
  },
  {
    name: "rprotocol", kind: "reverse", cli: "--rprotocol", dest: "rprotocol (append)",
    read: ["protocol"], args: "args.rprotocol",
    semantics: "剔除 protocol 命中的条目；字段缺失保留。",
    lines: "rprotocol.py:39,51-55",
    short: "args.rprotocol 为空 → rprotocol.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -p tcpdump --rprotocol tcp your.log"
  },
  {
    name: "rshost", kind: "reverse", cli: "--rshost", dest: "rshost (append)",
    read: ["source_host"], args: "args.rshost",
    semantics: "剔除 source_host 命中的条目；字段缺失保留。",
    lines: "rshost.py:39,50-53",
    short: "args.rshost 为空 → rshost.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -p tcpdump --rshost 10.0.0.1 your.log"
  },
  {
    name: "rsource", kind: "reverse", cli: "--rsource", dest: "rsource (append)",
    read: ["log_source（意图读取）"], args: "args.rsource",
    semantics: "意图剔除 log_source 命中的条目；字段缺失走 else 保留。",
    lines: "rsource.py:39,41-48,50-54",
    short: "args.rsource 为空 → rsource.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -s --rsource shade tests/files/exsyslog",
    bug: {
      title: "rsource.py:51：entry.keys 漏括号，--rsource 启用即抛 TypeError",
      body:
        "代码为 if 'log_source' in entry.keys: —— entry.keys 是绑定方法，Python 3 下 " +
        "'in <builtin_function_or_method>' 抛 TypeError。同目录其它过滤器均正确写成 entry.keys()，" +
        "这是孤例笔误。链上 rsource 排第 12、source 排第 14，任何带 --rsource 的作业都会在到达 source 前整体崩溃。"
    }
  },
  {
    name: "shost", kind: "forward", cli: "--shost", dest: "shost (append)",
    read: ["source_host"], args: "args.shost",
    semantics: "保留 source_host 命中的条目；字段缺失则丢弃。",
    lines: "shost.py:39,50-52",
    short: "args.shost 为空 → shost.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py -p tcpdump --shost 10.0.0.1 your.log"
  },
  {
    name: "source", kind: "forward", cli: "--source", dest: "source (append)",
    read: ["log_source"], args: "args.source",
    semantics: "保留 log_source 命中的条目；字段缺失则丢弃。",
    lines: "source.py:39,41-48,50-52",
    short: "args.source 为空 → source.py:39-40 原样 return data（同对象短路）",
    cmd: "python -W ignore logdissect.py --source shade tests/files/exsyslog"
  }
];

var STAGES = [
  { title: "CLI / argparse", sub: "logdissect.py:25-28 · core.py:124-156 config_args() · 各过滤器 __init__ 注册 dest" },
  { title: "load_filters()", sub: "core.py:197-202 对 __filters__ 做 sorted()，按名加载" },
  { title: "parse_file()", sub: "parsers/type.py:46-122 · 就地写 entry 与时间戳（utils.py:145-168）" },
  { title: "merge_logs(sort=True)", sub: "core.py:72-74 · utils.py:203-211 · 只留 entries，按 numeric_date_stamp_utc 就地排序" },
  { title: "run_output()", sub: "core.py:107-120 · 只读 entries 输出（不 mutate）" }
];

var KIND_LABEL = { forward: ["正向", "fwd"], reverse: ["反向", "rev"], time: ["时间窗", "tm"] };

function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) { node.className = cls; }
  if (text !== undefined && text !== null) { node.textContent = text; }
  return node;
}

function renderChain() {
  var chain = document.getElementById("chain");

  var legend = el("div", "legend");
  legend.innerHTML =
    '<span><i class="dot" style="background:var(--forward)"></i>正向（保留命中）</span>' +
    '<span><i class="dot" style="background:var(--reverse)"></i>反向（剔除命中）</span>' +
    '<span><i class="dot" style="background:var(--time)"></i>时间窗</span>' +
    '<span><i class="dot" style="background:var(--bug)"></i>已知缺陷</span>';
  chain.appendChild(legend);

  chain.appendChild(stageBox(STAGES[0]));
  chain.appendChild(arrow());
  chain.appendChild(stageBox(STAGES[1]));
  chain.appendChild(arrow());
  chain.appendChild(stageBox(STAGES[2]));
  chain.appendChild(arrow());
  chain.appendChild(stageBox(STAGES[3]));
  chain.appendChild(arrow());

  chain.appendChild(el("div", "sep", "▼ 以下为 run_filters() 实际执行序（sorted 字母序，共 14 个）▼"));

  var group = el("div", "filter-group");
  FILTERS.forEach(function (f, i) {
    var btn = el("button", "node " + f.kind);
    btn.type = "button";
    btn.dataset.index = String(i);
    btn.setAttribute("aria-pressed", "false");

    btn.appendChild(el("span", "idx", String(i + 1)));
    btn.appendChild(el("span", "nm", f.name));
    btn.appendChild(el("span", "ds", f.cli));
    if (f.bug) { btn.appendChild(el("span", "bugtag", "缺陷")); }

    btn.addEventListener("click", function () { selectFilter(i); });
    group.appendChild(btn);
  });
  chain.appendChild(group);

  chain.appendChild(arrow());
  chain.appendChild(stageBox(STAGES[4]));
}

function stageBox(stage) {
  var box = el("div", "stage");
  box.appendChild(document.createTextNode(stage.title));
  box.appendChild(el("small", null, stage.sub));
  return box;
}

function arrow() { return el("div", "arrow"); }

function selectFilter(index) {
  var nodes = document.querySelectorAll(".node");
  for (var i = 0; i < nodes.length; i++) {
    var on = Number(nodes[i].dataset.index) === index;
    nodes[i].classList.toggle("active", on);
    nodes[i].setAttribute("aria-pressed", on ? "true" : "false");
  }
  renderDetail(FILTERS[index], index);
}

function addRow(tbody, k, v, cls) {
  var tr = document.createElement("tr");
  var td1 = document.createElement("td");
  td1.textContent = k;
  var td2 = document.createElement("td");
  if (cls) { td2.className = cls; }
  td2.textContent = v;
  tr.appendChild(td1);
  tr.appendChild(td2);
  tbody.appendChild(tr);
}

function renderDetail(f, index) {
  var detail = document.getElementById("detail");
  detail.textContent = "";
  var card = el("div", "card");

  card.appendChild(el("h2", null, "#" + (index + 1) + " " + f.name));

  var kindLine = el("div", "kind");
  var label = KIND_LABEL[f.kind];
  kindLine.appendChild(el("span", "pill " + label[1], label[0]));
  if (f.bug) { kindLine.appendChild(el("span", "pill bg", "存在缺陷")); }
  kindLine.appendChild(document.createTextNode(
    "链上位置：" + (index + 1) + " / 14（sorted 字母序，与命令行参数先后无关）"));
  card.appendChild(kindLine);

  var table = el("table");
  var thead = document.createElement("thead");
  var hr = document.createElement("tr");
  ["对象", "字段 / 行为"].forEach(function (t) {
    var th = document.createElement("th");
    th.textContent = t;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  var tbody = document.createElement("tbody");
  table.appendChild(tbody);

  addRow(tbody, "CLI 参数", f.cli);
  addRow(tbody, "argparse dest", f.dest);
  addRow(tbody, "读 entry 字段", f.read.join(", "), "rw-read");
  addRow(tbody, "读 args", f.args);
  addRow(tbody, "写 entry 字段", "无（只读，不就地 mutate entry）", "rw-none");
  addRow(tbody, "写 tsdata 容器", COMMON_WRITE);
  addRow(tbody, "未启用时", f.short);
  addRow(tbody, "判定语义", f.semantics);
  addRow(tbody, "源码行", f.lines);
  card.appendChild(table);

  if (f.bug) {
    var bugbox = el("div", "bugbox");
    bugbox.appendChild(el("h3", null, "缺陷：" + f.bug.title));
    bugbox.appendChild(el("p", null, f.bug.body));
    card.appendChild(bugbox);
  }

  card.appendChild(el("div", null, "手工复现命令："));
  var pre = el("pre", null, f.cmd);
  pre.className = "cmd-pre";
  card.appendChild(pre);

  card.appendChild(el("p", "hint",
    "提示：所有过滤器对 entry 均为浅共享；别名在 merge_logs 拼接（utils.py:208）与 append(entry) 时产生。"));

  detail.appendChild(card);
}

// ---------- 自检：保证 14 个节点与叙述/测试一致，控制台不静默吞错 ----------
function selfCheck() {
  var errors = [];
  var expectedOrder = [
    "dhost", "grep", "last", "process", "protocol", "range",
    "rdhost", "rgrep", "rprocess", "rprotocol", "rshost", "rsource",
    "shost", "source"
  ];
  var actual = FILTERS.map(function (f) { return f.name; });
  if (actual.length !== 14) { errors.push("过滤器数量应为 14，实际 " + actual.length); }
  expectedOrder.forEach(function (name, i) {
    if (actual[i] !== name) {
      errors.push("第 " + (i + 1) + " 个过滤器应为 " + name + "，实际 " + actual[i]);
    }
  });
  FILTERS.forEach(function (f, i) {
    ["name", "kind", "cli", "dest", "read", "args", "semantics", "lines", "short", "cmd"]
      .forEach(function (key) {
        if (f[key] === undefined || f[key] === null || f[key] === "") {
          errors.push("FILTERS[" + i + "] 缺字段 " + key);
        }
      });
    if (["forward", "reverse", "time"].indexOf(f.kind) === -1) {
      errors.push(f.name + " 的 kind 非法: " + f.kind);
    }
    if (f.read.length < 1) { errors.push(f.name + " 至少要声明一个读取字段"); }
  });
  if (errors.length > 0) {
    throw new Error("chain.js 自检失败:\n" + errors.join("\n"));
  }
}

// 测试钩子：?selftest=click 时自动依次点击全部 14 个过滤器，并把结果写入
// #selftest-result，供 headless 浏览器验收（正常打开页面不会触发）。
function autoClickHarness() {
  var errors = [];
  var nodes = document.querySelectorAll(".node");
  if (nodes.length !== 14) { errors.push("node count " + nodes.length); }
  var seen = [];
  for (var i = 0; i < nodes.length; i++) {
    nodes[i].click();
    var h2 = document.querySelector(".card h2");
    seen.push(h2 ? h2.textContent.replace(/^#\d+\s*/, "") : "NO_CARD");
    if (document.querySelectorAll(".node.active").length !== 1) {
      errors.push("after click " + i + " active!=1");
    }
    var expect = FILTERS[i].read.join(", ");
    if (document.getElementById("detail").textContent.indexOf(expect) === -1) {
      errors.push("card " + i + " missing read field " + expect);
    }
    if (FILTERS[i].bug) {
      if (!document.querySelector(".bugbox h3")) {
        errors.push("card " + i + " should show bug box");
      }
    }
  }
  var marker = document.createElement("div");
  marker.id = "selftest-result";
  marker.setAttribute("data-status", errors.length ? "FAIL" : "PASS");
  marker.textContent = errors.length
    ? "FAIL: " + errors.join("; ")
    : "PASS clicked-order: " + seen.join(",");
  document.body.appendChild(marker);
}

document.addEventListener("DOMContentLoaded", function () {
  selfCheck();
  renderChain();
  selectFilter(0);
  if (/[?&]selftest=click\b/.test(window.location.search)) {
    autoClickHarness();
  }
});

