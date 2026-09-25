"""Every guarantee Chapters 1 to 4 state in prose, as an assertion.

Each test names the claim it backs. A claim with no test here must be
rewritten in the book as a description, not a guarantee.
"""

import re
from pathlib import Path

import pytest

from ahe import ch01, ch02, ch03, ch04
from ahe.ch02 import D, request


# ---------------------------------------------------------------- Chapter 1

def test_prompt_only_accepts_the_incomplete_claim():
    claim = ch01.prompt_only(ch01.task)
    assert claim["eligible"] is True
    assert claim["criteria"] == {"age": "met"}


def test_harness_rejects_omission_and_keeps_both_statuses():
    packet = ch01.harnessed(ch01.task)
    assert packet["rejected"] == "claim omits marker"
    assert packet["criteria"] == {"age": "met", "marker": "unknown"}
    assert packet["summary"] is None and packet["review"] == "pending"
    assert "eligible" not in packet


@pytest.mark.parametrize("claim, reason", [
    ({"eligible": None, "criteria": {"age": "met", "marker": "met"}},
     "claim contradicts marker"),
    ({"eligible": "true", "criteria": {"age": "met", "marker": "unknown"}},
     "eligible is not true, false, or null"),
    ({"eligible": False, "criteria": {"age": "met", "marker": "unknown"}},
     "ineligibility claimed without support"),
    ({"eligible": True, "criteria": {"age": "met", "marker": "unknown"}},
     "eligibility claimed without support"),
    ({"summary": "prose only"}, "claim lists no criteria"),
])
def test_check_claim_rejects_hostile_claims(claim, reason):
    results = {"age": "met", "marker": "unknown"}
    assert ch01.check_claim(claim, results) == reason


def test_harness_accepts_a_correct_claim():
    honest = {"eligible": None, "summary": "Marker M is missing.",
              "criteria": {"age": "met", "marker": "unknown"}}
    packet = ch01.harnessed(ch01.task, model=lambda ctx: honest)
    assert packet["rejected"] is None
    assert packet["summary"] == "Marker M is missing."


@pytest.mark.parametrize("value, status", [
    (None, "unknown"), (2, "not met"), (3, "met"), (5, "met"),
    (7, "met"), (9, "not met")])
def test_criterion_endpoints(value, status):
    assert ch01.criterion(value, 3, 7) == status


