"""Chapter 3: context assembly and KV-cache architecture.

Extends Chapter 2 with progressive disclosure (AGENTS.md,
read_lines, inspect_log), a prefix-stable context assembler with
a hard character budget, and an exact prefix-cache measurement.
"""

from copy import deepcopy
import hashlib
import json

from .ch02 import (Budget, Contract, INVOICE_BUGGY, INVOICE_FIXED,
                   PRICING_BUGGY, PRICING_FIXED, Run, TEST_SPEC,
                   TOOLS, gate, is_protected, is_writable, record,
                   run_loop, step, verify_invoice, workspace_rev)

# listing 3.1
AGENTS_MD = """\
# Workspace map (AGENTS.md)
- pricing.py: net_cents(price, pct), reject pct < 0 or > 100
- invoice.py: line_total(price, pct), None means 0%
- docs/billing_spec.md: full policy (read_lines on demand)
- tests/test_pricing.py: harness-owned, never edit
"""

BILLING_SPEC = "\n".join(
    f"Section {i:02d}: Billing rule details and audit note "
    f"for regional rounding and tax codes ({i * 17})."
    for i in range(1, 29)
) + "\n"

CI_TRACE = "\n".join(
    f"[ci {i:02d}] runner step output, env digest={i:04x}, "
    f"elapsed={i * 12}ms, status=ok"
    for i in range(1, 32)
) + "\n"

LARGE_WORKSPACE = {
    "AGENTS.md": AGENTS_MD,
    "pricing.py": PRICING_BUGGY,
    "invoice.py": INVOICE_BUGGY,
    "docs/billing_spec.md": BILLING_SPEC,
    "logs/ci_trace.log": CI_TRACE,
    "tests/test_pricing.py": TEST_SPEC,
}

def sha12(text):
    return hashlib.sha256(text.encode()).hexdigest()[:12]

def build_prefix(contract, files, tool_names):
    # Immutable across every turn of the run so prompt
    # caches stay warm from byte 0 onward.
    lines = [
        f"TASK: {contract.task_id}",
        f"GOAL: {contract.goal}",
        "WRITABLE: " + ", ".join(contract.writable),
        "PROTECTED: " + ", ".join(contract.protected),
        "TOOLS: " + ", ".join(sorted(tool_names)),
    ]
    if "AGENTS.md" in files:
        lines.append(files["AGENTS.md"].strip())
    return "\n".join(lines) + "\n---\n"
# end listing

# listing 3.2
class OverBudget(Exception):
    pass

def _obs_events(run):
    return [e for e in run.events
            if e["kind"] == "observation"]

def _render(prefix, required, recent):
    return (prefix
            + "STATE:\n" + json.dumps(required,
                                      sort_keys=True)
            + "\nRECENT:\n" + json.dumps(recent,
                                         sort_keys=True))

def assemble_context(run, char_budget=2400,
                     tool_names=None):
    names = tool_names or list(TOOLS_V3) + ["finish"]
    prefix = build_prefix(run.contract, run.files, names)
    obs = _obs_events(run)
    edited = sorted({
        e["result"]["path"] for e in obs
        if e.get("tool") == "write_file"
        and "path" in e.get("result", {})
    })
    failed = sorted(k for k, v in run.last_checks.items()
                    if v != "pass")
    required = {
        "rev": run.rev,
        "verified": run.verified_rev == run.rev,
        "failed_checks": failed,
        "edited": edited,
        "steps_left": run.contract.budget.steps
        - run.steps_used,
        "edits_left": run.contract.budget.edits
        - run.edits_used,
        "archive": [],
    }
    window = list(obs)
    omitted = []
    while True:
        required["archive"] = [
            {"seq": e["seq"], "tool": e["tool"],
             "chars": len(json.dumps(e["result"]))}
            for e in omitted
        ]
        prompt = _render(prefix, required, window)
        if len(prompt) <= char_budget:
            break
        if not window:
            raise OverBudget("context budget exceeded")
        omitted.append(window.pop(0))
    meta = {
        "prefix_hash": sha12(prefix),
        "bundle_hash": sha12(prompt),
        "prefix_chars": len(prefix),
        "total_chars": len(prompt),
        "window_obs": len(window),
        "omitted_seqs": [e["seq"] for e in omitted],
    }
    return prompt, meta
