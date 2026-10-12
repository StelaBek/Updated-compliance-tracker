from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from config_settings import DB_PATH

BASE_FINDING_COLUMNS=["id","is_demo","jurisdiction","geographic_level","authority","source_name","source_url","original_title","english_title","original_language","publication_date","effective_date","compliance_deadline","legislative_status","instrument_type","compliance_domain","category","subcategory","affected_parties","key_obligations","key_changes","business_impact","recommended_follow_up","confidence_score","evidence_excerpt","retrieved_at","finding_summary","ai_summary","ai_analysis","user_notes","publication_update_date","effective_application_date","scope","summary","legislation","status","business_action"]
EXTRA_FINDING_COLUMNS=["finding_status","development_type","event_key","source_quality_score","evidence_quality_score","evidence_basis","applicability_status","applicability_score","applicability_reasons","decision_trace","correlated_sources"]
FINDING_COLUMNS=BASE_FINDING_COLUMNS+EXTRA_FINDING_COLUMNS
SOURCE_COLUMNS=["id","jurisdiction","authority","name","url","source_type","language","verification_status","retrieval_method","active","last_success","last_failure","error_state","last_checked","discovered_automatically","discovery_reason","source_roles","source_quality_score"]

def connect(path=DB_PATH):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row; return con

def _now(): return datetime.now(timezone.utc).isoformat()
def _ensure(con,table,col,definition="TEXT"):
    if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}: con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

def init_db(path=DB_PATH):
    with connect(path) as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS findings (id TEXT PRIMARY KEY,is_demo INTEGER NOT NULL DEFAULT 0,jurisdiction TEXT NOT NULL,geographic_level TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS finding_versions (version_id INTEGER PRIMARY KEY AUTOINCREMENT,finding_id TEXT NOT NULL,field_name TEXT NOT NULL,previous_value TEXT,new_value TEXT,changed_at TEXT NOT NULL,change_type TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY,jurisdiction TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS runtime_state (key TEXT PRIMARY KEY,value TEXT,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS automated_decisions (decision_id INTEGER PRIMARY KEY AUTOINCREMENT,finding_id TEXT,event_key TEXT,stage TEXT,decision TEXT,reasons TEXT,evidence_url TEXT,created_at TEXT NOT NULL);
        ''')
        for c in FINDING_COLUMNS:
            if c in {"id","is_demo","jurisdiction","geographic_level"}: continue
            _ensure(con,"findings",c,"REAL" if c in {"confidence_score","source_quality_score","evidence_quality_score","applicability_score"} else "TEXT")
        for c in SOURCE_COLUMNS:
            if c in {"id","jurisdiction","name","url","active"}: continue
            _ensure(con,"sources",c,"REAL" if c=="source_quality_score" else "TEXT")
        _ensure(con,"sources","discovered_automatically","INTEGER NOT NULL DEFAULT 0")
        _ensure(con,"sources","updated_at","TEXT")

def upsert_finding(record,path=DB_PATH,change_type="manual"):
    init_db(path); clean={k:record.get(k) for k in FINDING_COLUMNS}; clean["is_demo"]=int(bool(clean.get("is_demo")))
    with connect(path) as con:
        old=con.execute("SELECT * FROM findings WHERE id=?",(clean["id"],)).fetchone()
        if old:
            for f in FINDING_COLUMNS:
                if str(old[f] if f in old.keys() and old[f] is not None else "") != str(clean.get(f) if clean.get(f) is not None else ""):
                    con.execute("INSERT INTO finding_versions(finding_id,field_name,previous_value,new_value,changed_at,change_type) VALUES(?,?,?,?,?,?)",(clean["id"],f,old[f] if f in old.keys() else None,clean.get(f),_now(),change_type))
        cols=FINDING_COLUMNS+["updated_at"]; vals=[clean.get(c) for c in FINDING_COLUMNS]+[_now()]
        q=",".join("?" for _ in cols); updates=",".join(f"{c}=excluded.{c}" for c in cols if c!="id")
        con.execute(f"INSERT INTO findings({','.join(cols)}) VALUES({q}) ON CONFLICT(id) DO UPDATE SET {updates}",vals)

def get_finding_by_event_key(event_key,path=DB_PATH):
    init_db(path)
    with connect(path) as con:
        r=con.execute("SELECT * FROM findings WHERE event_key=? ORDER BY updated_at DESC LIMIT 1",(event_key,)).fetchone(); return dict(r) if r else None

def list_findings(path=DB_PATH,include_candidates=False):
    init_db(path); where="" if include_candidates else "WHERE COALESCE(finding_status,'verified')='verified'"
    with connect(path) as con: return pd.read_sql_query(f"SELECT * FROM findings {where} ORDER BY publication_date DESC,updated_at DESC",con)
def list_candidate_findings(path=DB_PATH):
    init_db(path)
    with connect(path) as con: return pd.read_sql_query("SELECT * FROM findings WHERE finding_status='candidate' ORDER BY updated_at DESC",con)
def get_finding(fid,path=DB_PATH):
    init_db(path)
    with connect(path) as con:
        r=con.execute("SELECT * FROM findings WHERE id=?",(fid,)).fetchone(); return dict(r) if r else None
def versions(fid,path=DB_PATH):
    init_db(path)
    with connect(path) as con: return pd.read_sql_query("SELECT * FROM finding_versions WHERE finding_id=? ORDER BY version_id DESC",con,params=(fid,))
def purge_demo_findings(path=DB_PATH):
    init_db(path)
    with connect(path) as con: con.execute("DELETE FROM findings WHERE is_demo=1")

def upsert_source(record,path=DB_PATH):
    init_db(path); clean={c:record.get(c) for c in SOURCE_COLUMNS}; clean["active"]=int(bool(clean.get("active",True))); clean["discovered_automatically"]=int(bool(clean.get("discovered_automatically")))
    with connect(path) as con:
        cols=SOURCE_COLUMNS+["updated_at"]; vals=[clean.get(c) for c in SOURCE_COLUMNS]+[_now()]; q=",".join("?" for _ in cols); updates=",".join(f"{c}=excluded.{c}" for c in cols if c!="id")
        con.execute(f"INSERT INTO sources({','.join(cols)}) VALUES({q}) ON CONFLICT(id) DO UPDATE SET {updates}",vals)
def list_sources(path=DB_PATH):
    init_db(path)
    with connect(path) as con: return pd.read_sql_query("SELECT * FROM sources ORDER BY jurisdiction,name",con)

def record_automated_decision(*,finding_id,event_key,stage,decision,reasons,evidence_url,path=DB_PATH):
    init_db(path)
    payload=reasons if isinstance(reasons,str) else json.dumps(reasons,ensure_ascii=False)
    with connect(path) as con: con.execute("INSERT INTO automated_decisions(finding_id,event_key,stage,decision,reasons,evidence_url,created_at) VALUES(?,?,?,?,?,?,?)",(finding_id,event_key,stage,decision,payload,evidence_url,_now()))
def decisions_for_finding(fid,path=DB_PATH):
    init_db(path)
    with connect(path) as con: return pd.read_sql_query("SELECT * FROM automated_decisions WHERE finding_id=? ORDER BY decision_id DESC",con,params=(fid,))

def get_runtime_state(key,default=None,path=DB_PATH):
    init_db(path)
    with connect(path) as con:
        r=con.execute("SELECT value FROM runtime_state WHERE key=?",(key,)).fetchone(); return r["value"] if r else default
def set_runtime_state(key,value,path=DB_PATH):
    init_db(path)
    with connect(path) as con: con.execute("INSERT INTO runtime_state(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(key,value,_now()))
