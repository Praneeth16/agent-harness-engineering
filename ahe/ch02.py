"""Chapter 2: the minimal loop and executable contracts.

Imports Chapter 1's workspace verifier and builds an inspectable
multi-step loop with an append-only event log, a path/type gate,
revision-stamped verification, and five named endings.
"""

from copy import deepcopy
import hashlib
import json

from .ch01 import PRICING_BUGGY, PRICING_FIXED, TEST_SPEC, verify

# listing 2.1
from dataclasses import dataclass, field

INVOICE_BUGGY = """\
from pricing import net_cents

def line_total(price, pct):
    return net_cents(price, pct)
"""

WORKSPACE = {
    "pricing.py": PRICING_BUGGY,
    "invoice.py": INVOICE_BUGGY,
    "README.md": "Pricing utilities.\n",
    "tests/test_pricing.py": TEST_SPEC,
}

def workspace_rev(files):
    blob = json.dumps(files, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]

def verify_invoice(files):
    res = verify(files)
    ns = {"__builtins__": {"ValueError": ValueError,
          "TypeError": TypeError, "isinstance": isinstance,
          "int": int, "float": float, "abs": abs}}
    try:
        exec(files.get("pricing.py", ""), ns)
        src = files.get("invoice.py", "").replace(
            "from pricing import net_cents\n", "")
        exec(src, ns)
        fn = ns["line_total"]
        ok = fn(1000, None) == 1000 and fn(1000, 10) == 900
    except Exception:
        ok = False
    res["invoice_none"] = "pass" if ok else "fail"
    return res

@dataclass(frozen=True)
class Budget:
    steps: int = 12
    edits: int = 4

@dataclass(frozen=True)
class Contract:
    task_id: str
    goal: str
    writable: tuple = ("pricing.py", "invoice.py")
    protected: tuple = ("tests/",)
    checks: object = verify_invoice
    budget: Budget = Budget()

@dataclass
class Run:
    id: str
    contract: Contract
    files: dict
    rev: str
    events: list = field(default_factory=list)
    verified_rev: str | None = None
    last_checks: dict = field(default_factory=dict)
    state: str = "running"
    reason: str = ""
    steps_used: int = 0
    edits_used: int = 0

def record(run, kind, **data):
    seq = len(run.events) + 1
    ev = {"seq": seq, "kind": kind, **deepcopy(data)}
    run.events.append(ev)
    return deepcopy(ev)
# end listing

# listing 2.2
def is_protected(path, contract):
    return any(path == p or path.startswith(p)
               for p in contract.protected)

def is_writable(path, contract):
    if is_protected(path, contract):
        return False
    return any(path == p or path.startswith(p)
               for p in contract.writable)

def t_list_files(run):
    return {"files": sorted(run.files)}

def t_read_file(run, path):
    if path not in run.files:
        return {"error": f"unknown file: {path}"}
    return {"path": path, "content": run.files[path]}

def t_write_file(run, path, content):
    run.files[path] = content
    run.edits_used += 1
    run.rev = workspace_rev(run.files)
    return {"path": path, "rev": run.rev,
            "bytes": len(content)}

def t_run_checks(run):
    res = run.contract.checks(deepcopy(run.files))
    run.verified_rev = run.rev
    run.last_checks = dict(res)
    failed = sorted(k for k, v in res.items()
                    if v != "pass")
    return {"rev": run.rev, "checks": res,
            "failed": failed}

TOOLS = {
    "list_files": ((), t_list_files),
    "read_file": (("path",), t_read_file),
    "write_file": (("path", "content"), t_write_file),
    "run_checks": ((), t_run_checks),
}
# end listing

