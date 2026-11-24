"""Assertions for every guarantee stated in Part I (v2 First-Principles Chapters 1–3).

Every sentence in prose that states a guarantee points to a test here.
Also enforces the 60-character print width limit on every extracted listing.
"""

import re
from pathlib import Path

import pytest

from ahe import ch01, ch02, ch03


# ---------------------------------------------------------- Print width gate

def test_all_v2_listings_fit_60_chars():
    mark = re.compile(r"# listing (\d+\.\d+)\n(.*?)# end listing", re.S)
    code_dir = Path(ch01.__file__).parent
    found = 0
    for path in sorted(code_dir.glob("ch*.py")):
        for num, body in mark.findall(path.read_text()):
            found += 1
            for lineno, line in enumerate(body.splitlines(), 1):
                assert len(line) <= 60, (
                    f"Listing {num} line {lineno} is {len(line)} chars (>60): {line!r}"
                )
    assert found >= 10


# ---------------------------------------------------------------- Chapter 1

def test_ch1_prompt_only_trusts_incomplete_claim():
    out = ch01.prompt_only(ch01.TASK)
    assert out["fixed"] is True
    assert "reject_over" not in out["checks"]


def test_ch1_harnessed_rejects_partial_patch_and_omission():
    verdict = ch01.harnessed(ch01.TASK)
    assert verdict["verified"] is False
    assert verdict["status"] == "rejected"
    assert verdict["failed"] == ["reject_over"]
    assert verdict["claim_rejected"] == "claim omits reject_over"
    assert "summary" not in verdict


def test_ch1_harnessed_blocks_test_file_tampering():
    verdict = ch01.harnessed(ch01.TASK, model=ch01.tamper_model)
    assert verdict["verified"] is False
    assert verdict["blocked_edits"] == ["tests/test_pricing.py"]
    assert verdict["claim_rejected"] == "blocked edit: tests/test_pricing.py"
    assert ch01.REPO["tests/test_pricing.py"] == ch01.TEST_SPEC


def test_ch1_harnessed_accepts_honest_patch():
    verdict = ch01.harnessed(ch01.TASK, model=ch01.honest_model)
    assert verdict["verified"] is True
    assert verdict["status"] == "verified"
    assert verdict["failed"] == []
    assert verdict["blocked_edits"] == []
    assert verdict["claim_rejected"] is None


def test_ch1_model_cannot_mutate_stored_repo():
    def vandal(ctx):
        ctx["files"]["pricing.py"] = "# vandalized"
        ctx["files"]["tests/test_pricing.py"] = "# vandalized"
        return ch01.honest_model(ctx)

    verdict = ch01.harnessed(ch01.TASK, model=vandal)
    assert verdict["verified"] is True
    assert ch01.REPO["pricing.py"] == ch01.PRICING_BUGGY
    assert ch01.REPO["tests/test_pricing.py"] == ch01.TEST_SPEC


@pytest.mark.parametrize("claim, expected", [
    ("not a dict", "claim is not an object"),
    ({"fixed": True}, "claim lists no checks"),
    ({"fixed": True, "checks": {"happy_path": "pass", "zero_pct": "pass",
                                "reject_neg": "pass", "reject_over": "pass",
                                "extra": "pass"}}, "claim names unknown checks"),
    ({"fixed": True, "checks": {"happy_path": "pass", "zero_pct": "pass",
                                "reject_neg": "pass", "reject_over": "pass"}},
     "claim contradicts reject_over"),
    ({"fixed": 1, "checks": {"happy_path": "pass", "zero_pct": "pass",
                             "reject_neg": "pass", "reject_over": "fail"}},
     "fixed is not true or false"),
    ({"fixed": True, "checks": {"happy_path": "pass", "zero_pct": "pass",
                                "reject_neg": "pass", "reject_over": "fail"}},
     "fixed claimed with failing checks"),
])
def test_ch1_check_claim_edges(claim, expected):
    results = {"happy_path": "pass", "zero_pct": "pass",
               "reject_neg": "pass", "reject_over": "fail"}
    assert ch01.check_claim(claim, results, []) == expected


def test_ch1_live_adapter_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("AHE_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="AHE_LIVE=1"):
        ch01.chat("ping")


def test_ch1_parse_json_extracts_fenced_object():
    assert ch01.parse_json('```json\n{"fixed": true}\n```') == {"fixed": True}
    with pytest.raises(ValueError):
        ch01.parse_json("no braces at all")


# ---------------------------------------------------------------- Chapter 2

def test_ch2_fixed_workflow_solves_single_file_but_fails_invoice():
    single = ch02.fixed_patch(ch02.TASK_SINGLE, ch02.WORKSPACE)
    assert single["verified"] is True
    invoice = ch02.fixed_patch(ch02.TASK_INVOICE, ch02.WORKSPACE)
    assert invoice["verified"] is False
    assert invoice["failed"] == ["invoice_none"]


def test_ch2_smart_planner_completes_two_file_contract():
    run = ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.smart_planner)
    assert run.state == "completed"
    assert run.reason == "verified"
    assert run.verified_rev == run.rev
    assert set(run.last_checks.values()) == {"pass"}
    assert ch02.WORKSPACE["pricing.py"] == ch01.PRICING_BUGGY


