"""Verification suite for v2 Part III (Chapters 7, 8, and 9)."""

import datetime as dt
import re
from pathlib import Path
import pytest

from ahe import ch02, ch07, ch08, ch09

CODE_DIR = Path(__file__).resolve().parents[1] / "ahe"
MARK = re.compile(r"# listing (\d+\.\d+)\n(.*?)# end listing", re.S)


def test_all_v2_part3_listings_fit_60_chars():
    checked = 0
    for name in ("ch07.py", "ch08.py", "ch09.py"):
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
# Chapter 7: Durable execution and long-running sessions
# ---------------------------------------------------------------------------
def test_ch7_crash_mid_intent_resumes_with_zero_duplicate_model_calls(
        tmp_path):
    db = tmp_path / "durable.sqlite"
    store1 = ch07.DurableStore(db)
    r1 = ch07.DurableRunner(store1)
    run_id = "run-crash-mid-intent"

    with pytest.raises(ch07.SimulatedCrash):
        for idx in range(10):
            run, _ = r1.step(
                run_id, ch02.TASK_INVOICE, ch02.WORKSPACE,
                ch07._v4_fix_planner, idx,
                crash_after_intent=(idx == 4),
            )
            if run.state != "running":
                break
    calls_before = r1.model_calls
    assert calls_before == 5  # steps 0, 1, 2, 3, 4
    store1.close()

    # Fresh process / connection opens the same SQLite DB
    store2 = ch07.DurableStore(db)
    r2 = ch07.DurableRunner(store2)
    final_run = None
    for idx in range(10):
        final_run, _ = r2.step(
            run_id, ch02.TASK_INVOICE, ch02.WORKSPACE,
            ch07._v4_fix_planner, idx,
        )
        if final_run.state != "running":
            break

    assert final_run.state == "completed"
    # Steps 0..3 replayed from SQLite without calling model or tools
    assert r2.replayed_steps == 4
    # Step 4 intent resumed without re-calling model; only steps 5 & 6 called
    assert calls_before + r2.model_calls == 7
    commits = store2.conn.execute(
        "SELECT COUNT(*) FROM commits WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    assert commits == 1
    store2.close()


def test_ch7_crash_after_outcome_never_repeats_commit_or_model(tmp_path):
    db = tmp_path / "outcome.sqlite"
    store1 = ch07.DurableStore(db)
    r1 = ch07.DurableRunner(store1)
    run_id = "run-crash-after-commit"

    with pytest.raises(ch07.SimulatedCrash):
        for idx in range(10):
            run, _ = r1.step(
                run_id, ch02.TASK_INVOICE, ch02.WORKSPACE,
                ch07._v4_fix_planner, idx,
                crash_after_outcome=(idx == 5),
            )
            if run.state != "running":
                break
    store1.close()

    store2 = ch07.DurableStore(db)
    r2 = ch07.DurableRunner(store2)
    for idx in range(10):
        final_run, _ = r2.step(
            run_id, ch02.TASK_INVOICE, ch02.WORKSPACE,
            ch07._v4_fix_planner, idx,
        )
        if final_run.state != "running":
            break
    assert final_run.state == "completed"
    assert r2.replayed_steps == 6
    assert r1.model_calls + r2.model_calls == 7
    commits = store2.conn.execute(
        "SELECT COUNT(*) FROM commits WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    assert commits == 1
    store2.close()


def test_ch7_non_idempotent_intent_routes_to_needs_reconcile(tmp_path):
    db = tmp_path / "reconcile.sqlite"
    store = ch07.DurableStore(db)
    r1 = ch07.DurableRunner(store)
    def webhook_planner(run):
        return {"tool": "send_webhook", "args": {"url": "https://x"}}

    with pytest.raises(ch07.SimulatedCrash):
        r1.step("run-wh", ch02.TASK_INVOICE, ch02.WORKSPACE,
                webhook_planner, 0, crash_after_intent=True)

    r2 = ch07.DurableRunner(store)
    run, res = r2.step("run-wh", ch02.TASK_INVOICE, ch02.WORKSPACE,
                       webhook_planner, 0)
    assert run.state == "needs_reconcile"
    assert "error" in res
    assert r2.model_calls == 0
    store.close()


def test_ch7_commit_once_detects_key_conflict_and_replays_identical():
    store = ch07.DurableStore(":memory:")
    calls = {"n": 0}
    def effect():
        calls["n"] += 1
        return {"key": "k1", "files_hash": "h1", "commit_id": "dc-001"}

    first = store.commit_once("r1", "k1", "h1", effect)
    second = store.commit_once("r1", "k1", "h1", effect)
    conflict = store.commit_once("r1", "k1", "h2", effect)
    assert first["replayed"] is False and first["commit_id"] == "dc-001"
    assert second["replayed"] is True and second["commit_id"] == "dc-001"
    assert conflict["error"] == "idempotency key conflict"
    assert calls["n"] == 1
    store.close()


def test_ch7_task_ledger_persists_across_sessions(tmp_path):
    db = tmp_path / "ledger.sqlite"
    s1 = ch07.DurableStore(db)
    l1 = ch07.TaskLedger(s1)
    l1.upsert("r-long", ch07.LedgerItem(
        "m1", "Fix pricing guard", "verified", "rev1"))
    l1.upsert("r-long", ch07.LedgerItem(
        "m2", "Fix invoice sum", "in_progress", "rev1"))
    with pytest.raises(ValueError):
        l1.upsert("r-long", ch07.LedgerItem(
            "m3", "Bad", "unknown", "rev1"))
    s1.close()

    s2 = ch07.DurableStore(db)
    l2 = ch07.TaskLedger(s2)
    summary = l2.resume_summary("r-long")
    assert summary["verified"] == [{"id": "m1", "rev": "rev1"}]
    assert len(summary["remaining"]) == 1
    assert summary["remaining"][0]["id"] == "m2"
    s2.close()


def test_ch7_codex_hardening_atomic_contract_order_budget_and_hostile_inputs():
    store = ch07.DurableStore(":memory:")
    r = ch07.DurableRunner(store)

    # Blank/None run_id rejected
    with pytest.raises(ValueError):
        store.load_run(None, ch02.TASK_INVOICE)

    # Out-of-order step_index rejected
    with pytest.raises(ValueError, match="expected step 0"):
        r.step("r-ord", ch02.TASK_INVOICE, ch02.WORKSPACE,
               ch07._v4_fix_planner, 5)

    # Hostile proposals ({'tool': []}, oversized 50KB) safely refused & bounded
    _, res_bad = r.step("r-hostile", ch02.TASK_INVOICE, ch02.WORKSPACE,
                        lambda run: {"tool": []}, 0)
    assert "error" in res_bad
    _, res_big = r.step("r-hostile", ch02.TASK_INVOICE, ch02.WORKSPACE,
                        lambda run: {"tool": "read_lines",
                                     "args": {"path": "x" * 50000}}, 1)
    assert "size cap" in res_big["error"]
    saved = store.get_step("r-hostile", 1)
    assert len(str(saved["proposal"])) < 200

    # Contract mismatch on load rejected
    other = ch02.Contract(task_id="OTHER-99", goal="other")
    with pytest.raises(ValueError, match="contract mismatch"):
        store.load_run("r-hostile", other)

    # Budget check prevents calling model past step cap
    tiny = ch02.Contract(task_id="TINY", goal="t",
                         budget=ch02.Budget(steps=1, edits=1))
    r_tiny = ch07.DurableRunner(store)
    r_tiny.step("r-tiny", tiny, ch02.WORKSPACE, ch07._v4_fix_planner, 0)
    calls_at_cap = r_tiny.model_calls
    run_cap, res_cap = r_tiny.step("r-tiny", tiny, ch02.WORKSPACE,
                                   ch07._v4_fix_planner, 1)
    assert r_tiny.model_calls == calls_at_cap
    assert run_cap.state == "blocked"
    assert "step budget" in res_cap["error"]
    store.close()


def test_ch7_benchmark_crash_recovery_beats_naive():
    rows = ch07.benchmark_crash_recovery((2, 4, 6))
    assert len(rows) == 3
    for row in rows:
        assert row["final_state"] == "completed"
        assert row["durable_calls"] == 7
        assert row["durable_calls"] < row["naive_calls"]
        assert row["durable_commits"] == 1


# ---------------------------------------------------------------------------
# Chapter 8: State, memory, and workspace knowledge
# ---------------------------------------------------------------------------
def test_ch8_supersession_preserves_history_and_isolates_tenants():
    probes = ch08.run_memory_probes()
    assert probes["seeded"] == 1
    assert probes["accepted_update"] == 1
    assert probes["active_keys"] == [("mem-2", "max_pct=30")]
    assert probes["history_len"] == 2
    assert probes["history_statuses"] == ["superseded", "active"]
    assert probes["after_ttl_count"] == 0
    assert probes["cross_tenant_leaks"] == 0


def test_ch8_consolidation_blocks_ungrounded_and_policy_mutations():
    probes = ch08.run_memory_probes()
    reasons = dict(probes["rejected_candidates"])
    assert reasons["mem-bad-1"] == "ungrounded provenance"
    assert reasons["mem-bad-2"] == "cannot consolidate into procedural"
    assert reasons["mem-bad-3"] == "policy-mutation phrase blocked"


def test_ch8_workspace_truth_overrides_stale_memory_and_keeps_prefix():
    t0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    store = ch08.MemoryStore(":memory:")
    ctx_empty = ch08.assemble_with_memory(
        ch02.TASK_INVOICE, ch02.WORKSPACE, store,
        "tenant-a", "billing", now=t0,
    )
    store.put(ch08.MemoryRecord(
        memory_id="m1", tenant_id="tenant-a", repo_id="billing",
        plane="episodic", key="discount_cap", content="max_pct=20",
        source_event_id="ev-1",
        created_at="2026-11-01T00:00:00+00:00",
        expires_at="2026-12-01T00:00:00+00:00",
    ))
    ctx_conflict = ch08.assemble_with_memory(
        ch02.TASK_INVOICE, ch02.WORKSPACE, store,
        "tenant-a", "billing",
        workspace_facts={"discount_cap": "max_pct=100"},
        now=t0,
    )
    assert ctx_empty["prefix_hash"] == ctx_conflict["prefix_hash"]
    assert ctx_conflict["memories_included"] == 0
    assert ctx_conflict["conflicts"][0]["winner"] == "workspace_truth"
    store.close()


def test_ch8_cross_scope_supersede_is_blocked():
    store = ch08.MemoryStore(":memory:")
    store.put(ch08.MemoryRecord(
        memory_id="m1", tenant_id="tenant-a", repo_id="billing",
        plane="episodic", key="k", content="v1",
        source_event_id="ev-1",
        created_at="2026-11-01T00:00:00+00:00",
        expires_at="2026-12-01T00:00:00+00:00",
    ))
    with pytest.raises(ValueError, match="cross-scope"):
        store.supersede("m1", ch08.MemoryRecord(
            memory_id="m2", tenant_id="tenant-b", repo_id="billing",
            plane="episodic", key="k", content="v2",
            source_event_id="ev-2",
            created_at="2026-11-02T00:00:00+00:00",
            expires_at="2026-12-02T00:00:00+00:00",
            supersedes="m1",
        ))
    # Atomic rollback: colliding memory_id leaves m1 active
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.supersede("m1", ch08.MemoryRecord(
            memory_id="m1", tenant_id="tenant-a", repo_id="billing",
            plane="episodic", key="k", content="v2",
            source_event_id="ev-2",
            created_at="2026-11-02T00:00:00+00:00",
            expires_at="2026-12-02T00:00:00+00:00",
            supersedes="m1",
        ))
    assert len(store.query("tenant-a", "billing",
                           now=dt.datetime(2026, 11, 10,
                                           tzinfo=dt.timezone.utc))) == 1

    # UTC normalization: +02:00 expiry already passed at 00:00 UTC
    store.put(ch08.MemoryRecord(
        memory_id="m-tz", tenant_id="tenant-a", repo_id="billing",
        plane="episodic", key="tz", content="short",
        source_event_id="ev-1",
        created_at="2026-11-01T00:00:00+00:00",
        expires_at="2026-11-02T01:00:00+02:00",  # 2026-11-01T23:00Z
    ))
    at_mid = dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.timezone.utc)
    ids = [m.memory_id for m in store.query("tenant-a", "billing", now=at_mid)]
    assert "m-tz" not in ids

    # Hard OverBudget when mandatory context exceeds cap
    from ahe.ch03 import OverBudget
    with pytest.raises(OverBudget):
        ch08.assemble_with_memory(
            ch02.TASK_INVOICE, ch02.WORKSPACE, store,
            "tenant-a", "billing", char_budget=50, now=at_mid)
    store.close()


# ---------------------------------------------------------------------------
# Chapter 9: Delegation, sub-agents, and A2A interop
# ---------------------------------------------------------------------------
def test_ch9_subagent_isolates_context_and_enforces_budgets():
    ws = ch09.make_noisy_workspace()
    before_rev = ch02.workspace_rev(ws)
    ctrl = ch09.DelegationController(
        ch09.SubAgentBudget(max_depth=1, max_fanout=2,
                            max_steps=4, max_output_chars=120)
    )

    # Attempt to widen capability with write tool is narrowed away
    def rogue_child(run, goal):
        return {"tool": "draft_patch",
                "args": {"path": "pricing.py", "content": "x"}}

    r1 = ctrl.delegate(
        ws, ch02.TASK_INVOICE,
        ["list_files", "read_lines", "draft_patch", "finish"],
        "inspect", rogue_child,
        requested_tools=["draft_patch", "read_lines"],
    )
    assert r1["ok"] is True
    assert "blocked:draft_patch" in r1["summary"]
    assert ch02.workspace_rev(ws) == before_rev

    r2 = ctrl.delegate(
        ws, ch02.TASK_INVOICE,
        ["list_files", "read_lines", "finish"],
        "inspect2", ch09._readonly_inspector,
    )
    assert r2["ok"] is True
    assert len(r2["summary"]) <= 120

    # 3rd delegation exceeds max_fanout=2
    r3 = ctrl.delegate(
        ws, ch02.TASK_INVOICE,
        ["list_files", "read_lines", "finish"],
        "inspect3", ch09._readonly_inspector,
    )
    assert r3["error"] == "fan-out budget exceeded"

    # Nested controller at depth=1 exceeds max_depth=1
    nested = ch09.DelegationController(
        ch09.SubAgentBudget(max_depth=1), depth=1)
    r_deep = nested.delegate(
        ws, ch02.TASK_INVOICE, ["list_files"],
        "nested", ch09._readonly_inspector,
    )
    assert r_deep["error"] == "depth budget exceeded"


def test_ch9_context_isolation_benchmark_shrinks_parent_bloat():
    bench = ch09.benchmark_context_isolation()
    assert bench["prefix_hash_preserved"] is True
    assert bench["delegated_raw_chars"] < bench["inline_raw_chars"]
    assert bench["inline_evicted"] > bench["delegated_evicted"]
    assert bench["delegated_evicted"] == 0


def test_ch9_generator_evaluator_split_blocks_tampering_and_converges():
    def generator(files, feedback, round_idx):
        if round_idx == 1:
            # Round 1 fixes pricing only and tries to tamper with tests
            files["pricing.py"] = ch02.PRICING_FIXED
            files["tests/test_pricing.py"] = "# tampered"
            return files
        # Round 2 reacts to evaluator feedback and fixes invoice.py
        files["invoice.py"] = ch02.INVOICE_FIXED
        return files

    res = ch09.generator_evaluator_loop(
        ch02.TASK_INVOICE, ch02.WORKSPACE, generator,
        evaluator_checks={
            "no_bare_except": lambda f: (
                "bare except" if "except:" in f["pricing.py"]
                else None
            ),
        },
        max_rounds=3,
    )
    assert res["status"] == "completed"
    assert res["rounds"] == 2
    assert res["history"][0].passed is False
    assert res["history"][1].passed is True
    assert res["files"]["tests/test_pricing.py"] == (
        ch02.WORKSPACE["tests/test_pricing.py"]
    )


def test_ch9_a2a_server_card_and_opaque_task_lifecycle():
    card = ch09.AgentCard(
        name="ahe-inspector", version="0.9.0")

    def worker(skill, inp):
        # Internal prompt/steps never returned in artifact
        return {"skill": skill, "files_checked": len(inp.get("files", [])),
                "status": "clean"}

    srv = ch09.A2AServer(card, worker)
    c_resp = srv.handle({"jsonrpc": "2.0", "id": 1,
                         "method": "agent/card"})
    assert c_resp["result"]["protocol_version"] == "1.0.0"
    assert "workspace.inspect" in c_resp["result"]["skills"]

    s_resp = srv.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tasks/send",
        "params": {"skill": "workspace.inspect",
                   "input": {"files": ["pricing.py"]}},
    })
    tid = s_resp["result"]["id"]
    assert s_resp["result"]["state"] == "completed"
    assert s_resp["result"]["artifact"]["data"]["files_checked"] == 1
    assert "prompt" not in s_resp["result"]

    g_resp = srv.handle({"jsonrpc": "2.0", "id": 3,
                         "method": "tasks/get",
                         "params": {"id": tid}})
    assert g_resp["result"]["id"] == tid

    bad_skill = srv.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tasks/send",
        "params": {"skill": "shell.exec", "input": {}},
    })
    assert bad_skill["error"]["code"] == -32602