# end listing

# listing 3.3
def t_read_lines(run, path, start, limit):
    if path not in run.files:
        return {"error": f"unknown file: {path}"}
    lines = run.files[path].splitlines()
    s = max(1, int(start)) - 1
    n = max(1, min(int(limit), 40))
    chunk = lines[s:s + n]
    return {"path": path, "start": s + 1,
            "total_lines": len(lines),
            "lines": chunk,
            "more": (s + n) < len(lines)}

def t_inspect_log(run, seq, start, limit):
    target = int(seq)
    hit = next((e for e in run.events
                if e["seq"] == target), None)
    if hit is None:
        return {"error": f"unknown seq: {target}"}
    blob = json.dumps(hit, sort_keys=True)
    s = max(0, int(start))
    n = max(1, min(int(limit), 400))
    return {"seq": target, "start": s,
            "total_chars": len(blob),
            "slice": blob[s:s + n],
            "more": (s + n) < len(blob)}

TOOLS_V3 = {
    **TOOLS,
    "read_lines": (("path", "start", "limit"),
                   t_read_lines),
    "inspect_log": (("seq", "start", "limit"),
                    t_inspect_log),
}
# end listing

# listing 3.4
def lcp_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i

def measure_cache(prompts, char_budget=2400):
    total = sum(len(p) for p in prompts)
    cached = sum(lcp_len(a, b)
                 for a, b in zip(prompts, prompts[1:]))
    over = sum(1 for p in prompts if len(p) > char_budget)
    hit = (100.0 * cached / total) if total else 0.0
    return {
        "steps": len(prompts),
        "max_chars": max((len(p) for p in prompts),
                         default=0),
        "total_chars": total,
        "cached_chars": cached,
        "uncached_chars": total - cached,
        "cache_hit_pct": round(hit, 1),
        "over_budget": over,
    }
# end listing


INT_ARGS = {"start", "limit", "seq"}


def gate_v3(run, proposal, tools=TOOLS_V3):
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
        want = int if k in INT_ARGS else str
        if type(v) is not want:
            return f"{k} must be {want.__name__}"
        if want is int and v < 0:
            return f"{k} must be >= 0"
    if tool == "write_file":
        path = args["path"]
        if is_protected(path, run.contract):
            return f"protected path: {path}"
        if not is_writable(path, run.contract):
            return f"path not writable: {path}"
        if run.edits_used >= run.contract.budget.edits:
            return "edit budget spent"
    return None


def step_v3(run, planner, char_budget=2400, tools=TOOLS_V3):
    if not hasattr(run, "bundles"):
        run.bundles = {}
    if run.state != "running":
        raise RuntimeError(f"run already {run.state}")
    if run.steps_used >= run.contract.budget.steps:
        run.state, run.reason = "blocked", "step budget spent"
        record(run, "stop", state=run.state, reason=run.reason)
        return run
    run.steps_used += 1
    try:
        prompt, meta = assemble_context(
            run, char_budget=char_budget,
            tool_names=list(tools) + ["finish"])
    except OverBudget as exc:
        run.state, run.reason = "blocked", str(exc)
        record(run, "stop", state=run.state, reason=run.reason)
        return run
    ev = record(run, "context", **meta)
    run.bundles[ev["seq"]] = prompt
    try:
        proposal = planner(prompt, deepcopy(meta))
    except Exception as exc:
        run.state = "failed"
        run.reason = f"planner error: {type(exc).__name__}"
        record(run, "stop", state=run.state, reason=run.reason)
        return run
    record(run, "proposal", proposal=proposal)
    problem = gate_v3(run, proposal, tools=tools)
    if problem:
        record(run, "gate", allowed=False, reason=problem)
        record(run, "observation",
               tool=proposal.get("tool")
               if isinstance(proposal, dict) else None,
               result={"error": problem})
        return run
    tool = proposal["tool"]
    args = proposal.get("args", {})
    record(run, "gate", allowed=True, tool=tool)
    if tool == "finish":
        from .ch02 import finish_check
        err = finish_check(run)
        if err is None:
            run.state, run.reason = "completed", "verified"
            record(run, "stop", state=run.state, reason=run.reason)
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
        record(run, "stop", state=run.state, reason=run.reason)
        return run
    record(run, "observation", tool=tool, result=out)
    return run