def test_ch2_stale_verification_cannot_complete_run():
    run = ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.stale_editor)
    assert run.state == "blocked"
    assert run.reason == "step budget spent"
    stale_errors = [
        e["result"]["error"] for e in run.events
        if e["kind"] == "observation" and e.get("tool") == "finish"
    ]
    assert stale_errors and all(e == "stale verification after edit" for e in stale_errors)


def test_ch2_protected_tests_cannot_be_written():
    run = ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.test_tamperer)
    assert run.state == "blocked"
    assert run.edits_used == 0
    assert run.files["tests/test_pricing.py"] == ch01.TEST_SPEC
    gate_reasons = [e["reason"] for e in run.events
                    if e["kind"] == "gate" and not e["allowed"]]
    assert gate_reasons and all("protected path" in r for r in gate_reasons)


def test_ch2_five_named_endings():
    # 1. refused (before run starts)
    bad = ch02.Contract(task_id="X", goal="g", budget=ch02.Budget(steps=0))
    assert ch02.run_loop(bad, ch02.WORKSPACE, ch02.smart_planner)["status"] == "refused"
    # 2. completed
    assert ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.smart_planner).state == "completed"
    # 3. blocked
    assert ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.loopy_planner).state == "blocked"
    # 4. failed
    failed = ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.crashing_planner)
    assert failed.state == "failed" and "RuntimeError" in failed.reason
    # 5. cancelled
    cancelled = ch02.run_loop(
        ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.smart_planner,
        cancel=lambda r: r.steps_used >= 2,
    )
    assert cancelled.state == "cancelled" and cancelled.steps_used == 2


def test_ch2_gate_rejects_bad_types_and_unknown_tools():
    run = ch02.run_loop(ch02.TASK_INVOICE, ch02.WORKSPACE, ch02.bad_tool_planner)
    assert run.state == "blocked"
    reasons = [e["reason"] for e in run.events if e["kind"] == "gate" and not e["allowed"]]
    assert reasons[0] == "unknown tool: 'shell'"
    assert reasons[1] == "path must be str"


def test_ch2_record_is_monotonic_and_cannot_be_overwritten_by_planner():
    run = ch02.run_loop(
        ch02.TASK_INVOICE, ch02.WORKSPACE,
        lambda ctx: {"tool": "finish", "seq": 999, "kind": "stop"},
    )
    seqs = [e["seq"] for e in run.events]
    assert seqs == list(range(1, len(run.events) + 1))


# ---------------------------------------------------------------- Chapter 3

def test_ch3_prefix_hash_is_byte_identical_across_every_step():
    res = ch03.compare_strategies(char_budget=2400)
    run = res["run"]
    assert run.state == "completed"
    ctx_events = [e for e in run.events if e["kind"] == "context"]
    assert len(ctx_events) == 9
    hashes = {e["prefix_hash"] for e in ctx_events}
    assert len(hashes) == 1
    assert all(e["total_chars"] <= 2400 for e in ctx_events)
    assert any(e["omitted_seqs"] for e in ctx_events)


def test_ch3_over_budget_stops_run_cleanly_instead_of_truncating():
    run = ch03.run_v3(
        ch03.TASK_LARGE, ch03.LARGE_WORKSPACE,
        ch03.scripted_large_planner(), char_budget=250,
    )
    assert run.state == "blocked"
    assert run.reason == "context budget exceeded"


def test_ch3_prefix_stable_beats_front_rebuilt_and_respects_budget():
    res = ch03.compare_strategies(char_budget=2400)
    naive = res["naive_transcript"]
    front = res["rebuild_front"]
    stable = res["prefix_stable"]

    assert naive["over_budget"] > 0
    assert naive["max_chars"] > 2400
    assert front["over_budget"] == 0
    assert stable["over_budget"] == 0
    assert stable["cache_hit_pct"] > 35.0
    assert front["cache_hit_pct"] < 5.0
    assert stable["uncached_chars"] < front["uncached_chars"]


def test_ch3_archive_inspect_log_and_read_lines_work():
    res = ch03.compare_strategies(char_budget=2400)
    run = res["run"]
    ctx_events = [e for e in run.events if e["kind"] == "context" and e["omitted_seqs"]]
    evicted_seq = ctx_events[0]["omitted_seqs"][0]
    fetched = ch03.t_inspect_log(run, evicted_seq, 0, 120)
    assert fetched["seq"] == evicted_seq
    assert len(fetched["slice"]) == 120
    assert fetched["more"] is True

    lines = ch03.t_read_lines(run, "docs/billing_spec.md", 1, 5)
    assert len(lines["lines"]) == 5
    assert lines["more"] is True
    assert lines["lines"][0].startswith("Section 01:")


def test_ch3_gate_rejects_bool_as_int_for_line_and_log_tools():
    res = ch03.compare_strategies(char_budget=2400)
    run = res["run"]
    run.state = "running"
    assert ch03.gate_v3(run, {"tool": "read_lines",
                              "args": {"path": "pricing.py",
                                       "start": True, "limit": 5}}) == "start must be int"
    assert ch03.gate_v3(run, {"tool": "inspect_log",
                              "args": {"seq": -1, "start": 0, "limit": 50}}) == "seq must be >= 0"
