"""
Contact Researcher Agent - Specialized deep contact research for property management professionals.
"""

import json
import os
import re
from urllib.parse import urlparse
from typing import Any, Callable, Dict, List, Optional

import requests
from openai import OpenAI

import hubspot_client as hs
from utils import extract_company_name, extract_person_name


class ContactResearcher:
    """
    Contact Researcher Agent - Expert intelligence analyst specializing in deep contact research
    for property management professionals.
    """

    def __init__(self, model: str = "gpt-5-mini"):
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model  # "gpt-5" or "gpt-5-mini"

        # ---- Optimized developer prompt (first principles; deterministic routing & budgets) ----
        self.system_prompt = """You are the **Contact Research & Enrichment Specialist** for RentVine’s sales team.

# Objective
Given a partial prospect (name/company/title/email may be missing):
1) Resolve identity, 2) Enrich thoroughly (company & person), 3) Score fit with RentVine’s Contact ICP, 4) Return a concise **Markdown** briefing. Start with a top-line **Agent Summary**.

# Ground Rules
- Truthfulness over coverage; mark gaps with confidence.
 - We have already retrieved DB/HubSpot and additional enrichment searches; use them to ground answers.
- You MAY use `web_search` for limited breadth if critical gaps remain.
- **Identity lock** required before deep research (Name + Company + Title). If uncertain, proceed with `confidence="low"` and list assumptions.
- Respect privacy: professional/public sources only.

# Research Approach
- HubSpot first → harvest contact/company, LinkedIn/domain/title/phones/emails if present.
- Database second → merge prior enrichments from contacts and the NEO Research Database.
- Additional enrichment searches → confirm identity and find LinkedIn/company pages when missing.
- Use web search for breadth (company context, news, events, PMS mentions) only if needed.
- Reconcile conflicts with preference: HubSpot > company site > LinkedIn > reputable media > aggregators. Attach per-field confidence.

# Deep Research Checklist (after identity lock)
1) Company confirmation (domain, region, portfolio focus SFH vs HOA/MF/commercial, scale proxies)
2) LinkedIn & Bio (role/seniority, short bio)
3) Career history (prior roles, tenure, notable outcomes)
4) Signals (dept, buyer role, PMS familiarity, NARPM/IREM)
5) Contactability (domain email pattern; phone if public; LinkedIn preferred)
6) Recent activity (news/podcasts/events ≤24 months)
7) Scoring (apply RentVine Contact ICP; set tier with confidence)

# Contact ICP (RentVine)
Baseline:
- Property management company
- Primarily single-family (≥~50% SFH qualifies as ICP)

Core dimensions:
1) Portfolio Type (highest weight): + SFH-dominant; − HOA-only/commercial-only
2) Units (estimate listings×10): <50 low | 50–150 med | 150–1000 high | >1000 enterprise motion
3) PMS: competitors (AppFolio/Buildium/Yardi/DoorLoop) +; RentVine −; HOA/MF-centric −; unknown neutral
4) Employee count: 5–10 small; 20+ strong; 30–40+ enterprise
5) Website provider: PM-focused vendors (PMW, Doorgrow, Fourandhalf, Upkeep) +; generic neutral; unrelated −
6) Exclusions: HOA-only, MF-only, brokerages, no active mgmt

Each signal has confidence: high/medium/low.

# Output Contract
Return a concise, external-facing **Markdown** brief only. No JSON, no code fences.

## Markdown
### Agent Summary
2–4 sentences: growth/tech maturity/gaps/niche/openness to PMS change.

## Contact Overview
- **Name:** …
- **Title & Company:** …
- **Location:** …
- **LinkedIn:** …
- **Other Social Media:** … (confidence: H/M/L)
- **Email:** …
- **Contact ICP Score:** 0–100 (Confidence: High/Medium/Low)

## Professional Summary
2–4 sentences on role/expertise/relevance.

## Career Highlights
- Role — Company (Years) — one-line impact
- Role — Company (Years) — one-line impact

## Relevance to RentVine
- ICP signals (SFH focus, PMS hints, region, scale, timing)

## Talking Points & Personalization Hooks
- Specific interests, recent news, achievements, tech stack hints, strategic changes

## Personalization Data Points
- Professional (3): Publicly verifiable items such as awards/speaking, certifications, notable projects, associations (e.g., NARPM/IREM), articles/podcasts, community roles.
- Personal (3): Light, publicly shared items suitable for respectful outreach (e.g., volunteer causes, local/community affiliation, education, hobbies mentioned by the person). Avoid sensitive/private topics.

## Assumptions & Data Gaps
- Clear list of assumptions or missing info

## Sources
- Bulleted list with {title – URL} and (confidence: H/M/L)

 

# Streaming Behavior

* Emit **Agent Summary** as soon as identity locks; then progressively fill sections.
* If tool budgets are exhausted, return what you have plus clear data gaps.

## STYLE & TONE

* Professional but conversational — written like an SDR handoff.
* Summary-first: the Agent Summary should give the SDR immediate context without scrolling.
* Avoid robotic phrasing; use natural language.
* Show transparency about uncertainties.
* Never reveal internal formulas or raw tool outputs.
"""

        # ---- Tool definitions: HubSpot + Supabase as tool calls; Serper + native web search ----
        self.tools = [
            {
                "type": "function",
                "name": "query_hubspot_object",
                "description": "Search HubSpot for contacts or companies",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "object_type": {"type": "string", "enum": ["contacts", "companies"]},
                        "properties": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fields to return",
                        },
                        "filters": {
                            "type": "array",
                            "description": "Filter list (AND within each object). Example: [{\"propertyName\":\"email\",\"operator\":\"EQ\",\"value\":\"a@b.com\"}]",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "propertyName": {"type": "string"},
                                    "operator": {
                                        "type": "string",
                                        "enum": [
                                            "EQ",
                                            "NEQ",
                                            "GT",
                                            "GTE",
                                            "LT",
                                            "LTE",
                                            "HAS_PROPERTY",
                                            "NOT_HAS_PROPERTY",
                                            "CONTAINS_TOKEN",
                                            "NOT_CONTAINS_TOKEN",
                                        ],
                                    },
                                    "value": {
                                        "type": ["string", "number", "boolean", "array", "null"],
                                        "items": {"type": ["string", "number", "boolean"]}
                                    },
                                },
                                "required": ["propertyName", "operator", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["object_type", "properties", "filters", "limit"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "query_supabase_tables",
                "description": "Query Supabase tables for prior enrichments",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "enum": ["contacts", "research_database"]},
                        "columns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Columns to select",
                        },
                        "filters": {
                            "type": "array",
                            "description": "Filters: [{\"column\":\"email\",\"op\":\"eq\",\"value\":\"x@y.com\"}]",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "column": {"type": "string"},
                                    "op": {
                                        "type": "string",
                                        "enum": ["eq", "ilike", "gt", "gte", "lt", "lte", "is", "in"],
                                    },
                                    "value": {
                                        "type": ["string", "number", "boolean", "array", "null"],
                                        "items": {"type": ["string", "number", "boolean"]}
                                    },
                                },
                                "required": ["column", "op", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["table_name", "columns", "filters", "limit"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "search_google_serper",
                "description": "Precision search via Serper (e.g., site-restricted LinkedIn, /team, /about)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "Exact query (supports site: and quoted phrases)"},
                        "num": {"type": "integer", "default": 5},
                    },
                    "required": ["q", "num"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "web_search",
                "user_location": {"type": "approximate"},
                "search_context_size": "medium",
            },
        ]

    # ---------------------- Internal tool handlers ----------------------
    def _tool_query_hubspot_object(self, args: Dict[str, Any]) -> Dict[str, Any]:
        object_type = str(args.get("object_type"))
        props = [str(p) for p in (args.get("properties") or [])]
        filters = args.get("filters") or []
        limit = int(args.get("limit") or 5)

        try:
            if object_type == "contacts":
                email = None
                firstname = None
                lastname = None
                company = None
                for f in filters:
                    name = str(f.get("propertyName", "")).lower()
                    op = str(f.get("operator", "")).upper()
                    val = f.get("value")
                    if name == "email" and op == "EQ" and isinstance(val, str):
                        email = val
                    elif name == "firstname" and isinstance(val, str):
                        firstname = val
                    elif name == "lastname" and isinstance(val, str):
                        lastname = val
                    elif name == "company" and isinstance(val, str):
                        company = val

                if email:
                    one = hs.search_contact(email=email, properties=props)
                    return {"results": [one] if one else []}

                results: List[Dict[str, Any]] = []
                if firstname or lastname or company:
                    results = hs.search_contact_by_fields(
                        firstname=firstname, lastname=lastname, company=company, limit=limit, properties=props
                    )
                else:
                    # Fallback to free-text query from any provided values
                    q_terms = []
                    for f in filters:
                        if f.get("value"):
                            q_terms.append(str(f.get("value")))
                    if q_terms:
                        results = hs.search_contacts_by_query(" ".join(q_terms), limit=limit, properties=props)
                return {"results": results[:limit]}

            elif object_type == "companies":
                domain = None
                name_q = None
                for f in filters:
                    name = str(f.get("propertyName", "")).lower()
                    op = str(f.get("operator", "")).upper()
                    val = f.get("value")
                    if name == "domain" and op == "EQ" and isinstance(val, str):
                        domain = val
                    elif name == "name" and isinstance(val, str):
                        name_q = val

                if domain:
                    one = hs.search_company_by_domain(domain, properties=[p for p in props] or None)
                    return {"results": [one] if one else []}
                if name_q:
                    results = hs.search_companies_by_name(name_q, limit=limit)
                    return {"results": results[:limit]}
                return {"results": []}
            else:
                return {"results": []}
        except hs.HubSpotError as e:
            return {"error": str(e), "results": []}

    def _tool_query_supabase_tables(self, args: Dict[str, Any]) -> Dict[str, Any]:
        table = str(args.get("table_name"))
        columns = args.get("columns") or ["*"]
        filters = args.get("filters") or []
        limit = int(args.get("limit") or 10)

        base_url = (os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
        token = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
        if not base_url or not token:
            return {"error": "Supabase env vars missing", "results": []}
        url = f"{base_url}/rest/v1/{table}"
        headers = {
            "apikey": token,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        params: Dict[str, Any] = {
            "select": ",".join(columns) if columns else "*",
            "limit": str(limit),
        }
        # Map filters to PostgREST syntax
        for f in filters:
            col = str(f.get("column", ""))
            op = str(f.get("op", "eq")).lower()
            val = f.get("value")
            if not col:
                continue
            if op == "eq":
                params[col] = f"eq.{val}"
            elif op == "ilike":
                params[col] = f"ilike.*{val}*"
            elif op in ("gt", "gte", "lt", "lte"):
                params[col] = f"{op}.{val}"
            elif op == "is":
                params[col] = f"is.{val}"
            elif op == "in":
                try:
                    vals = val if isinstance(val, list) else [val]
                    params[col] = f"in.({','.join(map(str, vals))})"
                except Exception:
                    params[col] = f"in.({val})"

        try:
            r = requests.get(url, headers=headers, params=params, timeout=float(os.getenv("HTTP_TIMEOUT", "20")))
            if not r.ok:
                return {"error": f"{r.status_code} {r.text}", "results": []}
            data = r.json()
            return {"results": data if isinstance(data, list) else [data]}
        except Exception as e:
            return {"error": str(e), "results": []}

    def _tool_search_google_serper(self, args: Dict[str, Any]) -> Dict[str, Any]:
        q = str(args.get("q", "")).strip()
        num = int(args.get("num", 5) or 5)
        key = os.getenv("SERPER_API_KEY")
        if not key or not q:
            return {"results": []}
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": q, "num": num},
                timeout=float(os.getenv("HTTP_TIMEOUT", "20")),
            )
            if not resp.ok:
                return {"error": f"{resp.status_code} {resp.text}", "results": []}
            data = resp.json() or {}
            items = []
            for cat in ("knowledgeGraph", "organic", "news", "topStories"):
                v = data.get(cat)
                if isinstance(v, list):
                    for it in v:
                        items.append({
                            "title": it.get("title") or it.get("name"),
                            "url": it.get("link") or it.get("url"),
                            "snippet": it.get("snippet") or it.get("description"),
                        })
            return {"results": items[:num]}
        except Exception as e:
            return {"error": str(e), "results": []}

    def _filter_enrichment_items(
        self,
        items: List[Dict[str, Any]],
        person_name: Optional[str],
        company_name: Optional[str],
        email: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Filter and rank enrichment results to avoid irrelevant/sensitive links.
        - Drops help/support/login pages and off-topic domains (e.g., findagrave).
        - Prefers LinkedIn profiles, company domain, and reputable directories.
        - Requires loose match on person last name or company token in title/snippet when possible.
        """
        if not items:
            return []
        last = None
        first = None
        if person_name:
            parts = [p for p in re.split(r"\s+", person_name.strip()) if p]
            if parts:
                first, last = parts[0], parts[-1]
        comp_tok = (company_name or "").strip().lower()
        email_domain = None
        if email and "@" in email:
            try:
                email_domain = email.split("@", 1)[1].strip().lower()
            except Exception:
                email_domain = None

        BLOCK_SUBSTR = [
            "findagrave.com",
            "/help",
            "/support",
            "login",
            "forgot-password",
        ]
        BLOCK_NETLOCS = {
            "help.x.com",
            "support.x.com",
            "help.twitter.com",
            "support.twitter.com",
            "support.google.com",
        }
        PREFER_SUBSTR = [
            "linkedin.com/in/",
            "linkedin.com/company/",
            "narpm.org",
            "irem.org",
            "bbb.org",
            "zillow.com",
            "realtor.com",
            "biggerpockets.com",
            "youtube.com",
            "spotify.com",
            "podcast",
            "buzzsprout.com",
            "podbean.com",
            "libsyn.com",
        ]

        def score_item(it: Dict[str, Any]) -> int:
            url = (it.get("url") or "").strip()
            title = (it.get("title") or "").strip().lower()
            snip = (it.get("snippet") or "").strip().lower()
            try:
                p = urlparse(url)
                netloc = (p.netloc or "").lower()
                path = (p.path or "").lower()
            except Exception:
                netloc, path = "", ""
            # Block rules
            if netloc in BLOCK_NETLOCS:
                return -999
            if any(b in url.lower() for b in BLOCK_SUBSTR):
                return -999
            # Relevance heuristics
            s = 0
            u = url.lower()
            if "linkedin.com/in/" in u:
                s += 100
            if email_domain and (netloc.endswith(email_domain) or email_domain in netloc):
                s += 50
            if any(pref in u for pref in PREFER_SUBSTR):
                s += 20
            # Name/company mention boosts
            if last and (last.lower() in title or last.lower() in snip or last.lower() in u):
                s += 15
            if first and (first.lower() in title or first.lower() in snip):
                s += 5
            if comp_tok and (comp_tok in title or comp_tok in snip or comp_tok in u):
                s += 10
            # Penalize generic help/login pages further if slipped through
            if any(tok in netloc for tok in ("help", "support")) or any(tok in path for tok in ("help", "support")):
                s -= 50
            return s

        # Rank, drop negatives, and de-duplicate by URL
        ranked = sorted(items, key=score_item, reverse=True)
        out: List[Dict[str, Any]] = []
        seen = set()
        for it in ranked:
            url = (it.get("url") or "").strip()
            if not url or url in seen:
                continue
            if score_item(it) <= 0:
                continue
            out.append(it)
            seen.add(url)
            if len(out) >= 8:
                break
        return out

    def _serper_parallel(self, queries: List[str], num: int = 5, max_workers: Optional[int] = None) -> List[Dict[str, Any]]:
        """Execute multiple Serper queries concurrently and return de-duplicated items.
        Respects SERPER_API_KEY presence; returns [] if not configured.
        """
        if not os.getenv("SERPER_API_KEY"):
            return []
        if not queries:
            return []
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
        except Exception:
            # Fallback to serial if concurrency not available
            agg: List[Dict[str, Any]] = []
            seen = set()
            for q in queries:
                res = self._tool_search_google_serper({"q": q, "num": num}) or {}
                for it in (res.get("results") or [])[:num]:
                    url = (it.get("url") or "").strip()
                    if url and url not in seen:
                        agg.append(it)
                        seen.add(url)
            return agg

        workers = max(1, min(int(os.getenv("SERPER_MAX_WORKERS", "4")), len(queries)))
        if max_workers is not None:
            workers = max(1, min(max_workers, len(queries)))
        agg: List[Dict[str, Any]] = []
        seen = set()
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(self._tool_search_google_serper, {"q": q, "num": num}) for q in queries]
                for fut in as_completed(futs):
                    try:
                        res = fut.result() or {}
                    except Exception:
                        res = {}
                    for it in (res.get("results") or [])[:num]:
                        url = (it.get("url") or "").strip()
                        if url and url not in seen:
                            agg.append(it)
                            seen.add(url)
        except Exception:
            # On any concurrency failure, fall back to serial
            agg = []
            seen = set()
            for q in queries:
                res = self._tool_search_google_serper({"q": q, "num": num}) or {}
                for it in (res.get("results") or [])[:num]:
                    url = (it.get("url") or "").strip()
                    if url and url not in seen:
                        agg.append(it)
                        seen.add(url)
        return agg

    # ---------------------- Core flow (single-turn with prefetch) ----------------------
    def research(self, user_input: str, stream_callback: Optional[Callable[[str], None]] = None) -> str:
        """
        Single-turn contact research with DB-first enrichment and a precision Serper pass.
        - Prefetch from Supabase and HubSpot locally.
        - Use Serper for precise LinkedIn/company page discovery (pre-executed).
        - Use built-in web_search tool for additional breadth during the single streamed generation.
        """
        try:
            if stream_callback:
                stream_callback("🔍 Checking database (NEO Research Database) for existing contact...\n")

            # 1) Parse hints from user input
            email = None
            try:
                m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_input)
                if m:
                    email = m.group(0)
            except Exception:
                pass
            person_name = (extract_person_name(user_input) or "").strip()
            company_name = (extract_company_name(user_input) or "").strip()

            # 2) Supabase contact lookup
            try:
                from supabase_client import find_contact as sb_find_contact
            except Exception:
                sb_find_contact = None  # type: ignore
            db_contact = None
            if sb_find_contact:
                try:
                    db_contact = sb_find_contact(
                        email=email,
                        name_like=person_name or None,
                        company_name=company_name or None,
                        strict=bool(person_name and company_name),
                    )
                except Exception:
                    db_contact = None
            if stream_callback:
                stream_callback("📋 " + ("Found existing record; merging..." if db_contact else "No existing record found.") + "\n")

            # 3) HubSpot contact lookup
            if stream_callback:
                stream_callback("🌐 Querying HubSpot for contact details...\n")
            hs_contact = None
            hubspot_error_msg: Optional[str] = None
            candidates: List[Dict[str, Any]] = []
            try:
                # Heuristic match scorer to avoid false positives
                def _match_score(props: Dict[str, Any]) -> int:
                    tf, tl = None, None
                    if person_name:
                        pts = [p for p in re.split(r"\s+", person_name.strip()) if p]
                        if len(pts) >= 2:
                            tf, tl = pts[0].lower(), pts[-1].lower()
                    pf = (props.get("firstname") or "").strip().lower()
                    pl = (props.get("lastname") or "").strip().lower()
                    comp = (props.get("company") or "").strip().lower()
                    score = 0
                    if tf and tl and pf == tf and pl == tl:
                        score += 4
                    else:
                        if tl and pl == tl:
                            score += 2
                        if tf and pf == tf:
                            score += 1
                    if company_name:
                        cn = company_name.strip().lower()
                        if cn and (cn in comp or any(tok in comp for tok in cn.split())):
                            score += 2
                    if props.get("email"):
                        score += 1
                    return score

                if email:
                    one = hs.search_contact(email=email, properties=[
                        "firstname","lastname","email","jobtitle","company","phone","city","state","country","address","zip","hs_object_id","hs_lastmodifieddate","linkedinbio","linkedin_company_page","website"
                    ])
                    if one:
                        candidates = [one]
                elif person_name or company_name:
                    # Split name if possible
                    fn, ln = None, None
                    if person_name:
                        parts = [p for p in re.split(r"\s+", person_name) if p]
                        if len(parts) >= 2:
                            fn, ln = parts[0], parts[-1]
                    try:
                        by_fields = hs.search_contact_by_fields(
                            firstname=fn,
                            lastname=ln,
                            company=company_name or None,
                            limit=5,
                            properties=[
                                "firstname","lastname","email","jobtitle","company","phone","city","state","country","address","zip","hs_object_id","hs_lastmodifieddate","linkedinbio","linkedin_company_page","website"
                            ],
                        ) or []
                    except hs.HubSpotError:
                        by_fields = []
                    candidates.extend(by_fields)
                    q_terms = " ".join([t for t in [person_name or "", company_name or ""] if t]).strip()
                    if q_terms:
                        try:
                            by_query = hs.search_contacts_by_query(q_terms, limit=5, properties=[
                                "firstname","lastname","email","jobtitle","company","phone","city","state","country","address","zip","hs_object_id","hs_lastmodifieddate","linkedinbio","linkedin_company_page","website"
                            ]) or []
                        except hs.HubSpotError:
                            by_query = []
                        seen_ids = set(str(c.get("id")) for c in candidates if isinstance(c, dict) and c.get("id"))
                        for c in by_query:
                            cid = str(c.get("id")) if isinstance(c, dict) and c.get("id") else None
                            if cid and cid not in seen_ids:
                                candidates.append(c)
                # Choose best candidate above threshold
                best = None
                best_score = -1
                for c in candidates:
                    props = c.get("properties", {}) if isinstance(c, dict) else {}
                    s = _match_score(props)
                    if s > best_score:
                        best, best_score = c, s
                hs_contact = best if best_score >= 3 else None
            except hs.HubSpotError as _e:
                hs_contact = None
                try:
                    hubspot_error_msg = str(_e)
                except Exception:
                    hubspot_error_msg = "HubSpot lookup failed"

            # Normalize HS contact shape
            hs_props = hs_contact.get("properties", {}) if isinstance(hs_contact, dict) else {}
            # Status: indicate HubSpot presence
            if stream_callback:
                try:
                    if hubspot_error_msg:
                        stream_callback(f"⚠️ HubSpot lookup failed: {hubspot_error_msg}\n")
                    elif hs_contact and hs_props:
                        nm = (" ".join([p for p in [hs_props.get("firstname"), hs_props.get("lastname")] if p]) or hs_props.get("email") or "(contact)").strip()
                        comp_nm = (hs_props.get("company") or "").strip()
                        stream_callback(f"✅ Found in HubSpot: {nm}{(' — ' + comp_nm) if comp_nm else ''}\n")
                    else:
                        # If there were candidates but below the match threshold, inform user
                        if candidates:
                            stream_callback("ℹ️ No exact HubSpot match; continuing with enrichment.\n")
                        else:
                            stream_callback("ℹ️ No HubSpot contact found.\n")
                except Exception:
                    pass

            # 4) Additional enrichment searches (pre-executed)
            serper_items: List[Dict[str, Any]] = []
            # Determine identity lock from HubSpot fields
            full_name_hs = " ".join([p for p in [hs_props.get("firstname"), hs_props.get("lastname")] if p]).strip() if hs_props else ""
            comp_ref_hs = (company_name or hs_props.get("company") or "").strip()
            identity_locked = bool(hs_contact and (hs_props.get("email") or (full_name_hs and comp_ref_hs)))
            if stream_callback:
                if os.getenv("SERPER_API_KEY"):
                    stream_callback("🔎 Additional searches for personalization and enrichment...\n")
                else:
                    stream_callback("🔎 Additional searches for personalization and enrichment — API key missing; skipping\n")
            if os.getenv("SERPER_API_KEY"):
                queries: List[str] = []
                name_q = (full_name_hs or person_name or "").strip()
                comp_q = comp_ref_hs
                # Optional locality hints
                city = (hs_props.get("city") or "").strip() if hs_props else ""
                state = (hs_props.get("state") or "").strip() if hs_props else ""
                # LinkedIn (primary)
                if identity_locked:
                    if name_q and comp_q:
                        queries.append(f'"{name_q}" "{comp_q}" site:linkedin.com/in')
                    elif name_q:
                        queries.append(f'"{name_q}" site:linkedin.com/in')
                else:
                    if name_q and comp_q:
                        queries.append(f'"{name_q}" "{comp_q}" site:linkedin.com/in')
                        queries.append(f'{name_q} {comp_q} site:linkedin.com/in')
                    elif name_q:
                        queries.append(f'{name_q} site:linkedin.com/in')
                    if comp_q:
                        queries.append(f'"{comp_q}" site:linkedin.com/company')
                        queries.append(f'site:{comp_q} (team OR leadership OR about)')
                # Professional personalization: awards/speaking/interviews/podcasts
                if name_q and comp_q:
                    queries += [
                        f'"{name_q}" "{comp_q}" (award OR recognition OR keynote OR speaking OR webinar OR podcast)',
                        f'"{name_q}" "{comp_q}" (interview OR quoted)',
                    ]
                elif name_q:
                    queries += [
                        f'"{name_q}" (award OR recognition OR keynote OR speaking OR webinar OR podcast)',
                        f'"{name_q}" (interview OR quoted) property management',
                    ]
                # Community involvement (public): volunteer/board/chamber/charity
                loc_hint = f' "{city}"' if city else ''
                if name_q:
                    queries += [
                        f'"{name_q}"{loc_hint} (volunteer OR board OR "chamber of commerce" OR charity)',
                    ]
                # Social discovery (public profiles)
                if name_q:
                    queries += [
                        f'site:twitter.com "{name_q}" property management',
                        f'site:x.com "{name_q}" property management',
                        f'site:instagram.com "{name_q}" property management',
                        f'site:facebook.com "{name_q}" "{comp_q}"' if comp_q else f'site:facebook.com "{name_q}" property management',
                    ]
                # Execute in parallel and de-duplicate
                serper_items = self._serper_parallel(queries, num=5)
                # Filter and rank results to remove off-topic links (help/support/memorials, etc.)
                email_for_filter = email or (hs_props.get("email") if hs_props else None)
                serper_items = self._filter_enrichment_items(
                    serper_items, person_name or full_name_hs, comp_ref_hs, email_for_filter
                )

            # 5) Merge data into a compact context
            record: Dict[str, Any] = {}
            # From DB
            if isinstance(db_contact, dict):
                for k in ["full_name","first_name","last_name","email","job_title","company_name","company_domain","linkedin","phone"]:
                    if db_contact.get(k) not in (None, ""):
                        record[k] = db_contact.get(k)
            # From HubSpot
            if hs_props:
                record.setdefault("first_name", hs_props.get("firstname"))
                record.setdefault("last_name", hs_props.get("lastname"))
                record.setdefault("email", hs_props.get("email"))
                record.setdefault("job_title", hs_props.get("jobtitle"))
                record.setdefault("company_name", hs_props.get("company"))
                record.setdefault("phone", hs_props.get("phone"))
                record.setdefault("linkedin", hs_props.get("linkedinbio"))
                record.setdefault("company_website", hs_props.get("website"))

            # Basic identity from user input
            if person_name and not record.get("full_name"):
                record["full_name"] = person_name
            if company_name and not record.get("company_name"):
                record["company_name"] = company_name
            if email and not record.get("email"):
                record["email"] = email

            # Best guess LinkedIn from enrichment queries
            best_li = next((it.get("url") for it in serper_items if isinstance(it, dict) and isinstance(it.get("url"), str) and "linkedin.com/in" in it.get("url", "")), None)
            if best_li and not record.get("linkedin"):
                record["linkedin"] = best_li

            # Build sources list
            sources_used: List[str] = []
            if db_contact:
                sources_used.append("NEO Research Database")
            if hs_contact:
                sources_used.insert(0, "HubSpot")
            # Do not include raw enrichment URLs in sources_hint; pass them separately via enrichment_urls

            # 6) Prepare single-turn streaming call
            # Derive other social links (public profiles) from enrichment results (exclude LinkedIn)
            social_links: List[str] = []
            try:
                for it in serper_items:
                    url = (it.get("url") or "").strip()
                    u = url.lower()
                    if not url:
                        continue
                    if "linkedin.com/" in u:
                        continue
                    for host in ("instagram.com/", "facebook.com/", "x.com/", "twitter.com/", "youtube.com/", "tiktok.com/"):
                        if host in u and url not in social_links:
                            social_links.append(url)
                            break
                social_links = social_links[:3]
            except Exception:
                social_links = []
            if stream_callback:
                stream_callback("✍️ Constructing structured brief...\n")

            # Compact context text (do not echo)
            ctx_parts = []
            for k in ["full_name","first_name","last_name","job_title","company_name","company_domain","linkedin","email","phone","company_website"]:
                v = record.get(k)
                if v not in (None, ""):
                    ctx_parts.append(f"{k}: {v}")
            sources_hint = "; ".join(sources_used) if sources_used else ""
            serper_hint = "; ".join([it.get("url", "") for it in serper_items[:5] if isinstance(it, dict)])
            socials_hint = "; ".join(social_links) if social_links else ""
            context_text = (
                "Context (do not echo): "
                + "; ".join(ctx_parts)
                + (f"; sources_hint: {sources_hint}" if sources_hint else "")
                + (f"; enrichment_urls: {serper_hint}" if serper_hint else "")
                + ("; identity_locked: true; identity_confidence: high" if identity_locked else "; identity_locked: false")
                + (f"; social_links: {socials_hint}" if socials_hint else "")
            )

            # Final instructions = system prompt + output guardrails + context
            output_requirements = (
                "Follow the Output Contract exactly. Markdown only — no code fences, no JSON.\n"
                "- Do not fabricate; mark unknowns clearly.\n"
                "- If a HubSpot contact is present (see context), treat identity as LOCKED (Confidence: High) and do not claim ambiguity about name/company.\n"
                "- Prefer HubSpot/NEO DB for identity; prefer company site/LinkedIn for confirmation.\n"
                "- Include a '## Personalization Data Points' section with 3 Professional and 3 Personal items (public, non-sensitive).\n"
                "- Use web_search sparingly only if essential gaps remain.\n"
            )
            instructions = f"{self.system_prompt}\n\n{output_requirements}\n\n{context_text}"

            # Stream single response (web_search tool available to model); Serper already prefetched
            stream = self.openai_client.responses.create(
                model=self.model,
                input=user_input,
                instructions=instructions,
                tools=[t for t in self.tools if t.get("type") == "web_search"],
                store=True,
                stream=True,
            )

            full = ""
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    full += delta
                    if stream_callback:
                        stream_callback(delta)
            return full

        except Exception as e:
            err = f"❌ **Error during contact research:** {str(e)}"
            if stream_callback:
                stream_callback(err)
            return err

    def get_capabilities(self) -> Dict[str, Any]:
        """Return the capabilities and tools available to this agent."""
        tool_names: List[str] = []
        for t in self.tools:
            nm = t.get("name") if isinstance(t, dict) else None
            tool_names.append(nm or t.get("type", "tool"))  # type: ignore[arg-type]
        return {
            "model": self.model,
            "tools": tool_names,
            "specializations": [
                "Deep contact research",
                "Professional intelligence gathering",
            ],
        }
