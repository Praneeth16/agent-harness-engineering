"""Chapter 2: turn domain work into executable contracts."""

import re
from datetime import date

# listing 2.1
from dataclasses import dataclass
from typing import NamedTuple

@dataclass(frozen=True)
class Criterion:             # a numeric rule
    id: str
    field: str
    lower: float
    upper: float
    unit: str
    max_age_days: int | None = None   # None: any age
@dataclass(frozen=True)
class NoteRule:              # a rule stated in prose:
    id: str                  # "no <field> in the window"
    field: str               # what the reader looks for
    window_days: int
@dataclass(frozen=True)
class ProtocolVersion:
    study: str
    version: int
    effective_from: date
    rules: tuple             # Criterion or NoteRule
@dataclass(frozen=True)
class Evidence:
    id: str                  # evidence item or note id
    field: str
    value: float             # notes: 1 present, 0 absent
    unit: str
    observed: date
    quote: str = ""          # the sentence, for notes
@dataclass(frozen=True)
class Note:
    id: str
    written: date
    text: str
class Result(NamedTuple):
    status: str              # met, not met, or unknown
    reason: str
    source: str | None = None
    quote: str | None = None
# end listing

# listing 2.2
AGE = Criterion("age", "age", 18, 65, "years")
PROTOCOLS = [
    ProtocolVersion("T004", 2, date(2026, 1, 15), (AGE,
        Criterion("marker", "marker", 3, 7, "ng/mL"))),
    ProtocolVersion("T004", 3, date(2026, 8, 1), (AGE,
        Criterion("marker", "marker", 4, 8, "ng/mL", 90),
        NoteRule("no_anticoag", "anticoagulant", 180))),
]
ASSIGNED = {"reviewer-a": {"T004"}, "reviewer-b": {"T009"}}
VOCABULARY = {"age", "marker", "anticoagulant"}

class ContractError(Exception):
    pass

def select_protocol(study, version, on):
    live = [p for p in PROTOCOLS
            if p.study == study and p.effective_from <= on]
    if version == "current" and live:
        return max(live, key=lambda p: p.version)
    for p in live:
        if p.version == version:
            return p
    raise ContractError(f"{study} v{version} not in effect "
                        f"on {on}")
# end listing

# listing 2.3
def evaluate(rule, evidence, on):
    items = [e for e in evidence
             if e.field == rule.field and e.observed <= on]
    if not items:
        return Result("unknown", f"no value by {on}")
    odd = [e for e in items if e.unit != rule.unit]
    if odd:
        return Result("unknown", f"unit is {odd[0].unit}",
                      odd[0].id)
    limit = rule.max_age_days
    if limit is not None:
        fresh = [e for e in items
                 if (on - e.observed).days <= limit]
        if not fresh:
            e = max(items, key=lambda e: e.observed)
            return Result("unknown", f"stale: {e.observed}",
                          e.id)
        items = fresh
    e = max(items, key=lambda e: e.observed)
    inside = rule.lower <= e.value <= rule.upper
    why = f"{e.value} {e.unit} on {e.observed}"
    return Result("met" if inside else "not met", why, e.id)
# end listing

# listing 2.4
CUES = ("warfarin", "apixaban", "eliquis", "anticoagulant")

def fake_reader(context):
    # Stand-in for a model reading a note: finds a cue word
    # (a drug, a brand, the class) and returns that sentence
    # with any ISO date in it. A real model reads better;
    # the contract is the same.
    for sentence in context["note"].split(". "):
        low = sentence.lower()
        if any(cue in low for cue in CUES):
            when = re.search(r"\d{4}-\d{2}-\d{2}", sentence)
            no = low.startswith("no ")
            return {"field": context["field"],
                    "status": "absent" if no else "present",
                    "observed": when and when.group(),
                    "quote": sentence.strip().rstrip(".")}
    return {"field": context["field"], "status": "none",
            "observed": None, "quote": ""}
# end listing

# listing 2.5
STATUSES = {"present", "absent", "unclear", "none"}

