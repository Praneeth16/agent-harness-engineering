"""Chapter 4: engineer the context layer."""

from datetime import timedelta

from .ch03 import *            # noqa: F401,F403
from .ch03 import (D, NOTES, PROTOCOLS, RECORDS, SEARCH_TERMS, SPEC,
                   TOOLS, BudgetExhausted, Evidence, Note, Budget,
                   request, run_task, start, drive, unfinished, words,
                   context_for, Registry)

# listing 4.1
import hashlib, json
from dataclasses import asdict, dataclass

@dataclass
class Context:
    # Stable fields first: a provider can reuse a cached
    # prefix only while its bytes do not change.
    task: dict
    rules: list       # the selected version only, sourced
    results: dict     # status, reason, and source per rule
    pending: dict     # derived from the whole log
    recent: list      # the last few observations, verbatim
    archive: dict     # what was left out, how to reach it
    steps_left: int

def describe(rule, protocol):
    source = f"{protocol.study} v{protocol.version}"
    return {**asdict(rule), "source": source}
def observations(run):
    return [e for e in run.events
            if e["kind"] == "observation"]
def searched_terms(run):
    # Terms of the searches that actually ran: a proposal
    # followed directly by a search observation.
    ev = run.events
    ran = [a["proposal"]["args"]["terms"]
           for a, b in zip(ev, ev[1:])
           if a["kind"] == "proposal"
           and b.get("tool") == "search_notes"]
    return sorted({t.lower() for ts in ran for t in ts})
def pending_for(run):
    return {"searched": bool(searched_terms(run)),
            "searched_terms": searched_terms(run),
            "unread_hits": sorted(run.hits - run.seen),
            "unfinished": unfinished(run),
            "problems": dict(run.problems),
            "reads_left": run.budget.reads - run.reads}
# end listing

# listing 4.2
def size(ctx):
    return len(json.dumps(ctx, default=str))

def build(run, recent=4, limit=2400):
    obs = observations(run)
    left = run.budget.steps - run.count("proposal")
    ctx = asdict(Context(
        task={"patient": run.request["patient"],
              "on": str(run.request["on"])},
        rules=[describe(r, run.protocol)
               for r in run.protocol.rules],
        results={k: v._asdict() for k, v in
                 run.results.items()},
        pending=pending_for(run), recent=[],
        archive={"observations": len(obs),
                 "how": "inspect_log(kind, last<=5)"},
        steps_left=left))
    need = size(ctx)            # everything but the window
    if need > limit:
        raise BudgetExhausted(
            f"context over limit: {need} > {limit}")
    for e in reversed(obs[-recent:] if recent else []):
        trial = {**ctx, "recent": [deepcopy(e)]
                 + ctx["recent"]}
        if size(trial) > limit:
            break               # newest first; oldest drops
        ctx = trial
    return ctx

def assemble(run, recent=4, limit=2400):
    ctx = build(run, recent, limit)
    text = json.dumps(ctx, default=str)
    sha = hashlib.sha256(text.encode()).hexdigest()[:12]
    shown = json.loads(text)    # the planner gets a copy;
    run.record("context", chars=len(text), sha=sha,
               recent=len(shown["recent"]),  # the log keeps
               bundle=json.loads(text))      # its own
    return shown
# end listing

# listing 4.3
def t_inspect(run, kind, last):
    picked = [e for e in run.events if e["kind"] == kind]
    keep = lambda e: {k: v for k, v in e.items()
                      if k not in ("kind", "bundle")}
    return {"events": [deepcopy(keep(e))
                       for e in picked[-last:]]}
KINDS = {"proposal", "gate", "observation", "context"}
def kind_ok(run, v):
    return type(v) is str and v in KINDS
def last_ok(run, v):
    return type(v) is int and 1 <= v <= 5
REGISTRY4 = Registry(   # Chapter 3's tools plus this one
    {**TOOLS, "inspect_log": t_inspect},
    {**SPEC, "inspect_log": {"kind": kind_ok,
                             "last": last_ok}})

def planner_v2(context):         # reads context v2
    # Works from the derived pending block, not the history.
    p = context["pending"]
    if p["unread_hits"] and p["reads_left"] > 0:
        return {"tool": "read_note",
                "args": {"note_id": p["unread_hits"][0]}}
    for rule_id in p["unfinished"]:
        if rule_id == "no_anticoag" and not p["searched"]:
            return {"tool": "search_notes",
                    "args": {"terms": SEARCH_TERMS}}
        return {"tool": "evaluate_rule",
                "args": {"rule_id": rule_id}}
    return {"tool": "finish"}
planner_v2.context_schema = 2
# end listing

# listing 4.4
def naive_context(run):
    # Everything, for completeness. The anti-pattern.
    notes = NOTES.get(run.request["patient"], [])
    return {"protocols": [asdict(p) for p in PROTOCOLS],
            "record": [asdict(e) for e in run.evidence],
            "notes": [asdict(n) for n in notes],
            "events": run.events}

def required(ctx):
    return {k: v for k, v in ctx.items() if k != "recent"}

def exit_test(run, want, limits):
    # want: the required part, written out by hand.
    rows = []
    for limit in limits:
        try:
            ctx = build(run, recent=8, limit=limit)
        except BudgetExhausted as why:
            rows.append((limit, None, None, str(why)))
            continue
        assert size(ctx) <= limit
        got = json.loads(json.dumps(required(ctx),
                                    default=str))
        assert got == want, (limit, got)
        rows.append((limit, size(ctx), len(ctx["recent"]),
                     "hold"))
    return rows
# end listing


