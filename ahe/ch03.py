"""Chapter 3: build the smallest useful runtime."""

from .ch02 import *            # noqa: F401,F403
from .ch02 import (D, NOTES, RECORDS, ContractError, Evidence,
                   NoteRule, Note, Result, evaluate, fake_reader,
                   judge_note_rule, read_note, request, validate)

# listing 3.1
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import count

RUN_IDS = count(1)

class BudgetExhausted(Exception):
    pass
@dataclass
class Budget:
    steps: int = 12          # planner turns
    reads: int = 4           # notes the reader may be shown
@dataclass
class Run:
    id: str
    request: dict
    protocol: object
    budget: Budget
    state: str = "running"   # or completed, blocked,
    reason: str = ""         # failed, cancelled
    evidence: list = field(default_factory=list)
    problems: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    version: int = 0          # bumped when evidence changes
    computed: dict = field(default_factory=dict) # rule
    changed: dict = field(default_factory=dict)  # field
    hits: set = field(default_factory=set)   # search hits
    seen: set = field(default_factory=set)   # notes read
    events: list = field(default_factory=list)
    reads: int = 0
    cancel: bool = False

    def record(self, kind, **data):
        # The runtime's fields come last, so no caller can
        # overwrite the sequence number or the kind.
        self.events.append({**data, "seq": len(self.events),
                            "kind": kind})
        return self.events[-1]["seq"]

    def count(self, kind):
        return sum(e["kind"] == kind for e in self.events)

def start(req, budget=None):
    req = deepcopy(req)           # the run owns its copy
    protocol = validate(req)      # raises ContractError
    run = Run(f"run-{next(RUN_IDS)}-{req['patient']}", req,
              protocol, budget or Budget())
    run.evidence = list(RECORDS[req["patient"]])
    run.record("request", patient=req["patient"],
               protocol=protocol.version)
    return run
# end listing

# listing 3.2
def notes_of(run):          # only notes written by the day
    on = run.request["on"]
    return [n for n in NOTES.get(run.request["patient"], [])
            if n.written <= on]

def t_search(run, terms):
    low = [t.lower() for t in terms]
    hits = [n.id for n in notes_of(run)
            if any(w in n.text.lower() for w in low)]
    if set(hits) - run.hits:
        run.version += 1
        run.changed["anticoagulant"] = run.version
    run.hits.update(hits)
    return {"notes": hits}
def t_read(run, note_id, reader):
    if run.reads >= run.budget.reads:
        raise BudgetExhausted("read budget spent")
    run.reads += 1
    run.seen.add(note_id)
    note = next(n for n in notes_of(run) if n.id == note_id)
    item, problem = read_note(reader, note, "anticoagulant",
                              run.request["on"])
    run.version += 1
    run.changed["anticoagulant"] = run.version
    if item is None:
        if problem != "no statement in note":
            run.problems[note_id] = problem
        return {"note": note_id, "problem": problem}
    run.evidence.append(item)
    return {"note": note_id, "present": bool(item.value),
            "observed": str(item.observed)}
def t_evaluate(run, rule_id):
    rule = next(r for r in run.protocol.rules
                if r.id == rule_id)
    on = run.request["on"]
    if isinstance(rule, NoteRule):
        result = judge_note_rule(rule, run.evidence,
                                 run.problems, on)
        unread = sorted(run.hits - run.seen)
        if unread and result.status != "not met":
            result = Result("unknown", "matches not read: "
                            + ", ".join(unread))
    else:
        result = evaluate(rule, run.evidence, on)
    run.results[rule_id] = result
    run.computed[rule_id] = run.version
    return {"rule": rule_id, "status": result.status,
            "reason": result.reason}
# end listing

# listing 3.3
def words(v):
    return (isinstance(v, list) and 0 < len(v) <= 5
            and all(isinstance(t, str) and t.strip()
                    for t in v))
TOOLS = {"search_notes": t_search, "read_note": t_read,
         "evaluate_rule": t_evaluate}
