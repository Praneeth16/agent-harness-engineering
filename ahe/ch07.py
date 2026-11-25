"""Chapter 7: durable execution and long-running sessions.

Persists run state, step intents/outcomes, idempotent commits,
and cross-session task ledgers in SQLite (WAL mode) using
atomic transactions so crash recovery never loses acknowledged
state or repeats completed steps.
"""

from copy import deepcopy
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import sqlite3

from .ch02 import (
    INVOICE_FIXED, PRICING_FIXED, Run, TASK_INVOICE,
    WORKSPACE, record, workspace_rev,
)
from .ch04 import (
    CommitLog, HarnessV4, MAX_PROPOSAL_CHARS, refuse,
    start_v4,
)


class SimulatedCrash(RuntimeError):
    """Raised at an injected fault point to test recovery."""


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _check_run_id(run_id):
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")


# listing 7.1
class DurableStore:
    """SQLite intent-before / outcome-after durable log."""

    def __init__(self, db_path=":memory:"):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT NOT NULL,
                rev TEXT NOT NULL,
                verified_rev TEXT,
                steps_used INTEGER NOT NULL,
                edits_used INTEGER NOT NULL,
                files_json TEXT NOT NULL,
                drafts_json TEXT NOT NULL,
                checks_json TEXT NOT NULL,
                events_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                phase TEXT NOT NULL,
                replay TEXT NOT NULL,
                proposal_json TEXT NOT NULL,
                result_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, step_index)
            );
            CREATE TABLE IF NOT EXISTS commits (
                run_id TEXT NOT NULL,
                commit_key TEXT NOT NULL,
                files_hash TEXT NOT NULL,
                result_json TEXT NOT NULL,
                PRIMARY KEY (run_id, commit_key)
            );
            CREATE TABLE IF NOT EXISTS ledger (
                run_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                rev TEXT NOT NULL,
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, item_id)
            );
        """)
        self.conn.commit()

    def _upsert_run(self, cur, run_id, run):
        cur.execute(
            """INSERT OR REPLACE INTO runs VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, run.contract.task_id, run.state,
             run.reason, run.rev, run.verified_rev,
             run.steps_used, run.edits_used,
             json.dumps(run.files, sort_keys=True),
             json.dumps(getattr(run, "drafts", {}),
                        sort_keys=True),
             json.dumps(run.last_checks, sort_keys=True),
             json.dumps(run.events, sort_keys=True),
             _utc_now()),
        )

    def save_run(self, run_id, run):
        _check_run_id(run_id)
        with self.conn:
            self._upsert_run(self.conn, run_id, run)

    def load_run(self, run_id, contract):
        _check_run_id(run_id)
        row = self.conn.execute(
            "SELECT task_id, state, reason, rev, "
            "verified_rev, steps_used, edits_used, "
            "files_json, drafts_json, checks_json, "
            "events_json FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        (tid, state, reason, rev, vrev, s_u, e_u,
         f_js, d_js, c_js, ev_js) = row
        if tid != contract.task_id:
            raise ValueError(
                f"contract mismatch: {tid} != "
                f"{contract.task_id}")
        run = Run(id=run_id, contract=contract,
                  files=json.loads(f_js), rev=rev,
                  events=json.loads(ev_js),
                  verified_rev=vrev,
                  last_checks=json.loads(c_js),
                  state=state, reason=reason,
                  steps_used=s_u, edits_used=e_u)
        run.drafts = json.loads(d_js)
        return run

    def record_intent(self, run_id, step_index, proposal,
                      replay="safe"):
        _check_run_id(run_id)
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO steps VALUES
                   (?, ?, 'intent', ?, ?, NULL, ?)""",
                (run_id, step_index, replay,
                 json.dumps(proposal, sort_keys=True),
                 _utc_now()),
            )

    def complete_step(self, run_id, step_index, result,
                      run):
        # Atomic transaction: step outcome + run snapshot
        # commit together so state cannot diverge on crash.
        _check_run_id(run_id)
        with self.conn:
            self.conn.execute(
                """UPDATE steps SET phase='completed',
                   result_json=?, updated_at=?
                   WHERE run_id=? AND step_index=?""",
                (json.dumps(result, sort_keys=True),
                 _utc_now(), run_id, step_index),
            )
            self._upsert_run(self.conn, run_id, run)

    def get_step(self, run_id, step_index):
        _check_run_id(run_id)
        row = self.conn.execute(
            "SELECT phase, replay, proposal_json, "
            "result_json FROM steps "
            "WHERE run_id=? AND step_index=?",
            (run_id, step_index),
        ).fetchone()
        if row is None:
            return None
        return {
            "phase": row[0], "replay": row[1],
            "proposal": json.loads(row[2]),
            "result": (json.loads(row[3])
                       if row[3] is not None else None),
        }

    def next_step_index(self, run_id):
        row = self.conn.execute(
            "SELECT COUNT(*) FROM steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def commit_once(self, run_id, key, files_hash, fn):
        _check_run_id(run_id)
        with self.conn:
            row = self.conn.execute(
                "SELECT files_hash, result_json "
                "FROM commits WHERE run_id=? "
                "AND commit_key=?", (run_id, key),
            ).fetchone()
            if row is not None:
                if row[0] != files_hash:
                    return refuse(
                        "idempotency key conflict",
                        f"key {key!r} bound to another",
                        "use a new key for a new patch",
                    )
                return {**json.loads(row[1]),
                        "replayed": True}
            res = fn()
            blob = json.dumps(res, sort_keys=True)
            self.conn.execute(
                "INSERT INTO commits VALUES (?,?,?,?)",
                (run_id, key, files_hash, blob),
            )
        return {**res, "replayed": False}

    def close(self):
        self.conn.close()
# end listing


# listing 7.2
REPLAY_MODE = {
    "list_files": "safe",
    "read_lines": "safe",
    "draft_patch": "safe",
    "commit_patch": "safe",
    "finish": "safe",
    "send_webhook": "reconcile",
}

def _sanitize_proposal(raw):
    """Bound and type-check proposals before SQLite."""
    if not isinstance(raw, dict):
        return None, "proposal is not an object"
    tool = raw.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None, "tool name must be a string"
    try:
        blob = json.dumps(raw, sort_keys=True)
    except TypeError:
        return None, "proposal not JSON serializable"
    if len(blob) > MAX_PROPOSAL_CHARS:
        return None, "proposal exceeds size cap"
    return raw, None

class DurableCommitBackend:
    """SQLite-backed CommitLog compatible with HarnessV4."""

    def __init__(self, store, run_id):
        self.store = store
        self.run_id = run_id

    @property
    def commits(self):
        rows = self.store.conn.execute(
            "SELECT commit_key, result_json FROM commits "
            "WHERE run_id=?", (self.run_id,),
        ).fetchall()
        return {k: json.loads(r) for k, r in rows}

    def commit(self, key, files_hash, files):
        def _write():
            cnt = self.store.conn.execute(
                "SELECT COUNT(*) FROM commits "
                "WHERE run_id=?", (self.run_id,),
            ).fetchone()[0] + 1
            return {"key": key, "files_hash": files_hash,
                    "commit_id": f"dc-{cnt:03d}"}
        return self.store.commit_once(
            self.run_id, key, files_hash, _write)

class DurableRunner:
    """Crash-recoverable harness loop over DurableStore."""

    def __init__(self, store):
        self.store = store
        self.model_calls = 0
        self.replayed_steps = 0

    def step(self, run_id, contract, files, planner,
             step_index, crash_after_intent=False,
             crash_after_outcome=False):
        run = self.store.load_run(run_id, contract)
        if run is None:
            run = start_v4(contract, files)
            run.id = run_id
            self.store.save_run(run_id, run)

        # Replay recorded step even if run is now terminal.
        existing = self.store.get_step(run_id, step_index)
        if existing and existing["phase"] == "completed":
            self.replayed_steps += 1
            return run, existing["result"]
        if run.state != "running":
            return run, None

        if existing is None:
            want = self.store.next_step_index(run_id)
            if step_index != want:
                raise ValueError(
                    f"expected step {want}, got "
                    f"{step_index}")
            if run.steps_used >= run.contract.budget.steps:
                run.state = "blocked"
                run.reason = "step budget spent"
                record(run, "stop", state=run.state,
                       reason=run.reason)
                self.store.save_run(run_id, run)
                return run, refuse(
                    "step budget spent", run.reason, "stop")

        if existing and existing["phase"] == "intent":
            proposal = existing["proposal"]
            if existing["replay"] == "reconcile":
                run.state = "needs_reconcile"
                run.reason = (
                    f"step {step_index} crashed in flight")
                record(run, "stop", state=run.state,
                       reason=run.reason)
                self.store.save_run(run_id, run)
                return run, {"error": run.reason}
        else:
            raw = planner(run)
            self.model_calls += 1
            proposal, bad = _sanitize_proposal(raw)
            if bad is not None:
                err = refuse(bad, "invalid proposal",
                             "send bounded tool JSON")
                self.store.record_intent(
                    run_id, step_index,
                    {"tool": "_invalid", "error": bad},
                    "safe")
                record(run, "observation", tool=None,
                       result=err)
                self.store.complete_step(
                    run_id, step_index, err, run)
                return run, err
            tool = proposal["tool"]
            replay = REPLAY_MODE.get(tool, "reconcile")
            self.store.record_intent(
                run_id, step_index, proposal, replay)
            if crash_after_intent:
                raise SimulatedCrash(
                    f"crashed after intent {step_index}")

        backend = DurableCommitBackend(self.store, run_id)
        harness = HarnessV4(backend, teach=True)
        res = harness.dispatch(run, proposal)
        self.store.complete_step(
            run_id, step_index, res, run)
        if crash_after_outcome:
            raise SimulatedCrash(
                f"crashed after outcome {step_index}")
        return run, res
# end listing


# listing 7.3
@dataclass(frozen=True)
class LedgerItem:
    item_id: str
    title: str
    status: str  # pending|in_progress|verified|blocked
    rev: str
    note: str = ""

class TaskLedger:
    """Cross-session milestone ledger backed by SQLite."""

    VALID = frozenset(
        {"pending", "in_progress", "verified", "blocked"})

    def __init__(self, store):
        self.store = store

    def upsert(self, run_id, item):
        _check_run_id(run_id)
        if item.status not in self.VALID:
            raise ValueError(f"bad status: {item.status}")
        with self.conn if hasattr(self, "conn") else (
                self.store.conn):
            self.store.conn.execute(
                """INSERT OR REPLACE INTO ledger VALUES
                   (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, item.item_id, item.title,
                 item.status, item.rev, item.note,
                 _utc_now()),
            )

    def items(self, run_id):
        rows = self.store.conn.execute(
            "SELECT item_id, title, status, rev, note "
            "FROM ledger WHERE run_id=? ORDER BY item_id",
            (run_id,),
        ).fetchall()
        return [LedgerItem(*r) for r in rows]

    def resume_summary(self, run_id):
        items = self.items(run_id)
        done = [{"id": i.item_id, "rev": i.rev}
                for i in items if i.status == "verified"]
        open_items = [
            {"id": i.item_id, "title": i.title,
             "status": i.status, "rev": i.rev}
            for i in items if i.status != "verified"
        ]
        return {"run_id": run_id, "verified": done,
                "remaining": open_items}