def run_v3(contract, files, planner, char_budget=2400):
    snap = deepcopy(files)
    run = Run(id=f"run-{contract.task_id}",
              contract=contract, files=snap,
              rev=workspace_rev(snap))
    run.bundles = {}
    while run.state == "running":
        step_v3(run, planner, char_budget=char_budget)
    return run


TASK_LARGE = Contract(
    task_id="FIX-03",
    goal="Fix pricing bounds and invoice None handling under budget.",
    budget=Budget(steps=12, edits=4),
)


def scripted_large_planner():
    """Reproduces an 8-step workspace investigation with large reads."""
    plan = [
        {"tool": "run_checks"},
        {"tool": "read_file", "args": {"path": "docs/billing_spec.md"}},
        {"tool": "read_file", "args": {"path": "logs/ci_trace.log"}},
        {"tool": "read_file", "args": {"path": "pricing.py"}},
        {"tool": "write_file",
         "args": {"path": "pricing.py", "content": PRICING_FIXED}},
        {"tool": "read_file", "args": {"path": "invoice.py"}},
        {"tool": "write_file",
         "args": {"path": "invoice.py", "content": INVOICE_FIXED}},
        {"tool": "run_checks"},
        {"tool": "finish"},
    ]
    it = iter(plan)
    return lambda prompt, meta: next(it, {"tool": "finish"})


def compare_strategies(char_budget=2400):
    """Compare naive transcript, front-rebuilt, and prefix-stable on the same run."""
    run = run_v3(TASK_LARGE, LARGE_WORKSPACE,
                 scripted_large_planner(), char_budget=char_budget)
    names = list(TOOLS_V3) + ["finish"]
    prefix = build_prefix(run.contract, LARGE_WORKSPACE, names)

    naive_prompts = []
    rebuild_front_prompts = []
    stable_prompts = []

    obs_so_far = []
    step_no = 0
    for ev in run.events:
        if ev["kind"] == "context":
            step_no += 1
            stable_prompts.append(run.bundles[ev["seq"]])
            # Strategy 1: append-only raw transcript (never compacts)
            naive = prefix + "TRANSCRIPT:\n" + json.dumps(
                obs_so_far, sort_keys=True)
            naive_prompts.append(naive)
            # Strategy 2: dynamic header at byte 0 (mutates every turn)
            header = json.dumps({"step": step_no,
                                 "obs": len(obs_so_far)},
                                sort_keys=True) + "\n"
            window = list(obs_so_far)
            while True:
                body = (header + prefix + "RECENT:\n"
                        + json.dumps(window, sort_keys=True))
                if len(body) <= char_budget or not window:
                    break
                window.pop(0)
            rebuild_front_prompts.append(body)
        elif ev["kind"] == "observation":
            obs_so_far.append(ev)

    return {
        "run": run,
        "naive_transcript": measure_cache(naive_prompts, char_budget),
        "rebuild_front": measure_cache(rebuild_front_prompts, char_budget),
        "prefix_stable": measure_cache(stable_prompts, char_budget),
    }


if __name__ == "__main__":
    res = compare_strategies()
    for k in ("naive_transcript", "rebuild_front", "prefix_stable"):
        print(k, res[k])
