from datetime import datetime, timezone
from pathlib import Path
import json
from db_repository import init_db, upsert_finding, list_findings, list_candidate_findings, record_automated_decision, decisions_for_finding
from services_web_discovery import _finding_from_page, _scope_from_evidence, _event_key


def current_day(): return datetime.now(timezone.utc).date().isoformat()

def source(status="verified official source"):
    return {"jurisdiction":"European Union","authority":"European Union","name":"EUR-Lex","language":"English","verification_status":status,"source_quality_score":1.0}

def test_search_snippet_never_becomes_verified():
    p={"title":"Regulation (EU) 2026/9999 published battery update","text":"Regulation (EU) 2026/9999 was published and concerns batteries and labelling.","url":"https://eur-lex.europa.eu/x","publication_date":current_day(),"evidence_basis":"search snippet"}
    r=_finding_from_page(p,source()); assert r and r["finding_status"]=="candidate"

def test_direct_primary_page_can_be_verified():
    p={"title":"Regulation (EU) 2026/9998 published battery update","text":"Regulation (EU) 2026/9998 was published and concerns batteries, labelling and reporting for manufacturers. "*8,"url":"https://eur-lex.europa.eu/y","publication_date":current_day(),"evidence_basis":"direct primary page"}
    r=_finding_from_page(p,source()); assert r and r["finding_status"]=="verified"; assert r["source_quality_score"]>=.9; assert r["evidence_quality_score"]>=.72

def test_scope_does_not_invent_applicability():
    assert _scope_from_evidence("A battery rule was published.","European Union","Batteries").startswith("Scope is not established")

def test_candidates_are_excluded_from_default_list(tmp_path:Path):
    db=tmp_path/'x.db'; init_db(db)
    base={"id":"A","is_demo":False,"jurisdiction":"EU","geographic_level":"EU","finding_status":"candidate"}; upsert_finding(base,db)
    assert list_findings(db).empty; assert len(list_candidate_findings(db))==1

def test_audit_trail(tmp_path:Path):
    db=tmp_path/'x.db'; init_db(db); record_automated_decision(finding_id="A",event_key="E",stage="gate",decision="candidate",reasons={"why":"snippet"},evidence_url="u",path=db); assert len(decisions_for_finding("A",db))==1

def test_event_key_correlates_same_event():
    assert _event_key("Germany","Law 1/2026","Title A","Published","2026-10-12") == _event_key("Germany","Law 1/2026","Different title","Published","2026-10-12")
