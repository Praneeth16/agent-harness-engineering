"""Chapter 5: design tools and the action boundary."""

import hashlib
import json

from .ch04 import *            # noqa: F401,F403
from .ch04 import (Budget, BudgetExhausted, ContractError, RECORDS, Run,
                   assemble, deepcopy, fake_reader, notes_of,
                   select_protocol, unfinished, words, RUN_IDS)
from .ch03 import t_read, t_evaluate
from .ch02 import check_request
from .ch04 import t_inspect, kind_ok, last_ok

# listing 5.1
from dataclasses import dataclass

@dataclass(frozen=True)
class Param:
    name: str
    kind: type               # str, int, or list
    doc: str                 # what the model is told
    valid: object = None     # (run, value) -> bool
    fix: str = ""            # how to correct a bad value

@dataclass(frozen=True)
class Tool:
    name: str
    effect: str              # read, propose, or commit
    doc: str
    params: tuple
    fn: object

def refuse(what, why, fix):
    # An error the planner can act on: what, why, and how.
    return {"error": what, "why": why, "fix": fix}

def check_args(run, tool, args):
    if not isinstance(args, dict):
        return refuse("args is not an object",
                      "each tool takes named arguments",
                      "send args as a JSON object")
    names = sorted(p.name for p in tool.params)
    given = sorted(map(str, args))
    if given != names or not all(type(k) is str
                                 for k in args):
        return refuse(f"{tool.name} got {given}",
                      f"it takes exactly {names}",
                      f"send {names} and nothing else")
    for p in tool.params:
        v = args[p.name]
        try:
            ok = type(v) is p.kind and (
                p.valid is None or p.valid(run, v))
        except Exception:
            ok = False
        if not ok:
            return refuse(f"bad {p.name}: {preview(v)}",
                          p.doc, p.fix)
    return None

def preview(v, n=60):       # bounded, and safe to print
    try:
        text = json.dumps(v, default=str)
    except Exception:
        text = "<unprintable>"
    return text if len(text) <= n else text[:n] + "..."
# end listing

PAGE = 3                       # search results per page


# listing 5.2
def search(run, terms, page):
    low = [t.lower() for t in terms]
    hits = [n.id for n in notes_of(run)
            if any(w in n.text.lower() for w in low)]
    start = (page - 1) * PAGE
    shown = hits[start:start + PAGE]
    if set(shown) - run.hits:
        run.version += 1
        run.changed["anticoagulant"] = run.version
    run.hits.update(shown)       # only ids the planner saw
    return {"notes": shown, "page": page,
            "more": len(hits) > start + PAGE}

def in_run(ids):
    return lambda run, v: v in ids(run)

NOTE_IDS = lambda run: run.hits   # returned by a search
RULE_IDS = lambda run: {r.id for r in run.protocol.rules}

SEARCH = Tool("search_notes", "read",
    "Find this patient's notes that contain any of the "
    "terms. Returns note ids, 3 per page, and whether "
    "there are more.",
    (Param("terms", list, "1 to 5 words to look for",
           lambda run, v: words(v),
           'e.g. ["warfarin", "apixaban"]'),
     Param("page", int, "page of results, from 1",
           lambda run, v: v >= 1, "start at 1")), search)
READ = Tool("read_note", "read",
    "Show one note to the reader, which extracts a "
    "dated statement about anticoagulants.",
    (Param("note_id", str, "an id from search_notes",
           in_run(NOTE_IDS),
           "use an id returned by search_notes"),), None)
EVALUATE = Tool("evaluate_rule", "read",
    "Compute one rule's status from the evidence so "
    "far. Re-evaluate after reading more notes.",
    (Param("rule_id", str, "a rule of this protocol",
           in_run(RULE_IDS),
           "use an id from the rules in the context"),),
    t_evaluate)
# end listing

READ_TOOLS = (SEARCH, READ, EVALUATE)


