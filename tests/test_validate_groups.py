"""DESK-0265: `drew validate` stops shouting. One line per warning kind (count, three newest ids), errors in full, the summary line unchanged;
`--verbose` prints every line as before; `--gate KIND` makes one kind an error for that run; undispatched work is one line with its count and
the oldest wait. Protects: the owner's rule that there is no clutter around Desk however deep the review (2026-11-13), and the scripts that
parse the `N ticket(s), E error(s), W warning(s)` line."""

import os
import re
import time

import pytest

from desk import ops, warnkinds
from desk.cli import main
from desk.store import Store


def _validate(store, capsys, *argv):
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "validate", *argv])
    return e.value.code, capsys.readouterr().out


def _ready_without_commands(store, n, assignee="james"):
    ids = []
    for i in range(n):
        t = ops.new(store, f"Ready ticket {i}", "jre")
        t.meta.update({"status": "ready", "assignee": assignee})
        t.sections["Commands"] = ""
        t.write()
        ids.append(t.id)
    return ids


def _lines(out, prefix):
    return [ln for ln in out.splitlines() if ln.startswith(prefix)]


def test_fifty_tickets_without_commands_print_one_grouped_line(store, capsys):
    ids = _ready_without_commands(store, 50)
    code, out = _validate(store, capsys)
    cmd = [ln for ln in out.splitlines() if "no Commands" in ln]
    assert code == 0 and len(cmd) == 1
    assert cmd[0].startswith("warning: [commands] ") and "50 ticket(s)" in cmd[0]
    assert "newest " + ", ".join(reversed(ids[-3:])) in cmd[0] and ids[0] not in cmd[0]
    assert all(not ln.startswith(f"warning: {i}:") for ln in out.splitlines() for i in ids[:5])


def test_the_summary_line_keeps_its_form_and_counts_every_underlying_warning(store, capsys):
    _ready_without_commands(store, 7)
    _, grouped = _validate(store, capsys)
    _, verbose = _validate(store, capsys, "--verbose")
    sum_v = verbose.strip().splitlines()[-1]
    pat = r"^\d+ ticket\(s\), \d+ error\(s\), \d+ warning\(s\)$"
    assert re.match(pat, grouped.strip().splitlines()[-1]) and grouped.strip().splitlines()[-1] == sum_v
    assert len(_lines(verbose, "warning:")) == int(re.search(r"(\d+) warning\(s\)$", sum_v).group(1))


def test_verbose_prints_every_line_as_today(store, capsys):
    ids = _ready_without_commands(store, 12)
    code, out = _validate(store, capsys, "--verbose")
    assert code == 0
    for i in ids:
        assert any(ln.startswith(f"warning: {i}: ready with no Commands") for ln in out.splitlines())
    assert "[commands]" not in out


def test_gate_commands_turns_that_kind_into_errors_and_exits_one(store, capsys):
    ids = _ready_without_commands(store, 4)
    code, out = _validate(store, capsys, "--gate", "commands")
    assert code == 1
    for i in ids:
        assert any(ln.startswith(f"error: {i}: ready with no Commands") for ln in out.splitlines())
    assert not [ln for ln in out.splitlines() if ln.startswith("warning:") and "no Commands" in ln]
    assert re.search(r"\b4 error\(s\)", out.strip().splitlines()[-1])  # the gated lines count as errors, not warnings


def test_gate_on_a_kind_with_no_hits_passes(store, capsys):
    code, out = _validate(store, capsys, "--gate", "commands")
    assert code == 0 and "0 error(s)" in out.strip().splitlines()[-1]


def test_an_unknown_gate_is_refused_naming_the_kinds(store, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--home", str(store.home), "validate", "--gate", "nonsense"])
    assert e.value.code != 0
    err = capsys.readouterr().err
    assert "nonsense" in err and "commands" in err


def test_undispatched_is_one_line_with_the_count_and_the_oldest_wait(store, capsys):
    ids = []
    for age in (2.0, 30.0, 5.0):
        t = ops.new(store, f"Waiting {age}", "jre")
        t.meta.update({"status": "ready", "assignee": "main"})
        t.sections["Commands"] = "- `drew validate`"
        t.write()
        old = time.time() - age * 3600
        os.utime(t.path, (old, old))
        ids.append(t.id)
    _, out = _validate(store, capsys)
    und = [ln for ln in out.splitlines() if "undispatched" in ln]
    assert len(und) == 1 and und[0].startswith("warning: [undispatched] ")
    assert "3 ticket(s)" in und[0] and "oldest 30 h" in und[0] and ids[1] in und[0]
    _, verbose = _validate(store, capsys, "--verbose")
    assert len([ln for ln in verbose.splitlines() if "undispatched for" in ln]) == 3


def test_errors_are_printed_in_full_after_the_warning_groups(store, capsys):
    _ready_without_commands(store, 3)
    t = ops.new(store, "Blocked without a reason", "jre")
    t.meta["status"] = "blocked"
    t.write()
    code, out = _validate(store, capsys)
    lines = out.strip().splitlines()
    first_err = next(i for i, ln in enumerate(lines) if ln.startswith("error:"))
    assert code == 1 and all(not ln.startswith("warning:") for ln in lines[first_err:-1]) and f"error: {t.id}: status `blocked` needs `blocked_on`" in out


def test_every_warning_line_has_a_kind_and_the_commands_kind_is_named():
    assert warnkinds.classify("JRE-0001: ready with no Commands (list them)") == "commands"
    assert warnkinds.classify("JRE-0002: GPS fails P: no How to validate line") == "gps"
    assert warnkinds.classify("projects/x.md: no brand block (mark, accent, family); run `drew brand-check`") == "brand"
    assert warnkinds.classify("JRE-0003: something nobody wrote a rule for yet") == "other"
    assert "commands" in warnkinds.kind_keys()


def test_grouping_is_stable_and_largest_kind_first():
    lines = [f"JRE-{i:04d}: ready with no Commands (x)" for i in range(1, 6)] + ["JRE-0009: title is 90 characters; keep it under 60"]
    groups = warnkinds.group(lines, {})
    assert [g.key for g in groups] == ["commands", "title"] and groups[0].count == 5
