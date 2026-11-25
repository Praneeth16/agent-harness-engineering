"""Chapter 11: self-improving and meta-harnesses (2026 -> 2027).

Implements a 4-stage modular harness evolution loop across 4
unlocked runtime surfaces while locking the evaluator and
containment layers behind a checksummed held-out gate.
"""

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json

from .ch10 import make_eval_cases, run_eval_suite


# listing 11.1
UNLOCKED_SURFACES = frozenset({
    "prompt_policy",
    "context_budget",
    "tool_refusal",
    "retry_budget",
})

LOCKED_SURFACES = frozenset({
    "evaluator",
    "containment",
    "protected_tests",
})

@dataclass(frozen=True)
class HarnessConfig:
    version: str = "h-1.0"
    prompt_policy: str = "compact"
    context_budget: int = 2400
    tool_refusal: str = "terse"     # "terse" | "teaching"
    retry_budget: int = 2

@dataclass(frozen=True)
class EvolutionProposal:
    proposal_id: str
    surface: str
    new_value: object
    rationale: str
    edits_evaluator: bool = False

def validate_proposal(prop):
    """Reject locked surfaces and out-of-range values."""
    if prop.edits_evaluator:
        return "blocked: evaluator modification forbidden"
    if prop.surface in LOCKED_SURFACES:
        return f"blocked: surface {prop.surface} is locked"
    if prop.surface not in UNLOCKED_SURFACES:
        return f"blocked: unknown surface {prop.surface}"
    v = prop.new_value
    if prop.surface == "prompt_policy" and (
            v not in ("compact", "verbose")):
        return "blocked: invalid prompt_policy"
    if prop.surface == "tool_refusal" and (
            v not in ("terse", "teaching")):
        return "blocked: invalid tool_refusal"
    if prop.surface == "context_budget" and (
            type(v) is not int or not 1200 <= v <= 4800):
        return "blocked: context_budget out of range"
    if prop.surface == "retry_budget" and (
            type(v) is not int or not 1 <= v <= 4):
        return "blocked: retry_budget out of range"
    return None
# end listing


# listing 11.2
def diagnose_traces(trace_records):
    """Stage 1: classify dominant failure from traces."""
    if not trace_records:
        return {"dominant": "none", "count": 0}
    refusals = sum(r.get("refusals", 0)
                   for r in trace_records)
    evictions = sum(r.get("evictions", 0)
                    for r in trace_records)
    timeouts = sum(1 for r in trace_records
                   if r.get("timeout"))
    mean_ref = refusals / len(trace_records)
    if mean_ref >= 5.0:
        return {"dominant": "gate_thrashing",
                "mean_refusals": round(mean_ref, 2),
                "recommended_surface": "tool_refusal"}
    if evictions >= 4 * len(trace_records):
        return {"dominant": "context_churn",
                "mean_evictions": round(
                    evictions / len(trace_records), 2),
                "recommended_surface": "context_budget"}
    if timeouts > 0:
        return {"dominant": "transient_timeout",
                "timeouts": timeouts,
                "recommended_surface": "retry_budget"}
    return {"dominant": "none",
            "recommended_surface": None}

def propose_modular_patch(diagnosis, config, prop_id):
    """Stage 2: propose a single-surface modular edit."""
    surf = diagnosis.get("recommended_surface")
    if surf == "tool_refusal" and (
            config.tool_refusal != "teaching"):
        return EvolutionProposal(
            prop_id, "tool_refusal", "teaching",
            "enable actionable error/why/fix refusals")
    if surf == "context_budget":
        return EvolutionProposal(
            prop_id, "context_budget",
            min(config.context_budget + 800, 4800),
            "raise window cap to stop eviction churn")
    if surf == "retry_budget":
        return EvolutionProposal(
            prop_id, "retry_budget",
            min(config.retry_budget + 1, 4),
            "allow one bounded retry on timeout")
    return None
# end listing


# listing 11.3
def _checks_sig(fn):
    code = getattr(fn, "__code__", None)
    raw = (getattr(fn, "__qualname__", repr(fn)).encode()
           + (code.co_code if code else b""))
    return hashlib.sha256(raw).hexdigest()[:16]

def evaluator_checksum(cases):
    """Hash held-out protected fixtures and check code."""
    held = [
        {"id": c.case_id,
         "critical": c.critical,
         "checks_sig": _checks_sig(c.contract.checks),
         "protected": list(c.contract.protected),
         "tests": {k: v for k, v in sorted(c.files.items())
                   if any(k.startswith(p)
                          for p in c.contract.protected)}}
        for c in cases if c.split == "heldout"
    ]
    blob = json.dumps(held, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]

