"""Characterization tests for the argparse -> LogDissectCore filter chain path.

These tests document (and lock in) the behaviour described in
docs/analysis/. They are characterization tests: where upstream code has a
defect, the test asserts the *observed* behaviour and is tagged accordingly.
"""

import importlib
import os

import pytest


FILTER_NAMES = [
    "last", "range", "grep", "rgrep", "source", "rsource", "shost", "rshost",
    "dhost", "rdhost", "process", "rprocess", "protocol", "rprotocol",
]

HERE = os.path.dirname(os.path.abspath(__file__))
SYSLOG = os.path.join(HERE, "files", "exsyslog")


@pytest.fixture(scope="module")
def parsed_entries():
    from logdissect.parsers.syslog import ParseModule

    return ParseModule().parse_file(SYSLOG)["entries"]


@pytest.fixture
def fm():
    return {
        name: importlib.import_module("logdissect.filters." + name).FilterModule()
        for name in FILTER_NAMES
    }


class Args:
    def __init__(self, **kw):
        defaults = dict(
            pattern=None, rpattern=None, source=None, rsource=None,
            shost=None, rshost=None, dhost=None, rdhost=None,
            process=None, rprocess=None, protocol=None, rprotocol=None,
            last=None, range=None, utc=False,
        )
        defaults.update(kw)
        for key, value in defaults.items():
            setattr(self, key, value)


def sample_entries():
    return [
        {"raw_text": "alpha dbus systemd", "log_source": "hostA",
         "source_host": "sa", "dest_host": "da", "source_process": "sshd",
         "protocol": "tcp"},
        {"raw_text": "beta dbus only", "log_source": "hostB",
         "source_host": "sb", "dest_host": "db", "source_process": "cron",
         "protocol": "udp"},
        {"raw_text": "gamma nothing", "log_source": "hostA",
         "source_host": "sa", "dest_host": "dc", "source_process": "sshd",
         "protocol": "tcp"},
    ]


def run_chain(filter_modules, order, args):
    data = {"entries": sample_entries()}
    for name in order:
        data = filter_modules[name].filter_data(data, args=args)
    return [entry["raw_text"] for entry in data["entries"]]


# ---------------------------------------------------------------- chain order

def test_chain_order_is_alphabetical_not_definition_order():
    """core.load_filters() sorts __filters__; definition order is overridden."""
    import logdissect.filters as filters
    from logdissect.core import LogDissectCore

    assert filters.__filters__[0] == "last"
    assert filters.__filters__[3] == "rgrep"  # definition order: grep, rgrep

    core = LogDissectCore()
    core.load_filters()
    assert list(core.filter_modules) == sorted(filters.__filters__)


def test_grep_runs_before_rgrep_in_chain(fm):
    # 'grep' < 'rgrep' alphabetically: the forward filter is upstream.
    chain = list(fm)
    assert chain.index("grep") < chain.index("rgrep")
    assert chain.index("dhost") < chain.index("rdhost")
    assert chain.index("source") < chain.index("rsource")


# ----------------------------------------------------------------- short circuit

@pytest.mark.parametrize("name,attr", [
    ("grep", "pattern"), ("rgrep", "rpattern"),
    ("source", "source"), ("rsource", "rsource"),
    ("dhost", "dhost"), ("rdhost", "rdhost"),
    ("shost", "shost"), ("rshost", "rshost"),
    ("process", "process"), ("rprocess", "rprocess"),
    ("protocol", "protocol"), ("rprotocol", "rprotocol"),
    ("last", "last"), ("range", "range"),
])
def test_inactive_filter_short_circuits_returning_same_object(fm, name, attr):
    data = {"entries": [{"raw_text": "x"}]}
    result = fm[name].filter_data(data, args=Args(**{attr: None}))
    assert result is data


# -------------------------------------------------------- ordering equivalence

def test_grep_rgrep_pair_commutative_when_rgrep_pattern_survives(fm):
    # The rgrep regex bug (list formatted into a regex) is avoided here by
    # using a single-quoted pattern whose mangled char class never matches;
    # then both directions are set selections and commute.
    args = Args(pattern=["dbus"], rpattern=["zzqq"])
    forward = run_chain(fm, ["grep", "rgrep"], args)
    reverse = run_chain(fm, ["rgrep", "grep"], args)
    assert forward == reverse


def test_dhost_rdhost_pair_commutative(fm):
    args = Args(dhost=["da"], rdhost=["db"])
    assert run_chain(fm, ["dhost", "rdhost"], args) == ["alpha dbus systemd"]
    assert run_chain(fm, ["rdhost", "dhost"], args) == ["alpha dbus systemd"]


def test_cli_flag_order_does_not_change_chain_order(tmp_path):
    """Swapping the order of CLI flags must not change the filter chain."""
    from logdissect.core import LogDissectCore

    def load(argv):
        import sys
        old = sys.argv
        sys.argv = ["logdissect"] + argv
        try:
            core = LogDissectCore()
            core.load_filters()
            core.config_args()
            return core.args
        finally:
            sys.argv = old

    a = load(["--grep", "dbus", "--rgrep", "x", SYSLOG])
    b = load(["--rgrep", "x", "--grep", "dbus", SYSLOG])
    assert a.pattern == b.pattern == ["dbus"]
    assert a.rpattern == b.rpattern == ["x"]