def ground(p, note, field):
    # The model proposes. The note and the manifest decide.
    if p.get("field") != field:
        return f"answered {p.get('field')!r}, not {field!r}"
    if p.get("status") not in STATUSES:
        return f"unknown status {p.get('status')!r}"
    if p["status"] == "none":
        return "no statement in note"
    quote = (p.get("quote") or "").strip()
    if not quote or quote not in note.text:
        return "quotation missing or not in note"
    if p["status"] == "unclear":
        return "statement unclear; needs review"
    when = p.get("observed") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        return "statement has no date"
    if when not in quote:
        return "date is not in the quotation"
    try:
        if date.fromisoformat(when) > note.written:
            return "date is after the note was written"
    except ValueError:
        return f"no such date {when}"
    return None

def read_note(model, note, field):
    proposal = model({"note": note.text, "field": field})
    problem = ground(proposal, note, field)
    if problem:
        return None, problem
    present = proposal["status"] == "present"
    when = date.fromisoformat(proposal["observed"])
    return Evidence(note.id, field, 1.0 if present else 0.0,
                    "statement", when,
                    proposal["quote"].strip()), None

def judge_note_rule(rule, found, problems, on):
    recent = [f for f in found if f.field == rule.field
              and 0 <= (on - f.observed).days
              <= rule.window_days]
    hit = [f for f in recent if f.value]
    if hit:
        f = hit[0]
        return Result("not met", f"present {f.observed}",
                      f.id, f.quote)
    if problems:                # one unread note is enough
        nid, why = next(iter(problems.items()))
        return Result("unknown", f"{nid}: {why}", nid)
    if recent:
        f = recent[-1]
        return Result("met", f"absence stated {f.observed}",
                      f.id, f.quote)
    return Result("unknown", "no grounded statement")

def evaluate_note_rule(rule, notes, on, model):
    found, problems = [], {}
    for note in notes:
        item, problem = read_note(model, note, rule.field)
        if item is None:
            if problem != "no statement in note":
                problems[note.id] = problem
        else:
            found.append(item)
    return judge_note_rule(rule, found, problems, on)
# end listing


def live_reader(context):
    # The notebook's live reader: the same contract as fake_reader, through
    # Chapter 1's chat(). Not printed; the prompt is described in Section 2.5.
    from .ch01 import chat, parse_json
    prompt = (
        "You read one clinical note and report what it says about one field.\n"
        f"Field: {context['field']}\n"
        "Reply with JSON only: {\"field\": the field, \"status\": \"present\"|"
        "\"absent\"|\"unclear\"|\"none\", \"observed\": \"YYYY-MM-DD\" or null, "
        "\"quote\": the exact sentence from the note that supports the status}.\n"
        "present: the patient is on it. absent: the note says the patient is not. "
        "unclear: mentioned but not settled. none: not mentioned.\n"
        f"Note: {context['note']}")
    try:
        return parse_json(chat(prompt))
    except ValueError:
        return {"field": context["field"], "status": "none",
                "observed": None, "quote": ""}


# Records, notes, and fixtures for Table 2.3. Data, not printed as code.
D = date
RECORDS = {
    "P017": [Evidence("e01", "age", 47, "years", D(2026, 9, 1))],
    "P023": [Evidence("e02", "age", 52, "years", D(2026, 9, 1)),
             Evidence("e03", "marker", 5, "ng/mL", D(2025, 6, 3))],
    "P031": [Evidence("e04", "age", 39, "years", D(2026, 6, 1)),
             Evidence("e05", "marker", 5, "ng/mL", D(2026, 6, 20))],
    "P032": [Evidence("e06", "age", 44, "years", D(2026, 9, 1)),
             Evidence("e07", "marker", 3.5, "ng/mL", D(2026, 8, 22))],
    "P034": [Evidence("e10", "age", 29, "years", D(2026, 9, 1)),
             Evidence("e11", "marker", 5, "nmol/L", D(2026, 8, 25))],
    "P035": [Evidence("e12", "age", 33, "years", D(2026, 9, 1)),
             Evidence("e13", "marker", 5, "ng/mL", D(2026, 9, 20))],
    "P036": [Evidence("e15", "age", 58, "years", D(2026, 9, 1)),
             Evidence("e16", "marker", 6, "ng/mL", D(2026, 8, 28))],
    "P037": [Evidence("e17", "age", 41, "years", D(2026, 9, 1)),
             Evidence("e18", "marker", 5.5, "ng/mL", D(2026, 8, 27))],
    "P038": [Evidence("e19", "age", 36, "years", D(2026, 9, 1)),
             Evidence("e20", "marker", 6.2, "ng/mL", D(2026, 8, 29))],
    "P039": [Evidence("e25", "age", 62, "years", D(2026, 9, 1)),
             Evidence("e26", "marker", 4.4, "ng/mL", D(2026, 8, 21))],
}
NOTES = {
    "P036": [Note("n01", D(2026, 7, 2),
                  "Atrial fibrillation. Started warfarin 5 mg daily on "
                  "2026-07-02. Follow-up in six weeks.")],
    "P037": [Note("n02", D(2026, 8, 30),
                  "No anticoagulant therapy in the past year, reviewed "
                  "2026-08-30. Continues metformin.")],
    "P038": [Note("n03", D(2026, 8, 29),
                  "Palpitations reported. Discussed starting apixaban at "
                  "the next visit pending cardiology review.")],
    "P039": [Note("n04", D(2026, 8, 10),
                  "Cardiology referral. Plan to start warfarin on "
                  "2026-08-24 if the echo confirms the clot.")],
}


