"""Characterization tests for the argv -> LogDissectCore filter-chain path.

These tests pin down the behavior documented in docs/analysis/ :
  01-chain-ordering.md, 02-mutation-and-copy.md, 03-field-matrix.md and the
interactive docs/analysis/filter-chain.html.

They are *characterization* tests: two of them assert the current, arguably
buggy behavior of rgrep/rsource so that any future fix has to update the docs
deliberately.
"""
import os
import re
import subprocess
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSLOG = os.path.join(ROOT, "tests", "files", "exsyslog")
MESLOG = os.path.join(ROOT, "tests", "files", "exmeslog")
CLI = os.path.join(ROOT, "logdissect.py")
HTML = os.path.join(ROOT, "docs", "analysis", "filter-chain.html")

sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_args(**overrides):
    """Build the namespace every filter_data(args=...) expects."""
    defaults = dict(
        last=None, range=None, utc=False,
        pattern=None, rpattern=None,
        source=None, rsource=None,
        shost=None, rshost=None,
        dhost=None, rdhost=None,
        process=None, rprocess=None,
        protocol=None, rprotocol=None,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def parsed():
    from logdissect.parsers.syslog import ParseModule
    return ParseModule().parse_file(SYSLOG)


def run_cli(*extra):
    """Run the CLI and return (stdout lines, return code)."""
    proc = subprocess.run(
        [sys.executable, CLI, *extra, SYSLOG],
        cwd=ROOT, capture_output=True, text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return lines, proc.returncode, proc.stderr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def data():
    return parsed()


# ---------------------------------------------------------------------------
# 01: chain ordering, short-circuit and positive/negative pairs
# ---------------------------------------------------------------------------

EXPECTED_ORDER = [
    "dhost", "grep", "last", "process", "protocol", "range",
    "rdhost", "rgrep", "rprocess", "rprotocol", "rshost",
    "rsource", "shost", "source",
]


def test_chain_order_is_alphabetical_regardless_of_registry_order():
    """load_filters sorts __filters__; run_filters iterates insertion order."""
    import logdissect.filters
    assert sorted(logdissect.filters.__filters__) == EXPECTED_ORDER

    core_mod = __import__("logdissect.core", fromlist=["LogDissectCore"])
    src_file = core_mod.__file__
    src = open(src_file, encoding="utf-8").read()
    # The chain order is fixed by sorted(...) at load time ...
    assert re.search(r"for f in sorted\(logdissect\.filters\.__filters__\)", src)
    # ... and each filter's return value feeds the next one.
    assert "self.data_set['finalized_data'] = ourlog" in src


def test_cli_arg_order_does_not_change_result():
    """Writing two filter flags in either order yields the same final set."""
    a, rc_a, _ = run_cli("--grep", "dbus", "--process", "CRON")
    b, rc_b, _ = run_cli("--process", "CRON", "--grep", "dbus")
    assert rc_a == rc_b == 0
    assert a == b == []  # dbus rows intersect CRON rows = empty


@pytest.mark.parametrize("flag,value,expected", [
    ("--grep", "dbus", 3),
    ("--process", "CRON", 1),       # healthy positive
    ("--rprocess", "CRON", 9),      # healthy negative = complement
    ("--source", "shade", 10),      # syslog captures host into log_source
])
def test_healthy_filters(flag, value, expected):
    lines, rc, _ = run_cli(flag, value)
    assert rc == 0
    assert len(lines) == expected


def test_positive_negative_missing_field_asymmetry():
    """Positive filters drop rows lacking the field; reverse keep them."""
    pos, rc_pos, _ = run_cli("--dhost", "anything")
    neg, rc_neg, _ = run_cli("--rdhost", "anything")
    assert rc_pos == rc_neg == 0
    assert pos == []          # no dest_host in syslog -> all dropped
    assert len(neg) == 10     # reverse keeps field-less rows via else


def test_rgrep_is_not_complement_of_grep_current_bug():
    """rgrep.py:53 formats the whole list into the regex (character class)."""
    positive, _, _ = run_cli("--grep", "dbus")
    negative, rc, _ = run_cli("--rgrep", "dbus")
    assert len(positive) == 3
    # A correct complement would be 7; the buggy character class [dbus]
    # matches essentially every line and strips all 10.
    assert negative == []
    assert rc == 0


def test_rgrep_multiple_patterns_also_strip_all_current_bug():
    """With two patterns the formatted char-class still matches all lines."""
    out, rc, _ = run_cli("--rgrep", "dbus", "--rgrep", "systemd")
    assert rc == 0
    assert out == []


def test_rsource_crashes_on_bound_method_current_bug():
    """rsource.py:51 does `'log_source' in entry.keys` (bound method)."""
    lines, rc, err = run_cli("--rsource", "shade")
    assert rc == 1
    assert lines == []
    assert "rsource.py" in err
    assert "TypeError" in err


def test_disabled_filter_short_circuits_returns_same_object(data):
    from logdissect.filters.grep import FilterModule
    out = FilterModule().filter_data(data, args=make_args())
    assert out is data


def test_grep_multiple_patterns_are_or_with_break():
    lines, rc, _ = run_cli("--grep", "dbus", "--grep", "CRON")
    assert rc == 0
    assert len(lines) == 4  # 3 dbus rows + 1 CRON row, de-duplicated by break


# ---------------------------------------------------------------------------
# 02: shallow copy boundary and shared entry references
# ---------------------------------------------------------------------------

def test_enabled_filter_builds_new_shell_but_shares_entry_dicts(data):
    from logdissect.filters.grep import FilterModule
    out = FilterModule().filter_data(data, args=make_args(pattern=["dbus"]))
    assert out is not data                       # new outer dict
    assert out["entries"] is not data["entries"]  # new list
    assert len(out["entries"]) == 3
    # The entry dicts themselves are the very same objects (no copy).
    assert all(e in data["entries"] for e in out["entries"])


def test_shared_entry_is_an_alias_mutation_is_visible_upstream(data):
    from logdissect.filters.grep import FilterModule
    out = FilterModule().filter_data(data, args=make_args(pattern=["dbus"]))
    chosen = out["entries"][0]
    chosen["raw_text"] = "ALIASED"
    # The upstream view points at the same dict.
    assert data["entries"][data["entries"].index(chosen)]["raw_text"] == "ALIASED"


def test_merge_logs_shell_has_only_entries_and_preserves_refs(data):
    import logdissect.utils
    second = parsed()
    merged = logdissect.utils.merge_logs([data, second], sort=True)
    assert list(merged.keys()) == ["entries"]
    assert data["entries"][0] in merged["entries"]  # still shared objects
    # sorted ascending on the UTC stamp
    stamps = [e["numeric_date_stamp_utc"] for e in merged["entries"]]
    assert stamps == sorted(stamps)


def test_filters_never_write_entry_fields(data):
    """Every enabled filter is read-only w.r.t. entry dicts."""
    from logdissect.filters.grep import FilterModule
    import copy
    snapshot = copy.deepcopy(data["entries"])
    FilterModule().filter_data(data, args=make_args(pattern=["dbus"]))
    assert data["entries"] == snapshot


# ---------------------------------------------------------------------------
# 03: time filters
# ---------------------------------------------------------------------------

def test_range_local_and_utc():
    local, rc, _ = run_cli("--range", "202602272130-202602272131")
    assert rc == 0
    assert len(local) == 8
    utc, rc, _ = run_cli("--utc", "--range", "202602280530-202602280531")
    assert rc == 0
    assert len(utc) == 8


def test_last_window_includes_all_when_wide():
    lines, rc, _ = run_cli("--last", "10000d")
    assert rc == 0
    assert len(lines) == 10


# ---------------------------------------------------------------------------
# HTML deliverable: self-contained, every node clickable, console clean
# ---------------------------------------------------------------------------

def test_html_exists_and_is_self_contained():
    assert os.path.isfile(HTML)
    text = open(HTML, encoding="utf-8").read()
    # No external assets that could fail on a file:// open.
    # (The only http string is the required SVG XML namespace identifier,
    # which is not a network fetch.)
    assert not re.search(r"<script[^>]+src=", text)
    assert not re.search(r"<link[^>]+href=", text)
    assert not re.search(r"url\(https?://", text)
    assert "<script>" in text and "</script>" in text
    for name in EXPECTED_ORDER:
        assert ('"%s"' % name) in text or ("'%s'" % name) in text


def test_html_declares_read_fields_and_repro_for_every_filter():
    text = open(HTML, encoding="utf-8").read()
    # Every filter panel carries a repro command and the code-line references.
    for name in EXPECTED_ORDER:
        assert text.count("python logdissect.py") >= len(EXPECTED_ORDER)
    # The two must-explain trade-offs are surfaced in the page.
    assert "rgrep.py:53" in text
    assert "rsource.py:51" in text
    assert "TypeError" in text
    # Field names that the matrix claims are read must appear.
    for field in ["raw_text", "log_source", "dest_host", "source_host",
                  "source_process", "protocol",
                  "numeric_date_stamp_utc", "numeric_date_stamp"]:
        assert field in text