# listing 2.3
def gate(run, proposal, tools=TOOLS):
    if not isinstance(proposal, dict):
        return "proposal is not an object"
    tool = proposal.get("tool")
    if tool == "finish":
        args = proposal.get("args", {})
        if not isinstance(args, dict) or args:
            return "finish takes no arguments"
        return None
    if not isinstance(tool, str) or tool not in tools:
        return f"unknown tool: {tool!r}"
    args = proposal.get("args", {})
    if not isinstance(args, dict):
        return "args is not an object"
    expected, _ = tools[tool]
    if set(args) != set(expected):
        return f"{tool} expects {list(expected)}"
    for k, v in args.items():
        if type(v) is not str:
            return f"{k} must be str"
    if tool == "write_file":
        path = args["path"]
        if is_protected(path, run.contract):
            return f"protected path: {path}"
        if not is_writable(path, run.contract):
            return f"path not writable: {path}"
        if run.edits_used >= run.contract.budget.edits:
            return "edit budget spent"
    return None

def finish_check(run):
    if run.verified_rev is None:
        return "no checks run"
    if run.verified_rev != run.rev:
        return "stale verification after edit"
    failed = sorted(k for k, v in run.last_checks.items()
                    if v != "pass")
    if failed:
        return "failing checks: " + ", ".join(failed)
    return None
# end listing

# listing 2.4
def check_contract(contract):
    b = contract.budget
    if (type(b.steps) is not int or b.steps < 1
            or type(b.edits) is not int or b.edits < 0):
        return "invalid budget"
    if not contract.task_id or not contract.goal:
        return "missing task_id or goal"
    return None

def default_context(run):
    return {
        "task_id": run.contract.task_id,
        "goal": run.contract.goal,
        "rev": run.rev,
        "verified": run.verified_rev == run.rev,
        "last_checks": dict(run.last_checks),
        "steps_left": run.contract.budget.steps
        - run.steps_used,
        "edits_left": run.contract.budget.edits
        - run.edits_used,
        "events": deepcopy(run.events),
    }

def step(run, planner, context_fn=default_context,
         tools=TOOLS):
    if run.state != "running":
        raise RuntimeError(f"run already {run.state}")
    if run.steps_used >= run.contract.budget.steps:
        run.state = "blocked"
        run.reason = "step budget spent"
        record(run, "stop", state=run.state,
               reason=run.reason)
        return run
    run.steps_used += 1
    ctx = context_fn(run)
    try:
        proposal = planner(deepcopy(ctx))
    except Exception as exc:
        run.state = "failed"
        run.reason = f"planner error: {type(exc).__name__}"
        record(run, "stop", state=run.state,
               reason=run.reason)
        return run
    record(run, "proposal", proposal=proposal)
    problem = gate(run, proposal, tools=tools)
    if problem:
        record(run, "gate", allowed=False, reason=problem)
        record(run, "observation", tool=proposal.get("tool")
               if isinstance(proposal, dict) else None,
               result={"error": problem})
        return run
    tool = proposal["tool"]
    args = proposal.get("args", {})
    record(run, "gate", allowed=True, tool=tool)
    if tool == "finish":
        err = finish_check(run)
        if err is None:
            run.state, run.reason = "completed", "verified"
            record(run, "stop", state=run.state,
                   reason=run.reason)
        else:
            record(run, "observation", tool="finish",
                   result={"error": err})
        return run
    _, fn = tools[tool]
    try:
        out = fn(run, **args)
    except Exception as exc:
        run.state = "failed"
        run.reason = f"tool error: {type(exc).__name__}"
        record(run, "stop", state=run.state,
               reason=run.reason)
        return run
    record(run, "observation", tool=tool, result=out)
    return run

def run_loop(contract, files, planner, cancel=None,
             context_fn=default_context, tools=TOOLS):
    err = check_contract(contract)
    if err:
        return {"status": "refused", "reason": err}
    snap = deepcopy(files)
    run = Run(id=f"run-{contract.task_id}",
              contract=contract, files=snap,
              rev=workspace_rev(snap))
    while run.state == "running":
        if cancel and cancel(run):
            run.state, run.reason = "cancelled", "cancelled"
            record(run, "stop", state=run.state,
                   reason=run.reason)
            break
        step(run, planner, context_fn=context_fn,
             tools=tools)
    return run
# end listing