def describe(tools, full=True):
    """The tool list the model reads, generated from the same specs the gate uses."""
    lines = []
    for t in tools:
        if full:
            args = ", ".join(f'"{p.name}": {p.kind.__name__} ({p.doc})' for p in t.params)
            lines.append(f"- {t.name} {{{args}}}: {t.doc}")
        else:
            lines.append(f"- {t.name} {{{', '.join(p.name for p in t.params)}}}")
    return "\n".join(lines)


# listing 5.3
class Submissions:
    # The system the packet goes to. It stores a packet
    # once per key and returns the same receipt after that.
    def __init__(self):
        self.stored, self.calls = {}, 0

    def submit(self, key, text):
        self.calls += 1       # text: the serialized packet
        if key not in self.stored:
            receipt = f"S{len(self.stored) + 1:04d}"
            self.stored[key] = (receipt, text)
        return self.stored[key][0]

def digest(packet):
    text = json.dumps(packet, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:12]

def draft(run):
    todo = unfinished(run)
    if todo:
        return refuse("draft refused",
                      f"not current: {todo}",
                      "evaluate those rules first")
    p, results = run.protocol, sorted(run.results.items())
    run.draft = {"patient": run.request["patient"],
                 "protocol": f"{p.study} v{p.version}",
                 "criteria": {k: v._asdict()
                              for k, v in results}}
    run.draft_version = run.version
    return {"drafted": digest(run.draft)}

def submit(run, backend):
    if getattr(run, "draft", None) is None:
        return refuse("no draft", "submit sends the draft",
                      "call draft_packet first")
    if run.draft_version != run.version:
        return refuse("draft is stale",
                      "evidence changed after drafting",
                      "call draft_packet again")
    text = json.dumps(run.draft, sort_keys=True,
                      default=str)
    r, p = run.request, run.protocol   # the version used
    key = (f"{r['patient']}:{p.study}:{p.version}:"
           f"{r['on']}:{digest(run.draft)}")
    run.receipt = backend.submit(key, text)
    return {"submitted": run.receipt}
# end listing


# listing 5.4
@dataclass(frozen=True)
class Principal:
    # Who the harness acts for. Set by the caller that
    # authenticated the reviewer, never by the request.
    name: str
    studies: frozenset
    may_submit: bool = False

def authorize(principal, tool, run):
    study = run.protocol.study
    if study not in principal.studies:
        return refuse(f"{principal.name} not on {study}",
                      "only assigned reviewers act here",
                      "ask the study owner for access")
    if tool.effect == "commit" and not principal.may_submit:
        return refuse(f"{principal.name} may not submit",
                      "submitting commits the packet",
                      "leave the draft for a coordinator")
    return None

def audit(log):          # an after hook: calls that ran
    def hook(run, tool, args, principal, result):
        log.append((run.id, principal.name, tool.name,
                    "error" not in result))
    return hook
# end listing


@dataclass
class Harness:
    """One screening harness: who it acts for, and with what."""
    principal: Principal
    backend: Submissions
    reader: object = fake_reader
    before: tuple = ()
    after: tuple = ()            # (run, tool, args, principal, result)
    limit: int = 2400
    teach: bool = True           # False: refusals say only "refused"
    skill: object = None         # narrows the visible tools

    def __post_init__(self):
        self.tools = {t.name: t for t in (
            SEARCH,
            Tool(READ.name, READ.effect, READ.doc, READ.params,
                 lambda run, note_id: t_read(run, note_id, self.reader)),
            EVALUATE,
            Tool("inspect_log", "read",
                 "Return the last 1 to 5 events of one kind from the run's log.",
                 (Param("kind", str, "proposal, gate, observation, or context", kind_ok,
                        'e.g. "observation"'),
                  Param("last", int, "how many, 1 to 5", last_ok, "use 1 to 5")),
                 t_inspect),
            Tool("draft_packet", "propose",
                 "Assemble the packet from current results.", (), draft),
            Tool("submit_packet", "commit",
                 "Send the drafted packet to the study. Needs permission.", (),
                 lambda run: submit(run, self.backend)))}

    def visible(self, run):
        return visible(self, run)

    def admit(self, run, proposal):
        return admit(self, run, proposal)

    def step(self, run, planner):
        return step(self, run, planner)

    def dispatch(self, run, proposal):
        return dispatch(self, run, proposal)

    def run(self, req, planner, budget=None):
        return run_harness(self, req, planner, budget)


