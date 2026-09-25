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
TERMS = {"anticoagulant": (   # words that make a note count
    "warfarin", "apixaban", "eliquis", "doac",
    "anticoagulant")}
COVERS = {"past year": 365, "past 12 months": 365,
          "past six months": 182}  # how far back "no" looks

class ContractError(Exception):
    pass

def check_manifest(protocols):
    for p in protocols:
        ids = [r.id for r in p.rules]
        if len(ids) != len(set(ids)):
            raise ContractError(f"v{p.version}: same id")
        for r in p.rules:
            if r.field not in VOCABULARY or (
                    isinstance(r, NoteRule)
                    and r.field not in TERMS):
                raise ContractError(f"no field {r.field}")

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

check_manifest(PROTOCOLS)
# end listing

# listing 2.3
import math

def finite(v):
    return (type(v) in (int, float)) and math.isfinite(v)

def evaluate(rule, evidence, on):
    items = [e for e in evidence
             if e.field == rule.field and e.observed <= on]
    if not items:
        return Result("unknown", f"no value by {on}")
    bad = [e for e in items if not finite(e.value)]
    if bad:
        return Result("unknown", "value is not a number",
                      bad[0].id)
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
def fake_reader(context):
    # Stand-in for a model reading a note: finds a cue word
    # (a drug, a brand, the class) and returns that sentence
    # with any ISO date in it. A real model reads better;
    # the contract is the same.
    for sentence in context["note"].split(". "):
        low = sentence.lower()
        if any(t in low for t in TERMS[context["field"]]):
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
NEGATIONS = ("no ", "not ", "never ", "denies ")

def sentences(text):
    return [s.strip().rstrip(".") for s in text.split(". ")
            if s.strip()]

def relevant(note, field):
    terms = TERMS[field]
    return [s for s in sentences(note.text)
            if any(t in s.lower() for t in terms)]

def ground(p, note, field, on):
    # The model proposes. The note and the manifest decide.
    if not isinstance(p, dict):
        return "reply is not an object"
    if p.get("field") != field:
        return f"answered {p.get('field')!r}, not {field!r}"
    status = p.get("status")
    if type(status) is not str or status not in STATUSES:
        return f"unknown status {status!r}"
    said = relevant(note, field)
    if status == "none":
        return ("reader missed a mention" if said
                else "no statement in note")
    if len(said) > 1:
        return "several statements; needs review"
    quote = p.get("quote")
    if type(quote) is not str or (
            quote.strip().rstrip(".") not in said):
        return "quotation is not the note's statement"
    if status == "unclear":
        return "statement unclear; needs review"
    no = quote.strip().lower().startswith(NEGATIONS)
    if no != (status == "absent"):
        return f"quotation does not say {status}"
    when = p.get("observed")
    if type(when) is not str or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", when):
        return "statement has no date"
    if when not in quote:
        return "date is not in the quotation"
    try:
        day = date.fromisoformat(when)
    except ValueError:
        return f"no such date {when}"
    if day > note.written or day > on:
        return "date is after the note or the request"
    return None
# end listing

# listing 2.6
def read_note(model, note, field, on):
    proposal = model({"note": note.text, "field": field})
    problem = ground(proposal, note, field, on)
    if problem:
        return None, problem
    present = proposal["status"] == "present"
    when = date.fromisoformat(proposal["observed"])
    quote = proposal["quote"].strip().rstrip(".")
    return Evidence(note.id, field, 1.0 if present else 0.0,
                    "statement", when, quote), None

def covers(quote):
    low = quote.lower()
    return max([d for k, d in COVERS.items() if k in low],
               default=0)

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
    ok = [f for f in recent
          if covers(f.quote) >= rule.window_days]
    if ok:
        f = ok[-1]
        return Result("met", f"absence stated {f.observed}",
                      f.id, f.quote)
    if recent:
        why = f"absence does not cover {rule.window_days} days"
        return Result("unknown", why, recent[-1].id)
    return Result("unknown", "no grounded statement")

