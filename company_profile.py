from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE_PATH = Path(os.environ.get("COMPANY_PROFILE_PATH", ROOT / "company_profile.json"))

DEFAULT_PROFILE = {
    "company_name": "Your company",
    "markets": [],
    "economic_roles": ["manufacturer", "importer", "distributor", "retailer", "online marketplace"],
    "product_categories": [],
    "compliance_categories": [],
    "keywords": [],
    "excluded_categories": [],
    "critical_categories": [],
}


def load_company_profile() -> dict:
    if not PROFILE_PATH.exists():
        return DEFAULT_PROFILE.copy()
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        merged = DEFAULT_PROFILE.copy(); merged.update(data or {})
        return merged
    except Exception:
        return DEFAULT_PROFILE.copy()


def applicability_from_evidence(*, jurisdiction: str, category: str, text: str, detected_operators: list[str]) -> tuple[str, float, list[str]]:
    p = load_company_profile(); reasons=[]; score=0.0
    markets = {str(x).lower() for x in p.get("markets", [])}
    if not markets or jurisdiction.lower() in markets or (jurisdiction == "European Union" and any(x for x in markets if x not in {"united kingdom", "switzerland", "norway", "iceland", "liechtenstein"})):
        score += 0.25; reasons.append("geography matches configured markets or no market restriction is configured")
    cats = {str(x).lower() for x in p.get("compliance_categories", []) + p.get("product_categories", [])}
    if not cats or category.lower() in cats:
        score += 0.30; reasons.append("category matches configured company scope or no category restriction is configured")
    roles = {str(x).lower() for x in p.get("economic_roles", [])}
    matched_roles = [r for r in detected_operators if r.lower().rstrip("s") in {x.rstrip("s") for x in roles}]
    if matched_roles:
        score += 0.25; reasons.append("official evidence mentions company economic role(s): " + ", ".join(matched_roles))
    keys = [str(x).lower() for x in p.get("keywords", []) if str(x).strip()]
    matched_keys = [k for k in keys if k in text.lower()]
    if matched_keys:
        score += 0.20; reasons.append("configured keyword(s) matched: " + ", ".join(matched_keys[:6]))
    excluded = {str(x).lower() for x in p.get("excluded_categories", [])}
    if category.lower() in excluded:
        return "Not currently relevant", 0.0, ["category is explicitly excluded by company profile"]
    status = "Likely relevant" if score >= 0.65 else "Possibly relevant" if score >= 0.30 else "Unclear"
    return status, round(score, 2), reasons