# end listing


def _v4_fix_planner(run):
    """Deterministic multi-step v4 planner for FIX-02."""
    seen = [e.get("tool") for e in run.events
            if e.get("kind") == "observation"]
    if "list_files" not in seen:
        return {"tool": "list_files", "args": {}}
    reads = [e.get("result", {}).get("path")
             for e in run.events
             if e.get("kind") == "observation"
             and e.get("tool") == "read_lines"]
    if "pricing.py" not in reads:
        return {"tool": "read_lines",
                "args": {"path": "pricing.py",
                         "start": 1, "limit": 20}}
    if "invoice.py" not in reads:
        return {"tool": "read_lines",
                "args": {"path": "invoice.py",
                         "start": 1, "limit": 20}}
    drafts = getattr(run, "drafts", {})
    if ("pricing.py" not in drafts
            and run.files["pricing.py"] != PRICING_FIXED):
        return {"tool": "draft_patch",
                "args": {"path": "pricing.py",
                         "content": PRICING_FIXED}}
    if ("invoice.py" not in drafts
            and run.files["invoice.py"] != INVOICE_FIXED):
        return {"tool": "draft_patch",
                "args": {"path": "invoice.py",
                         "content": INVOICE_FIXED}}
    if drafts:
        return {"tool": "commit_patch",
                "args": {"key": "fix-02-commit"}}
    return {"tool": "finish", "args": {}}


