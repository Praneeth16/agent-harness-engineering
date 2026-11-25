"""Chapter 9: delegation, sub-agents, and A2A interop.

Implements context-isolated read-only sub-agents with explicit
depth, fan-out, step, and output-character budgets; a separate
generator vs. evaluator loop restoring all protected baselines;
and a synchronous in-process A2A-inspired JSON-RPC adapter.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import json

from .ch02 import (
    INVOICE_FIXED, PRICING_FIXED, TASK_INVOICE, WORKSPACE,
    is_protected, is_writable, record, workspace_rev,
)
from .ch03 import assemble_context, build_prefix, sha12
from .ch04 import CommitLog, HarnessV4, refuse, start_v4


# listing 9.1
READ_ONLY_TOOLS = frozenset(
    {"list_files", "read_lines", "finish"})

@dataclass(frozen=True)
class SubAgentBudget:
    max_depth: int = 1
    max_fanout: int = 2
    max_steps: int = 4
    max_output_chars: int = 360

class DelegationController:
    """Context-isolated read-only sub-agent delegator."""

    def __init__(self, budget=None, depth=0):
        self.budget = budget or SubAgentBudget()
        self.depth = depth
        self.fanout_used = 0

    def delegate(self, parent_files, parent_contract,
                 parent_tools, goal, child_planner,
                 requested_tools=None):
        if self.depth >= self.budget.max_depth:
            return refuse(
                "depth budget exceeded",
                f"depth={self.depth} >= "
                f"{self.budget.max_depth}",
                "answer from current context",
            )
        if self.fanout_used >= self.budget.max_fanout:
            return refuse(
                "fan-out budget exceeded",
                f"used {self.fanout_used}/"
                f"{self.budget.max_fanout} delegations",
                "synthesize prior sub-agent findings",
            )
        req = (set(READ_ONLY_TOOLS)
               if requested_tools is None
               else set(requested_tools))
        allowed = req & set(parent_tools) & READ_ONLY_TOOLS
        if not allowed:
            return refuse(
                "no permitted read-only tools",
                "delegation cannot widen capabilities",
                "request subset of read-only tools",
            )
        self.fanout_used += 1
        # Isolated child run: cannot mutate parent files
        # and child reads stay out of parent context.
        child_run = start_v4(parent_contract,
                             deepcopy(parent_files))
        child_harness = HarnessV4(
            CommitLog(), teach=True, allowed_tools=allowed)
        notes = []
        steps = 0
        for _ in range(self.budget.max_steps):
            prop = child_planner(child_run, goal)
            steps += 1
            tool = (prop.get("tool")
                    if isinstance(prop, dict) else None)
            if not isinstance(tool, str) or (
                    tool not in allowed):
                notes.append(f"blocked:{tool}")
                break
            if tool == "finish":
                args = prop.get("args", {})
                if not isinstance(args, dict) or args:
                    notes.append("blocked:finish_args")
                    break
                raw_sum = prop.get("summary", "done")
                notes.append(raw_sum
                             if isinstance(raw_sum, str)
                             else "done")
                break
            res = child_harness.dispatch(child_run, prop)
            if "error" not in res and tool == "read_lines":
                notes.append(
                    f"{res['path']}:L{res['start']}")
        cap = self.budget.max_output_chars
        summary = "; ".join(notes)[:cap]
        return {
            "ok": True,
            "goal": str(goal)[:120],
            "summary": summary,
            "child_steps": steps,
            "isolated": True,
        }
# end listing


# listing 9.2
@dataclass(frozen=True)
class EvalVerdict:
    passed: bool
    workspace_rev: str
    failed_checks: tuple
    findings: tuple

def evaluate_candidate(contract, files,
                       evaluator_checks=None):
    """Independent evaluator: grades an immutable copy."""
    snap = deepcopy(files)
    rev = workspace_rev(snap)
    checks = contract.checks(deepcopy(snap))
    failed = sorted(k for k, v in checks.items()
                    if v != "pass")
    findings = []
    if failed:
        findings.append(
            f"deterministic failures: {', '.join(failed)}")
    for name, fn in (evaluator_checks or {}).items():
        msg = fn(deepcopy(snap))
        if msg:
            failed.append(name)
            findings.append(f"{name}: {msg}")
    return EvalVerdict(
        passed=(len(failed) == 0),
        workspace_rev=rev,
        failed_checks=tuple(failed),
        findings=tuple(findings),
    )

def _restore_baseline(contract, baseline, proposed):
    """Keep only writable edits; restore protected files."""
    clean = deepcopy(baseline)
    if isinstance(proposed, dict):
        for path, content in proposed.items():
            if (isinstance(path, str)
                    and isinstance(content, str)
                    and not is_protected(path, contract)
                    and is_writable(path, contract)):
                clean[path] = content
    return clean

def generator_evaluator_loop(contract, files, generator,
                             evaluator_checks=None,
                             max_rounds=3):
    """Separate generator edits from independent grading."""
    baseline = deepcopy(files)
    current = deepcopy(files)
    history = []
    for round_idx in range(1, max_rounds + 1):
        feedback = history[-1] if history else None
        raw = generator(deepcopy(current), feedback,
                        round_idx)
        candidate = _restore_baseline(
            contract, baseline, raw)
        verdict = evaluate_candidate(
            contract, candidate, evaluator_checks)
        current = candidate
        history.append(verdict)
        if verdict.passed:
            return {"status": "completed",
                    "rounds": round_idx,
                    "files": deepcopy(current),
                    "history": history}
    return {"status": "failed", "rounds": max_rounds,
            "files": deepcopy(current), "history": history}
# end listing


# listing 9.3
@dataclass(frozen=True)
class AgentCard:
    name: str
    version: str
    protocol_version: str = "1.0.0"
    skills: tuple = (
        "workspace.inspect", "workspace.verify")
    input_modes: tuple = ("application/json",)
    output_modes: tuple = ("application/json",)

class A2AServer:
    """In-process A2A-inspired JSON-RPC task adapter."""

    def __init__(self, card, worker_fn):
        self.card = card
        self.worker_fn = worker_fn
        self._tasks = {}
        self._seq = 0

    def agent_card(self):
        return asdict(self.card)

    def _err(self, req_id, code, msg):
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": msg}}

    def handle(self, req):
        if (not isinstance(req, dict)
                or req.get("jsonrpc") != "2.0"):
            return self._err(None, -32600,
                             "invalid request")
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._err(req_id, -32602,
                             "params must be object")

        if method == "agent/card":
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": self.agent_card()}

        if method == "tasks/send":
            skill = params.get("skill")
            if (not isinstance(skill, str)
                    or skill not in self.card.skills):
                return self._err(req_id, -32602,
                                 "unknown skill")
            self._seq += 1
            tid = f"a2a-task-{self._seq}"
            task = {"id": tid, "state": "working",
                    "artifact": None}
            self._tasks[tid] = task
            # Synchronous in-process execution; masks raw
            # worker exceptions behind a public error code.
            try:
                inp = deepcopy(params.get("input", {}))
                art = self.worker_fn(skill, inp)
                task["state"] = "completed"
                task["artifact"] = {
                    "mime": "application/json", "data": art}
            except Exception:
                task["state"] = "failed"
                task["error"] = "worker execution failed"
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": deepcopy(task)}

        if method in ("tasks/get", "tasks/cancel"):
            tid = params.get("id")
            if not isinstance(tid, str):
                return self._err(req_id, -32602,
                                 "id must be string")
            if tid not in self._tasks:
                return self._err(req_id, -32004,
                                 "task not found")
            t = self._tasks[tid]
            if (method == "tasks/cancel"
                    and t["state"] in (
                        "submitted", "working")):
                t["state"] = "canceled"
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": deepcopy(t)}

        return self._err(req_id, -32601,
                         "method not found")
# end listing


def make_noisy_workspace():
    """Workspace with 8 extra log/spec files for Ch 9 probe."""
    ws = deepcopy(WORKSPACE)
    for idx in range(1, 9):
        lines = [
            f"# module_{idx}.py — helper {idx} line {n}"
            for n in range(1, 22)
        ]
        ws[f"docs/ref_{idx:02d}.md"] = "\n".join(lines) + "\n"
    return ws


def _readonly_inspector(child_run, goal):
    seen = [e.get("tool") for e in child_run.events
            if e.get("kind") == "observation"]
    if "list_files" not in seen:
        return {"tool": "list_files", "args": {}}
    reads = [e.get("result", {}).get("path")
             for e in child_run.events
             if e.get("kind") == "observation"
             and e.get("tool") == "read_lines"]
    for target in ("pricing.py", "invoice.py"):
        if target not in reads:
            return {"tool": "read_lines",
                    "args": {"path": target,
                             "start": 1, "limit": 20}}
    return {
        "tool": "finish",
        "summary": (
            "pricing.py misses pct > 100 guard; "
            "invoice.py fails when pct is None"
        ),
    }


def benchmark_context_isolation():
    """Compare inline parent exploration vs sub-agent digest."""
    ws = make_noisy_workspace()
    tool_names = ["list_files", "read_lines", "draft_patch",
                  "commit_patch", "finish"]
    prefix = build_prefix(TASK_INVOICE, ws, tool_names)
    p_hash_before = sha12(prefix)

    inline_run = start_v4(TASK_INVOICE, ws)
    h = HarnessV4(CommitLog(), teach=True)
    for idx in range(1, 9):
        h.dispatch(inline_run, {
            "tool": "read_lines",
            "args": {"path": f"docs/ref_{idx:02d}.md",
                     "start": 1, "limit": 15},
        })
    for target in ("pricing.py", "invoice.py"):
        h.dispatch(inline_run, {
            "tool": "read_lines",
            "args": {"path": target, "start": 1, "limit": 20},
        })
    inline_raw_chars = sum(
        len(json.dumps(e, sort_keys=True))
        for e in inline_run.events
        if e.get("kind") == "observation"
    )
    inline_prompt, inline_meta = assemble_context(
        inline_run, char_budget=2600, tool_names=tool_names)

    del_run = start_v4(TASK_INVOICE, ws)
    ctrl = DelegationController(SubAgentBudget())
    out = ctrl.delegate(
        del_run.files, TASK_INVOICE,
        ["list_files", "read_lines", "draft_patch", "finish"],
        "locate billing bugs", _readonly_inspector,
    )
    record(del_run, "observation", tool="delegate_readonly",
           result={"summary": out["summary"]})
    del_raw_chars = sum(
        len(json.dumps(e, sort_keys=True))
        for e in del_run.events
        if e.get("kind") == "observation"
    )
    del_prompt, del_meta = assemble_context(
        del_run, char_budget=2600, tool_names=tool_names)

    return {
        "prefix_hash_preserved": (
            p_hash_before == inline_meta["prefix_hash"]
            == del_meta["prefix_hash"]
        ),
        "inline_raw_chars": inline_raw_chars,
        "inline_evicted": len(inline_meta["omitted_seqs"]),
        "inline_prompt_chars": len(inline_prompt),
        "delegated_raw_chars": del_raw_chars,
        "delegated_evicted": len(del_meta["omitted_seqs"]),
        "delegated_prompt_chars": len(del_prompt),
        "digest_chars": len(out["summary"]),
    }
