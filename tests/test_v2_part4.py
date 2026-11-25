"""Verification suite for v2 Part IV (Chapters 10, 11, and 12)."""

import json
import re
from pathlib import Path
import pytest

from ahe import ch02, ch10, ch11, ch12

CODE_DIR = Path(__file__).resolve().parents[1] / "ahe"
MARK = re.compile(r"# listing (\d+\.\d+)\n(.*?)# end listing", re.S)


def test_all_v2_part4_listings_fit_60_chars():
    checked = 0
    for name in ("ch10.py", "ch11.py", "ch12.py"):
        text = (CODE_DIR / name).read_text()
        for num, body in MARK.findall(text):
            checked += 1
            for lineno, line in enumerate(body.splitlines(), 1):
                assert len(line) <= 60, (
                    f"{name} listing {num} line {lineno} "
                    f"is {len(line)} chars (>60): {line!r}"
                )
    assert checked == 9


# ---------------------------------------------------------------------------
# Chapter 10: Tracing, evaluation harnesses, and release gates
# ---------------------------------------------------------------------------
def test_ch10_otel_trace_exporter_emits_valid_genai_spans():
    exp = ch10.TraceExporter(harness_version="v2.10")
    s1 = exp.emit("tr-01", "chat", "openai/gpt-6-luna",
                  input_tokens=320, output_tokens=64,
                  cost_usd=0.0004, duration_ms=410.5)
    s2 = exp.emit("tr-01", "execute_tool", "openai/gpt-6-luna",
                  duration_ms=12.3, parent_span_id=s1.span_id)
    lines = exp.to_jsonl().splitlines()
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1["name"] == "gen_ai.chat"
    assert rec1["attributes"]["gen_ai.usage.input_tokens"] == 320
    assert rec1["attributes"]["ahe.harness.version"] == "v2.10"
    assert rec2["parent_span_id"] == s1.span_id


def test_ch10_pass_at_k_vs_pass_pow_k_and_plausible_summary_scores_zero():
    # 1 pass out of 3 trials -> pass@3=1.0, pass^3=1/27 (~0.037)
    assert ch10.pass_at_k([True, False, False], 3) == 1.0
    assert abs(ch10.pass_pow_k([True, False, False], 3) - (1 / 27)) < 1e-6

    # Plausible summary claiming fixed while failing hidden checks scores 0
    cases = ch10.make_eval_cases()
    def bluff_runner(case, trial):
        bad = dict(case.files)
        bad["tests/test_pricing.py"] = "# always pass"
        return {"files": bad, "summary": "All fixed!", "cost_usd": 0.001}

    rep = ch10.run_eval_suite(cases, bluff_runner, split="heldout",
                              trials=3, k=3)
    assert rep["passed_trials"] == 0
    assert rep["total_trials"] == 9
    assert rep["pass_at_k"] == 0.0
    assert rep["pass_pow_k"] == 0.0
    assert rep["cost_per_success"] is None


def test_ch10_release_gate_blocks_overfit_dev_and_ships_hardened():
    bench = ch10.benchmark_release_candidates()
    assert bench["overfit"]["dev"]["pass_pow_k"] == 1.0
    assert bench["overfit"]["heldout"]["pass_pow_k"] < 0.1
    assert bench["overfit"]["gate"]["ship"] is False
    assert any("critical regression" in r
               for r in bench["overfit"]["gate"]["reasons"])

    # Dev split alone is rejected by release gate
    dev_gate = ch10.evaluate_release(
        bench["baseline"]["heldout"], bench["overfit"]["dev"])
    assert dev_gate["ship"] is False
    assert "gate requires heldout split" in dev_gate["reasons"]

    assert bench["hardened"]["heldout"]["pass_pow_k"] == 1.0
    assert bench["hardened"]["gate"]["ship"] is True


