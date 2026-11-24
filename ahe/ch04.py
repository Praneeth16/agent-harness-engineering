"""Chapter 4: tools, actionable refusals, and hooks.

Builds declarative tool specifications, read/propose/commit with
idempotency keys, a read-parallel / write-serial batch scheduler,
and zero-token out-of-band before/after hooks.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json

from .ch02 import (Budget, Contract, INVOICE_FIXED, PRICING_FIXED,
                   Run, WORKSPACE, is_protected, is_writable, record,
                   t_list_files, verify_invoice, workspace_rev)
from .ch03 import LARGE_WORKSPACE, t_read_lines

# listing 4.1
PREVIEW_CAP = 60
MAX_PROPOSAL_CHARS = 2000

@dataclass(frozen=True)
class Param:
    name: str
    kind: type
    doc: str
    valid: object = None
    fix: str = ""

@dataclass(frozen=True)
class Tool:
    name: str
    effect: str          # "read", "propose", or "commit"
    doc: str
    params: tuple
    fn: object

def preview(value, cap=PREVIEW_CAP):
    raw = repr(value)
    return raw if len(raw) <= cap else raw[:cap - 3] + "..."

def refuse(what, why, fix):
    return {"error": what, "why": why, "fix": fix}

def describe(tools, full=True):
    lines = []
    for t in tools:
        if not full:
            pnames = ", ".join(p.name for p in t.params)
            lines.append(f"- {t.name} {{{pnames}}}")
            continue
        pdocs = ", ".join(
            f'"{p.name}": {p.kind.__name__} ({p.doc})'
            for p in t.params
        )
        lines.append(
            f"- {t.name} [{t.effect}]: {t.doc} "
            f"args={{{pdocs}}}"
        )
    return "\n".join(lines)

def check_args(run, tool, args):
    if not isinstance(args, dict):
        return refuse("args is not an object",
                      "tools take named JSON arguments",
                      'pass {"arg": value}')
    want = {p.name for p in tool.params}
    got = set(args)
    if got != want:
        msg = (f"want {sorted(want)}, "
               f"got {preview(sorted(got))}")
        return refuse(
            f"{tool.name} arg mismatch", msg[:60],
            "pass exact declared parameter names",
        )
    for p in tool.params:
        val = args[p.name]
        if type(val) is not p.kind:
            hint = p.fix or f"send {p.kind.__name__}"
            return refuse(
                f"bad {p.name}",
                f"expected {p.kind.__name__}, "
                f"got {type(val).__name__}",
                hint,
            )
        if p.valid is not None:
            try:
                ok = bool(p.valid(run, val))
            except Exception:
                ok = False
            if not ok:
                return refuse(
                    f"invalid {p.name}: {preview(val)}",
                    p.doc,
                    p.fix or f"supply a valid {p.name}",
                )
    return None
# end listing

# listing 4.2
class CommitLog:
    """Backend commit store keyed by idempotency key."""

    def __init__(self):
        self.commits = {}
        self.writes = 0

    def commit(self, key, files_hash, files):
        if key in self.commits:
            prev = self.commits[key]
            if prev["files_hash"] != files_hash:
                return refuse(
                    "idempotency key conflict",
                    f"key {key!r} already used for another "
                    f"patch",
                    "use a new key for a changed patch",
                )
            return {**prev, "replayed": True}
        self.writes += 1
        rec = {"key": key, "files_hash": files_hash,
               "commit_id": f"c-{self.writes:03d}",
               "replayed": False}
        self.commits[key] = rec
        return dict(rec)

def t_draft_patch(run, path, content):
    if not hasattr(run, "drafts"):
        run.drafts = {}
    run.drafts[path] = content
    staged = deepcopy(run.files)
    staged.update(run.drafts)
    res = run.contract.checks(staged)
    failed = sorted(k for k, v in res.items()
                    if v != "pass")
    return {"staged": sorted(run.drafts),
            "checks": res, "failed": failed}

def make_commit_tool(backend):
    def _commit(run, key):
        drafts = getattr(run, "drafts", {})
        if not drafts:
            if key in backend.commits:
                return {**backend.commits[key],
                        "replayed": True}
            return refuse("nothing staged",
                          "no draft_patch edits exist",
                          "call draft_patch before commit")
        staged = deepcopy(run.files)
        staged.update(drafts)
        res = run.contract.checks(staged)
        failed = sorted(k for k, v in res.items()
                        if v != "pass")
        if failed:
            return refuse("checks failing: "
                          + ", ".join(failed),
                          "cannot commit unverified drafts",
                          "fix failing checks first")
        fhash = workspace_rev(staged)
        out = backend.commit(key, fhash, staged)
        if "error" not in out:
            run.files = staged
            run.rev = fhash
            run.verified_rev = fhash
            run.last_checks = dict(res)
            run.edits_used += len(drafts)
            run.drafts = {}
        return out
    return _commit
# end listing

# listing 4.3
def execute_batch(harness, run, proposals, max_workers=4):
    # All-read batches run concurrently when run is active
    # and has budget; mixed batches run serially in order.
    if not proposals:
        return []
    fits = (run.state == "running"
            and run.steps_used + len(proposals)
            <= run.contract.budget.steps)
    all_reads = fits and all(
        isinstance(p, dict)
        and isinstance(p.get("tool"), str)
        and p["tool"] in harness.tools
        and harness.tools[p["tool"]].effect == "read"
        and p["tool"] in harness.allowed_tools
        for p in proposals
    )
    if all_reads and len(proposals) > 1:
        def _eval_read(prop):
            err = harness.check_proposal(run, prop)
            if err:
                return prop, err, None
            tool = harness.tools[prop["tool"]]
            return prop, None, tool.fn(
                run, **prop.get("args", {}))
        with ThreadPoolExecutor(
                max_workers=min(max_workers, len(proposals))
        ) as pool:
            results = list(pool.map(_eval_read, proposals))
        outs = []
        for prop, err, res in results:
            outs.append(
                harness.record_precomputed(
                    run, prop, err, res))
        return outs
    return [harness.dispatch(run, p) for p in proposals]
# end listing

# listing 4.4
def make_tools(backend):
    def _known_path(run, p):
        return p in run.files

    def _writable_path(run, p):
        return (not is_protected(p, run.contract)
                and is_writable(p, run.contract))

    def _good_key(run, k):
        return 1 <= len(k) <= 40 and k.strip() == k

    return {
        "list_files": Tool(
            "list_files", "read",
            "list workspace file paths", (), t_list_files),
        "read_lines": Tool(
            "read_lines", "read",
            "read bounded line range (1..40 lines)",
            (Param("path", str, "existing workspace path",
                   _known_path, "use path from list_files"),
             Param("start", int, "1-based start line >= 1",
                   lambda r, v: v >= 1, "send start >= 1"),
             Param("limit", int, "lines to read (1..40)",
                   lambda r, v: 1 <= v <= 40,
                   "send limit between 1 and 40")),
            t_read_lines),
        "draft_patch": Tool(
            "draft_patch", "propose",
            "stage an edit and run checks on the draft",
            (Param("path", str, "writable non-test path",
                   _writable_path,
                   "edit writable paths, never tests/"),
             Param("content", str, "complete file text",
                   lambda r, v: len(v) > 0,
                   "provide non-empty file content")),
            t_draft_patch),
        "commit_patch": Tool(
            "commit_patch", "commit",
            "commit verified drafts with idempotency key",
            (Param("key", str, "unique 1..40 char key",
                   _good_key, "pass key like 'fix-01'"),),
            make_commit_tool(backend)),
    }

class HarnessV4:
    """Declarative tools, unified gate, zero-token hooks."""

    def __init__(self, backend, before=(), after=(),
                 teach=True, allowed_tools=None):
        self.backend = backend
        self.tools = make_tools(backend)
        self.before = list(before)
        self.after = list(after)
        self.teach = teach
        self.allowed_tools = (
            frozenset(allowed_tools)
            if allowed_tools is not None
            else frozenset(self.tools) | {"finish"}
        )

    def _planner_view(self, refusal):
        if self.teach:
            return refusal
        return {"error": "refused"}

    def check_proposal(self, run, proposal):
        try:
            raw = json.dumps(proposal)
        except TypeError:
            return refuse("unserializable proposal",
                          "proposal must be JSON-safe",
                          "return plain JSON objects")
        if len(raw) > MAX_PROPOSAL_CHARS:
            lim = f"exceeds {MAX_PROPOSAL_CHARS} chars"
            return refuse("oversized proposal", lim,
                          "shorten proposal arguments")
        if not isinstance(proposal, dict):
            return refuse("proposal is not an object",
                          "return a JSON object",
                          'send {"tool": t, "args": {}}')
        name = proposal.get("tool")
        if (not isinstance(name, str)
                or name not in self.allowed_tools):
            allowed = ", ".join(sorted(self.allowed_tools))
            return refuse(
                f"tool not permitted: {preview(name)}",
                "unknown or outside scope",
                "choose from: " + allowed,
            )
        if name == "finish":
            args = proposal.get("args", {})
            if not isinstance(args, dict) or args:
                return refuse("finish takes no arguments",
                              "unexpected args on finish",
                              'send {"tool": "finish"}')
            return None
        tool = self.tools[name]
        args = proposal.get("args", {})
        err = check_args(run, tool, args)
        if err:
            return err
        for hook in self.before:
            verdict = hook(run, tool, args)
            if verdict is not None:
                return verdict
        return None

    def _run_after(self, run, name, result):
        for hook in self.after:
            try:
                hook(deepcopy(run.rev), name,
                     deepcopy(result))
            except Exception as exc:
                record(run, "hook_error", hook=str(hook),
                       error=type(exc).__name__)

    def record_precomputed(self, run, proposal, err, res):
        run.steps_used += 1
        record(run, "proposal", proposal=proposal)
        if err is not None:
            record(run, "gate", allowed=False, **err)
            obs = self._planner_view(err)
            record(run, "observation",
                   tool=proposal.get("tool")
                   if isinstance(proposal, dict) else None,
                   result=obs)
            return obs
        name = proposal["tool"]
        record(run, "gate", allowed=True, tool=name)
        record(run, "observation", tool=name, result=res)
        self._run_after(run, name, res)
        return res

    def dispatch(self, run, proposal):
        if run.state != "running":
            return refuse("run is over",
                          f"state is {run.state}",
                          "start a new run")
        if run.steps_used >= run.contract.budget.steps:
            run.state = "blocked"
            run.reason = "step budget spent"
            record(run, "stop", state=run.state,
                   reason=run.reason)
            return refuse("step budget spent",
                          "no steps left", "stop")
        run.steps_used += 1
        err = self.check_proposal(run, proposal)
        oversized = (
            err is not None
            and err["error"] == "oversized proposal"
        )
        logged = ({"truncated": True} if oversized
                  else proposal)
        record(run, "proposal", proposal=logged)
        if err is not None:
            record(run, "gate", allowed=False, **err)
            obs = self._planner_view(err)
            record(run, "observation",
                   tool=proposal.get("tool")
                   if isinstance(proposal, dict) else None,
                   result=obs)
            return obs
        name = proposal["tool"]
        record(run, "gate", allowed=True, tool=name)
        if name == "finish":
            from .ch02 import finish_check
            ferr = finish_check(run)
            if ferr is None:
                run.state = "completed"
                run.reason = "verified"
                record(run, "stop", state=run.state,
                       reason=run.reason)
                return {"state": "completed"}
            obs = self._planner_view(
                refuse(ferr, "completion check failed",
                       "commit verified drafts first"))
            record(run, "observation", tool="finish",
                   result=obs)
            return obs
        tool = self.tools[name]
        out = tool.fn(run, **proposal.get("args", {}))
        record(run, "observation", tool=name, result=out)
        self._run_after(run, name, out)
        return out
# end listing


def start_v4(contract=None, files=None):
    c = contract or Contract(
        task_id="FIX-04",
        goal="Fix pricing.py and invoice.py, then commit_patch.",
        budget=Budget(steps=14, edits=4),
    )
    snap = deepcopy(files or LARGE_WORKSPACE)
    return Run(id=f"run-{c.task_id}", contract=c, files=snap,
               rev=workspace_rev(snap))


def demo_happy_run():
    backend = CommitLog()
    audit = []
    h = HarnessV4(backend, after=[lambda rev, t, r: audit.append((rev, t))])
    run = start_v4()
    reads = execute_batch(h, run, [
        {"tool": "read_lines", "args": {"path": "pricing.py", "start": 1, "limit": 20}},
        {"tool": "read_lines", "args": {"path": "invoice.py", "start": 1, "limit": 20}},
    ])
    h.dispatch(run, {"tool": "draft_patch",
                     "args": {"path": "pricing.py", "content": PRICING_FIXED}})
    h.dispatch(run, {"tool": "draft_patch",
                     "args": {"path": "invoice.py", "content": INVOICE_FIXED}})
    c1 = h.dispatch(run, {"tool": "commit_patch", "args": {"key": "fix-04-v1"}})
    c2 = h.dispatch(run, {"tool": "commit_patch", "args": {"key": "fix-04-v1"}})
    h.dispatch(run, {"tool": "finish"})
    return run, backend, audit, reads, c1, c2
