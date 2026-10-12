from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from urllib.parse import urljoin, urlparse
import json, os, re
import requests
from bs4 import BeautifulSoup
from config_settings import AUTO_WEB_DISCOVERY,AUTO_WEB_DISCOVERY_INTERVAL_MINUTES,AUTO_WEB_MAX_RESULTS,AUTO_WEB_SOURCE_LINKS,AUTO_WEB_TIMEOUT_SECONDS,AUTO_WEB_RESULTS_PER_JURISDICTION,COMPLIANCE_SEARCH_TERMS,JURISDICTION_REGISTRY,PRODUCT_CATEGORIES,REPORT_START_DATE
from company_profile import applicability_from_evidence, load_company_profile
from db_repository import get_runtime_state,list_sources,set_runtime_state,upsert_finding,upsert_source,get_finding_by_event_key,record_automated_decision

USER_AGENT="ComplianceIntelligence/2.0 (+verified-source-monitoring)"
APPROVED_SOURCE_STATUSES={"verified official source","likely official source"}
SEARCH_GROUPS={
 "product":"published amended adopted guidance enforcement deadline product safety CE marking market surveillance machinery LVD EMC radio equipment batteries ecodesign energy labelling REACH RoHS packaging EPR digital product passport cybersecurity",
 "environment":"published amended guidance deadline packaging waste EPR batteries ecodesign sustainability environmental compliance",
 "tax":"published amended guidance deadline VAT indirect tax e-invoicing digital reporting excise registration threshold tax reporting",
 "customs":"published amended guidance enforcement deadline customs tariff import duty import VAT customs declaration origin classification",
}

# Broad multilingual vocabulary used for discovery/extraction. It intentionally avoids
# claiming semantic equivalence beyond simple keyword detection.
MULTILINGUAL={
 "published":["published","publication","gepubliceerd","veröffentlicht","publié","pubblicato","publicado","opublikowano","publicado","publicat","zveřejněn","uverejnen","objavljeno","közzétett","publicerad","offentliggjort","julkaistu","avaldatud","publicēts","paskelbta","обнародван","objavljeno","δημοσιεύθηκε"],
 "amended":["amended","amendment","wijzig","änder","modif","modifica","enmienda","zmian","alteração","modificare","novel","zmena","sprememb","módosít","ändring","ændring","muutos","muudet","grozīj","pakeit","измен","izmjen","τροποποι"],
 "guidance":["guidance","guideline","faq","richtsnoer","leitfaden","orientation","linee guida","directrices","wytyczne","orientação","ghid","pokyn","usmernenie","smernice","útmutató","vägledning","vejledning","ohje","juhend","vadlīnijas","gairės","указания","smjernice","οδηγίες"],
 "enforcement":["enforcement","sanction","penalty","handhaving","durchsetzung","sanction","sanzione","sanción","kara","coima","sancțiune","pokuta","sankcia","kazen","szankció","sanktion","bøde","seuraamus","sanktsioon","sods","sankcija","санкция","kazna","κύρωση"],
 "deadline":["deadline","application date","transition period","applicable from","entry into force","inwerkingtred","inkraft","entrée en vigueur","entrata in vigore","entrada en vigor","wejście w życie","entrada em vigor","intrare în vigoare","nabytí účinnosti","účinnosť","začetek veljavnosti","hatálybalépés","ikraftträdande","ikrafttræden","voimaantulo","jõustumine","stājas spēkā","įsigalioja","влиза в сила","stupa na snagu","έναρξη ισχύος"],
}
RELEVANCE_TERMS=list(dict.fromkeys(COMPLIANCE_SEARCH_TERMS+["productveiligheid","produktsicherheit","sécurité des produits","sicurezza dei prodotti","seguridad de los productos","bezpieczeństwo produktów","verpakking","verpackung","emballage","imballaggi","envases","opakowania","douane","zoll","dogana","aduanas","cło","btw","mehrwertsteuer","umsatzsteuer","tva","iva","dph","moms","mva"]))
CATEGORY_RULES=[
 ("General Product Safety",["general product safety","gpsr"]),("Product Safety",["product safety","productveiligheid","produktsicherheit","sécurité des produits","sicurezza dei prodotti","seguridad de los productos","bezpieczeństwo produktów"]),("CE Marking",["ce marking","ce-marking","ce mark"]),("Market Surveillance",["market surveillance"]),("Machinery",["machinery","machine regulation"]),("Electrical Safety / LVD",["low voltage directive","lvd","electrical safety"]),("EMC",["electromagnetic compatibility"," emc "]),("Radio Equipment",["radio equipment","red directive"]),("Batteries",["battery","batteries","battery regulation"]),("Ecodesign",["ecodesign","eco-design"]),("Energy Labelling",["energy labelling","energy labeling"]),("Construction Products",["construction products","cpr"]),("Chemicals / REACH",["reach","chemical restriction"]),("RoHS",["rohs"]),("Packaging",["packaging","verpakking","verpackung","emballage","imballaggi","envases","opakowania","ppwr"]),("Waste / EPR",["extended producer responsibility","epr","producer responsibility","waste"]),("Digital Product Passport",["digital product passport"," dpp "]),("Consumer Protection",["consumer protection","consumer rights"]),("Cybersecurity",["cybersecurity","cyber resilience","cra regulation"]),("Sustainability",["sustainability","sustainable products"]),("Environmental Compliance",["environmental compliance","emissions"]),
]

