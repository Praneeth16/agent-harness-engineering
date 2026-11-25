"""Chapter 12: domain transfer, production ops, and 2027.

Plugs three distinct domains (Workspace Ops, preserved HLS
clinical-trial pre-screening, and Finance Q3 covenant audit)
into the UNCHANGED Ch 2/4/6 harness kernel, gates model-only
upgrades, and runs the executable Assumption Ledger.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import datetime as dt
import json

from .ch02 import (
    Budget, Contract, INVOICE_FIXED, PRICING_FIXED,
    TASK_INVOICE, WORKSPACE,
)
from .ch04 import CommitLog, start_v4
from .ch06 import Approval, ContainedHarness, args_digest
from .ch10 import evaluate_release, run_eval_suite
from .domains.finance.filing import (
    FINANCE_FIXED_VERDICT, FINANCE_WORKSPACE, verify_filing,
)
from .domains.hls import ch01 as hls_ch01


# listing 12.1
@dataclass(frozen=True)
class DomainPlugin:
    name: str
    contract: Contract
    files: dict
    target_edits: dict

def make_hls_plugin():
    """Wrap preserved v1 HLS domain; zero kernel edits."""
    patient = hls_ch01.read_record("P017")
    rules = hls_ch01.PROTOCOLS[("T004", 2)]
    truth = {k: hls_ch01.criterion(patient[k], *b)
             for k, b in rules.items()}

    def verify_hls(files):
        try:
            claim = json.loads(files["packet.json"])
        except Exception:
            return {"hls_claim": "fail"}
        err = hls_ch01.check_claim(claim, truth)
        if err is not None:
            return {"hls_claim": "fail"}
        # Reject contradictory prose summaries.
        summary = str(claim.get("summary", "")).lower()
        if (claim.get("eligible") is not True
                and "all criteria met" in summary):
            return {"hls_claim": "fail"}
        return {"hls_claim": "pass"}

    files = {
        "patient.json": json.dumps(patient, indent=2),
        "packet.json": json.dumps(
            hls_ch01.fake_model(
                {"record": patient, "rules": rules})),
        "tests/protocol.txt": "Protected T004-v2 rules.\n",
    }
    good = json.dumps({
        "eligible": None,
        "criteria": truth,
        "summary": "marker unknown; held for review",
    })
    contract = Contract(
        task_id="HLS-P017",
        goal="Produce grounded clinical trial packet.",
        writable=("packet.json",),
        protected=("tests/",),
        checks=verify_hls,
        budget=Budget(steps=10, edits=2),
    )
    return DomainPlugin("hls", contract, files,
                        {"packet.json": good})

def make_finance_plugin():
    contract = Contract(
        task_id="FIN-Q3",
        goal="Audit Q3 filing and Note 4 debt covenant.",
        writable=("analysis/verdict.json",),
        protected=("tests/", "filing/"),
        checks=verify_filing,
        budget=Budget(steps=10, edits=2),
    )
    return DomainPlugin(
        "finance", contract, deepcopy(FINANCE_WORKSPACE),
        {"analysis/verdict.json": FINANCE_FIXED_VERDICT},
    )

def run_on_shared_kernel(plugin):
    """Run any DomainPlugin on the unchanged kernel."""
    now = dt.datetime(2026, 11, 25, tzinfo=dt.timezone.utc)
    harness = ContainedHarness(CommitLog())
    run = start_v4(plugin.contract, plugin.files)

    # 1. Protected path tamper is blocked across domains
    prot = plugin.contract.protected[0] + "tamper.txt"
    blocked = harness.dispatch(
        run, {"tool": "draft_patch",
              "args": {"path": prot, "content": "x"}},
        now=now)

    # 2. Stage domain-specific edits through same gate
    for path, content in plugin.target_edits.items():
        harness.dispatch(
            run, {"tool": "draft_patch",
                  "args": {"path": path,
                           "content": content}}, now=now)

    # 3. Grant snapshot-bound approval and commit
    bound = harness._bind_args(
        run, "commit_patch", {"key": f"{plugin.name}-1"})
    harness.grant(Approval(
        approval_id=f"ap-{plugin.name}",
        tool="commit_patch",
        digest=args_digest("commit_patch", bound),
        policy_version=harness.policy_version,
        approver="ops@ahe",
        expires_at=now + dt.timedelta(minutes=10),
    ))
    committed = harness.dispatch(
        run, {"tool": "commit_patch",
              "args": {"key": f"{plugin.name}-1"}}, now=now)
    harness.dispatch(
        run, {"tool": "finish", "args": {}}, now=now)
    return {
        "domain": plugin.name,
        "tamper_blocked": blocked.get("error"),
        "committed": "error" not in committed,
        "state": run.state,
        "checks": dict(run.last_checks),
    }
# end listing


# listing 12.2
@dataclass(frozen=True)
class VersionStamp:
    harness_version: str
    model_id: str
    prompt_hash: str
    policy_version: str

def check_model_upgrade(cases, old_stamp, new_stamp,
                        old_runner, new_runner,
                        policy=None):
    """Gate a model-only upgrade on a fixed suite."""
    suite = list(cases)
    if (old_stamp.harness_version
            != new_stamp.harness_version
            or old_stamp.prompt_hash
            != new_stamp.prompt_hash
            or old_stamp.policy_version
            != new_stamp.policy_version):
        raise ValueError(
            "model upgrade requires fixed harness/policy")
    base = run_eval_suite(suite, old_runner,
                          split="heldout", trials=3, k=3)
    cand = run_eval_suite(suite, new_runner,
                          split="heldout", trials=3, k=3)
    gate = evaluate_release(base, cand, policy=policy)
    return {
        "from_stamp": asdict(old_stamp),
        "to_stamp": asdict(new_stamp),
        "base_pass_pow_k": base["pass_pow_k"],
        "new_pass_pow_k": cand["pass_pow_k"],
        "new_cps": cand["cost_per_success"],
        "ship": gate["ship"],
        "reasons": gate["reasons"],
    }
# end listing


# listing 12.3
@dataclass(frozen=True)
class LedgerControl:
    name: str
    kind: str            # "scaffold" | "control_plane"
    assumption: str
    retire_threshold: float

def decide_retirement(control, with_control_pow_k,
                      without_control_pow_k):
    """Retire only validated scaffolds proven unneeded."""
    if control.kind not in ("scaffold", "control_plane"):
        raise ValueError(f"bad kind: {control.kind!r}")
    if control.kind == "control_plane":
        return {
            "control": control.name,
            "action": "keep_permanent",
            "reason": "non-negotiable control plane",
        }
    drop = with_control_pow_k - without_control_pow_k
    if (without_control_pow_k >= control.retire_threshold
            and drop <= 0.02):
        return {
            "control": control.name,
            "action": "retire",
            "reason": (
                f"pass^k={without_control_pow_k:.2f}"
                f" without scaffold"),
        }
    return {
        "control": control.name,
        "action": "keep_for_now",
        "reason": (
            f"still needed (drop={drop:.2f} if removed)"),
    }
# end listing


def make_workspace_plugin():
    return DomainPlugin(
        "workspace", TASK_INVOICE, deepcopy(WORKSPACE),
        {"pricing.py": PRICING_FIXED,
         "invoice.py": INVOICE_FIXED},
    )


def run_all_domain_transfers():
    """Execute Workspace, HLS, and Finance on one kernel."""
    plugins = [
        make_workspace_plugin(),
        make_hls_plugin(),
        make_finance_plugin(),
    ]
    return [run_on_shared_kernel(p) for p in plugins]


def run_ledger_retirement_demo():
    """Illustrative scaffold retirement vs control-plane keep."""
    controls = [
        (LedgerControl(
            "verbose_format_nudge", "scaffold",
            "Model forgets JSON wrapper without reminder.",
            retire_threshold=0.95), 0.98, 0.97),
        (LedgerControl(
            "actionable_refusal_hints", "scaffold",
            "Model repeats invalid tool args on terse error.",
            retire_threshold=0.95), 0.96, 0.42),
        (LedgerControl(
            "sandbox_and_bound_approvals", "control_plane",
            "Untrusted input must never touch host secrets.",
            retire_threshold=1.0), 1.00, 1.00),
        (LedgerControl(
            "deterministic_verifier", "control_plane",
            "Completion must be verified externally.",
            retire_threshold=1.0), 1.00, 1.00),
    ]
    return [decide_retirement(c, w, wo)
            for c, w, wo in controls]
