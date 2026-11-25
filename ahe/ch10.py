"""Chapter 10: tracing, evaluation harnesses, and release gates.

Implements OTel-shaped GenAI spans, an independent dev/held-out
evaluation harness computing pass@k, pass^k, and cost per
verified success, and a CI release gate that checks suite
identity, critical regressions, and finite metric bounds.
"""

from copy import deepcopy
from dataclasses import dataclass
import datetime as dt
import json
import math
import uuid

from .ch01 import PRICING_FIXED
from .ch02 import (
    INVOICE_FIXED, TASK_INVOICE, WORKSPACE, is_protected,
    is_writable,
)


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _finite_nonneg(val, name):
    if isinstance(val, bool) or not isinstance(
            val, (int, float)):
        raise ValueError(f"{name} must be numeric")
    f = float(val)
    if not math.isfinite(f) or f < 0.0:
        raise ValueError(f"{name} must be finite >= 0")
    return f


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
            "timestamp": self.timestamp,
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
    """Append-only OTel-shaped GenAI span collector."""

    def __init__(self, harness_version="v2.10"):
        self.harness_version = harness_version
        self.spans = []

    def emit(self, trace_id, operation, model,
             input_tokens=0, output_tokens=0, cost_usd=0.0,
             duration_ms=0.0, status="ok",
             parent_span_id=None):
        span = GenAISpan(
            trace_id=str(trace_id),
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_span_id,
            operation=str(operation),
            model=str(model),
            harness_version=self.harness_version,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost_usd=_finite_nonneg(cost_usd, "cost_usd"),
            duration_ms=_finite_nonneg(
                duration_ms, "duration_ms"),
            status=str(status),
            timestamp=_utc_now(),
        )
        self.spans.append(span)
        return span

    def to_jsonl(self):
        return "\n".join(
            json.dumps(s.to_otel(), sort_keys=True,
                       allow_nan=False)
            for s in self.spans
        )
# end listing


# listing 10.2
@dataclass(frozen=True)
class EvalCase:
    case_id: str
    split: str               # "dev" | "heldout"
    contract: object
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
    """Grade isolated copies; restore protected files."""
    selected = [c for c in cases if c.split == split]
    if not selected:
        raise ValueError(f"no cases for split: {split}")
    seen_ids = set()
    per_case = []
    total_cost = 0.0
    total_passes = 0
    total_trials = 0

    for case in selected:
        if case.case_id in seen_ids:
            raise ValueError(
                f"duplicate case_id: {case.case_id}")
        seen_ids.add(case.case_id)
        baseline = deepcopy(case.files)
        outcomes = []
        case_cost = 0.0
        for t in range(1, trials + 1):
            # Runner receives an isolated EvalCase copy.
            public = EvalCase(
                case.case_id, case.split, case.contract,
                deepcopy(baseline), case.critical)
            out = runner_fn(public, t)
            if not isinstance(out, dict) or (
                    "cost_usd" not in out):
                raise ValueError(
                    "runner output must include cost_usd")
            cost = _finite_nonneg(
                out["cost_usd"], "cost_usd")
            cand = deepcopy(baseline)
            proposed = out.get("files", {})
            if isinstance(proposed, dict):
                for path, src in proposed.items():
                    if (isinstance(path, str)
                            and isinstance(src, str)
                            and not is_protected(
                                path, case.contract)
                            and is_writable(
                                path, case.contract)):
                        cand[path] = src
            checks = case.contract.checks(cand)
            ok = (isinstance(checks, dict)
                  and len(checks) > 0
                  and all(v == "pass"
                          for v in checks.values()))
            outcomes.append(ok)
            case_cost += cost
        total_cost += case_cost
        total_passes += sum(1 for o in outcomes if o)
        total_trials += len(outcomes)
        per_case.append({
            "case_id": case.case_id,
            "critical": case.critical,
            "passed": sum(1 for o in outcomes if o),
            "trials": len(outcomes),
            "pass_at_k": pass_at_k(outcomes, k),
            "pass_pow_k": pass_pow_k(outcomes, k),
            "cost_usd": case_cost,
        })

    n = len(per_case)
    mean_at = sum(r["pass_at_k"] for r in per_case) / n
    mean_pow = sum(r["pass_pow_k"] for r in per_case) / n
    cps = (total_cost / total_passes
           if total_passes > 0 else None)
    return {
        "split": split, "k": k, "trials": trials,
        "cases": n,
        "passed_trials": total_passes,
        "total_trials": total_trials,
        "pass_at_k": mean_at,
        "pass_pow_k": mean_pow,
        "total_cost_usd": total_cost,
        "cost_per_success": cps,
        "per_case": per_case,
    }
# end listing


# listing 10.3
@dataclass(frozen=True)
class ReleasePolicy:
    min_pass_pow_k: float = 0.80
    max_cost_per_success: float = 0.01
    block_critical_regression: bool = True

def _valid_prob(v):
    return (isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v) and 0.0 <= v <= 1.0)

def evaluate_release(baseline, candidate, policy=None):
    """Gate shipment strictly on matched held-out suites."""
    pol = policy or ReleasePolicy()
    reasons = []
    if candidate.get("split") != "heldout":
        reasons.append("gate requires heldout split")
    if candidate.get("k") != baseline.get("k"):
        reasons.append("k mismatch vs baseline")
    if candidate.get("trials") != baseline.get("trials"):
        reasons.append("trials mismatch vs baseline")

    base_by = {r["case_id"]: r
               for r in baseline.get("per_case", [])}
    cand_by = {r["case_id"]: r
               for r in candidate.get("per_case", [])}
    if not base_by or set(cand_by) != set(base_by):
        reasons.append("case manifest mismatch")

    cpow = candidate.get("pass_pow_k")
    bpow = baseline.get("pass_pow_k")
    if not _valid_prob(cpow) or not _valid_prob(bpow):
        reasons.append("invalid pass^k value")
    elif cpow < pol.min_pass_pow_k:
        reasons.append(
            f"pass^k {cpow:.2f} < {pol.min_pass_pow_k:.2f}")

    cps = candidate.get("cost_per_success")
    if (cps is None or isinstance(cps, bool)
            or not isinstance(cps, (int, float))
            or not math.isfinite(cps)
            or cps > pol.max_cost_per_success):
        reasons.append(
            f"cost/success {cps} "
            f"> {pol.max_cost_per_success}")

    if pol.block_critical_regression:
        for cid, b in base_by.items():
            c = cand_by.get(cid)
            if (b.get("critical") and c
                    and c["pass_pow_k"] < b["pass_pow_k"]):
                reasons.append(
                    f"critical regression on {cid}")

    delta = (round(cpow - bpow, 4)
             if (_valid_prob(cpow) and _valid_prob(bpow))
             else None)
    return {"ship": len(reasons) == 0,
            "reasons": reasons,
            "delta_pass_pow_k": delta}
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
    """Scripted gate benchmark: baseline, overfit, hardened."""
    cases = make_eval_cases()

    def baseline_runner(case, trial):
        ok = not (case.case_id == "HELD-03" and trial == 3)
        return {
            "files": (_good_files(case.files)
                      if ok else _partial_files(case.files)),
            "cost_usd": 0.0020,
        }

    def overfit_runner(case, trial):
        ok = (case.split == "dev") or (trial == 1)
        return {
            "files": (_good_files(case.files)
                      if ok else _partial_files(case.files)),
            "summary": "All billing checks verified!",
            "cost_usd": 0.0025,
        }

    def hardened_runner(case, trial):
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