# ----------------------------------------------------------- observed defects

def test_rgrep_compiles_list_into_character_class(fm):
    # DEFECT: rgrep.py:52 formats the *list* args.rpattern into the regex,
    # producing .*(['CRON']).*, a character class instead of the word.
    import re

    args = Args(rpattern=["CRON"])
    data = {"entries": [
        {"raw_text": "this line survives, plain lowercase"},
        {"raw_text": "this one dies, it has an apostrophe ' "},
    ]}
    out = fm["rgrep"].filter_data(data, args=args)
    assert [e["raw_text"] for e in out["entries"]] == [
        "this line survives, plain lowercase"]

    pattern = r".*({}).*".format(["CRON"])
    assert re.match(pattern, "x'x")  # the quote itself triggers a match


def test_rsource_crashes_on_entry_keys_method(fm):
    # DEFECT: rsource.py:51 uses `entry.keys` (bound method) instead of
    # entry.keys(); `in <bound method>` raises TypeError in Python 3.
    args = Args(rsource=["hostB"])
    with pytest.raises(TypeError, match="builtin_function_or_method"):
        fm["rsource"].filter_data(
            {"entries": sample_entries()}, args=args)


def test_forward_filters_drop_keyless_entries_reverse_keep_them(fm):
    entries = [{"raw_text": "has", "dest_host": "da"},
               {"raw_text": "keyless"}]
    args_keep = Args(rdhost=["da"])
    args_match = Args(dhost=["da"])

    kept = fm["rdhost"].filter_data({"entries": entries}, args=args_keep)
    assert [e["raw_text"] for e in kept["entries"]] == ["keyless"]

    matched = fm["dhost"].filter_data({"entries": entries}, args=args_match)
    assert [e["raw_text"] for e in matched["entries"]] == ["has"]


# ------------------------------------------------------------- aliasing model

def test_filters_share_entry_dicts_no_copy(fm):
    entries = sample_entries()
    args = Args(source=["hostA"])

    first = fm["source"].filter_data({"entries": entries}, args=args)
    second = fm["dhost"].filter_data({"entries": entries},
                                     args=Args(dhost=["da"]))

    # Same underlying dict object flows through every stage of the chain.
    assert first["entries"][0] is second["entries"][0] is entries[0]

    # Mutating through one view is visible through the other: there is no
    # per-filter deep copy protecting consumers.
    first["entries"][0]["raw_text"] = "mutated"
    assert second["entries"][0]["raw_text"] == "mutated"


def test_filter_builds_new_container_but_moves_header_scalars(fm):
    data = {"parser": "syslog", "source_path": "/a", "source_file": "a",
            "source_file_mtime": 1.0, "source_file_year": 2026,
            "entries": sample_entries()}
    out = fm["grep"].filter_data(data, args=Args(pattern=["dbus"]))

    assert out is not data  # new outer dict + new entries list
    assert out["parser"] == "syslog"  # header scalars copied by value binding


def test_merge_logs_reuses_entry_dicts(parsed_entries):
    import logdissect.utils as utils

    merged = utils.merge_logs([{"entries": list(parsed_entries)}], sort=True)
    survivors = {id(e) for e in parsed_entries}
    assert all(id(e) in survivors for e in merged["entries"])
    assert [e["numeric_date_stamp_utc"] for e in merged["entries"]] == \
        sorted(e["numeric_date_stamp_utc"] for e in parsed_entries)


# ------------------------------------------------------------- timestamp write

def test_parser_writes_utc_stamp_during_parse_not_filters(parsed_entries, fm):
    # numeric_date_stamp_utc is populated by utils.get_utc_date() during
    # parsing (parsers/type.py:110). Filters only read it.
    assert all("numeric_date_stamp_utc" in e for e in parsed_entries)

    before = parsed_entries[0]["numeric_date_stamp_utc"]
    fm["grep"].filter_data({"entries": list(parsed_entries)},
                           args=Args(pattern=["dbus"]))
    assert parsed_entries[0]["numeric_date_stamp_utc"] == before


def test_range_reads_local_stamp_by_default(parsed_entries, fm):
    args = Args(range="202602272130-202602272131", utc=False)
    out = fm["range"].filter_data(
        {"entries": list(parsed_entries)}, args=args)
    assert len(out["entries"]) == 8


def test_last_and_range_read_utc_stamp(fm):
    entries = [
        {"raw_text": "new", "numeric_date_stamp_utc": "29990101000000.0"},
        {"raw_text": "old", "numeric_date_stamp_utc": "20000101000000.0"},
    ]
    args = Args(last="10d")
    out = fm["last"].filter_data({"entries": entries}, args=args)
    assert [e["raw_text"] for e in out["entries"]] == ["new"]