def liar(context):
    # Invents a quotation. Grounding must catch it.
    return {"field": context["field"], "status": "present",
            "observed": "2026-08-01", "quote": "Currently on warfarin"}

def forger(context):
    # Claims absence with an empty quotation, which every note "contains".
    return {"field": context["field"], "status": "absent",
            "observed": "2026-08-01", "quote": ""}

def drifter(context):
    # Answers a different vocabulary field than the one asked for.
    return {**fake_reader(context), "field": "marker"}

def redater(context):
    # Quotes the note faithfully but reports a different date.
    return {**fake_reader(context), "observed": "2026-03-01"}


# listing 2.6
def validate(req):
    if req["study"] not in ASSIGNED.get(req["by"], set()):
        raise ContractError(f"{req['by']} is not assigned "
                            f"to {req['study']}")
    if req["patient"] not in RECORDS:   # only now, and only
        raise ContractError(            # to the assigned
            f"no record: {req['patient']}")
    return select_protocol(req["study"], req["version"],
                           req["on"])

def prepare(req, model=fake_reader):
    try:
        protocol = validate(req)
    except ContractError as why:
        return {"status": "refused", "reason": str(why)}
    evidence = RECORDS[req["patient"]]
    notes = NOTES.get(req["patient"], [])
    results = {}
    for rule in protocol.rules:
        if isinstance(rule, NoteRule):
            results[rule.id] = evaluate_note_rule(
                rule, notes, req["on"], model)
        else:
            results[rule.id] = evaluate(rule, evidence,
                                        req["on"])
    name = f"{protocol.study} v{protocol.version}"
    return {"status": "prepared", "protocol": name,
            "criteria": results, "review": "pending"}

def check(fx, got, calls):
    # Every expectation is written before the code runs.
    bad = [k for k, v in fx["expect"].items()
           if got.get(k) != v]
    if calls != fx["calls"]:
        bad.append(f"calls {calls} != {fx['calls']}")
    for rule, want in fx.get("criteria", {}).items():
        r = got["criteria"][rule]
        status, *why = want
        if r.status != status or (
                why and why[0] not in r.reason):
            bad.append(f"{rule}: {r.status}, {r.reason}")
    return bad

def run_fixtures(fixtures, prepare=prepare):
    failures = {}
    for fx in fixtures:
        calls, model = [], fx.get("model", fake_reader)
        def counted(context, m=model):
            calls.append(context)
            return m(context)
        got = prepare(fx["request"], model=counted)
        bad = check(fx, got, len(calls))
        verdict = f"FAIL {bad}" if bad else "pass"
        print(fx["id"], verdict, got["status"],
              "| model calls:", len(calls))
        if bad:
            failures[fx["id"]] = bad
    return failures
# end listing


def request(patient, version, on=D(2026, 9, 1), by="reviewer-a"):
    return {"patient": patient, "study": "T004", "version": version,
            "on": on, "by": by}

PREPARED = {"status": "prepared"}

