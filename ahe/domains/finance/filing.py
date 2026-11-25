"""Chapter 12 Finance transfer domain: FY2026 Q3 covenant audit.

Proves the universal harness kernel (Contract + HarnessV4 +
ContainedHarness) transfers cleanly to quarterly financial
statement review without modifying kernel code.
"""

import json
import math

FILING_NUMBERS = {
    "company": "Acme Cloud Corp",
    "period": "FY2026-Q3",
    "revenue_m": 420.0,
    "yoy_growth_pct": 14.0,
    "debt_to_ebitda": 4.4,
    "covenant_max_leverage": 4.0,
}

FOOTNOTE_4 = (
    "Note 4 (Credit Facility): Senior debt carries a "
    "maximum Debt/EBITDA maintenance covenant of 4.0x. "
    "Actual Q3 leverage of 4.4x constitutes a technical "
    "covenant breach requiring lender waiver.\n"
)

FINANCE_WORKSPACE = {
    "filing/summary.json": json.dumps(FILING_NUMBERS,
                                      indent=2) + "\n",
    "filing/footnote_4.txt": FOOTNOTE_4,
    "analysis/verdict.json": json.dumps({
        "healthy": True,
        "covenant_breach": False,
        "leverage": 4.4,
    }, indent=2) + "\n",
    "tests/audit_rules.txt": (
        "Protected audit assertions: covenant_breach must be "
        "true when debt_to_ebitda > covenant_max_leverage.\n"
    ),
}

FINANCE_FIXED_VERDICT = json.dumps({
    "healthy": False,
    "covenant_breach": True,
    "leverage": 4.4,
    "citation": "Note 4 (Credit Facility)",
}, indent=2) + "\n"


def _finite_num(val):
    return (isinstance(val, (int, float))
            and not isinstance(val, bool)
            and math.isfinite(val))


def verify_filing(files):
    """Deterministic audit checks on analysis/verdict.json."""
    try:
        filing = json.loads(files["filing/summary.json"])
        verdict = json.loads(files["analysis/verdict.json"])
    except Exception:
        return {"valid_json": "fail", "covenant": "fail",
                "citation": "fail"}
    if not isinstance(filing, dict) or not isinstance(
            verdict, dict):
        return {"valid_json": "fail", "covenant": "fail",
                "citation": "fail"}
    lev = filing.get("debt_to_ebitda")
    cap = filing.get("covenant_max_leverage")
    rep_lev = verdict.get("leverage")
    if not (_finite_num(lev) and _finite_num(cap)
            and _finite_num(rep_lev)):
        return {"valid_json": "fail", "covenant": "fail",
                "citation": "fail"}

    breach = lev > cap
    covenant_ok = (
        verdict.get("covenant_breach") is breach
        and verdict.get("healthy") is (not breach)
        and abs(float(rep_lev) - float(lev)) < 1e-6
    )
    footnote = files.get("filing/footnote_4.txt", "")
    cit = verdict.get("citation")
    citation_ok = (
        isinstance(footnote, str)
        and "Note 4" in footnote
        and isinstance(cit, str)
        and "Note 4" in cit
        and cit in footnote
    )
    return {
        "valid_json": "pass",
        "covenant": "pass" if covenant_ok else "fail",
        "citation": "pass" if citation_ok else "fail",
    }
