from __future__ import annotations
from db_repository import init_db, upsert_source, purge_demo_findings
from regulatory_source_registry import SOURCE_REGISTRY

def _quality(status:str)->float:
    return {"verified official source":1.0,"likely official source":0.75,"pending verification":0.35}.get(status,0.2)

def seed_official_sources():
    init_db(); purge_demo_findings()
    for s in SOURCE_REGISTRY:
        row=dict(s)
        row.update(retrieval_method="HTML + restricted web search",active=True,last_success=None,last_failure=None,error_state=None,last_checked=None,discovered_automatically=False,discovery_reason="Curated jurisdiction registry.",source_quality_score=_quality(row["verification_status"]))
        upsert_source(row)