FIXTURES = [
    {"id": "F01", "shape": "all criteria met (v2)",
     "request": request("P031", 2), "expect": PREPARED, "calls": 0,
     "criteria": {"age": ("met",), "marker": ("met", "5 ng/mL")}},
    {"id": "F02", "shape": "marker missing",
     "request": request("P017", 2), "expect": PREPARED, "calls": 0,
     "criteria": {"age": ("met",), "marker": ("unknown", "no value")}},
    {"id": "F03", "shape": "marker stale under v3",
     "request": request("P023", 3), "expect": PREPARED, "calls": 0,
     "criteria": {"age": ("met",), "marker": ("unknown", "stale"),
                  "no_anticoag": ("unknown", "no grounded")}},
    {"id": "F04", "shape": "marker in the wrong unit",
     "request": request("P034", 3), "expect": PREPARED, "calls": 0,
     "criteria": {"age": ("met",), "marker": ("unknown", "nmol/L"),
                  "no_anticoag": ("unknown",)}},
    {"id": "F05", "shape": "version not yet in effect",
     "request": request("P031", 3, on=D(2026, 7, 15)), "calls": 0,
     "expect": {"status": "refused",
                "reason": "T004 v3 not in effect on 2026-07-15"}},
    {"id": "F06", "shape": "'current' resolves to v3",
     "request": request("P032", "current"), "calls": 0,
     "expect": {**PREPARED, "protocol": "T004 v3"},
     "criteria": {"age": ("met",), "marker": ("not met", "3.5"),
                  "no_anticoag": ("unknown",)}},
    {"id": "F07", "shape": "reviewer not assigned to study",
     "request": request("P031", 2, by="reviewer-b"), "calls": 0,
     "expect": {"status": "refused",
                "reason": "reviewer-b is not assigned to T004"}},
    {"id": "F08", "shape": "no such patient record",
     "request": request("P999", 2), "calls": 0,
     "expect": {"status": "refused", "reason": "no record: P999"}},
    {"id": "F09", "shape": "unassigned reviewer asks for a missing record",
     "request": request("P999", 2, by="reviewer-b"), "calls": 0,
     "expect": {"status": "refused",
                "reason": "reviewer-b is not assigned to T004"}},
    {"id": "F10", "shape": "marker measured after the request date",
     "request": request("P035", 2), "expect": PREPARED, "calls": 0,
     "criteria": {"age": ("met",), "marker": ("unknown", "no value by")}},
    {"id": "F11", "shape": "note states anticoagulant started in window",
     "request": request("P036", 3), "expect": PREPARED, "calls": 1,
     "criteria": {"age": ("met",), "marker": ("met",),
                  "no_anticoag": ("not met", "present 2026-07-02")}},
    {"id": "F12", "shape": "note states absence, with a date",
     "request": request("P037", 3), "expect": PREPARED, "calls": 1,
     "criteria": {"age": ("met",), "marker": ("met",),
                  "no_anticoag": ("met", "absence stated 2026-08-30")}},
    {"id": "F13", "shape": "note mentions the drug but no date",
     "request": request("P038", 3), "expect": PREPARED, "calls": 1,
     "criteria": {"age": ("met",), "marker": ("met",),
                  "no_anticoag": ("unknown", "no date")}},
    {"id": "F14", "shape": "model invents a quotation",
     "request": request("P036", 3), "model": liar,
     "expect": PREPARED, "calls": 1,
     "criteria": {"no_anticoag": ("unknown", "not in note")}},
    {"id": "F15", "shape": "model forges absence with an empty quotation",
     "request": request("P036", 3), "model": forger,
     "expect": PREPARED, "calls": 1,
     "criteria": {"no_anticoag": ("unknown", "quotation missing")}},
    {"id": "F16", "shape": "model answers a different field",
     "request": request("P036", 3), "model": drifter,
     "expect": PREPARED, "calls": 1,
     "criteria": {"no_anticoag": ("unknown", "not 'anticoagulant'")}},
    {"id": "F17", "shape": "model reports a date the quotation lacks",
     "request": request("P036", 3), "model": redater,
     "expect": PREPARED, "calls": 1,
     "criteria": {"no_anticoag": ("unknown", "not in the quotation")}},
    {"id": "F18", "shape": "note plans a start date after it was written",
     "request": request("P039", 3), "expect": PREPARED, "calls": 1,
     "criteria": {"no_anticoag": ("unknown", "after the note")}},
]


if __name__ == "__main__":
    assert not run_fixtures(FIXTURES)