# listing 5.5
def visible(h, run):
    # The planner is shown only the tools it may call, and
    # a skill can narrow that list but never widen it.
    names = {n for n, t in h.tools.items()
             if not authorize(h.principal, t, run)}
    if h.skill is not None:
        names &= h.skill.wants
    return sorted(names)

def admit(h, run, proposal):
    if not isinstance(proposal, dict):
        shape = '{"tool": "...", "args": {...}}'
        return refuse("proposal is not an object",
                      "a proposal names a tool and its "
                      "args", shape)
    name = proposal.get("tool")
    if name == "finish":
        return None if not proposal.get("args") else refuse(
            "finish got arguments", "it takes none",
            "send {\"tool\": \"finish\"}")
    if type(name) is not str or name not in h.tools:
        return refuse(f"no tool {preview(name)}",
                      f"tools: {visible(h, run)}",
                      "use one of the listed tools")
    if h.skill is not None and name not in h.skill.wants:
        return refuse(f"{name} is outside the skill",
                      f"skill tools: {visible(h, run)}",
                      "use one of the listed tools")
    tool, args = h.tools[name], proposal.get("args", {})
    problem = (authorize(h.principal, tool, run)
               or check_args(run, tool, args))
    for hook in h.before:
        problem = problem or hook(run, tool, args,
                                  h.principal)
    return problem
# end listing


# listing 5.6
def step(h, run, planner):
    ctx = assemble(run, limit=h.limit,     # hashed as given
                   extra={"tools": visible(h, run)})
    dispatch(h, run, deepcopy(planner(ctx)))

def dispatch(h, run, proposal):
    # The one path to a tool, for the loop and for MCP.
    if run.state != "running":
        return refuse("run is over", f"it is {run.state}",
                      "start a new run")
    if run.count("proposal") >= run.budget.steps:
        return refuse("no steps left", "budget spent",
                      "start a run with a larger budget")
    if isinstance(proposal, dict):
        proposal = {k: proposal[k] for k in ("tool", "args")
                    if k in proposal}
    run.record("proposal", proposal=proposal)
    problem = admit(h, run, proposal)
    if problem:
        run.record("gate", refused=problem)
        seen = problem if h.teach else {"error": "refused"}
        run.record("observation", **seen)
        return seen
    name = proposal["tool"]
    if name == "finish":
        return finish(run)
    tool, args = h.tools[name], proposal.get("args", {})
    out = tool.fn(run, **args)
    run.record("observation", tool=name, **out)
    for hook in h.after:   # the call already happened
        try:
            hook(run, tool, args, h.principal, out)
        except Exception as why:
            run.record("hook_failed", tool=name,
                       error=f"{type(why).__name__}: {why}")
    return out

def finish(run):
    todo = unfinished(run)
    if todo:
        out = refuse("cannot finish",
                     f"not current: {todo}",
                     "evaluate those rules, then finish")
    elif getattr(run, "draft_version", None) != run.version:
        out = refuse("cannot finish", "no current draft",
                     "call draft_packet, then finish")
    else:
        out = {"tool": "finish", "accepted": True}
        run.state = "completed"
        run.reason = "packet drafted from current results"
    run.record("observation", **out)
    return out
# end listing


