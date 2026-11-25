"""Chapter 10: tracing, evaluation harnesses, and release gates.

Implements OpenTelemetry GenAI semantic spans, an independent
dev/held-out evaluation harness computing pass@k, pass^k, and
cost-per-verified-success, and a CI release gate that blocks
regressions hidden behind optimistic dev scores.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import datetime as dt
import hashlib
import json
import math
import uuid

from .ch01 import PRICING_BUGGY, PRICING_FIXED, TEST_SPEC
from .ch02 import (
    Contract, INVOICE_BUGGY, INVOICE_FIXED, TASK_INVOICE,
    WORKSPACE, verify_invoice,
)


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


# listing 10.1
@dataclass(frozen=True)
class GenAISpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    operation: str           # e.g. "chat" | "execute_tool"
    model: str
    harness_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: float
    status: str              # "ok" | "error"
    timestamp: str

    def to_otel(self):
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": f"gen_ai.{self.operation}",
            "status": self.status,
            "duration_ms": self.duration_ms,
            "attributes": {
                "gen_ai.operation.name": self.operation,
                "gen_ai.request.model": self.model,
                "gen_ai.usage.input_tokens":
                    self.input_tokens,
                "gen_ai.usage.output_tokens":
                    self.output_tokens,
                "ahe.harness.version": self.harness_version,
                "ahe.cost_usd": self.cost_usd,
            },
        }

class TraceExporter:
    """Append-only OTel GenAI span collector & JSONL."""

    def __init__(self, harness_version="v2.10"):
        self.harness_version = harness_version
        self.spans = []

    def emit(self, trace_id, operation, model,
             input_tokens=0, output_tokens=0, cost_usd=0.0,
             duration_ms=0.0, status="ok",
             parent_span_id=None):
        span = GenAISpan(
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_span_id,
            operation=operation,
            model=model,
            harness_version=self.harness_version,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost_usd=float(cost_usd),
            duration_ms=float(duration_ms),
            status=status,
            timestamp=_utc_now(),
        )
        self.spans.append(span)
        return span

    def to_jsonl(self):
        return "\n".join(
            json.dumps(s.to_otel(), sort_keys=True)
            for s in self.spans
        )
# end listing


# listing 10.2
@dataclass(frozen=True)
class EvalCase:
    case_id: str
    split: str               # "dev" | "heldout"
    contract: Contract
    files: dict
    critical: bool = False

def pass_at_k(outcomes, k):
    """Probability >=1 of k trials passes (exact group)."""
    if k <= 0 or len(outcomes) < k:
        return 0.0
    n = len(outcomes)
    c = sum(1 for ok in outcomes if ok)
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)

def pass_pow_k(outcomes, k):
    """Empirical reliability that all k repeats pass."""
    if not outcomes or k <= 0:
        return 0.0
    p = sum(1 for ok in outcomes if ok) / len(outcomes)
    return p ** k

def run_eval_suite(cases, runner_fn, split="heldout",
                   trials=3, k=3):
    """Grade only hidden deterministic checks per split."""
    selected = [c for c in cases if c.split == split]
    per_case = []
    total_cost = 0.0
    total_passes = 0
    total_trials = 0

    for case in selected:
        outcomes = []
        case_cost = 0.0
        for t in range(1, trials + 1):
            # Runner gets a fresh copy; hidden grader runs
            # against original protected test definitions.
            out = runner_fn(case, t)
            cand = deepcopy(out.get("files", {}))
            for p in case.contract.protected:
                for path in list(cand):
                    if path.startswith(p):
                        del cand[path]
                for path, src in case.files.items():
                    if path.startswith(p):
                        cand[path] = src
            checks = case.contract.checks(cand)
            ok = all(v == "pass" for v in checks.values())
            outcomes.append(ok)
            case_cost += float(out.get("cost_usd", 0.0))
        total_cost += case_cost
        total_passes += sum(1 for o in outcomes if o)
        total_trials += len(outcomes)
        per_case.append({
            "case_id": case.case_id,
            "critical": case.critical,
            "passed": sum(1 for o in outcomes if o),
            "trials": len(outcomes),
            "pass_at_k": round(pass_at_k(outcomes, k), 4),
            "pass_pow_k": round(pass_pow_k(outcomes, k), 4),
            "cost_usd": round(case_cost, 6),
        })

    n = len(per_case) or 1
    mean_at = sum(r["pass_at_k"] for r in per_case) / n
    mean_pow = sum(r["pass_pow_k"] for r in per_case) / n
    cps = (total_cost / total_passes
           if total_passes > 0 else float("inf"))
    return {
        "split": split,
        "k": k,
        "cases": len(per_case),
        "passed_trials": total_passes,
        "total_trials": total_trials,
        "pass_at_k": round(mean_at, 4),
        "pass_pow_k": round(mean_pow, 4),
        "total_cost_usd": round(total_cost, 6),
        "cost_per_success": (round(cps, 6)
                             if math.isfinite(cps)
                             else None),
        "per_case": per_case,
    }
# end listing


# listing 10.3
@dataclass(frozen=True)
class ReleasePolicy:
    min_pass_pow_k: float = 0.80
    max_cost_per_success: float = 0.01
    block_critical_regression: bool = True

def evaluate_release(baseline, candidate, policy=None):
    """Gate shipment strictly on held-out metrics."""
    pol = policy or ReleasePolicy()
    reasons = []
    if candidate["split"] != "heldout":
        reasons.append("gate requires heldout split")
    if candidate["pass_pow_k"] < pol.min_pass_pow_k:
        reasons.append(
            f"pass^k {candidate['pass_pow_k']:.2f} "
            f"< {pol.min_pass_pow_k:.2f}")
    cps = candidate["cost_per_success"]
    if cps is None or cps > pol.max_cost_per_success:
        reasons.append(
            f"cost/success {cps} "
            f"> {pol.max_cost_per_success}")
    if pol.block_critical_regression:
        base_by = {r["case_id"]: r
                   for r in baseline["per_case"]}
        for r in candidate["per_case"]:
            b = base_by.get(r["case_id"])
            if (r["critical"] and b
                    and r["pass_pow_k"] < b["pass_pow_k"]):
                reasons.append(
                    f"critical regression on "
                    f"{r['case_id']}")
    return {
        "ship": len(reasons) == 0,
        "reasons": reasons,
        "delta_pass_pow_k": round(
            candidate["pass_pow_k"]
            - baseline["pass_pow_k"], 4),
    }
# end listing


def make_eval_cases():
    """Construct dev (2 cases) and held-out (3 cases) split."""
    return [
        EvalCase("DEV-01", "dev", TASK_INVOICE,
                 deepcopy(WORKSPACE), critical=False),
        EvalCase("DEV-02", "dev", TASK_INVOICE,
                 deepcopy(WORKSPACE), critical=False),
        EvalCase("HELD-01", "heldout", TASK_INVOICE,
                 deepcopy(WORKSPACE), critical=True),
        EvalCase("HELD-02", "heldout", TASK_INVOICE,
                 deepcopy(WORKSPACE), critical=True),
        EvalCase("HELD-03", "heldout", TASK_INVOICE,
                 deepcopy(WORKSPACE), critical=False),
    ]


def _good_files(ws):
    f = deepcopy(ws)
    f["pricing.py"] = PRICING_FIXED
    f["invoice.py"] = INVOICE_FIXED
    return f


def _partial_files(ws):
    f = deepcopy(ws)
    f["pricing.py"] = PRICING_FIXED
    return f


def benchmark_release_candidates():
    """Compare baseline, overfit-dev, and reliable candidate."""
    cases = make_eval_cases()

    def baseline_runner(case, trial):
        # 2/3 reliability on HELD-03, 3/3 on critical cases
        ok = not (case.case_id == "HELD-03" and trial == 3)
        return {
            "files": (_good_files(case.files)
                      if ok else _partial_files(case.files)),
            "cost_usd": 0.0020,
        }

    def overfit_runner(case, trial):
        # 100% on dev, flaky 1/3 on heldout + false summary!
        ok = (case.split == "dev") or (trial == 1)
        return {
            "files": (_good_files(case.files)
                      if ok else _partial_files(case.files)),
            "summary": "All billing checks verified!",
            "cost_usd": 0.0025,
        }

    def hardened_runner(case, trial):
        # 100% reliable across all 3 repeats on dev & heldout
        return {
            "files": _good_files(case.files),
            "cost_usd": 0.0022,
        }

    base_dev = run_eval_suite(cases, baseline_runner,
                              split="dev", trials=3, k=3)
    base_h = run_eval_suite(cases, baseline_runner,
                            split="heldout", trials=3, k=3)
    ov_dev = run_eval_suite(cases, overfit_runner,
                            split="dev", trials=3, k=3)
    ov_h = run_eval_suite(cases, overfit_runner,
                          split="heldout", trials=3, k=3)
    ha_dev = run_eval_suite(cases, hardened_runner,
                            split="dev", trials=3, k=3)
    ha_h = run_eval_suite(cases, hardened_runner,
                          split="heldout", trials=3, k=3)

    return {
        "baseline": {"dev": base_dev, "heldout": base_h},
        "overfit": {
            "dev": ov_dev, "heldout": ov_h,
            "gate": evaluate_release(base_h, ov_h),
        },
        "hardened": {
            "dev": ha_dev, "heldout": ha_h,
            "gate": evaluate_release(base_h, ha_h),
        },
    }
