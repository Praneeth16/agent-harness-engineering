"""Chapter 5's guarantees, as assertions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ahe.domains.hls import ch05
from ahe.domains.hls.ch02 import request
from ahe.domains.hls.ch03 import start
from ahe.domains.hls.ch05 import (COORDINATOR, OUTSIDER, REVIEWER_A, Harness, Submissions,
                      check_args, describe, planner_v5)

ROOT = Path(__file__).resolve().parents[1]
P042 = request("P042", 3)


def test_tool_text_is_generated_from_the_specs():
    h = Harness(COORDINATOR, Submissions())
    full = describe(h.tools.values())
    assert '"page": int (page of results, from 1)' in full
    assert describe(h.tools.values(), full=False).splitlines()[0] == "- search_notes {terms, page}"


@pytest.mark.parametrize("args, what", [
    ({"terms": "warfarin", "page": 1}, "bad terms"),
    ({"terms": ["warfarin"], "page": "1"}, "bad page"),
    ({"terms": ["warfarin"], "page": 0}, "bad page"),
    ({"terms": ["warfarin"]}, "search_notes got"),
    ({"terms": ["warfarin"], "page": 1, "limit": 9}, "search_notes got"),
    ([], "args is not an object"),
])
def test_bad_arguments_get_errors_that_say_how_to_fix_them(args, what):
    tool = ch05.READ_TOOLS[0]
    problem = check_args(start(P042), tool, args)
    assert what in problem["error"] and problem["why"] and problem["fix"]


def test_search_is_paged():
    run = start(request("P041", 3))
    out = ch05.search(run, ["physiotherapy", "review", "routine", "dental"], 1)
    assert len(out["notes"]) == ch05.PAGE and out["more"] is True
    assert ch05.search(run, ["physiotherapy", "review", "routine", "dental"], 2)["page"] == 2


def test_a_coordinator_run_submits_once():
    backend = Submissions()
    run = Harness(COORDINATOR, backend).run(P042, planner_v5)
    assert run.state == "completed" and backend.calls == 1 and len(backend.stored) == 1
    assert run.receipt == "S0001"


def test_repeated_submits_store_one_packet():
    backend = Submissions()
    h = Harness(COORDINATOR, backend)
    run = h.run(P042, planner_v5)
    first = h.tools["submit_packet"].fn(run)
    again = h.tools["submit_packet"].fn(run)
    assert first == again == {"submitted": "S0001"}
    assert backend.calls == 3 and len(backend.stored) == 1


def test_stale_draft_cannot_be_submitted_or_finished():
    h = Harness(COORDINATOR, Submissions())
    run = h.run(P042, planner_v5)
    run.version += 1                          # new evidence arrives
    assert h.tools["submit_packet"].fn(run)["error"] == "draft is stale"


def test_reviewer_never_sees_or_reaches_submit():
    backend = Submissions()
    h = Harness(REVIEWER_A, backend)
    run = h.run(P042, planner_v5)
    assert run.state == "completed" and backend.calls == 0
    assert "submit_packet" not in h.visible(run)
    problem = h.admit(run, {"tool": "submit_packet", "args": {}})
    assert problem["error"] == "reviewer-a may not submit"


def test_outsider_is_refused_before_anything_is_read():
    out = Harness(OUTSIDER, Submissions()).run(P042, planner_v5)
    assert out == {"status": "refused", "reason": "reviewer-b not on T004"}


def test_identity_comes_from_the_principal_not_the_request():
    claims_a = request("P042", 3, by="reviewer-a")
    assert Harness(OUTSIDER, Submissions()).run(claims_a, planner_v5)["status"] == "refused"
    claims_b = request("P042", 3, by="reviewer-b")
    assert Harness(COORDINATOR, Submissions()).run(claims_b, planner_v5).state == "completed"


def test_a_hook_can_refuse_and_nothing_reaches_the_backend():
    backend = Submissions()
    def no_commits(run, tool, args, principal):
        if tool.effect == "commit":
            return ch05.refuse("commits paused", "maintenance window", "try later")
    h = Harness(COORDINATOR, backend, before=[no_commits])
    run = h.run(P042, planner_v5, budget=ch05.Budget(steps=12))
    assert backend.calls == 0
    assert any(e.get("refused", {}).get("error") == "commits paused" for e in run.events)


def test_audit_hook_records_only_calls_that_ran():
    log = []
    def deny_submit(run, tool, args, principal):
        if tool.effect == "commit":
            return ch05.refuse("paused", "maintenance", "later")
    h = Harness(COORDINATOR, Submissions(), before=[deny_submit], after=[ch05.audit(log)])
    h.run(P042, planner_v5, budget=ch05.Budget(steps=12))
    assert "submit_packet" not in [t for _, _, t, _ in log]
    log2 = []
    Harness(COORDINATOR, Submissions(), after=[ch05.audit(log2)]).run(P042, planner_v5)
    assert [t for _, _, t, _ in log2].count("submit_packet") == 1


def test_terse_errors_hide_the_reason_from_the_planner():
    h = Harness(COORDINATOR, Submissions(), teach=False)
    run = start(P042)
    h.step(run, lambda c: {"tool": "search_notes", "args": {"terms": "x", "page": 1}})
    assert run.events[-1] == {"error": "refused", "seq": run.events[-1]["seq"], "kind": "observation"}


def test_a_skill_cannot_grant_a_tool():
    skill = ch05.load_skill(ROOT / "skills/screen-anticoagulant-notes/SKILL.md")
    assert "submit_packet" in skill.wants
    run = start(P042)
    reviewer = ch05.with_skill(Harness(REVIEWER_A, Submissions()), skill)
    assert "submit_packet" not in reviewer.visible(run)
    assert "submit_packet" in ch05.with_skill(Harness(COORDINATOR, Submissions()), skill).visible(run)
    narrow = ch05.Skill("read-only", "x", frozenset({"search_notes"}), "")
    h = ch05.with_skill(Harness(COORDINATOR, Submissions()), narrow)
    assert h.visible(run) == ["search_notes"]
    assert "outside the skill" in h.admit(run, {"tool": "evaluate_rule", "args": {"rule_id": "age"}})["error"]


def mcp(msgs, patient="P042"):
    raw = "".join(m if isinstance(m, str) else json.dumps(m) + "\n" for m in msgs)
    done = subprocess.run([sys.executable, "-m", "ahe.domains.hls.mcp_server", patient], cwd=ROOT,
                          input=raw, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return {r["id"]: r for r in map(json.loads, done.stdout.splitlines())}


def call(i, name, args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


def test_mcp_calls_go_through_the_same_gate():
    replies = mcp([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                   {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                   call(3, "search_notes", {"terms": ["apixaban"], "page": 1}),
                   call(4, "submit_packet", {}),
                   call(5, "search_notes", {"terms": "apixaban", "page": 1})])
    names = [t["name"] for t in replies[2]["result"]["tools"]]
    assert "submit_packet" not in names and "search_notes" in names
    assert json.loads(replies[3]["result"]["content"][0]["text"])["notes"] == ["n58"]
    assert replies[4]["error"]["code"] == -32602 and "unknown tool" in replies[4]["error"]["message"]
    assert replies[5]["result"]["isError"] is True
    assert "bad terms" in replies[5]["result"]["content"][0]["text"]


def test_mcp_survives_malformed_messages_and_failing_tools():
    replies = mcp(["{not json\n",
                   call(2, "finish", {}),
                   call(3, "read_note", {"note_id": "n58"}),            # never searched
                   call(4, "search_notes", {"terms": ["apixaban"], "page": 1}),
                   call(5, "read_note", {"note_id": "n58"})])
    assert replies[None]["error"]["code"] == -32700
    assert replies[2]["error"]["code"] == -32602 and "result" not in replies[2]
    assert replies[3]["result"]["isError"] and "bad note_id" in replies[3]["result"]["content"][0]["text"]
    assert replies[5]["result"]["isError"] is False


def test_no_dispatch_after_the_run_ends_or_the_budget_is_spent():
    backend = Submissions()
    h = Harness(COORDINATOR, backend)
    run = h.run(P042, planner_v5)
    assert h.dispatch(run, {"tool": "submit_packet", "args": {}})["error"] == "run is over"
    fresh = start(P042, ch05.Budget(steps=0))
    assert h.dispatch(fresh, {"tool": "evaluate_rule", "args": {"rule_id": "age"}})["error"] == "no steps left"
    assert backend.calls == 1


def test_only_ids_a_search_returned_can_be_read():
    h = Harness(COORDINATOR, Submissions())
    run = start(request("P041", 3))
    assert h.admit(run, {"tool": "read_note", "args": {"note_id": "n25"}})
    h.dispatch(run, {"tool": "search_notes", "args": {"terms": ["physiotherapy", "review", "routine", "dental"], "page": 1}})
    assert len(run.hits) == ch05.PAGE
    assert h.admit(run, {"tool": "read_note", "args": {"note_id": sorted(run.hits)[0]}}) is None


def test_the_stored_packet_cannot_change_after_the_receipt():
    backend = Submissions()
    run = Harness(COORDINATOR, backend).run(P042, planner_v5)
    stored = next(iter(backend.stored.values()))[1]
    run.draft["patient"] = "P999"
    assert json.loads(next(iter(backend.stored.values()))[1])["patient"] == "P042" and stored


def test_the_same_request_run_twice_stores_one_packet():
    backend = Submissions()
    h = Harness(COORDINATOR, backend)
    a, b = h.run(P042, planner_v5), h.run(P042, planner_v5)
    assert a.receipt == b.receipt and len(backend.stored) == 1


def test_hostile_arguments_get_bounded_refusals():
    run = start(P042)
    tool = ch05.SEARCH
    assert check_args(run, tool, {"terms": ["x"], 1: 2})["error"].startswith("search_notes got")
    long = check_args(run, tool, {"terms": "x" * 10_000, "page": 1})
    assert len(long["error"]) < 100


def test_unassigned_reviewer_is_refused_before_version_lookup():
    for version in (3, 999):
        out = Harness(OUTSIDER, Submissions()).run(request("P042", version), planner_v5)
        assert out == {"status": "refused", "reason": "reviewer-b not on T004"}


def test_finish_needs_a_current_draft():
    h = Harness(COORDINATOR, Submissions())
    run = h.run(P042, planner_v5)
    fresh = start(P042)
    for rule in ("age", "marker", "no_anticoag"):
        h.dispatch(fresh, {"tool": "evaluate_rule", "args": {"rule_id": rule}})
    out = h.dispatch(fresh, {"tool": "finish"})
    assert out["error"] == "cannot finish" and out["why"] == "no current draft"
    assert fresh.state == "running"



# ---------------------------------------------------------------- full-repository review

def test_unlisted_drug_after_an_absence_goes_to_review(monkeypatch):
    from ahe.domains.hls import ch02
    monkeypatch.setitem(ch02.NOTES, "P037", [ch02.Note("n02", ch02.D(2026, 8, 31),
        "No anticoagulant therapy in the past year, reviewed 2026-08-30. "
        "Started fondaparinux on 2026-08-31.")])
    backend = Submissions()
    run = Harness(COORDINATOR, backend).run(request("P037", 3), planner_v5)
    assert run.results["no_anticoag"].status == "unknown"
    assert "medication" in run.results["no_anticoag"].reason


def test_unclassified_medication_note_blocks_a_clean_met(monkeypatch):
    from ahe.domains.hls import ch02
    monkeypatch.setitem(ch02.NOTES, "P037", ch02.NOTES["P037"] + [
        ch02.Note("n99", ch02.D(2026, 8, 31), "Started fondaparinux 2.5 mg daily on 2026-08-31.")])
    got = ch02.prepare(request("P037", 3))["criteria"]["no_anticoag"]
    assert got.status == "unknown" and "unclassified medication" in got.reason


def test_a_failing_after_hook_does_not_undo_a_submit():
    backend = Submissions()
    def broken(run, tool, args, principal, result):
        raise RuntimeError("audit unavailable")
    run = Harness(COORDINATOR, backend, after=[broken]).run(P042, planner_v5)
    assert run.state == "completed" and len(backend.stored) == 1
    assert run.count("hook_failed") >= 1


def test_version_aliases_store_one_packet():
    backend = Submissions()
    h = Harness(COORDINATOR, backend)
    a = h.run(request("P042", 3), planner_v5)
    b = h.run(request("P042", "current"), planner_v5)
    assert a.receipt == b.receipt and len(backend.stored) == 1


def test_deeply_nested_mcp_input_gets_a_parse_error():
    replies = mcp(["[" * 10_000 + "\n", {"jsonrpc": "2.0", "id": 7, "method": "initialize"}])
    assert replies[None]["error"]["code"] == -32700 and "result" in replies[7]


def test_logged_context_is_what_the_planner_got():
    import hashlib
    seen = []
    def spy(context):
        seen.append(json.dumps(context, default=str))
        return planner_v5(context)
    run = Harness(COORDINATOR, Submissions()).run(P042, spy)
    logged = [e for e in run.events if e["kind"] == "context"]
    assert [hashlib.sha256(t.encode()).hexdigest()[:12] for t in seen] == [e["sha"] for e in logged]


@pytest.mark.parametrize("req", [{**P042, "on": "2026-09-01"}, {**P042, "version": 3.0},
                                 {k: v for k, v in P042.items() if k != "patient"}])
def test_malformed_requests_are_refused_everywhere(req):
    from ahe.domains.hls import ch02, ch03
    assert ch02.prepare(req)["status"] == "refused"
    assert ch03.run_task(req, ch03.fake_planner)["status"] == "refused"
    assert Harness(COORDINATOR, Submissions()).run(req, planner_v5)["status"] == "refused"


def test_negative_budgets_are_refused_in_chapter_5():
    out = Harness(COORDINATOR, Submissions()).run(P042, planner_v5, budget=ch05.Budget(steps=-1))
    assert out["status"] == "refused"


def test_fixed_program_runs_on_version_2():
    from ahe.domains.hls import ch03
    run = ch03.fixed_screen(request("P017", 2))
    assert run.state == "completed" and set(run.results) == {"age", "marker"}


def test_finish_takes_no_arguments():
    h = Harness(COORDINATOR, Submissions())
    run = start(P042)
    assert h.admit(run, {"tool": "finish", "args": {"unexpected": 1}})["error"] == "finish got arguments"


def test_a_hash_inside_an_env_value_is_kept(tmp_path, monkeypatch):
    from ahe import live
    env = tmp_path / "ahe.env"
    env.write_text("# settings\nAHE_TEST_KEY=sk-ab#cd   # the key\n")
    monkeypatch.setattr(live, "ENV_FILE", env)
    monkeypatch.delenv("AHE_TEST_KEY", raising=False)
    live.load_env()
    assert os.environ["AHE_TEST_KEY"] == "sk-ab#cd"


def test_nan_is_unknown_in_chapter_1_too():
    from ahe.domains.hls import ch01
    assert ch01.criterion(float("nan"), 3, 7) == "unknown"


@pytest.mark.parametrize("name", ["../outside", "a/b", ".hidden", ""])
def test_experiment_names_stay_inside_runs(name):
    from ahe import live
    with pytest.raises(ValueError):
        live.Recorder(name)


def test_inspect_log_is_a_chapter_5_tool():
    h = Harness(COORDINATOR, Submissions())
    run = start(P042)
    assert "inspect_log" in h.visible(run)
    assert h.admit(run, {"tool": "inspect_log", "args": {"kind": "observation", "last": 3}}) is None


# ---------------------------------------------------------------- exit test

def test_exit_test_holds_on_both_patients():
    for pid in ("P042", "P043"):
        rows = ch05.exit_test(request(pid, 3))
        assert len(rows) == len(ch05.HOSTILE)
        assert all(err != "accepted" for _, err, *_ in rows)


def test_exit_test_fails_without_authorization(monkeypatch):
    monkeypatch.setattr(ch05, "authorize", lambda principal, tool, run: None)
    with pytest.raises(AssertionError):
        ch05.exit_test(P042)


def test_exit_test_fails_without_the_argument_check(monkeypatch):
    monkeypatch.setattr(ch05, "check_args", lambda run, tool, args: None)
    with pytest.raises(TypeError, match="force"):
        ch05.exit_test(P042)


@pytest.mark.parametrize("page", [True, 1.0])
def test_exact_types_refuse_bools_and_floats(page):
    problem = check_args(start(P042), ch05.SEARCH, {"terms": ["warfarin"], "page": page})
    assert problem["error"].startswith("bad page")


def test_a_validity_check_that_raises_is_a_refusal():
    def boom(run, v):
        raise RuntimeError("check broke")
    tool = ch05.Tool("t", "read", "", (ch05.Param("x", str, "any", boom, "fix"),), None)
    assert check_args(start(P042), tool, {"x": "y"})["error"] == 'bad x: "y"'


def test_a_tool_that_raises_under_mcp_is_an_error_result():
    from ahe.domains.hls import mcp_server
    out = []
    run = start(P042, ch05.Budget(steps=24, reads=0))
    server = mcp_server.Server(Harness(REVIEWER_A, Submissions()), run)
    msgs = [call(1, "search_notes", {"terms": ["apixaban"], "page": 1}),
            call(2, "read_note", {"note_id": "n58"}),
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}]
    server.serve([json.dumps(m) for m in msgs], lambda s: out.append(json.loads(s)))
    assert out[1]["result"]["isError"] is True
    assert "BudgetExhausted" in out[1]["result"]["content"][0]["text"]
    assert "tools" in out[2]["result"]


def test_ready_stops_when_the_principal_cannot_submit():
    with pytest.raises(ValueError, match="completed before a submit"):
        ch05.ready(Harness(REVIEWER_A, Submissions()), P042)


# ---------------------------------------------------------------- chapter review, round 1

def test_exit_test_fails_when_only_the_study_check_is_removed(monkeypatch):
    real = ch05.authorize
    def no_study_check(principal, tool, run):
        wide = ch05.Principal(principal.name, principal.studies | {run.protocol.study},
                              principal.may_submit)
        return real(wide, tool, run)
    monkeypatch.setattr(ch05, "authorize", no_study_check)
    with pytest.raises(AssertionError, match="coordinator-9|accepted"):
        ch05.exit_test(P042)


@pytest.mark.parametrize("args", [[], False, 0, "", {"x": 1}])
def test_finish_takes_exactly_no_arguments(args):
    h = Harness(COORDINATOR, Submissions())
    assert h.admit(start(P042), {"tool": "finish", "args": args})["error"] == "finish got arguments"


def test_an_off_study_principal_cannot_finish_a_run():
    run = ch05.ready(Harness(COORDINATOR, Submissions()), P042)
    out = Harness(ch05.OFF_STUDY, Submissions()).dispatch(run, {"tool": "finish"})
    assert out["error"] == "coordinator-9 not on T004" and run.state == "running"


def test_a_different_packet_for_the_same_request_is_a_conflict():
    backend = Submissions()
    first = Harness(COORDINATOR, backend).run(P042, planner_v5)
    def says_unclear(ctx):
        return {"status": "unclear", "date": None, "quote": None}
    second = Harness(COORDINATOR, backend, reader=says_unclear).run(P042, planner_v5)
    assert first.results["no_anticoag"].status != second.results["no_anticoag"].status
    assert len(backend.stored) == 1 and getattr(second, "receipt", None) is None
    assert any(e.get("refused", {}).get("error", "").startswith("conflicts with")
               or e.get("error", "").startswith("conflicts with") for e in second.events)


def test_an_after_hook_cannot_change_what_the_caller_gets():
    def tamper(run, tool, args, principal, result):
        result["submitted"] = "S9999"
    run = Harness(COORDINATOR, Submissions(), after=[tamper]).run(P042, planner_v5)
    assert run.receipt == "S0001"
    shown = [e for e in run.events if e.get("tool") == "submit_packet"]
    assert shown and all(e["submitted"] == "S0001" for e in shown)


def test_long_argument_names_get_bounded_refusals():
    problem = check_args(start(P042), ch05.SEARCH, {"terms": ["x"], "page": 1, "y" * 10_000: 1})
    assert len(problem["error"]) < 100


@pytest.mark.parametrize("terms, ok", [
    (["atrial fibrillation"], True), (["a"] * 5, True), (["a"] * 6, False),
    (["x" * 41], False), ([" "], False), ([], False)])
def test_search_terms_are_what_the_tool_text_says(terms, ok):
    problem = check_args(start(P042), ch05.SEARCH, {"terms": terms, "page": 1})
    assert (problem is None) == ok
    assert f"up to {ch05.TERM_CHARS} characters" in ch05.SEARCH.params[0].doc


def test_an_oversized_proposal_is_refused_and_not_logged_whole():
    h = Harness(COORDINATOR, Submissions())
    run = start(P042)
    out = h.dispatch(run, {"tool": "search_notes", "args": {"terms": ["x"], "page": 1, "z" * 5000: 0}})
    assert out["error"] == "proposal too large"
    assert all(len(json.dumps(e)) < 1000 for e in run.events)


def test_only_submit_packet_commits():
    h = Harness(COORDINATOR, Submissions())
    assert {n for n, t in h.tools.items() if t.effect == "commit"} == {"submit_packet"}
    assert {t.effect for t in h.tools.values()} == {"read", "propose", "commit"}


def test_a_draft_needs_every_rule_current():
    out = ch05.draft(start(P042))
    assert out["error"] == "draft refused" and "not current" in out["why"]


def test_skill_instructions_reach_the_live_planner(monkeypatch):
    from ahe.domains.hls import ch01
    seen = []
    monkeypatch.setattr(ch01, "chat", lambda prompt: seen.append(prompt) or '{"tool": "finish"}')
    skill = ch05.load_skill(ROOT / "skills/screen-anticoagulant-notes/SKILL.md")
    h = ch05.with_skill(Harness(COORDINATOR, Submissions()), skill)
    h.step(start(P042), ch05.live_planner5(h))
    assert skill.body and skill.body in seen[0]


def test_mcp_rejects_bad_envelopes_before_any_tool_runs():
    from ahe.domains.hls import mcp_server
    backend, out = Submissions(), []
    run = ch05.ready(Harness(COORDINATOR, backend), P042)
    server = mcp_server.Server(Harness(COORDINATOR, backend), run)
    before = backend.calls
    msgs = [{"jsonrpc": "1.0", "id": 1, "method": "tools/call",
             "params": {"name": "submit_packet", "arguments": {}}},
            {"id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": [1]},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "submit_packet", "arguments": []}}]
    server.serve([json.dumps(m) for m in msgs], lambda s: out.append(json.loads(s)))
    assert [r["error"]["code"] for r in out] == [-32600, -32600, -32600, -32602]
    assert backend.calls == before