SPEC = {  # each argument, and the check its value must pass
    "search_notes": {"terms": lambda run, v: words(v)},
    "read_note": {"note_id": lambda run, v: v in
                  {n.id for n in notes_of(run)}},
    "evaluate_rule": {"rule_id": lambda run, v: v in
                      {r.id for r in run.protocol.rules}},
    "finish": {}}

def gate(run, proposal):
    if not isinstance(proposal, dict):
        return "proposal is not an object"
    tool = proposal.get("tool")
    args = proposal.get("args", {})
    if type(tool) is not str or tool not in SPEC:
        return f"tool not allowed: {tool}"
    names = set(SPEC[tool])
    if not isinstance(args, dict) or set(args) != names:
        return f"{tool} takes {sorted(names)}"
    for name, ok in SPEC[tool].items():
        try:
            fine = ok(run, args[name])
        except Exception:        # a check that cannot run
            fine = False         # refuses; it never crashes
        if not fine:
            return f"bad value for {name}: {args[name]!r}"
    return None
# end listing

# listing 3.4
def context_for(run):
    return {"task": run.request,
            "rules": [r.id for r in run.protocol.rules],
            "results": {k: v.status
                        for k, v in run.results.items()},
            "observations": [e for e in run.events
                             if e["kind"] == "observation"],
            "steps_left": (run.budget.steps
                           - run.count("proposal"))}

def unfinished(run):
    # A rule is unfinished if it has no result, or if the
    # evidence for its field changed after it was computed.
    return [r.id for r in run.protocol.rules
            if r.id not in run.results
            or run.changed.get(r.field, 0)
            > run.computed[r.id]]

def step(run, planner, reader, context=context_for):
    ctx = deepcopy(context(run))   # the planner gets a copy
    have = 2 if "pending" in ctx else 1  # v2: Chapter 4
    want = getattr(planner, "context_schema", have)
    if want != have:
        raise ContractError(
            f"planner expects context v{want}, "
            f"harness gives v{have}")
    proposal = planner(ctx)
    if isinstance(proposal, dict):  # keep only what may run
        proposal = {k: proposal[k] for k in ("tool", "args")
                    if k in proposal}
    run.record("proposal", proposal=proposal)
    refusal = gate(run, proposal)
    if refusal:
        run.record("gate", refused=refusal)
        run.record("observation", error=refusal)
        return
    tool, args = proposal["tool"], proposal.get("args", {})
    if tool == "finish":
        todo = unfinished(run)
        if todo:
            run.record("observation",
                       error=f"cannot finish: {todo}")
            return
        run.state = "completed"
        run.reason = "every rule has a current result"
        run.record("observation", tool="finish",
                   accepted=True)
        return
    if tool == "read_note":
        args = {**args, "reader": reader}
    observation = TOOLS[tool](run, **args)
    run.record("observation", tool=tool, **observation)
# end listing

# listing 3.5
def drive(run, planner, reader=fake_reader,
          context=context_for):
    if run.state != "running":
        raise ContractError(f"{run.id} already {run.state}")
    while run.state == "running":
        if run.cancel:
            run.state = "cancelled"
            run.reason = "asked to stop"
        elif run.count("proposal") >= run.budget.steps:
            run.state = "blocked"
            run.reason = "step budget spent"
        else:
            try:
                step(run, planner, reader, context)
            except BudgetExhausted as why:
                run.state, run.reason = "blocked", str(why)
            except Exception as why:      # a tool failed
                run.state = "failed"
                run.reason = f"{type(why).__name__}: {why}"
                run.record("observation", error=run.reason)
    run.record("stop", state=run.state, reason=run.reason)
    return run

def run_task(req, planner, reader=fake_reader, budget=None,
             context=context_for):
    try:
        run = start(req, budget)
    except ContractError as why:
        return {"status": "refused", "reason": str(why)}
    return drive(run, planner, reader, context)