# ---------------------------------------------------------------------------
# Chapter 11: Self-improving and meta-harnesses
# ---------------------------------------------------------------------------
def test_ch11_evolution_gate_blocks_tampering_and_cost_bloat_promotes_modular():
    out = ch11.benchmark_evolution_cycle()
    assert out["diagnosis"]["dominant"] == "gate_thrashing"
    assert out["diagnosis"]["recommended_surface"] == "tool_refusal"

    p_verbose, p_tamper, p_teach = out["proposals"]
    # Verbose prompt exceeded cost cap -> rejected
    assert p_verbose["promoted"] is False
    # Tampering with evaluator -> blocked before running
    assert p_tamper["promoted"] is False
    assert "evaluator" in p_tamper["reason"]
    # Modular refusal patch improved held-out pass^3 within cap -> promoted
    assert p_teach["promoted"] is True
    assert p_teach["pass_pow_k"] == 1.0
    assert p_teach["pass_pow_k"] > out["baseline"]["pass_pow_k"]
    assert out["final_config"].tool_refusal == "teaching"


def test_ch11_locked_surfaces_cannot_be_targeted():
    for locked in ("evaluator", "containment", "protected_tests"):
        prop = ch11.EvolutionProposal("p-x", locked, "off", "bypass")
        err = ch11.validate_proposal(prop)
        assert err is not None and "locked" in err


def test_ch11_runtime_evaluator_mutation_triggers_checksum_drift():
    cases = ch10.make_eval_cases()
    gate = ch11.EvolutionGate(cases, ch11._sim_runner_factory)
    # Tamper directly with held-out protected test after init
    held = next(c for c in gate.cases if c.split == "heldout")
    held.files["tests/test_pricing.py"] = "# sabotaged"
    with pytest.raises(RuntimeError, match="checksum drift"):
        gate.evaluate(ch11.HarnessConfig())


# ---------------------------------------------------------------------------
# Chapter 12: Domain transfer, versioned ops, and Assumption Ledger
# ---------------------------------------------------------------------------
def test_ch12_workspace_hls_and_finance_run_on_unchanged_kernel():
    results = ch12.run_all_domain_transfers()
    by_dom = {r["domain"]: r for r in results}
    assert set(by_dom) == {"workspace", "hls", "finance"}
    for name, res in by_dom.items():
        assert res["tamper_blocked"] == "policy denied", name
        assert res["committed"] is True, name
        assert res["state"] == "completed", name
        assert all(v == "pass" for v in res["checks"].values()), name


def test_ch12_model_upgrade_gate_links_versions_and_blocks_regression():
    cases = ch10.make_eval_cases()
    old_s = ch12.VersionStamp("v2.12", "model-2026", "h-abc", "pol-2026.1")
    new_s = ch12.VersionStamp("v2.12", "model-2027", "h-abc", "pol-2026.1")

    good = lambda c, t: {"files": ch10._good_files(c.files),
                         "cost_usd": 0.002}
    flaky = lambda c, t: {
        "files": (ch10._good_files(c.files)
                  if t == 1 else ch10._partial_files(c.files)),
        "cost_usd": 0.002,
    }
    # Passing an iterator must still evaluate BOTH baseline and candidate
    ok_res = ch12.check_model_upgrade(iter(cases), old_s, new_s, good, good)
    bad_res = ch12.check_model_upgrade(cases, old_s, new_s, good, flaky)
    assert ok_res["ship"] is True
    assert ok_res["to_stamp"]["model_id"] == "model-2027"
    assert bad_res["ship"] is False

    # Changing harness_version at the same time is rejected
    bad_stamp = ch12.VersionStamp("v2.99", "model-2027", "h-abc", "pol-2026.1")
    with pytest.raises(ValueError, match="fixed harness/policy"):
        ch12.check_model_upgrade(cases, old_s, bad_stamp, good, good)


def test_ch12_assumption_ledger_retires_scaffold_keeps_control_planes():
    decisions = {d["control"]: d
                 for d in ch12.run_ledger_retirement_demo()}
    assert decisions["verbose_format_nudge"]["action"] == "retire"
    assert decisions["actionable_refusal_hints"]["action"] == "keep_for_now"
    assert decisions["sandbox_and_bound_approvals"]["action"] == "keep_permanent"
    assert decisions["deterministic_verifier"]["action"] == "keep_permanent"
    # Typo in kind must raise instead of retiring a control plane
    with pytest.raises(ValueError, match="bad kind"):
        ch12.decide_retirement(
            ch12.LedgerControl("x", "control-plane", "a", 0.9), 0.99, 0.99)