def test_parse_json_handles_fences_and_prose():
    assert ch01.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ch01.parse_json('Here: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        ch01.parse_json("no json here")


# ---------------------------------------------------------------- Chapter 2

def test_all_fixtures_pass():
    assert ch02.run_fixtures(ch02.FIXTURES) == {}


def test_refused_and_numeric_requests_make_no_model_calls():
    for fx in ch02.FIXTURES:
        if fx["expect"].get("status") == "refused" or fx["request"]["version"] == 2:
            assert fx["calls"] == 0, fx["id"]


def test_forged_empty_quote_cannot_flip_a_real_exclusion():
    # Before the fix, this proposal turned P036's warfarin into "met".
    got = ch02.prepare(request("P036", 3), model=ch02.forger)
    assert got["criteria"]["no_anticoag"].status == "unknown"


def test_grounded_result_keeps_note_and_quotation():
    r = ch02.prepare(request("P036", 3))["criteria"]["no_anticoag"]
    assert r.source == "n01"
    assert r.quote in ch02.NOTES["P036"][0].text
    assert "warfarin" in r.quote


def test_future_evidence_is_not_fresh():
    r = ch02.prepare(request("P035", 2))["criteria"]["marker"]
    assert r.status == "unknown" and "no value by 2026-09-01" in r.reason


def test_existence_is_not_revealed_to_unassigned_reviewer():
    got = ch02.prepare(request("P999", 2, by="reviewer-b"))
    assert "no record" not in got["reason"]


def test_unresolved_note_blocks_a_clean_met():
    rule = ch02.PROTOCOLS[1].rules[2]
    absent = ch02.Evidence("n9", "anticoagulant", 0.0, "statement",
                           D(2026, 8, 1), "No anticoagulant therapy")
    r = ch02.judge_note_rule(rule, [absent], {"n8": "statement has no date"},
                             D(2026, 9, 1))
    assert r.status == "unknown"


@pytest.mark.parametrize("proposal, problem", [
    ({"field": "anticoagulant", "status": "maybe", "quote": "x",
      "observed": "2026-07-02"}, "unknown status"),
    ({"field": "anticoagulant", "status": "present", "quote": "  ",
      "observed": "2026-07-02"}, "quotation missing"),
    ({"field": "anticoagulant", "status": "present",
      "quote": "Started warfarin 5 mg daily on 2026-07-02",
      "observed": "2026-13-02"}, "date is not in the quotation"),
    ({"field": "anticoagulant", "status": "present",
      "quote": "Started warfarin 5 mg daily on 2026-07-02",
      "observed": None}, "no date"),
])
def test_ground_rejects_malformed_proposals(proposal, problem):
    note = ch02.NOTES["P036"][0]
    assert problem in ch02.ground(proposal, note, "anticoagulant")


# ---------------------------------------------------------------- Chapter 3

P041 = request("P041", 3)


def rows():
    return {name: rest for name, *rest in ch03.trials(P041)}


def test_every_trial_ends_in_a_named_state():
    got = rows()
    assert got["planner"][:2] == ["completed", "every rule has a current result"]
    assert got["planner"][2:] == [7, 2]
    assert got["loopy"][:3] == ["blocked", "step budget spent", 12]
    assert got["eager"][:3] == ["blocked", "step budget spent", 12]
    assert got["rogue"][0] == "completed"
    assert got["sloppy"][0] == "completed"
    assert got["hasty"][0] == "blocked"
    assert got["broken reader"][:2] == ["failed", "RuntimeError: model endpoint timed out"]
    assert got["one read"][:2] == ["blocked", "read budget spent"]
    assert got["cancelled"][:2] == ["cancelled", "asked to stop"]


def test_gate_refuses_bad_values_before_any_tool_runs():
    run = ch03.start(P041)
    for proposal in [None, {"tool": "write_record", "args": {}},
                     {"tool": "search_notes", "args": {"terms": "apixaban"}},
                     {"tool": "search_notes", "args": None},
                     {"tool": "read_note", "args": {"note_id": "n01"}},  # P036's
                     {"tool": "evaluate_rule", "args": {"rule_id": "x"}}]:
        assert ch03.gate(run, proposal), proposal


def test_planner_cannot_overwrite_event_metadata():
    run = ch03.start(P041)
    ch03.step(run, lambda c: {"tool": "finish", "seq": 99, "kind": "stop"},
              ch03.fake_reader)
    assert [e["seq"] for e in run.events] == list(range(len(run.events)))
    assert run.events[1]["kind"] == "proposal"


def test_stale_result_cannot_complete_a_run():
    run = ch03.start(P041)
    for tool, args in [("evaluate_rule", {"rule_id": "age"}),
                       ("evaluate_rule", {"rule_id": "marker"}),
                       ("evaluate_rule", {"rule_id": "no_anticoag"}),
                       ("search_notes", {"terms": ch03.SEARCH_TERMS}),
                       ("read_note", {"note_id": "n25"})]:
        ch03.step(run, lambda c, t=tool, a=args: {"tool": t, "args": a},
                  ch03.fake_reader)
    ch03.step(run, lambda c: {"tool": "finish"}, ch03.fake_reader)
    assert run.state == "running"
    assert "cannot finish: ['no_anticoag']" in run.events[-1]["error"]


def test_packet_matches_chapter_2_for_a_completed_run():
    run = ch03.run_task(P041, ch03.fake_planner)
    pk = ch03.packet(run)
    assert pk["status"] == "completed" and pk["unfinished"] == []
    assert pk["protocol"] == "T004 v3" and pk["review"] == "pending"
    assert pk["criteria"]["no_anticoag"].status == "not met"
    blocked = ch03.packet(ch03.run_task(P041, ch03.eager))
    assert blocked["status"] == "blocked"
    assert blocked["unfinished"] == ["age", "marker", "no_anticoag"]


def test_unread_matches_appear_in_the_reason():
    run = ch03.start(P041)
    ch03.t_search(run, ch03.SEARCH_TERMS)
    r = ch03.t_evaluate(run, "no_anticoag")
    assert r["status"] == "unknown" and "n25" in r["reason"]


def test_fixed_program_solves_p041_but_not_the_brand_name():
    assert ch03.fixed_screen(P041).results["no_anticoag"].status == "not met"
    assert ch03.fixed_screen(request("P043", 3)).results["no_anticoag"].status == "unknown"
    planned = ch03.run_task(request("P043", 3), ch03.fake_planner)
    assert planned.results["no_anticoag"].status == "not met"


def test_note_dates_are_not_before_the_events_they_report():
    for pid, notes in ch02.NOTES.items():
        for n in notes:
            for d in re.findall(r"\d{4}-\d{2}-\d{2}", n.text):
                if "Plan to" not in n.text:
                    assert D.fromisoformat(d) <= n.written, (pid, n.id)


# ---------------------------------------------------------------- Chapter 4

def test_limit_is_a_limit_or_the_run_blocks():
    run, mid = ch04.table_4_3()
    rows = ch04.exit_test(mid, ["n58", "n61"], (2400, 1200, 900, 500))
    assert rows[-1][1] is None and "over limit" in rows[-1][3]
    assert all(r[1] <= r[0] for r in rows[:-1])


def test_over_limit_blocks_a_live_run():
    run = ch03.run_task(request("P042", 3), ch04.planner_v2,
                        context=lambda r: ch04.assemble(r, limit=300))
    assert run.state == "blocked" and "over limit" in run.reason


def test_unknown_reason_survives_an_empty_window():
    run = ch03.start(request("P023", 3))
    ch03.t_evaluate(run, "marker")
    ctx = ch04.build(run, recent=0)
    assert ctx["recent"] == []
    assert ctx["results"]["marker"]["reason"].startswith("stale")
    assert ctx["results"]["marker"]["source"] == "e03"


def test_context_event_holds_exact_bundle():
    run = ch03.run_task(request("P042", 3), ch04.planner_v2, context=ch04.context_v2)
    events = [e for e in run.events if e["kind"] == "context"]
    assert len(events) == run.count("proposal")
    import hashlib, json
    for e in events:
        text = json.dumps(e["bundle"], default=str)
        assert hashlib.sha256(text.encode()).hexdigest()[:12] == e["sha"]
    assert run.events[-1]["kind"] == "stop"


def test_build_writes_no_events():
    run, _ = ch04.table_4_3()
    n = len(run.events)
    ch04.build(run)
    assert len(run.events) == n


def test_inspect_log_is_bounded():
    run = ch03.start(request("P042", 3))
    assert ch03.gate(run, {"tool": "inspect_log",
                           "args": {"kind": "observation", "last": 0}})
    assert ch03.gate(run, {"tool": "inspect_log",
                           "args": {"kind": "observation", "last": 50}})
    assert ch03.gate(run, {"tool": "inspect_log",
                           "args": {"kind": "observation", "last": 3}}) is None


def test_exercise_4_3_note_is_invisible_to_lexical_search():
    run = ch03.run_task(request("P044", 3), ch04.planner_v2, context=ch04.context_v2)
    assert run.hits == set()
    assert run.results["no_anticoag"].status == "unknown"


# ---------------------------------------------------------------- listings

LISTING = re.compile(r"# listing (\d+\.\d+)\n(.*?)# end listing", re.S)


def test_printed_listings_fit_the_page():
    for path in Path(ch01.__file__).parent.glob("ch0*.py"):
        for number, body in LISTING.findall(path.read_text()):
            for line in body.splitlines():
                assert len(line) <= 60, (path.name, number, line)