# listing 5.7
def run_harness(h, req, planner, budget=None):
    req, p = deepcopy(req), h.principal
    budget = budget or Budget()
    try:
        check_request(req)
        if not all(type(v) is int and v >= 0
                   for v in (budget.steps, budget.reads)):
            raise ContractError("budgets are whole numbers")
    except ContractError as why:
        return {"status": "refused", "reason": str(why)}
    study = req.get("study")
    if study not in p.studies:             # before lookups
        return {"status": "refused",
                "reason": f"{p.name} not on {study}"}
    try:
        protocol = select_protocol(
            study, req["version"], req["on"])
    except ContractError as why:
        return {"status": "refused", "reason": str(why)}
    if req["patient"] not in RECORDS:
        return {"status": "refused", "reason": "no record"}
    run = Run(f"run-{next(RUN_IDS)}-{req['patient']}",
              req, protocol, budget)
    run.evidence = list(RECORDS[req["patient"]])
    run.record("request", patient=req["patient"],
               protocol=protocol.version,
               principal=h.principal.name)
    while run.state == "running":
        if run.count("proposal") >= run.budget.steps:
            run.state, run.reason = "blocked", "step budget"
            break
        try:
            step(h, run, planner)
        except BudgetExhausted as why:
            run.state, run.reason = "blocked", str(why)
        except Exception as why:
            run.state = "failed"
            run.reason = f"{type(why).__name__}: {why}"
            run.record("observation", error=run.reason)
    run.record("stop", state=run.state,
               reason=run.reason)
    return run
# end listing


# listing 5.8
from pathlib import Path

@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    wants: frozenset          # its allowed-tools: a request
    body: str

def load_skill(path):
    text = Path(path).read_text()
    _, head, body = text.split("---\n", 2)
    meta = dict(line.split(": ", 1) for line in
                head.splitlines() if ": " in line
                and not line.startswith(" "))
    wants = meta.get("allowed-tools", "").split()
    return Skill(meta["name"], meta["description"],
                 frozenset(wants), body.strip())

def with_skill(h, skill):
    # The same harness, narrowed to what the skill asks
    # for; visible() still drops what the principal lacks.
    from dataclasses import replace
    return replace(h, skill=skill)
# end listing


def planner_v5(context):
    """Stand-in planner for Chapter 5: v2 context plus draft and submit."""
    p = context["pending"]
    if p["unread_hits"] and p["reads_left"] > 0:
        return {"tool": "read_note", "args": {"note_id": p["unread_hits"][0]}}
    for rule_id in p["unfinished"]:
        if rule_id == "no_anticoag" and not p["searched"]:
            return {"tool": "search_notes",
                    "args": {"terms": ["anticoagulant", "warfarin", "apixaban"], "page": 1}}
        return {"tool": "evaluate_rule", "args": {"rule_id": rule_id}}
    done = [e for e in context["recent"] if e.get("tool") in ("draft_packet", "submit_packet")]
    if not any(e.get("tool") == "draft_packet" and "drafted" in e for e in done):
        return {"tool": "draft_packet", "args": {}}
    if "submit_packet" in context["tools"] and not any("submitted" in e for e in done):
        return {"tool": "submit_packet", "args": {}}
    return {"tool": "finish"}


planner_v5.context_schema = 2

REVIEWER_A = Principal("reviewer-a", frozenset({"T004"}))
COORDINATOR = Principal("coordinator-1", frozenset({"T004"}), may_submit=True)
OUTSIDER = Principal("reviewer-b", frozenset({"T009"}))


PLANNER5 = """You run one step of a screening task for a clinical-trial reviewer.
Screen the patient against every rule of the protocol, draft the packet, submit it if
submit_packet is among your tools, then finish. Reply with JSON only:
{"tool": name, "args": {...}}. finish takes no args. Your tools:
"""


def live_planner5(harness, full=True):
    """The notebook's live planner for Chapter 5; its tool text comes from the specs."""
    from .ch01 import chat, parse_json

    def planner(context):
        tools = [harness.tools[n] for n in context["tools"]]
        prompt = (PLANNER5 + describe(tools, full) + "\nRun so far:\n"
                  + json.dumps(context, default=str))
        try:
            return parse_json(chat(prompt))
        except ValueError:
            return {"tool": None}
    planner.context_schema = 2
    return planner