def evaluate_note_rule(rule, notes, on, model):
    found, problems = [], {}
    for note in notes:
        if note.written > on:       # not yet written
            continue
        item, problem = read_note(model, note, rule.field,
                                  on)
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
        return {"field": context["field"], "status": "unreadable",
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
    "P045": [Evidence("e31", "age", 50, "years", D(2026, 9, 1)),
             Evidence("e32", "marker", 5, "ng/mL", D(2026, 8, 25))],
    "P046": [Evidence("e33", "age", 51, "years", D(2026, 9, 1)),
             Evidence("e34", "marker", 5, "ng/mL", D(2026, 8, 25))],
    "P047": [Evidence("e35", "age", 52, "years", D(2026, 9, 1)),
             Evidence("e36", "marker", 5, "ng/mL", D(2026, 8, 25))],
    "P048": [Evidence("e37", "age", 53, "years", D(2026, 9, 1)),
             Evidence("e38", "marker", float("nan"), "ng/mL", D(2026, 8, 25))],
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
    "P045": [Note("n05", D(2026, 8, 31),
                  "No anticoagulant therapy in the past year, reviewed "
                  "2026-08-30. Started warfarin on 2026-08-31 after the "
                  "stroke-risk review.")],
    "P046": [Note("n06", D(2026, 9, 20),     # written after the request
                  "No anticoagulant therapy in the past year, reviewed "
                  "2026-08-30.")],
    "P047": [Note("n07", D(2026, 8, 30),
                  "No anticoagulant therapy on 2026-08-30. Continues "
                  "metformin.")],
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

def flipper(context):
    # Quotes the start of therapy and calls it absence.
    return {**fake_reader(context), "status": "absent"}

def fragment(context):
    # Quotes only the date, which is a substring of the note.
    return {**fake_reader(context), "quote": "2026-07-02"}

def hider(context):
    # Says the note is silent when it is not.
    return {"field": context["field"], "status": "none",
            "observed": None, "quote": ""}

def garbler(context):
    # Well-formed JSON with the wrong types in it.
    return {"field": context["field"], "status": "present",
            "observed": 20260702, "quote": 42}


# listing 2.7
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
    want = fx.get("criteria", {})
    if got["status"] == "prepared" and set(want) != set(
            got["criteria"]):
        bad.append(f"rules {sorted(got['criteria'])}")
    for rule, (status, why, src) in want.items():
        r = got["criteria"][rule]
        if (r.status, r.source) != (status, src) or (
                why not in r.reason):
            bad.append(f"{rule}: {r}")
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
REFUSED = "refused"

def crit(**rules):
    # rule id -> (status, words the reason must contain, source)
    return rules

AGE_MET = lambda src: ("met", "", src)
MET = lambda src, why="": ("met", why, src)
NO_NOTES = ("unknown", "no grounded statement", None)

FIXTURES = [
    {"id": "F01", "shape": "all criteria met (v2)", "calls": 0,
     "request": request("P031", 2), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e04"), marker=MET("e05", "5 ng/mL"))},
    {"id": "F02", "shape": "marker missing", "calls": 0,
     "request": request("P017", 2), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e01"), marker=("unknown", "no value by", None))},
    {"id": "F03", "shape": "marker stale under v3", "calls": 0,
     "request": request("P023", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e02"), marker=("unknown", "stale", "e03"),
                      no_anticoag=NO_NOTES)},
    {"id": "F04", "shape": "marker in the wrong unit", "calls": 0,
     "request": request("P034", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e10"), marker=("unknown", "unit is nmol/L", "e11"),
                      no_anticoag=NO_NOTES)},
    {"id": "F05", "shape": "version not yet in effect", "calls": 0,
     "request": request("P031", 3, on=D(2026, 7, 15)),
     "expect": {"status": REFUSED, "reason": "T004 v3 not in effect on 2026-07-15"}},
    {"id": "F06", "shape": "'current' resolves to v3", "calls": 0,
     "request": request("P032", "current"),
     "expect": {**PREPARED, "protocol": "T004 v3"},
     "criteria": crit(age=AGE_MET("e06"), marker=("not met", "3.5", "e07"),
                      no_anticoag=NO_NOTES)},
    {"id": "F07", "shape": "reviewer not assigned to study", "calls": 0,
     "request": request("P031", 2, by="reviewer-b"),
     "expect": {"status": REFUSED, "reason": "reviewer-b is not assigned to T004"}},
    {"id": "F08", "shape": "no such patient record", "calls": 0,
     "request": request("P999", 2),
     "expect": {"status": REFUSED, "reason": "no record: P999"}},
    {"id": "F09", "shape": "unassigned reviewer asks for a missing record", "calls": 0,
     "request": request("P999", 2, by="reviewer-b"),
     "expect": {"status": REFUSED, "reason": "reviewer-b is not assigned to T004"}},
    {"id": "F10", "shape": "marker measured after the request date", "calls": 0,
     "request": request("P035", 2), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e12"), marker=("unknown", "no value by", None))},
    {"id": "F11", "shape": "note states anticoagulant started in window", "calls": 1,
     "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("not met", "present 2026-07-02", "n01"))},
    {"id": "F12", "shape": "note states absence over the past year", "calls": 1,
     "request": request("P037", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e17"), marker=MET("e18"),
                      no_anticoag=("met", "absence stated 2026-08-30", "n02"))},
    {"id": "F13", "shape": "note mentions the drug but no date", "calls": 1,
     "request": request("P038", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e19"), marker=MET("e20"),
                      no_anticoag=("unknown", "no date", "n03"))},
    {"id": "F14", "shape": "reader invents a quotation", "calls": 1, "model": liar,
     "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "not the note's statement", "n01"))},
    {"id": "F15", "shape": "reader forges absence with an empty quotation", "calls": 1,
     "model": forger, "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "not the note's statement", "n01"))},
    {"id": "F16", "shape": "reader answers a different field", "calls": 1, "model": drifter,
     "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "not 'anticoagulant'", "n01"))},
    {"id": "F17", "shape": "reader reports a date the quotation lacks", "calls": 1,
     "model": redater, "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "not in the quotation", "n01"))},
    {"id": "F18", "shape": "note plans a start date after it was written", "calls": 1,
     "request": request("P039", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e25"), marker=MET("e26"),
                      no_anticoag=("unknown", "after the note", "n04"))},
    {"id": "F19", "shape": "reader calls a start of therapy absence", "calls": 1,
     "model": flipper, "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "does not say absent", "n01"))},
    {"id": "F20", "shape": "reader quotes only the date", "calls": 1, "model": fragment,
     "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "not the note's statement", "n01"))},
    {"id": "F21", "shape": "reader says a relevant note is silent", "calls": 1,
     "model": hider, "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "missed a mention", "n01"))},
    {"id": "F22", "shape": "one note states absence, then a start", "calls": 1,
     "request": request("P045", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e31"), marker=MET("e32"),
                      no_anticoag=("unknown", "several statements", "n05"))},
    {"id": "F23", "shape": "note written after the request date", "calls": 0,
     "request": request("P046", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e33"), marker=MET("e34"), no_anticoag=NO_NOTES)},
    {"id": "F24", "shape": "absence stated for one day only", "calls": 1,
     "request": request("P047", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e35"), marker=MET("e36"),
                      no_anticoag=("unknown", "does not cover 180 days", "n07"))},
    {"id": "F25", "shape": "marker value is not a number", "calls": 0,
     "request": request("P048", 2), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e37"), marker=("unknown", "not a number", "e38"))},
    {"id": "F26", "shape": "reader returns the wrong types", "calls": 1, "model": garbler,
     "request": request("P036", 3), "expect": PREPARED,
     "criteria": crit(age=AGE_MET("e15"), marker=MET("e16"),
                      no_anticoag=("unknown", "not the note's statement", "n01"))},
]

if __name__ == "__main__":
    assert not run_fixtures(FIXTURES)
