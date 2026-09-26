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


def test_audit_hook_records_every_admitted_call():
    log = []
    run = Harness(COORDINATOR, Submissions(), before=[ch05.audit(log)]).run(P042, planner_v5)
    tools = [t for _, _, t, _ in log]
    assert tools.count("submit_packet") == 1 and all(p == "coordinator-1" for _, p, _, _ in log)


def test_terse_errors_hide_the_reason_from_the_planner():
    h = Harness(COORDINATOR, Submissions(), teach=False)
    run = start(P042)
    h.step(run, lambda c: {"tool": "search_notes", "args": {"terms": "x", "page": 1}})
    assert run.events[-1] == {"error": "refused", "seq": run.events[-1]["seq"], "kind": "observation"}


def test_a_skill_cannot_grant_a_tool():
    skill = ch05.load_skill(ROOT / "skills/screen-anticoagulant-notes/SKILL.md")
    assert "submit_packet" in skill.wants
    run = start(P042)
    assert "submit_packet" not in ch05.with_skill(Harness(REVIEWER_A, Submissions()), run, skill)
    assert "submit_packet" in ch05.with_skill(Harness(COORDINATOR, Submissions()), run, skill)


def test_mcp_calls_go_through_the_same_gate():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_notes", "arguments": {"terms": ["apixaban"], "page": 1}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "submit_packet", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "search_notes", "arguments": {"terms": "apixaban", "page": 1}}}]
    out = subprocess.run([sys.executable, "-m", "ahe.mcp_server", "P042"], cwd=ROOT,
                         input="".join(json.dumps(m) + "\n" for m in msgs),
                         capture_output=True, text=True, timeout=30).stdout
    replies = {r["id"]: r["result"] for r in map(json.loads, out.splitlines())}
    names = [t["name"] for t in replies[2]["tools"]]
    assert "submit_packet" not in names and "search_notes" in names
    assert json.loads(replies[3]["content"][0]["text"])["notes"] == ["n58"]
    assert replies[4]["isError"] and "no tool" in replies[4]["content"][0]["text"] or \
        "may not submit" in replies[4]["content"][0]["text"]
    assert replies[5]["isError"] and "bad terms" in replies[5]["content"][0]["text"]