def test_ch11_recorded_live_evolution_run_matches_claims():
    from ahe import ch01
    path = (Path(__file__).resolve().parents[1]
            / "runs" / "v2_ch11_evolve_gpt-6-luna.jsonl")
    recs = [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]
    calls = [r for r in recs if "cost_usd" in r]
    outs = [r["outcome"] for r in recs if "outcome" in r]
    assert len(calls) == 5
    assert len(outs) == 5
    cases = ch10.make_eval_cases()
    # True replay: re-parse every raw model reply and re-run EvolutionGate
    for idx, call in enumerate(calls):
        parsed = ch01.parse_json(call["reply"]) or {}
        gate = ch11.EvolutionGate(cases, ch11._sim_runner_factory)
        prop = ch11.EvolutionProposal(
            f"replay-{idx}", str(parsed.get("surface", "")),
            parsed.get("new_value"), "replay")
        _, res = gate.try_proposal(ch11.HarnessConfig(), prop)
        assert parsed["surface"] == "tool_refusal"
        assert parsed["new_value"] == "teaching"
        assert res["promoted"] is True


def test_part4_codex_hardening_manifests_checksum_and_domain_verifiers():
    # Ch 10: runner mutating case.files cannot pollute grading; dropped case blocked
    cases = ch10.make_eval_cases()
    def mutator(case, trial):
        case.files["tests/test_pricing.py"] = "# mutated in place"
        return {"files": ch10._good_files(case.files), "cost_usd": 0.002}
    rep = ch10.run_eval_suite(cases, mutator, split="heldout", trials=3, k=3)
    assert rep["passed_trials"] == 9
    # Dropping HELD-01 from candidate report must be blocked
    partial_cand = dict(rep, per_case=[r for r in rep["per_case"]
                                       if r["case_id"] != "HELD-01"])
    g = ch10.evaluate_release(rep, partial_cand)
    assert g["ship"] is False and "case manifest mismatch" in g["reasons"]

    # Ch 11: out-of-range retry_budget=-1 rejected; swapping checks triggers drift
    assert ch11.validate_proposal(
        ch11.EvolutionProposal("p-neg", "retry_budget", -1, "bad")
    ) is not None
    gate = ch11.EvolutionGate(cases, ch11._sim_runner_factory)
    from dataclasses import replace
    for idx, c in enumerate(gate.cases):
        if c.split == "heldout":
            gate.cases[idx] = replace(
                c, contract=replace(c.contract, checks=lambda f: {"ok": "pass"}))
            break
    with pytest.raises(RuntimeError, match="checksum drift"):
        gate.evaluate(ch11.HarnessConfig())

    # Ch 12: Finance rejects forged leverage=0, missing footnote, list verdict, NaN
    from ahe.domains.finance.filing import FINANCE_WORKSPACE, verify_filing
    bad_lev = dict(FINANCE_WORKSPACE)
    bad_lev["analysis/verdict.json"] = json.dumps({
        "healthy": False, "covenant_breach": True,
        "leverage": 0, "citation": "Note 4 (Credit Facility)"})
    assert verify_filing(bad_lev)["covenant"] == "fail"
    no_fn = dict(FINANCE_WORKSPACE)
    del no_fn["filing/footnote_4.txt"]
    assert verify_filing(no_fn)["citation"] == "fail"
    bad_shape = dict(FINANCE_WORKSPACE)
    bad_shape["analysis/verdict.json"] = "[]"
    assert verify_filing(bad_shape)["valid_json"] == "fail"

    # Ch 12: HLS rejects contradictory summary claiming all criteria met
    hls = ch12.make_hls_plugin()
    contradict = dict(hls.files)
    contradict["packet.json"] = json.dumps({
        "eligible": None,
        "criteria": {"age": "met", "marker": "unknown"},
        "summary": "All criteria met.",
    })
    assert hls.contract.checks(contradict)["hls_claim"] == "fail"