def packet(run):
    # Chapter 2's packet, from a run in any end state.
    p = run.protocol
    return {"status": run.state, "reason": run.reason,
            "protocol": f"{p.study} v{p.version}",
            "criteria": dict(run.results),
            "unfinished": unfinished(run),
            "review": "pending"}
# end listing

# listing 3.6
SEARCH_TERMS = ["anticoagulant", "warfarin", "apixaban"]

def fixed_screen(req, reader=fake_reader):
    # The coordinator's procedure as a fixed program: search
    # the written-down terms, read every hit, evaluate.
    run = start(req, Budget(reads=99))
    for rule in run.protocol.rules:
        if not isinstance(rule, NoteRule):
            t_evaluate(run, rule.id)
    for note_id in t_search(run, SEARCH_TERMS)["notes"]:
        t_read(run, note_id, reader)
    t_evaluate(run, "no_anticoag")
    run.state, run.reason = "completed", "procedure ran"
    run.record("stop", state=run.state, reason=run.reason)
    return run
# end listing

# listing 3.7
BRANDS = ["eliquis", "coumadin", "blood thinner"]

def fake_planner(context):         # reads context v1
    # Stand-in for a model choosing the next step. It takes
    # the rules in order, searches before it reads, and
    # searches again with brand names if the first finds
    # nothing: the kind of choice a live model makes.
    obs = context["observations"]
    searches = [e for e in obs
                if e.get("tool") == "search_notes"]
    read = {e["note"] for e in obs
            if e.get("tool") == "read_note"}
    for rule_id in context["rules"]:
        if rule_id in context["results"]:
            continue
        if rule_id != "no_anticoag":
            return {"tool": "evaluate_rule",
                    "args": {"rule_id": rule_id}}
        hits = [n for e in searches for n in e["notes"]]
        if not searches or (not hits and len(searches) < 2):
            terms = BRANDS if searches else SEARCH_TERMS
            return {"tool": "search_notes",
                    "args": {"terms": terms}}
        todo = [n for n in hits if n not in read]
        if todo:
            return {"tool": "read_note",
                    "args": {"note_id": todo[0]}}
        return {"tool": "evaluate_rule",
                "args": {"rule_id": rule_id}}
    return {"tool": "finish"}
fake_planner.context_schema = 1
# end listing

# listing 3.8
def loopy(context):      # never stops searching
    return {"tool": "search_notes",
            "args": {"terms": ["anything"]}}
def eager(context):      # declares victory at once
    return {"tool": "finish"}
def rogue(context):      # tries a tool it was never given
    if not context["observations"]:
        return {"tool": "write_record",
                "args": {"marker": 5}}
    return fake_planner(context)
def sloppy(context):     # a string where a list belongs
    if not context["observations"]:
        return {"tool": "search_notes",
                "args": {"terms": "apixaban"}}
    return fake_planner(context)
def hasty(context):      # evaluates first, reads, finishes
    obs = context["observations"]
    done = {e.get("rule") for e in obs}
    for rule_id in context["rules"]:
        if rule_id not in done:
            return {"tool": "evaluate_rule",
                    "args": {"rule_id": rule_id}}
    if not any(e.get("tool") == "read_note" for e in obs):
        hits = [n for e in obs for n in e.get("notes", [])]
        if not hits:
            return {"tool": "search_notes",
                    "args": {"terms": SEARCH_TERMS}}
        return {"tool": "read_note",
                "args": {"note_id": hits[0]}}
    return {"tool": "finish"}
def broken_reader(context):
    raise RuntimeError("model endpoint timed out")
# end listing


PLANNER_PROMPT = """You choose the next step of a screening run. Reply with JSON only:
{"tool": name, "args": {...}}. Tools:
- search_notes {"terms": [up to 5 strings]}: ids of the patient's notes containing any term
- read_note {"note_id": id}: a reader extracts a dated statement about anticoagulants
- evaluate_rule {"rule_id": id}: computes the rule's status from the evidence so far
- finish {}: ends the run; refused while any rule lacks a current result
Rule no_anticoag means: no anticoagulant therapy within 180 days. Notes may name drugs by
brand. Read only notes that matter; reads are limited. Evaluate every rule, then finish.
Run so far:
"""