def test_ch9_recorded_live_evaluator_probe_matches_claims():
    import json
    from ahe import ch01
    path = (Path(__file__).resolve().parents[1]
            / "runs" / "v2_ch09_evaluator_gpt-6-luna.jsonl")
    recs = [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]
    calls = [r for r in recs if "cost_usd" in r]
    outs = [r["outcome"] for r in recs if "outcome" in r]
    assert len(calls) == 10
    assert len(outs) == 5
    # True replay: recompute parsed verdicts from raw model replies
    for idx in range(5):
        p_coupled = ch01.parse_json(calls[idx * 2]["reply"]) or {}
        p_iso = ch01.parse_json(calls[idx * 2 + 1]["reply"]) or {}
        assert bool(p_coupled.get("passed")) is False
        assert bool(p_iso.get("passed")) is False
        assert outs[idx]["harness_passed"] is False
        assert outs[idx]["harness_failed"] == ["invoice_none"]


def test_ch9_codex_hardening_empty_tools_deletion_restore_and_a2a_errors():
    ws = ch09.make_noisy_workspace()
    ctrl = ch09.DelegationController(
        ch09.SubAgentBudget(max_output_chars=40))

    # Explicit empty requested_tools=[] must NOT fall back to defaults
    empty = ctrl.delegate(ws, ch02.TASK_INVOICE, ["list_files"],
                          "g", ch09._readonly_inspector,
                          requested_tools=[])
    assert empty["error"] == "no permitted read-only tools"

    # Oversized summary is actually truncated to max_output_chars=40
    big = ctrl.delegate(ws, ch02.TASK_INVOICE, ["finish"], "g",
                        lambda r, g: {"tool": "finish", "args": {},
                                      "summary": "A" * 200})
    assert len(big["summary"]) == 40
    assert big["child_steps"] == 1

    # Generator deleting tests/test_pricing.py has it restored from baseline
    def deleter(files, fb, rnd):
        return {"pricing.py": ch02.PRICING_FIXED,
                "invoice.py": ch02.INVOICE_FIXED}  # omitted tests/

    res = ch09.generator_evaluator_loop(
        ch02.TASK_INVOICE, ch02.WORKSPACE, deleter, max_rounds=1)
    assert "tests/test_pricing.py" in res["files"]
    assert res["files"]["tests/test_pricing.py"] == (
        ch02.WORKSPACE["tests/test_pricing.py"])

    # A2AServer rejects non-dict params, non-str id, and masks raw errors
    srv = ch09.A2AServer(ch09.AgentCard("a", "1"),
                         lambda s, i: (_ for _ in ()).throw(
                             RuntimeError("secret db pw")))
    assert srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
                       "params": [1]})["error"]["code"] == -32602
    assert srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                       "params": {"id": []}})["error"]["code"] == -32602
    fail = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tasks/send",
                       "params": {"skill": "workspace.inspect", "input": {}}})
    assert fail["result"]["state"] == "failed"
    assert "secret db pw" not in fail["result"]["error"]