class EvolutionGate:
    """Stages 3 & 4: fresh held-out gate + audit ledger."""

    def __init__(self, cases, runner_factory,
                 max_cost_per_success=0.01):
        self.cases = deepcopy(cases)
        self.runner_factory = runner_factory
        self.max_cps = max_cost_per_success
        self.expected_hash = evaluator_checksum(self.cases)
        self.ledger = []

    def evaluate(self, config):
        if evaluator_checksum(self.cases) != (
                self.expected_hash):
            raise RuntimeError("evaluator checksum drift")
        runner = self.runner_factory(config)
        return run_eval_suite(
            self.cases, runner, split="heldout",
            trials=3, k=3)

    def try_proposal(self, current_cfg, proposal):
        err = validate_proposal(proposal)
        if err:
            rec = {"proposal_id": proposal.proposal_id,
                   "surface": proposal.surface,
                   "promoted": False, "reason": err,
                   "pass_pow_k": None, "cps": None}
            self.ledger.append(rec)
            return current_cfg, rec

        base = self.evaluate(current_cfg)
        new_ver = (
            f"{current_cfg.version}+{proposal.proposal_id}"
        )
        cand_cfg = replace(
            current_cfg, version=new_ver,
            **{proposal.surface: proposal.new_value},
        )
        cand = self.evaluate(cand_cfg)

        if evaluator_checksum(self.cases) != (
                self.expected_hash):
            rec = {"proposal_id": proposal.proposal_id,
                   "surface": proposal.surface,
                   "promoted": False,
                   "reason": "evaluator tampered",
                   "pass_pow_k": 0.0, "cps": None}
            self.ledger.append(rec)
            return current_cfg, rec

        base_by = {r["case_id"]: r
                   for r in base["per_case"]}
        crit_slip = any(
            r["critical"]
            and r["pass_pow_k"] < base_by[
                r["case_id"]]["pass_pow_k"]
            for r in cand["per_case"]
        )
        cps = cand["cost_per_success"]
        within_cost = (cps is not None
                       and cps <= self.max_cps)
        improved = (
            cand["pass_pow_k"] > base["pass_pow_k"]
            or (cand["pass_pow_k"] == base["pass_pow_k"]
                and cps is not None
                and base["cost_per_success"] is not None
                and cps < base["cost_per_success"])
        )
        promoted = (not crit_slip) and within_cost and (
            improved)
        reason = (
            "promoted: held-out pass^k improved"
            if promoted
            else ("rejected: critical case regressed"
                  if crit_slip
                  else "rejected: no gain within cap")
        )
        rec = {
            "proposal_id": proposal.proposal_id,
            "surface": proposal.surface,
            "promoted": promoted,
            "reason": reason,
            "base_pass_pow_k": base["pass_pow_k"],
            "pass_pow_k": cand["pass_pow_k"],
            "cps": cps,
        }
        self.ledger.append(rec)
        return (cand_cfg if promoted else current_cfg), rec
# end listing


def _sim_runner_factory(config):
    from .ch10 import _good_files, _partial_files

    def runner(case, trial):
        if config.tool_refusal == "teaching":
            ok = True
            cost = 0.0024
        elif config.prompt_policy == "verbose":
            ok = (trial <= 2)
            cost = 0.0150
        else:
            ok = (trial == 1)
            cost = 0.0020
        return {
            "files": (_good_files(case.files)
                      if ok else _partial_files(case.files)),
            "cost_usd": cost,
        }
    return runner


def benchmark_evolution_cycle():
    """Run baseline + 3 simulated proposals through gate."""
    cases = make_eval_cases()
    gate = EvolutionGate(cases, _sim_runner_factory,
                         max_cost_per_success=0.01)
    cfg = HarnessConfig()
    base = gate.evaluate(cfg)

    p_verbose = EvolutionProposal(
        "p-verbose", "prompt_policy", "verbose",
        "stuff long instructions into prompt")
    cfg, rec_a = gate.try_proposal(cfg, p_verbose)

    p_tamper = EvolutionProposal(
        "p-tamper", "evaluator", "skip_invoice_check",
        "relax invoice check to raise score",
        edits_evaluator=True)
    cfg, rec_b = gate.try_proposal(cfg, p_tamper)

    diag = diagnose_traces([
        {"refusals": 10, "evictions": 0},
        {"refusals": 10, "evictions": 0},
        {"refusals": 10, "evictions": 0},
    ])
    p_mod = propose_modular_patch(diag, cfg, "p-teach")
    cfg, rec_c = gate.try_proposal(cfg, p_mod)

    return {
        "diagnosis": diag,
        "baseline": base,
        "proposals": [rec_a, rec_b, rec_c],
        "final_config": cfg,
    }
