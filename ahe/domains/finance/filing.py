"""Chapter 12 Finance transfer domain: 10-K covenant audit.

Proves the universal harness kernel (Contract + HarnessV4 +
ContainedHarness) transfers cleanly to financial statement
analysis without modifying a single line of kernel code.
"""

import json

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


def verify_filing(files):
    """Deterministic audit checks on analysis/verdict.json."""
    out = {}
    try:
        filing = json.loads(files["filing/summary.json"])
        verdict = json.loads(files["analysis/verdict.json"])
    except Exception:
        return {"valid_json": "fail", "covenant": "fail",
                "citation": "fail"}
    out["valid_json"] = "pass"
    breach = (filing["debt_to_ebitda"]
              > filing["covenant_max_leverage"])
    covenant_ok = (
        verdict.get("covenant_breach") is breach
        and verdict.get("healthy") is (not breach)
    )
    out["covenant"] = "pass" if covenant_ok else "fail"
    cit = str(verdict.get("citation", ""))
    out["citation"] = ("pass" if "Note 4" in cit
                       else "fail")
    return out
