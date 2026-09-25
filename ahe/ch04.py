"""Chapter 4: engineer the context layer."""

from datetime import timedelta

from .ch03 import *            # noqa: F401,F403
from .ch03 import (D, NOTES, PROTOCOLS, RECORDS, SEARCH_TERMS, SPEC,
                   TOOLS, BudgetExhausted, Evidence, Note, Budget,
                   request, run_task, start, drive, unfinished, words)

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
    asked = [e["proposal"] for e in run.events
             if e["kind"] == "proposal"]
    terms = [p.get("args", {}).get("terms") for p in asked
             if isinstance(p, dict)
             and p.get("tool") == "search_notes"]
    return sorted({t.lower() for ts in terms if words(ts)
                   for t in ts})
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
        if size(ctx) + size(e) + 2 > limit:
            break               # newest first; oldest drops
        ctx["recent"].insert(0, e)
    return ctx

def assemble(run, recent=4, limit=2400):
    ctx = build(run, recent, limit)
    text = json.dumps(ctx, default=str)
    sha = hashlib.sha256(text.encode()).hexdigest()[:12]
    run.record("context", chars=len(text), sha=sha,
               recent=len(ctx["recent"]),
               bundle=ctx)      # exactly what the model saw
    return ctx
# end listing

# listing 4.3
def t_inspect(run, kind, last):
    picked = [e for e in run.events if e["kind"] == kind]
    return {"events": [{k: v for k, v in e.items()
                        if k not in ("kind", "bundle")}
                       for e in picked[-last:]]}
TOOLS["inspect_log"] = t_inspect
SPEC["inspect_log"] = {
    "kind": lambda run, v: v in
        {"proposal", "gate", "observation", "context"},
    "last": lambda run, v: v in range(1, 6)}

def planner_v2(context):
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
# end listing

# listing 4.4
def naive_context(run):
    # Everything, for completeness. The anti-pattern.
    notes = NOTES.get(run.request["patient"], [])
    return {"protocols": [asdict(p) for p in PROTOCOLS],
            "record": [asdict(e) for e in run.evidence],
            "notes": [asdict(n) for n in notes],
            "events": run.events}

def ranges(ctx):
    return {(r.get("lower"), r.get("upper"))
            for r in ctx["rules"]}

def exit_test(run, want_unread, limits):
    rows = []
    for limit in limits:
        try:
            ctx = build(run, recent=8, limit=limit)
        except BudgetExhausted as why:
            rows.append((limit, None, None, str(why)))
            continue
        sources = {r["source"] for r in ctx["rules"]}
        assert size(ctx) <= limit
        assert sources == {"T004 v3"}
        assert (3, 7) not in ranges(ctx)
        assert ctx["pending"]["unread_hits"] == want_unread
        assert all(r["reason"]
                   for r in ctx["results"].values())
        rows.append((limit, size(ctx), len(ctx["recent"]),
                     "hold"))
    return rows
# end listing


def context_v2(run):
    return assemble(run)


def context_without_terms(run):
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


def table_4_3():
    run = run_task(request("P042", 3), planner_v2, context=context_v2)
    mid = run_task(request("P042", 3), planner_v2, context=context_v2,
                   budget=Budget(steps=3))
    return run, mid


if __name__ == "__main__":
    run, mid = table_4_3()
    print(run.state, "|", run.reason, "| notes read:", run.reads,
          "of", len(NOTES["P042"]))
    print("naive context:", size(naive_context(run)), "chars")
    print("mid-run (3 steps):", mid.state, mid.reason,
          "| unread:", sorted(mid.hits - mid.seen))
    for row in exit_test(mid, ["n58", "n61"], (2400, 1200, 900, 500)):
        print(row)