@dataclass
class SearchHit:
    title:str; url:str; snippet:str=""; published_date:str|None=None; score:float|None=None

def _now(): return datetime.now(timezone.utc).isoformat()
def _host(url):
    try:return urlparse(url).netloc.lower().split(":")[0]
    except:return ""
def _registered_host_match(url,source_url):
    a,b=_host(url),_host(source_url); return bool(a and b and (a==b or a.endswith("."+b)))
def _clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _normalise_date(v):
    if not v:return None
    m=re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b",str(v))
    if m:return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(str(v)).date().isoformat()
    except:return None

def reporting_window(now=None):
    now=now or datetime.now(timezone.utc); today=now.date(); monday=today-timedelta(days=today.weekday()); floor=datetime.fromisoformat(REPORT_START_DATE).date(); monday=max(monday,floor); return monday.isoformat(),today.isoformat()
def _date_in_reporting_week(v):
    try:d=datetime.fromisoformat(_normalise_date(v)).date()
    except:return False
    a,b=reporting_window(); return datetime.fromisoformat(a).date()<=d<=datetime.fromisoformat(b).date()
def _extract_date_from_text(text):
    text=_clean(text)
    for p in [r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b",r"\b(0?[1-9]|[12]\d|3[01])[-/](0?[1-9]|1[0-2])[-/](20\d{2})\b"]:
        m=re.search(p,text)
        if m:
            try:
                g=list(map(int,m.groups())); y,mo,d=(g if g[0]>1900 else [g[2],g[1],g[0]]); return datetime(y,mo,d).date().isoformat()
            except:pass
    return None

def _extract_legal_reference(text):
    t=_clean(text)
    patterns=[r"\b(?:Commission\s+)?(?:Delegated\s+|Implementing\s+)?(?:Regulation|Directive|Decision)\s*\((?:EU|EC|EEC)\)\s*(?:No\.?\s*)?\d{4}/\d{1,5}\b",r"\bCELEX[:\s]*[0-9A-Z]{8,14}\b",r"\b(?:Act|Law|Decree|Regulation|Order|Ordinance|Statutory Instrument|S\.?I\.?|Wet|Gesetz|Verordnung|Loi|Décret|Legge|Decreto|Ley|Real Decreto|Ustawa|Rozporządzenie|Lei|Decreto-Lei|Lege|Ordonanță|Zákon|Zákon č\.|Zakon|Törvény)\s+(?:No\.?\s*)?[A-Z0-9.-]{1,12}(?:[/.-]\d{2,4})?\b",r"\b(?:No\.?|Nr\.?|N°|č\.)\s*\d{1,6}[/-]20\d{2}\b"]
    for p in patterns:
        m=re.search(p,t,re.I)
        if m:return m.group(0).strip()
    return None

def _material_update_type(text):
    low=text.lower()
    groups=[("Amended",MULTILINGUAL["amended"]),("Official guidance",MULTILINGUAL["guidance"]),("Enforcement development",MULTILINGUAL["enforcement"]),("Compliance milestone",MULTILINGUAL["deadline"]),("Published",MULTILINGUAL["published"]),("Adopted",["adopted","approved","adoption","aangenomen","angenommen","adopté","adottato","aprobado","przyjęto"]),("Implementing act",["implementing act","implementing regulation","implementing decision"]),("Delegated act",["delegated act","delegated regulation","delegated decision"]),("Consultation / proposal",["consultation","public consultation","proposal","draft","ontwerp","entwurf","projet","bozza","proyecto","projekt"]),("Official update",["updated","update","new version","bijgewerkt","aktualisiert","mise à jour","aggiornato","actualizado"])]
    for label,words in groups:
        if any(w in low for w in words):return label
    return None

def _relevance(text):
    low=text.lower(); hits=[x for x in RELEVANCE_TERMS if x.lower() in low]
    if not hits:return 0.0,"Other","Other"
    cat="Other"
    for label,terms in CATEGORY_RULES:
        if any(x in low for x in terms):cat=label;break
    if any(x in low for x in ["customs","tariff","import duty","douane","zoll","dogana","aduanas","cło"]):domain="Customs"
    elif any(x in low for x in ["vat","tax","excise","e-invoic","btw","iva","tva","mehrwertsteuer","umsatzsteuer","dph","moms"]):domain="Tax compliance"
    elif any(x in low for x in ["epr","waste","ecodesign","environmental","packaging"]):domain="Environmental compliance"
    else:domain="Product compliance"
    return min(1.0,.38+.08*len(set(hits))),cat,domain

def _detected_scope(text):
    low=text.lower(); operators=[]
    for label,terms in [("manufacturers",["manufacturer","fabrikant","hersteller","fabricant","fabbricante","fabricante"]),("importers",["importer","importeur","importateur","importatore","importador"]),("distributors",["distributor","distributeur","vertreiber"]),("retailers",["retailer","retail","detailhandel"]),("online marketplaces",["online marketplace","marketplace provider"]),("taxable persons",["taxable person","vat registered","btw-plicht","assujetti","soggetto passivo"]),("customs declarants",["declarant","customs representative","douanevertegenwoordiger"])]:
        if any(x in low for x in terms):operators.append(label)
    obligations=[]
    for label,terms in [("labelling",["label","labelling","labeling"]),("documentation",["documentation","technical file","declaration of conformity"]),("registration",["registration","register"]),("reporting",["reporting","report","return"]),("testing",["testing","test"]),("due diligence",["due diligence"]),("customs declaration",["customs declaration"]),("invoicing",["invoice","invoicing","e-invoicing"]),("payment",["payment","payable"]),("restriction/prohibition",["restriction","prohibition","ban"])]:
        if any(x in low for x in terms):obligations.append(label)
    return operators,obligations

def _scope_from_evidence(text,jurisdiction,category):
    ops,oblig=_detected_scope(text)
    if not ops and not oblig:return "Scope is not established from the retrieved evidence. Review the cited primary source before deciding applicability."
    parts=[]
    if ops:parts.append("The retrieved evidence explicitly mentions: "+", ".join(ops)+".")
    if oblig:parts.append("It also explicitly mentions compliance topics including: "+", ".join(oblig)+".")
    parts.append("Product-level exclusions, thresholds and exact operator scope are not inferred and must be confirmed in the cited source.")
    return " ".join(parts)

def _evidence_sentences(text,max_sentences=3):
    ss=[s.strip() for s in re.split(r"(?<=[.!?])\s+",_clean(text)) if len(s.strip())>=30]
    return ss[:max_sentences]
def _one_paragraph_summary(page,source,category,domain):
    level="EU-level" if source.get("jurisdiction")=="European Union" else "national-level"
    intro=f"This {level} finding for {source.get('jurisdiction') or 'the relevant jurisdiction'} concerns {category or domain}."
    ev=_evidence_sentences(page.get("text") or "")
    if ev:return (intro+" "+" ".join(ev))[:900]
    return intro+" The retrieved official-source evidence does not contain enough text for a reliable summary. Review the cited source."
def _summary_from_evidence(text,update_type,legislation,effective_date,jurisdiction):
    ev=_evidence_sentences(text,2); parts=[]
    if legislation:parts.append(f"Legal reference identified: {legislation}.")
    parts.append(f"Detected development type: {update_type}.")
    parts.extend(ev)
    if effective_date:parts.append(f"A date associated with application/effect was detected as {effective_date}; verify its exact legal meaning in the source.")
    else:parts.append("No effective/application date is treated as confirmed from the retrieved evidence.")
    return " ".join(parts)[:1200]
def _business_action(text,category,update_type):
    ops,oblig=_detected_scope(text); pieces=["Open and verify the cited primary source."]
    if oblig:pieces.append("Assess the explicitly detected compliance topics: "+", ".join(oblig)+".")
    if ops:pieces.append("Confirm whether your company acts as one of the mentioned operator types: "+", ".join(ops)+".")
    pieces.append(f"Record the {update_type.lower()} in the compliance change log only after applicability is confirmed.")
    return " ".join(pieces)
def _effective_date(text):
    low=text.lower()
    for anchor in MULTILINGUAL["deadline"]:
        pos=low.find(anchor)
        if pos>=0:
            d=_extract_date_from_text(text[pos:pos+320])
            if d:return d
    return None

def _source_quality(source):
    base={"verified official source":1.0,"likely official source":.78,"pending verification":.35,"rejected":0.0}.get(source.get("verification_status"),.25)
    return round(float(source.get("source_quality_score") or base),2)
def _evidence_quality(*,basis,date,reference,event_type,text):
    score=.25 if basis=="search snippet" else .55
    score+=.12 if date else 0; score+=.12 if reference else 0; score+=.10 if event_type else 0; score+=.06 if len(_clean(text))>500 else 0
    return round(min(1.0,score),2)
def _development_type(reference,update_type): return "Legislative development" if reference else ("Regulatory/authority development" if update_type else "Discovery candidate")
def _event_key(jurisdiction,reference,title,update_type,date):
    base="|".join([jurisdiction,reference or re.sub(r"\W+"," ",title.lower())[:120],update_type or "",date or ""]); return sha256(base.encode()).hexdigest()[:28]

def _finding_from_page(page,source):
    text=_clean(f"{page.get('title','')} {page.get('text','')}"); score,cat,domain=_relevance(text)
    if score<.42:return None
    date=_normalise_date(page.get("publication_date")) or _extract_date_from_text(text)
    if not date or date<REPORT_START_DATE or not _date_in_reporting_week(date):return None
    update=_material_update_type(text)
    if not update:return None
    ref=_extract_legal_reference(text); development=_development_type(ref,update)
    # Broadened gate: regulatory/authority developments may lack a formal legal reference,
    # but only direct primary-source evidence can verify them.
    basis=page.get("evidence_basis") or "direct primary page"
    source_q=_source_quality(source); evidence_q=_evidence_quality(basis=basis,date=date,reference=ref,event_type=update,text=text)
    approved=source.get("verification_status") in APPROVED_SOURCE_STATUSES
    verified=bool(approved and basis=="direct primary page" and evidence_q>=.72 and (ref or development=="Regulatory/authority development"))
    finding_status="verified" if verified else "candidate"
    jurisdiction=source.get("jurisdiction") or "Unknown"; level="EU" if jurisdiction=="European Union" else "National"
    if cat not in PRODUCT_CATEGORIES:cat="Other"
    scope=_scope_from_evidence(text,jurisdiction,cat); summary=_summary_from_evidence(text,update,ref,_effective_date(text),jurisdiction); ops,_=_detected_scope(text)
    app_status,app_score,app_reasons=applicability_from_evidence(jurisdiction=jurisdiction,category=cat,text=text,detected_operators=ops)
    impact="Informational"
    if app_status=="Likely relevant" and update in {"Compliance milestone","Enforcement development"}:impact="High"
    elif app_status=="Likely relevant":impact="Medium"
    elif app_status=="Possibly relevant":impact="Low"
    profile=load_company_profile()
    if cat in set(profile.get("critical_categories",[])) and app_status=="Likely relevant":impact="High"
    event_key=_event_key(jurisdiction,ref,page.get("title") or "",update,date)
    existing=get_finding_by_event_key(event_key); correlated=[]
    if existing:
        correlated=list(dict.fromkeys([x for x in str(existing.get("correlated_sources") or "").split("\n") if x]+[page.get("url") or ""]))
        fid=existing["id"]
    else:
        fid="WEB-"+sha256((event_key+(page.get("url") or "")).encode()).hexdigest()[:20].upper(); correlated=[page.get("url") or ""]
    eff=_effective_date(text)
    trace={"relevance_score":round(score,2),"source_approved":approved,"source_quality":source_q,"evidence_quality":evidence_q,"evidence_basis":basis,"publication_date_verified":bool(date),"legal_reference":ref,"development_type":development,"material_event":update,"applicability_status":app_status,"applicability_reasons":app_reasons}
    rec={"id":fid,"is_demo":False,"jurisdiction":"EU" if level=="EU" else jurisdiction,"geographic_level":level,"authority":source.get("authority") or "Official authority","source_name":source.get("name"),"source_url":page.get("url"),"original_title":page.get("title") or ref or "Regulatory development","english_title":page.get("title") or (f"{ref} — {cat} {update.lower()}" if ref else f"{cat} — {update}"),"original_language":source.get("language") or "Unknown","publication_date":date,"effective_date":eff,"compliance_deadline":eff if update=="Compliance milestone" else None,"legislative_status":update,"instrument_type":"Regulatory notice" if not ref else "Legal instrument","compliance_domain":domain,"category":cat,"subcategory":None,"affected_parties":scope,"key_obligations":scope,"key_changes":summary,"business_impact":impact,"recommended_follow_up":_business_action(text,cat,update),"confidence_score":round((source_q+evidence_q+score)/3,2),"evidence_excerpt":_clean(page.get("text") or text)[:1200],"retrieved_at":_now(),"finding_summary":summary,"ai_summary":"","ai_analysis":"","user_notes":"","publication_update_date":date,"effective_application_date":eff,"scope":scope,"summary":summary,"legislation":ref,"status":update,"business_action":_business_action(text,cat,update),"finding_status":finding_status,"development_type":development,"event_key":event_key,"source_quality_score":source_q,"evidence_quality_score":evidence_q,"evidence_basis":basis,"applicability_status":app_status,"applicability_score":app_score,"applicability_reasons":json.dumps(app_reasons,ensure_ascii=False),"decision_trace":json.dumps(trace,ensure_ascii=False),"correlated_sources":"\n".join(correlated)}
    return rec

def _tavily_key(): return os.environ.get("TAVILY_API_KEY","").strip()
def _search_tavily(query,max_results,include_domains=None):
    key=_tavily_key()
    if not key:return []
    payload={"query":query,"search_depth":"basic","max_results":max_results,"topic":"general","include_answer":False,"include_raw_content":False,"include_published_date":True,"time_range":"week"}
    if include_domains:payload.update(include_domains=include_domains,include_domains_mode="restrict")
    r=requests.post("https://api.tavily.com/search",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json=payload,timeout=AUTO_WEB_TIMEOUT_SECONDS)
    if r.status_code>=400 and "time_range" in payload:payload.pop("time_range");r=requests.post("https://api.tavily.com/search",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json=payload,timeout=AUTO_WEB_TIMEOUT_SECONDS)
    r.raise_for_status(); data=r.json(); return [SearchHit(str(x.get("title") or ""),str(x.get("url") or ""),str(x.get("content") or ""),_normalise_date(x.get("published_date")),float(x.get("score")) if x.get("score") is not None else None) for x in data.get("results",[]) if x.get("url")]
def _search_ddgs(query,max_results):
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:return [SearchHit(str(x.get("title") or ""),str(x.get("href") or ""),str(x.get("body") or "")) for x in ddgs.text(query,max_results=max_results) if x.get("href")]
    except:return []
def web_search(query,max_results=8,include_domains=None):
    if _tavily_key():
        try:set_runtime_state("last_search_provider","Tavily");return _search_tavily(query,max_results,include_domains),"Tavily"
        except Exception as e:set_runtime_state("last_search_error",str(e)[:700])
    h=_search_ddgs(query,max_results);p="DDGS" if h else "Unavailable";set_runtime_state("last_search_provider",p);return h,p

def _download(url):
    r=requests.get(url,timeout=AUTO_WEB_TIMEOUT_SECONDS,allow_redirects=True,headers={"User-Agent":USER_AGENT,"Accept-Language":"en,*;q=.7"});r.raise_for_status();return r.text,r.url

def _extract_page(url):
    try:html,final=_download(url)
    except:return None
    soup=BeautifulSoup(html,"html.parser")
    for t in soup(["script","style","noscript","svg"]):t.decompose()
    title=(soup.title.string.strip() if soup.title and soup.title.string else final); text=_clean(" ".join(soup.stripped_strings))[:150000]
    date=None
    for attrs in [{"property":"article:published_time"},{"name":"date"},{"itemprop":"datePublished"}]:
        n=soup.find(attrs=attrs)
        if n:date=_normalise_date(n.get("content") or n.get("datetime") or n.get_text(" ",strip=True));
        if date:break
    return {"title":title,"text":text,"url":final,"publication_date":date,"evidence_basis":"direct primary page"}
def _page_from_hit(hit):return {"title":hit.title or hit.url,"text":hit.snippet or "","url":hit.url,"publication_date":hit.published_date,"evidence_basis":"search snippet"}

def _candidate_links(source):
    try:html,final=_download(source["url"])
    except:return []
    soup=BeautifulSoup(html,"html.parser");out=[]
    for a in soup.find_all("a",href=True):
        u=urljoin(final,a["href"]);label=(a.get_text(" ",strip=True)+" "+u).lower()
        if _registered_host_match(u,source["url"]) and any(x.lower() in label for x in RELEVANCE_TERMS+['law','legal','regulation','tax','customs','gazette','guidance']):out.append(u.split('#')[0])
        if len(out)>=AUTO_WEB_SOURCE_LINKS:break
    return list(dict.fromkeys(out))

def _store(rec,stage):
    upsert_finding(rec,change_type=stage)
    record_automated_decision(finding_id=rec["id"],event_key=rec["event_key"],stage=stage,decision=rec["finding_status"],reasons=json.loads(rec["decision_trace"]),evidence_url=rec["source_url"])

def ingest_registered_sources():
    findings=candidates=checked=0; errors=[]; providers=set()
    for source in list_sources().to_dict("records"):
        if not source.get("active") or source.get("verification_status") not in APPROVED_SOURCE_STATUSES:continue
        host=_host(source["url"]); roles=str(source.get("source_roles") or "product;environment;tax;customs").split(';')
        try:
            hits=[]
            for role in roles:
                group=SEARCH_GROUPS.get(role)
                if not group:continue
                h,p=web_search(group,max(4,AUTO_WEB_MAX_RESULTS//4),include_domains=[host] if host else None);providers.add(p);hits.extend([x for x in h if _registered_host_match(x.url,source["url"])])
            urls=list(dict.fromkeys(_candidate_links(source)+[x.url for x in hits]+[source["url"]]))[:AUTO_WEB_MAX_RESULTS+AUTO_WEB_SOURCE_LINKS]
            for u in urls:
                checked+=1; page=_extract_page(u)
                if not page:continue
                rec=_finding_from_page(page,source)
                if rec:_store(rec,"registered-primary-source"); findings+=rec["finding_status"]=="verified"; candidates+=rec["finding_status"]=="candidate"
            # snippets are discovery only; they can create candidates, never verified findings.
            for h in hits[:AUTO_WEB_MAX_RESULTS]:
                rec=_finding_from_page(_page_from_hit(h),source)
                if rec and rec["finding_status"]=="candidate":_store(rec,"registered-search-discovery");candidates+=1
        except Exception as e:errors.append(f"{source.get('name')}: {e}")
    return {"findings":int(findings),"candidates":int(candidates),"checked_urls":checked,"providers":sorted(providers),"errors":errors}

def _sources_for(jurisdiction,role):
    df=list_sources();out=[]
    for s in df.to_dict("records"):
        if s.get("jurisdiction")==jurisdiction and s.get("verification_status") in APPROVED_SOURCE_STATUSES and role in str(s.get("source_roles") or "").split(';'):out.append(s)
    return out

def scan_all_jurisdictions():
    verified=candidates=hits_total=0; reports=[];providers=set();a,b=reporting_window()
    for item in JURISDICTION_REGISTRY:
        j=item["name"];before=verified
        for role,terms in SEARCH_GROUPS.items():
            sources=_sources_for(j,role)
            for source in sources:
                host=_host(source["url"]); query=f'"{j}" {terms} {a} {b}'
                hits,p=web_search(query,AUTO_WEB_RESULTS_PER_JURISDICTION,include_domains=[host] if host else None);providers.add(p);hits_total+=len(hits)
                for h in hits:
                    if not _registered_host_match(h.url,source["url"]):continue
                    # Discovery result stays candidate until direct primary page is retrievable.
                    rec=_finding_from_page(_page_from_hit(h),source)
                    if rec:_store(rec,"jurisdiction-discovery");candidates+=1
                    page=_extract_page(h.url)
                    if page:
                        rec=_finding_from_page(page,source)
                        if rec:_store(rec,"jurisdiction-primary-verification");verified+=rec["finding_status"]=="verified";candidates+=rec["finding_status"]=="candidate"
        reports.append({"jurisdiction":"EU" if item["level"]=="EU" else j,"geographic_level":item["level"],"findings":verified-before})
    return {"reporting_week_start":a,"reporting_week_end":b,"jurisdictions_checked":len(JURISDICTION_REGISTRY),"findings":int(verified),"candidates":int(candidates),"search_hits":hits_total,"providers":sorted(providers),"jurisdiction_reports":reports}

def discover_source_candidates(batch_size=6):
    # Unregistered domains are discovery-only and always inactive/pending.
    existing=list_sources(); known={_host(x) for x in existing["url"].tolist()} if not existing.empty else set(); saved=0
    for item in JURISDICTION_REGISTRY[:batch_size]:
        j=item["name"];hits,_=web_search(f'official government legislation gazette tax customs regulator "{j}"',8)
        for h in hits:
            host=_host(h.url)
            if not host or host in known:continue
            sid="DISC-"+sha256((j+host).encode()).hexdigest()[:18].upper();upsert_source({"id":sid,"jurisdiction":j,"authority":"Pending verification","name":h.title[:180] or host,"url":f"https://{host}/","source_type":"discovered candidate","language":"Unknown","verification_status":"pending verification","retrieval_method":"web discovery","active":False,"last_success":None,"last_failure":None,"error_state":None,"last_checked":_now(),"discovered_automatically":True,"discovery_reason":"Unregistered domain discovered by search. Cannot create verified findings until a human approves it.","source_roles":"","source_quality_score":.35});known.add(host);saved+=1
    return {"candidates":saved}
def run_live_monitoring(force=False):
    if not AUTO_WEB_DISCOVERY and not force:return {"ran":False,"reason":"Automatic web discovery disabled"}
    last=get_runtime_state("last_live_web_sync")
    if last and not force:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last)<timedelta(minutes=AUTO_WEB_DISCOVERY_INTERVAL_MINUTES):return {"ran":False,"reason":"Not due yet","last_sync":last}
        except:pass
    started=_now();ing=ingest_registered_sources();scope=scan_all_jurisdictions();disc=discover_source_candidates();finished=_now();report={"ran":True,"started":started,"finished":finished,"provider":"tavily" if _tavily_key() else "ddgs","ingestion":ing,"jurisdiction_scan":scope,"discovery":disc};set_runtime_state("last_live_web_sync",finished);set_runtime_state("last_live_web_report",json.dumps(report));return report
def last_live_report():
    try:return json.loads(get_runtime_state("last_live_web_report","") or "{}")
    except:return {}
def search_diagnostics():
    r=last_live_report();i=r.get("ingestion",{});s=r.get("jurisdiction_scan",{});return {"provider":get_runtime_state("last_search_provider","Unavailable"),"tavily_configured":bool(_tavily_key()),"last_error":get_runtime_state("last_search_error","") or (i.get("errors") or [None])[0],"last_findings":int(i.get("findings",0))+int(s.get("findings",0)),"last_candidates":int(i.get("candidates",0))+int(s.get("candidates",0)),"last_search_hits":int(s.get("search_hits",0)),"jurisdictions_checked":s.get("jurisdictions_checked",0),"last_sync":r.get("finished")}
def tavily_connection_test():
    if not _tavily_key():return {"ok":False,"error":"TAVILY_API_KEY is not visible to the application."}
    try:h=_search_tavily("EU product compliance regulation VAT packaging official journal",5,["eur-lex.europa.eu"]);return {"ok":True,"result_count":len(h),"sample":[x.title for x in h[:3]]}
    except Exception as e:return {"ok":False,"error":str(e)}
