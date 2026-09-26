"""Chapter 5's guarantees, as assertions."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ahe import ch05
from ahe.ch02 import request
from ahe.ch03 import start
from ahe.ch05 import (COORDINATOR, OUTSIDER, REVIEWER_A, Harness, Submissions,
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
    done = subprocess.run([sys.executable, "-m", "ahe.mcp_server", patient], cwd=ROOT,
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
    assert replies[4]["result"]["isError"] is True
    assert "no tool" in replies[4]["result"]["content"][0]["text"]
    assert replies[5]["result"]["isError"] is True
    assert "bad terms" in replies[5]["result"]["content"][0]["text"]


def test_mcp_survives_malformed_messages_and_failing_tools():
    replies = mcp(["{not json\n",
                   call(2, "finish", {}),
                   call(3, "read_note", {"note_id": "n58"}),            # never searched
                   call(4, "search_notes", {"terms": ["apixaban"], "page": 1}),
                   call(5, "read_note", {"note_id": "n58"})])
    assert replies[None]["error"]["code"] == -32700
    assert replies[2]["result"]["isError"] and "no tool" in replies[2]["result"]["content"][0]["text"]
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
    assert out["error"] == "cannot finish" and "not current" in out["why"] or "draft" in out["why"]
    assert fresh.state == "running"