P042_AFTER_3 = {  # P042 v3 stopped after 3 steps, checked by hand
    "task": {"patient": "P042", "on": "2026-09-01"},
    "rules": [
        {"id": "age", "field": "age", "lower": 18, "upper": 65,
         "unit": "years", "max_age_days": None, "source": "T004 v3"},
        {"id": "marker", "field": "marker", "lower": 4, "upper": 8,
         "unit": "ng/mL", "max_age_days": 90, "source": "T004 v3"},
        {"id": "no_anticoag", "field": "anticoagulant", "window_days": 180,
         "source": "T004 v3"}],
    "results": {
        "age": {"status": "met", "reason": "50 years on 2026-09-01",
                "source": "e23", "quote": None},
        "marker": {"status": "met", "reason": "6.1 ng/mL on 2026-08-24",
                   "source": "e24", "quote": None}},
    "pending": {"searched": True,
                "searched_terms": ["anticoagulant", "apixaban", "warfarin"],
                "unread_hits": ["n58", "n61"], "unfinished": ["no_anticoag"],
                "problems": {}, "reads_left": 4},
    "archive": {"observations": 3, "how": "inspect_log(kind, last<=5)"},
    "steps_left": 0}


def context_v2(run):
    return assemble(run)


context_v2.schema = 2
assemble.schema = 2


def context_history_with_terms(run):
    # Experiment condition D in Section 4.5: Chapter 3's full observation
    # history plus the one derived field, to separate the two effects.
    return {**context_for(run), "searched_terms": searched_terms(run)}


def context_all_observations(run):
    # Experiment condition E: the Chapter 4 bundle with every observation and
    # no searched terms, so that only the window length differs from B.
    ctx = build(run, recent=10_000, limit=10_000_000)
    del ctx["pending"]["searched_terms"]
    text = json.dumps(ctx, default=str)
    run.record("context", chars=len(text), recent=len(ctx["recent"]),
               sha=hashlib.sha256(text.encode()).hexdigest()[:12], bundle=json.loads(text))
    return json.loads(text)


context_all_observations.schema = 2


def context_without_terms(run):  # noqa: D401
    # Experiment condition B in Section 4.5: the assembled bundle as first
    # built, before the pending block listed the terms already searched.
    ctx = build(run)
    del ctx["pending"]["searched_terms"]
    text = json.dumps(ctx, default=str)
    run.record("context", chars=len(text), recent=len(ctx["recent"]),
               sha=hashlib.sha256(text.encode()).hexdigest()[:12], bundle=ctx)
    return ctx


# Chapter 4 data: P042 has thirty notes over a year; P044 describes
# anticoagulation without any of the search terms (Exercise 4.3).
RECORDS["P042"] = [Evidence("e23", "age", 50, "years", D(2026, 9, 1)),
                   Evidence("e24", "marker", 6.1, "ng/mL", D(2026, 8, 24))]
ROUTINE = [
    "Physiotherapy session. Shoulder range improving. Home exercises reviewed.",
    "Dermatology review. Psoriasis plaques reduced on current topical treatment.",
    "Routine check. Blood pressure 124/80. Weight stable. No new complaints.",
    "Dietitian consultation. Mediterranean pattern discussed. Follow-up in three months.",
    "Optometry. Mild presbyopia. New prescription issued.",
    "Travel clinic. Hepatitis A booster given. Malaria advice for the region discussed.",
    "Podiatry. Callus debrided. Footwear advice given.",
    "Dental hygiene appointment. Scaling completed.",
]
CARDIAC = {
    18: (D(2026, 4, 10), "Palpitations reported. ECG shows atrial fibrillation. "
                         "Started apixaban 5 mg twice daily on 2026-04-10."),
    21: (D(2026, 5, 11), "Cardiology follow-up. Rate controlled. Anticoagulant "
                         "counselling reinforced; bleeding precautions discussed."),
}
NOTES["P042"] = [
    Note(f"n{40 + i}", *CARDIAC[i]) if i in CARDIAC else
    Note(f"n{40 + i}", D(2025, 9, 1) + timedelta(days=12 * i),
         ROUTINE[i % len(ROUTINE)])
    for i in range(30)]
RECORDS["P044"] = [Evidence("e29", "age", 48, "years", D(2026, 9, 1)),
                   Evidence("e30", "marker", 5.2, "ng/mL", D(2026, 8, 30))]
NOTES["P044"] = [
    Note("n70", D(2026, 5, 10), "Atrial fibrillation confirmed. Started a "
                                "DOAC on 2026-05-10; review in four weeks."),
    Note("n71", D(2026, 8, 30), "Routine bloods taken. Marker M 5.2 ng/mL."),
]


def run_task4(req, planner, **kw):
    """Chapter 4's runs: the assembled context and the Chapter 4 registry."""
    kw.setdefault("context", context_v2)
    return run_task(req, planner, registry=REGISTRY4, **kw)


def table_4_3():
    run = run_task4(request("P042", 3), planner_v2)
    mid = run_task4(request("P042", 3), planner_v2, budget=Budget(steps=3))
    return run, mid


if __name__ == "__main__":
    run, mid = table_4_3()
    print(run.state, "|", run.reason, "| notes read:", run.reads,
          "of", len(NOTES["P042"]))
    print("naive context:", size(naive_context(run)), "chars")
    print("mid-run (3 steps):", mid.state, mid.reason,
          "| unread:", sorted(mid.hits - mid.seen))
    for row in exit_test(mid, P042_AFTER_3, (2400, 1200, 900, 500)):
        print(row)


context_without_terms.schema = 2