# listing 2.5
INVOICE_FIXED = """\
from pricing import net_cents

def line_total(price, pct):
    return net_cents(price, 0 if pct is None else pct)
"""

def fixed_patch(contract, files):
    # Deterministic 1-file workflow: no planner loop.
    snap = deepcopy(files)
    snap["pricing.py"] = PRICING_FIXED
    res = contract.checks(snap)
    failed = sorted(k for k, v in res.items()
                    if v != "pass")
    return {"verified": not failed, "checks": res,
            "failed": failed}

def smart_planner(ctx):
    # Inspects checks, fixes pricing.py, discovers the
    # invoice_none failure, reads invoice.py, fixes it.
    seen = {e["result"].get("path") for e in ctx["events"]
            if e["kind"] == "observation"
            and isinstance(e["result"], dict)}
    checks = ctx["last_checks"]
    if not checks:
        return {"tool": "run_checks"}
    if not ctx["verified"]:
        return {"tool": "run_checks"}
    failed = [k for k, v in checks.items() if v != "pass"]
    if not failed:
        return {"tool": "finish"}
    if "reject_neg" in failed or "reject_over" in failed:
        if "pricing.py" not in seen:
            return {"tool": "read_file",
                    "args": {"path": "pricing.py"}}
        return {"tool": "write_file",
                "args": {"path": "pricing.py",
                         "content": PRICING_FIXED}}
    if "invoice_none" in failed:
        if "invoice.py" not in seen:
            return {"tool": "read_file",
                    "args": {"path": "invoice.py"}}
        return {"tool": "write_file",
                "args": {"path": "invoice.py",
                         "content": INVOICE_FIXED}}
    return {"tool": "finish"}
# end listing


TASK_SINGLE = Contract(
    task_id="FIX-01",
    goal="Reject pct < 0 or pct > 100 in pricing.py.",
    writable=("pricing.py",),
    checks=verify,
)

TASK_INVOICE = Contract(
    task_id="FIX-02",
    goal="Fix pricing bounds and handle pct=None in invoice.py.",
)


def stale_editor(ctx):
    """Passes checks, makes one extra unverified edit, then insists on finishing."""
    writes = sum(1 for e in ctx["events"]
                 if e["kind"] == "gate" and e.get("tool") == "write_file")
    if writes == 0:
        return {"tool": "write_file",
                "args": {"path": "pricing.py", "content": PRICING_FIXED}}
    if writes == 1:
        return {"tool": "write_file",
                "args": {"path": "invoice.py", "content": INVOICE_FIXED}}
    if not ctx["last_checks"]:
        return {"tool": "run_checks"}
    if writes == 2:
        return {"tool": "write_file",
                "args": {"path": "pricing.py",
                         "content": PRICING_FIXED + "# cleanup\n"}}
    return {"tool": "finish"}


def test_tamperer(ctx):
    return {"tool": "write_file",
            "args": {"path": "tests/test_pricing.py",
                     "content": "# deleted\n"}}


def loopy_planner(ctx):
    return {"tool": "list_files"}


def bad_tool_planner(ctx):
    if len(ctx["events"]) == 0:
        return {"tool": "shell", "args": {"cmd": "rm -rf /"}}
    return {"tool": "read_file", "args": {"path": 123}}


def crashing_planner(ctx):
    raise RuntimeError("model endpoint crashed")


def run_trials(contract=TASK_INVOICE):
    planners = [
        ("smart_planner", smart_planner),
        ("stale_editor", stale_editor),
        ("test_tamperer", test_tamperer),
        ("loopy_planner", loopy_planner),
        ("bad_tool_planner", bad_tool_planner),
        ("crashing_planner", crashing_planner),
    ]
    rows = []
    for name, fn in planners:
        r = run_loop(contract, WORKSPACE, fn)
        refusals = sum(1 for e in r.events
                       if e["kind"] == "gate" and not e["allowed"])
        rows.append((name, r.state, r.reason, r.steps_used,
                     r.edits_used, refusals))
    return rows


if __name__ == "__main__":
    for row in run_trials():
        print(row)