def live_planner(context):
    # The notebook's live planner: the same context dict, through chat().
    import json
    from .ch01 import chat, parse_json
    try:
        return parse_json(chat(PLANNER_PROMPT + json.dumps(context, default=str)))
    except ValueError:
        return {"tool": None}


# Chapter 3 data: P041 has nine notes over a year, two about
# anticoagulants; P043 names the drug only by brand.
def _notes(prefix, dated_texts):
    return [Note(f"{prefix}{i}", day, text)
            for i, (day, text) in enumerate(dated_texts)]

RECORDS["P041"] = [Evidence("e21", "age", 45, "years", D(2026, 9, 1)),
                   Evidence("e22", "marker", 5.5, "ng/mL", D(2026, 8, 26))]
NOTES["P041"] = _notes("n2", [
    (D(2025, 10, 1), "Physiotherapy review. Knee mobility improving. "
                     "Continue exercises."),
    (D(2025, 11, 3), "Dermatology follow-up. Eczema settled with "
                     "emollients."),
    (D(2025, 12, 1), "Annual check. Blood pressure 128/82. No new "
                     "complaints."),
    (D(2026, 1, 12), "Physiotherapy discharge. Goals met."),
    (D(2026, 3, 2), "Dental referral for wisdom tooth extraction."),
    (D(2026, 6, 15), "Palpitations. ECG shows atrial fibrillation. "
                     "Started apixaban 5 mg twice daily on 2026-06-15."),
    (D(2026, 7, 1), "Cardiology review. Rate controlled. Anticoagulant "
                    "counselling given."),
    (D(2026, 7, 20), "Travel vaccination advice given."),
    (D(2026, 8, 26), "Routine bloods taken. Marker M 5.5 ng/mL."),
])
RECORDS["P043"] = [Evidence("e27", "age", 55, "years", D(2026, 9, 1)),
                   Evidence("e28", "marker", 6.8, "ng/mL", D(2026, 8, 18))]
NOTES["P043"] = _notes("n3", [
    (D(2026, 2, 9), "Hip pain after a fall. X-ray normal."),
    (D(2026, 5, 2), "Deep vein thrombosis confirmed. Eliquis 5 mg twice "
                    "daily from 2026-05-02."),
    (D(2026, 8, 18), "Routine bloods taken."),
])

TRIALS = [("planner", fake_planner, fake_reader, None),
          ("loopy", loopy, fake_reader, None),
          ("eager", eager, fake_reader, None),
          ("rogue", rogue, fake_reader, None),
          ("sloppy", sloppy, fake_reader, None),
          ("hasty", hasty, fake_reader, None),
          ("broken reader", fake_planner, broken_reader, None),
          ("one read", fake_planner, fake_reader, Budget(reads=1))]

def trials(req):
    rows = []
    for name, planner, reader, budget in TRIALS:
        run = run_task(req, planner, reader, budget)
        rows.append((name, run.state, run.reason,
                     run.count("proposal"), run.reads))
    run = start(req)
    run.cancel = True
    drive(run, fake_planner)
    rows.append(("cancelled", run.state, run.reason, 0, 0))
    return rows


if __name__ == "__main__":
    for row in trials(request("P041", 3)):
        print(f"{row[0]:14s} {row[1]:10s} {row[2][:40]:40s} "
              f"steps={row[3]:2d} reads={row[4]}")
    for pid in ("P041", "P043"):
        fixed = fixed_screen(request(pid, 3))
        planned = run_task(request(pid, 3), fake_planner)
        print(pid, "fixed:", fixed.results["no_anticoag"].status,
              "| planner:", planned.results["no_anticoag"].status)