def benchmark_crash_recovery(crash_steps=(2, 4, 6)):
    """Simulated crash benchmark: naive restart vs WAL."""
    rows = []
    for crash_at in crash_steps:
        naive_calls = 0
        naive_commits = 0
        for attempt in range(2):
            run = start_v4(TASK_INVOICE, WORKSPACE)
            h = HarnessV4(CommitLog(), teach=True)
            for idx in range(10):
                if run.state != "running":
                    break
                prop = _v4_fix_planner(run)
                naive_calls += 1
                if attempt == 0 and idx == crash_at:
                    break
                res = h.dispatch(run, prop)
                if (prop["tool"] == "commit_patch"
                        and "error" not in res):
                    naive_commits += 1

        store = DurableStore(":memory:")
        r1 = DurableRunner(store)
        run_id = f"run-crash-{crash_at}"
        try:
            for idx in range(10):
                run, _ = r1.step(
                    run_id, TASK_INVOICE, WORKSPACE,
                    _v4_fix_planner, idx,
                    crash_after_intent=(idx == crash_at),
                )
                if run.state != "running":
                    break
        except SimulatedCrash:
            pass

        r2 = DurableRunner(store)
        final_run = None
        for idx in range(10):
            final_run, _ = r2.step(
                run_id, TASK_INVOICE, WORKSPACE,
                _v4_fix_planner, idx,
            )
            if final_run.state != "running":
                break

        durable_calls = r1.model_calls + r2.model_calls
        commit_rows = store.conn.execute(
            "SELECT COUNT(*) FROM commits WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        store.close()
        rows.append({
            "crash_step": crash_at,
            "naive_calls": naive_calls,
            "durable_calls": durable_calls,
            "replayed_steps": r2.replayed_steps,
            "naive_commits": naive_commits,
            "durable_commits": commit_rows,
            "final_state": final_run.state,
        })
    return rows
