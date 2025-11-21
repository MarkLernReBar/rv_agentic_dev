import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import json
import requests
import psycopg
from psycopg.types.json import Json


class SupabaseError(Exception):
    pass


def _env_first(*keys: str) -> str:
    for k in keys:
        val = os.getenv(k)
        if val and str(val).strip():
            return str(val).strip()
    return ""


def _base_url() -> str:
    raw = _env_first("NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_URL", "PUBLIC_SUPABASE_URL")
    url = (raw or "").rstrip("/")
    if not url:
        raise SupabaseError(
            "Supabase URL is not set (tried NEXT_PUBLIC_SUPABASE_URL, SUPABASE_URL, PUBLIC_SUPABASE_URL)"
        )
    return f"{url}/rest/v1"


def _pg_conn():
    dsn = os.getenv("POSTGRES_URL") or os.getenv("SUPABASE_POSTGRES_URL")
    if not dsn:
        raise SupabaseError("POSTGRES_URL is not set for direct Postgres access")
    return psycopg.connect(dsn, autocommit=True)


def _headers() -> Dict[str, str]:
    token = _env_first(
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_JWT",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "SUPABASE_ANON_KEY",
    )
    if not token:
        raise SupabaseError(
            "Supabase key is not set (tried SERVICE/ROLE/SECRET/JWT and ANON variants)"
        )
    profile = os.getenv("SUPABASE_PROFILE", "public").strip() or "public"
    return {
        "apikey": token,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Profile": profile,
        "Content-Profile": profile,
    }


def _headers_for_profile(profile: str) -> Dict[str, str]:
    """Return Supabase headers for a specific PostgREST profile/schema."""

    token = _env_first(
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_JWT",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "SUPABASE_ANON_KEY",
    )
    if not token:
        raise SupabaseError(
            "Supabase key is not set (tried SERVICE/ROLE/SECRET/JWT and ANON variants)"
        )
    profile_name = profile.strip() or "public"
    return {
        "apikey": token,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Profile": profile_name,
        "Content-Profile": profile_name,
    }


COMPANY_TABLE = os.getenv("SUPABASE_COMPANY_TABLE", "research_database")
CONTACT_TABLE = os.getenv("SUPABASE_CONTACT_TABLE", "contacts")
COMPANY_CONFLICT_TARGET = os.getenv("SUPABASE_COMPANY_CONFLICT", "domain")
CONTACT_CONFLICT_TARGET = os.getenv("SUPABASE_CONTACT_CONFLICT", "email")
CITY_COLUMNS = [
    col.strip()
    for col in os.getenv(
        "SUPABASE_CITY_COLUMNS", "headquarters_location"
    ).split(",")
    if col.strip()
]
CONTACT_TITLE_COLUMN = os.getenv("SUPABASE_CONTACT_TITLE_COLUMN", "job_title")
EMAIL_PATTERNS_TABLE = os.getenv("SUPABASE_EMAIL_PATTERNS_TABLE", "email_patterns")
ENRICHMENT_REQUESTS_TABLE = os.getenv("SUPABASE_ENRICHMENT_REQUESTS_TABLE", "enrichment_requests")
ENRICHMENT_REQUESTS_PATH = os.getenv("SUPABASE_ENRICHMENT_REQUESTS_PATH", "").lstrip("/")
CONTACT_ENRICH_TASKS_TABLE = os.getenv("SUPABASE_CONTACT_ENRICH_TASKS_TABLE", "contact_enrichment_tasks")
RUN_BATCHES_TABLE = os.getenv("SUPABASE_RUN_BATCHES_TABLE", "run_batches")
MAJOR_PMS = [
    p.strip()
    for p in os.getenv(
        "SUPABASE_MAJOR_PMS",
        "AppFolio,Buildium,Yardi,Propertyware,Rent Manager,Entrata,RealPage,ResMan",
    ).split(",")
    if p.strip()
]

# pm_pipeline tables for the async lead list agent
PM_PROFILE = os.getenv("SUPABASE_PM_PROFILE", "pm_pipeline")
PM_RUNS_TABLE = os.getenv("SUPABASE_PM_RUNS_TABLE", "pm_pipeline.runs")
PM_COMPANY_CANDIDATES_TABLE = os.getenv(
    "SUPABASE_PM_COMPANY_CANDIDATES_TABLE", "pm_pipeline.company_candidates"
)
PM_CONTACT_CANDIDATES_TABLE = os.getenv(
    "SUPABASE_PM_CONTACT_CANDIDATES_TABLE", "pm_pipeline.contact_candidates"
)
PM_COMPANY_GAP_VIEW = os.getenv(
    "SUPABASE_PM_COMPANY_GAP_VIEW", "pm_pipeline.v_company_gap"
)
PM_RUN_RESUME_PLAN_VIEW = os.getenv(
    "SUPABASE_PM_RUN_RESUME_PLAN_VIEW", "pm_pipeline.v_run_resume_plan"
)
PM_COMPANY_RESEARCH_QUEUE_VIEW = os.getenv(
    "SUPABASE_PM_COMPANY_RESEARCH_QUEUE_VIEW", "pm_pipeline.v_company_research_queue"
)
PM_CONTACT_GAP_VIEW = os.getenv(
    "SUPABASE_PM_CONTACT_GAP_VIEW", "pm_pipeline.v_contact_gap"
)
PM_CONTACT_GAP_PER_COMPANY_VIEW = os.getenv(
    "SUPABASE_PM_CONTACT_GAP_PER_COMPANY_VIEW", "pm_pipeline.v_contact_gap_per_company"
)
PM_STAGING_COMPANIES_TABLE = os.getenv(
    "SUPABASE_PM_STAGING_COMPANIES_TABLE", "pm_pipeline.staging_companies"
)
FOCUS_ACCOUNT_METRICS_VIEW = os.getenv(
    "SUPABASE_FOCUS_ACCOUNT_METRICS_VIEW", "mv_focus_account_metrics"
)

# Public table with imported PMS subdomains (Buildium, AppFolio, etc.)
PMS_SUBDOMAINS_TABLE = os.getenv("SUPABASE_PMS_SUBDOMAINS_TABLE", "public.pms_subdomains")
PM_BLOCKED_DOMAINS_VIEW = os.getenv(
    "SUPABASE_PM_BLOCKED_DOMAINS_VIEW", "pm_pipeline.v_blocked_domains"
)
PM_HUBSPOT_SUPPRESSION_TABLE = os.getenv(
    "SUPABASE_PM_HUBSPOT_SUPPRESSION_TABLE", "pm_pipeline.hubspot_recent_contact_suppression"
)


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    url = f"{_base_url()}/{path.lstrip('/')}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    r = requests.get(url, headers=_headers(), params=params or {}, timeout=timeout)
    if not r.ok:
        raise SupabaseError(f"GET {url} failed: {r.status_code} {r.text}")
    try:
        return r.json()
    except Exception as e:
        raise SupabaseError(f"Invalid JSON from Supabase: {r.text[:500]}") from e


def _get_pm(path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """GET helper targeting pm_pipeline objects via direct Postgres.

    ``path`` is expected to be a fully-qualified table or view name.
    """

    # Direct Postgres access since pm_pipeline is not exposed via REST
    table = path
    clauses: List[str] = []
    values: List[Any] = []
    if params:
        for key, val in params.items():
            if key == "select":
                continue
            if key == "limit":
                continue
            if key == "order":
                continue
            # Expect "eq.<value>" style
            if isinstance(val, str) and val.startswith("eq."):
                clauses.append(f"{key} = %s")
                values.append(val[3:])
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_sql = ""
    if params and "order" in params:
        order_val = str(params["order"])
        col = order_val
        direction = ""
        if "." in order_val:
            col, dir_part = order_val.split(".", 1)
            direction = " ASC" if dir_part.lower().startswith("asc") else " DESC"
        order_sql = f" ORDER BY {col}{direction}"
    limit_sql = ""
    if params and "limit" in params:
        limit_sql = f" LIMIT {int(params['limit'])}"
    sql = f"SELECT * FROM {table} {where_sql}{order_sql}{limit_sql}"
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, values)
        return list(cur.fetchall())


def find_company(
    domain: Optional[str] = None,
    company_name: Optional[str] = None,
    *,
    pms: Optional[str] = None,
    city: Optional[str] = None,
    fully_enriched: Optional[bool] = None,
    limit: Optional[int] = None,
) -> Any:
    """Lookup company records by domain/name or by PMS + geo.

    Behaviour:
      * If ``pms`` or ``city`` is provided, returns a list of rows (for orchestrator usage).
      * Otherwise returns the most recently updated matching record (legacy behaviour).
    """

    # Orchestrator path – list of records filtered by PMS / geo
    if pms or city:
        params: Dict[str, Any] = {"select": "*", "order": "updated_at.desc.nullslast"}
        if pms:
            params["pms"] = f"eq.{pms}"
        if city:
            city_val = city.strip()
            city_filters = [f"{col}.ilike.*{city_val}*" for col in CITY_COLUMNS]
            if city_filters:
                params["or"] = f"({','.join(city_filters)})"
        if fully_enriched:
            required = [
                "domain.not.is.null",
                "pms_confidence.not.is.null",
            ]
            if CITY_COLUMNS:
                required.append(f"{CITY_COLUMNS[0]}.not.is.null")
            params["and"] = f"({','.join(required)})"
        if limit:
            params["limit"] = str(limit)
        rows = _get(COMPANY_TABLE, params)
        normalized: List[Dict[str, Any]] = []
        for row in rows or []:
            data = dict(row)
            if not data.get("target_city"):
                for col in CITY_COLUMNS:
                    if data.get(col):
                        data["target_city"] = data[col]
                        break
            # map unit fields to orchestrator expectations
            if "unit_count" in data and "units" not in data:
                data["units"] = data.get("unit_count")
            if "unit_count_source" in data and "units_source" not in data:
                data["units_source"] = data.get("unit_count_source")
            if "unit_count_confidence" in data and "units_confidence" not in data:
                data["units_confidence"] = data.get("unit_count_confidence")
            # derive PMS source URL from data_sources json when present
            if "pms_source_url" not in data:
                sources = data.get("data_sources") or {}
                if isinstance(sources, dict):
                    data["pms_source_url"] = sources.get("pms") or sources.get("pms_url")
            normalized.append(data)
        return normalized

    # Legacy single-record lookup by domain / company name
    params: Dict[str, Any] = {"select": "*"}
    if domain:
        params["domain"] = f"eq.{domain}"
    elif company_name:
        params["company_name"] = f"ilike.*{company_name}*"
    else:
        return None
    params["order"] = "updated_at.desc.nullslast"
    rows = _get(COMPANY_TABLE, params)
    return rows[0] if rows else None


def _post(path: str, json_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = f"{_base_url()}/{path.lstrip('/')}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation"
    r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
    if not r.ok:
        raise SupabaseError(f"POST {url} failed: {r.status_code} {r.text}")
    try:
        data = r.json()
        return data if isinstance(data, list) else [data]
    except Exception as e:
        raise SupabaseError(f"Invalid JSON from Supabase: {r.text[:500]}") from e


def _post_pm(path: str, json_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """INSERT helper targeting pm_pipeline tables via direct Postgres."""

    table = path
    if not isinstance(json_body, list):
        rows = [json_body]
    else:
        rows = json_body
    if not rows:
        return []
    cols = sorted(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) RETURNING *"
    results: List[Dict[str, Any]] = []
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        for row in rows:
            values: List[Any] = []
            for c in cols:
                v = row.get(c)
                if isinstance(v, (dict, list)):
                    v = Json(v)
                values.append(v)
            cur.execute(sql, values)
            results.append(cur.fetchone())
    return results


def _patch(path: str, match_params: Dict[str, Any], json_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = f"{_base_url()}/{path.lstrip('/')}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation"
    r = requests.patch(url, headers=headers, params=match_params, json=json_body, timeout=timeout)
    if not r.ok:
        raise SupabaseError(f"PATCH {url} failed: {r.status_code} {r.text[:400]}")
    try:
        data = r.json()
        return data if isinstance(data, list) else [data]
    except Exception as e:
        raise SupabaseError(f"Invalid JSON from Supabase: {r.text[:500]}") from e


def _patch_pm(path: str, match_params: Dict[str, Any], json_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """UPDATE helper targeting pm_pipeline tables via direct Postgres."""

    table = path
    if not match_params:
        return []
    set_cols = sorted(json_body.keys())
    set_sql = ", ".join(f"{c} = %s" for c in set_cols)
    where_clauses: List[str] = []
    where_values: List[Any] = []
    for key, val in match_params.items():
        if isinstance(val, str) and val.startswith("eq."):
            where_clauses.append(f"{key} = %s")
            where_values.append(val[3:])
        else:
            where_clauses.append(f"{key} = %s")
            where_values.append(val)
    where_sql = " AND ".join(where_clauses)
    sql = f"UPDATE {table} SET {set_sql} WHERE {where_sql} RETURNING *"
    values: List[Any] = []
    for c in set_cols:
        v = json_body[c]
        if isinstance(v, (dict, list)):
            v = Json(v)
        values.append(v)
    values.extend(where_values)
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, values)
        return list(cur.fetchall())


def insert_enrichment_request(
    request: Dict[str, Any], status: str = "queued"
) -> Dict[str, Any]:
    """Insert a new enrichment request row and return the inserted record.

    Supports two shapes, trying a view-friendly shape first, then a raw-table shape:
      1) View shape: {batch_id, request_text, notify_email, parameters, source, request_status}
      2) Table shape: {request (jsonb), request_time, request_status}
    """
    path = ENRICHMENT_REQUESTS_PATH or ENRICHMENT_REQUESTS_TABLE

    # Variant 1: likely view schema with explicit columns
    natural_text = None
    try:
        val = request.get("natural_request") if isinstance(request, dict) else None
        if isinstance(val, str) and val.strip():
            natural_text = val.strip()
    except Exception:
        natural_text = None
    v1: Dict[str, Any] = {
        "batch_id": (request.get("batch_id") if isinstance(request, dict) else None),
        "request_text": natural_text or json.dumps(request, ensure_ascii=False),
        "notify_email": (request.get("notify_email") if isinstance(request, dict) else None),
        "parameters": (request.get("parameters") if isinstance(request, dict) else None),
        "source": (request.get("source") if isinstance(request, dict) else None) or "lead_list_generator",
        "request_status": status,
    }
    # Drop None values to satisfy strict views
    v1 = {k: v for k, v in v1.items() if v is not None}

    # Variant 2: raw table with JSON request
    v2: Dict[str, Any] = {
        "request": request,
        "request_time": datetime.now(timezone.utc).isoformat(),
        "request_status": status,
    }

    first_error = None
    try:
        inserted = _post(path, v1)
        return inserted[0] if inserted else {}
    except Exception as e:
        first_error = e
        # Fall through to variant 2
    inserted = _post(path, v2)
    return inserted[0] if inserted else {}


def query_potential_fit_companies(
    *,
    pms_include: Optional[List[str]] = None,
    pms_exclude: Optional[List[str]] = None,
    exclude_major_pms: bool = False,
    locations: Optional[List[str]] = None,
    units_min: Optional[int] = None,
    units_max: Optional[int] = None,
    icp_min_score: Optional[int] = None,
    meets_basic_icp: Optional[bool] = True,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Search the research database for likely-fit companies.

    Server-side filters applied where possible; some exclusions (like major PMS) may
    be applied client-side for portability across PostgREST versions.

    Args:
        pms_include: list of PMS names to include (OR semantics)
        pms_exclude: list of PMS names to exclude
        exclude_major_pms: if True, exclude common PMS in MAJOR_PMS
        locations: list of free-text locations matched against `headquarters_location`
        units_min/units_max: numeric unit_count range
        icp_min_score: filter by minimum icp_score
        meets_basic_icp: if True/False, filter on meets_basic_icp; if None, ignore
        limit: max rows to return

    Returns: list of company dicts (normalized with `units`, `target_city` fields when possible)
    """

    params: Dict[str, Any] = {"select": "*", "order": "icp_score.desc.nullslast"}

    # Numeric range filters
    if units_min is not None:
        params["unit_count"] = f"gte.{int(units_min)}"
    if units_max is not None:
        # PostgREST supports multiple filters on the same column via `and` param
        params.setdefault("and", "(")
        if params["and"] == "(":
            params["and"] += f"unit_count.lte.{int(units_max)}"
        else:
            params["and"] += f",unit_count.lte.{int(units_max)}"
    if icp_min_score is not None:
        # Append to `and` group
        params.setdefault("and", "(")
        cond = f"icp_score.gte.{int(icp_min_score)}"
        params["and"] = params["and"] + (cond if params["and"].endswith("(") else f",{cond}")

    # Boolean ICP fit
    if meets_basic_icp is True:
        params.setdefault("and", "(")
        cond = "meets_basic_icp.is.true"
        params["and"] = params["and"] + (cond if params["and"].endswith("(") else f",{cond}")
    elif meets_basic_icp is False:
        params.setdefault("and", "(")
        cond = "meets_basic_icp.is.false"
        params["and"] = params["and"] + (cond if params["and"].endswith("(") else f",{cond}")

    # Close `and` group if used
    if params.get("and", "") and not params["and"].endswith(")"):
        params["and"] = params["and"] + ")"

    # PMS include OR filters
    include_vals = [p for p in (pms_include or []) if isinstance(p, str) and p.strip()]
    if include_vals:
        ors = [f"pms.eq.{val}" for val in include_vals]
        params["or"] = f"({','.join(ors)})"

    # Location OR filters (free-text on headquarters_location)
    loc_vals = [l for l in (locations or []) if isinstance(l, str) and l.strip()]
    if loc_vals:
        loc_ors = [f"headquarters_location.ilike.*{val}*" for val in loc_vals]
        if "or" in params:
            # Combine with existing OR via an additional grouping using PostgREST `or` again is not supported directly
            # Instead, bias towards location filter by appending to `and` group
            params.setdefault("and", "(")
            group = f"or.({','.join(loc_ors)})"
            params["and"] = params["and"] + (group if params["and"].endswith("(") else f",{group}")
        else:
            params["or"] = f"({','.join(loc_ors)})"

    params["limit"] = str(int(limit))

    rows = _get(COMPANY_TABLE, params)

    # Client-side exclusions
    excluded = set(x.strip().lower() for x in (pms_exclude or []) if isinstance(x, str))
    if exclude_major_pms:
        excluded.update(s.lower() for s in MAJOR_PMS)

    normalized: List[Dict[str, Any]] = []
    for row in rows or []:
        data = dict(row)
        # Map unit fields
        if "unit_count" in data and "units" not in data:
            data["units"] = data.get("unit_count")
        # Derive target_city
        if not data.get("target_city"):
            for col in CITY_COLUMNS:
                if data.get(col):
                    data["target_city"] = data[col]
                    break
        # Optional PMS exclusions
        pms_val = (data.get("pms") or "").strip().lower()
        if pms_val and pms_val in excluded:
            continue
        normalized.append(data)

    return normalized


def find_contact(
    email: Optional[str] = None,
    name_like: Optional[str] = None,
    company_name: Optional[str] = None,
    strict: bool = False,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    *,
    company_id: Optional[str] = None,
    fully_enriched: Optional[bool] = None,
    limit: Optional[int] = None,
) -> Any:
    """Lookup contact records by company or by legacy email/name search."""

    if company_id:
        params: Dict[str, Any] = {
            "select": "*",
            "company_id": f"eq.{company_id}",
            "order": "updated_at.desc.nullslast",
        }
        if fully_enriched:
            required = [
                "full_name.not.is.null",
                f"{CONTACT_TITLE_COLUMN}.not.is.null",
                "email.not.is.null",
            ]
            params["and"] = f"({','.join(required)})"
        params["limit"] = str(limit or 10)
        rows = _get(CONTACT_TABLE, params) or []
        for row in rows:
            row.setdefault("anecdotes_personal", [])
            row.setdefault("anecdotes_professional", [])
            if not row.get("title"):
                if row.get(CONTACT_TITLE_COLUMN):
                    row["title"] = row.get(CONTACT_TITLE_COLUMN)
                elif row.get("job_title_normalized"):
                    row["title"] = row.get("job_title_normalized")
        return rows

    # Legacy lookup path (returns single record)
    params: Dict[str, Any] = {"select": "*"}
    if email:
        params["email"] = f"eq.{email}"
    else:
        if strict and company_name and (name_like or (first_name and last_name)):
            if first_name and last_name:
                params["first_name"] = f"ilike.*{first_name}*"
                params["last_name"] = f"ilike.*{last_name}*"
            elif name_like:
                params["full_name"] = f"ilike.*{name_like}*"
            params["company_name"] = f"ilike.*{company_name}*"
        else:
            ors = []
            if name_like:
                ors.append(f"full_name.ilike.*{name_like}*")
            if company_name:
                ors.append(f"company_name.ilike.*{company_name}*")
            if not ors:
                return None
            params["or"] = f"({','.join(ors)})"
    params["order"] = "updated_at.desc.nullslast"
    rows = _get(CONTACT_TABLE, params)
    return rows[0] if rows else None


def upsert_company(company: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert a company row and return the stored representation."""

    url = f"{_base_url()}/{COMPANY_TABLE}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    params = {"on_conflict": COMPANY_CONFLICT_TARGET}
    response = requests.post(
        url,
        headers=headers,
        params=params,
        json=[company],
        timeout=timeout,
    )
    if not response.ok:
        raise SupabaseError(
            f"POST {url} failed: {response.status_code} {response.text[:400]}"
        )
    data = response.json()
    if not data:
        raise SupabaseError("Supabase returned no rows for upsert_company")
    return data[0]


def upsert_contact(contact: Dict[str, Any], *, company_id: str) -> Dict[str, Any]:
    """Upsert a contact row and return the stored representation."""

    body = dict(contact)
    body.setdefault("company_id", company_id)
    url = f"{_base_url()}/{CONTACT_TABLE}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    params = {"on_conflict": CONTACT_CONFLICT_TARGET}
    response = requests.post(
        url,
        headers=headers,
        params=params,
        json=[body],
        timeout=timeout,
    )
    if not response.ok:
        raise SupabaseError(
            f"POST {url} failed: {response.status_code} {response.text[:400]}"
        )
    data = response.json()
    if not data:
        raise SupabaseError("Supabase returned no rows for upsert_contact")
    return data[0]


def get_email_pattern(domain: str) -> Optional[Dict[str, Any]]:
    """Fetch cached email pattern for a domain. Returns dict with keys: pattern, evidence_count."""
    if not domain:
        return None
    params = {"select": "*", "domain": f"eq.{domain.lower().strip()}", "limit": "1"}
    try:
        rows = _get(EMAIL_PATTERNS_TABLE, params) or []
    except Exception:
        return None
    return rows[0] if rows else None


def upsert_email_pattern(domain: str, pattern: str, evidence_count: int) -> None:
    if not domain or not pattern:
        return
    url = f"{_base_url()}/{EMAIL_PATTERNS_TABLE}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    params = {"on_conflict": "domain"}
    payload = [{"domain": domain.lower().strip(), "pattern": pattern, "evidence_count": evidence_count}]
    try:
        r = requests.post(url, headers=headers, params=params, json=payload, timeout=timeout)
        if not r.ok:
            # Silently ignore if table/policy missing
            return
    except Exception:
        return


def create_contact_enrichment_task(*, batch_id: str, contact_seed: Dict[str, Any], status: str = "queued") -> Dict[str, Any]:
    """Insert a new contact enrichment task. Returns the inserted row or raises on error."""
    url = f"{_base_url()}/{CONTACT_ENRICH_TASKS_TABLE}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation"
    row = {
        "batch_id": batch_id,
        "status": status,
        "contact_seed": contact_seed,
    }
    r = requests.post(url, headers=headers, json=[row], timeout=timeout)
    if not r.ok:
        raise SupabaseError(f"POST {url} failed: {r.status_code} {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


def fetch_contact_enrichment_tasks(*, batch_id: str, status: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"select": "*", "batch_id": f"eq.{batch_id}", "order": "updated_at.desc.nullslast"}
    if status:
        params["status"] = f"eq.{status}"
    if limit:
        params["limit"] = str(limit)
    try:
        rows = _get(CONTACT_ENRICH_TASKS_TABLE, params)
        return rows or []
    except Exception:
        return []


# ---------------------------
# Run batch metadata API
# ---------------------------

def create_run_batch(*, batch_id: str, requester_email: str, request_text: str, constraints: Dict[str, Any], requested_count: int, status: str = "pending") -> Dict[str, Any]:
    """Create a run batch metadata row."""
    url = f"{_base_url()}/{RUN_BATCHES_TABLE}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation"
    row = {
        "batch_id": batch_id,
        "request_text": request_text,
        "requester_email": requester_email,
        "constraints": constraints,
        "requested_count": requested_count,
        "status": status,
    }
    r = requests.post(url, headers=headers, json=[row], timeout=timeout)
    if not r.ok:
        # Non-fatal: just skip metadata if table/policy missing
        return {}
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


def get_run_batch(*, batch_id: str) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {"select": "*", "batch_id": f"eq.{batch_id}", "limit": "1"}
    try:
        rows = _get(RUN_BATCHES_TABLE, params)
        return rows[0] if rows else None
    except Exception:
        return None


def update_run_batch(*, batch_id: str, **fields: Any) -> Dict[str, Any]:
    if not fields:
        return {}
    try:
        rows = _patch(RUN_BATCHES_TABLE, {"batch_id": f"eq.{batch_id}"}, fields)
        return rows[0] if rows else {}
    except Exception:
        return {}


def update_contact_enrichment_task_email(*, task_id: str, email: str, email_deliverable: Optional[bool] = None, verified_at: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"email": email}
    if email_deliverable is not None:
        body["email_deliverable"] = bool(email_deliverable)
    if verified_at:
        body["email_verified_at"] = verified_at
    if status:
        body["status"] = status
    rows = _patch(CONTACT_ENRICH_TASKS_TABLE, {"task_id": f"eq.{task_id}"}, body)
    return rows[0] if rows else {}


def update_contact_enrichment_task_anecdotes(*, task_id: str, personal_anecdotes: Optional[List[Dict[str, Any]]] = None, professional_anecdotes: Optional[List[Dict[str, Any]]] = None, sources: Optional[List[str]] = None, agent_summary: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if personal_anecdotes is not None:
        body["personal_anecdotes"] = personal_anecdotes
    if professional_anecdotes is not None:
        body["professional_anecdotes"] = professional_anecdotes
    if sources is not None:
        body["sources"] = sources
    if agent_summary is not None:
        body["agent_summary"] = agent_summary
    if status:
        body["status"] = status
    rows = _patch(CONTACT_ENRICH_TASKS_TABLE, {"task_id": f"eq.{task_id}"}, body)
    return rows[0] if rows else {}


def bulk_upsert_companies(companies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not companies:
        return []
    url = f"{_base_url()}/{COMPANY_TABLE}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    headers = _headers()
    headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    params = {"on_conflict": COMPANY_CONFLICT_TARGET}
    cleaned: List[Dict[str, Any]] = []
    for item in companies:
        data = dict(item)
        if "employees" in data and "employee_count" not in data:
            data["employee_count"] = data.pop("employees")
        cleaned.append(data)

    response = requests.post(
        url,
        headers=headers,
        params=params,
        json=cleaned,
        timeout=timeout,
    )
    if not response.ok:
        raise SupabaseError(
            f"POST {url} failed: {response.status_code} {response.text[:400]}"
        )
    data = response.json()
    if not data:
        raise SupabaseError("Supabase returned no rows for bulk_upsert_companies")
    return data


# ---------------------------
# pm_pipeline async lead list helpers
# ---------------------------

def create_pm_run(
    *,
    criteria: Dict[str, Any],
    target_quantity: int,
    contacts_min: int = 1,
    contacts_max: int = 3,
    target_distribution: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new pm_pipeline.runs row."""

    row: Dict[str, Any] = {
        "criteria": criteria,
        "target_quantity": int(target_quantity),
        "contacts_min": int(contacts_min),
        "contacts_max": int(contacts_max),
        "status": "active",
    }
    if target_distribution is not None:
        row["target_distribution"] = target_distribution
    if notes is not None:
        row["notes"] = notes
    if created_by is not None:
        row["created_by"] = created_by

    rows = _post_pm(PM_RUNS_TABLE, [row])
    return rows[0] if rows else {}


def fetch_active_pm_runs(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch active pm_pipeline runs that need processing."""

    params: Dict[str, Any] = {
        "select": "*",
        "status": "eq.active",
        "order": "created_at.asc",
    }
    if limit:
        params["limit"] = str(int(limit))
    try:
        return _get_pm(PM_RUNS_TABLE, params) or []
    except SupabaseError:
        return []


def insert_company_candidate(
    *,
    run_id: str,
    name: str,
    website: str,
    domain: str,
    state: str,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """Insert a single company candidate row for a run.

    Relies on pm_pipeline.company_candidates constraints to prevent duplicates.
    Returns the stored row, or None on duplicate/conflict.
    """

    # Normalize state to 2-letter code
    state_normalized = state.upper().strip()
    if len(state_normalized) > 2:
        # Map common full names to abbreviations
        state_map = {
            "TEXAS": "TX", "CALIFORNIA": "CA", "FLORIDA": "FL", "NEW YORK": "NY",
            "PENNSYLVANIA": "PA", "ILLINOIS": "IL", "OHIO": "OH", "GEORGIA": "GA",
            "NORTH CAROLINA": "NC", "MICHIGAN": "MI", "NEW JERSEY": "NJ",
            "VIRGINIA": "VA", "WASHINGTON": "WA", "ARIZONA": "AZ", "MASSACHUSETTS": "MA",
            "TENNESSEE": "TN", "INDIANA": "IN", "MISSOURI": "MO", "MARYLAND": "MD",
            "WISCONSIN": "WI", "COLORADO": "CO", "MINNESOTA": "MN", "SOUTH CAROLINA": "SC",
            "ALABAMA": "AL", "LOUISIANA": "LA", "KENTUCKY": "KY", "OREGON": "OR",
            "OKLAHOMA": "OK", "CONNECTICUT": "CT", "UTAH": "UT", "IOWA": "IA",
            "NEVADA": "NV", "ARKANSAS": "AR", "MISSISSIPPI": "MS", "KANSAS": "KS",
            "NEW MEXICO": "NM", "NEBRASKA": "NE", "WEST VIRGINIA": "WV", "IDAHO": "ID",
            "HAWAII": "HI", "NEW HAMPSHIRE": "NH", "MAINE": "ME", "MONTANA": "MT",
            "RHODE ISLAND": "RI", "DELAWARE": "DE", "SOUTH DAKOTA": "SD",
            "NORTH DAKOTA": "ND", "ALASKA": "AK", "VERMONT": "VT", "WYOMING": "WY"
        }
        state_normalized = state_map.get(state_normalized, state_normalized[:2])

    row: Dict[str, Any] = {
        "run_id": run_id,
        "name": name,
        "website": website,
        "domain": domain.lower().strip(),
        "state": state_normalized,
        "idem_key": extra.get("idem_key") or domain.lower().strip(),
    }
    # Optional fields
    for key in (
        "description",
        "discovery_source",
        "pms_detected",
        "units_estimate",
        "company_type",
        "evidence",
        "status",
        "meets_all_requirements",
    ):
        if key in extra and extra[key] is not None:
            row[key] = extra[key]

    try:
        rows = _post_pm(PM_COMPANY_CANDIDATES_TABLE, [row])
    except Exception as e:
        msg = str(e)
        # Ignore duplicate key violations; treat as no-op
        if (
            "duplicate key" in msg
            or "uq_cc_run_domain" in msg
            or "uq_cc_idem" in msg
            or "uq_company_candidates_domain" in msg
        ):
            return None
        print(f"[insert_company_candidate] error: {e}")
        raise
    return rows[0] if rows else None


def insert_contact_candidate(
    *,
    run_id: str,
    company_id: str,
    full_name: str,
    title: Optional[str] = None,
    email: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """Insert a single contact candidate row for a run/company.

    Uses email / LinkedIn uniqueness constraints to avoid duplicates.
    Returns the stored row, or None on duplicate/conflict.
    """

    row: Dict[str, Any] = {
        "run_id": run_id,
        "company_id": company_id,
        "full_name": full_name,
    }
    if title:
        row["title"] = title
    if email:
        row["email"] = email
    if linkedin_url:
        row["linkedin_url"] = linkedin_url
    if "idem_key" in extra and extra["idem_key"]:
        row["idem_key"] = extra["idem_key"]

    for key in (
        "department",
        "seniority",
        "quality_score",
        "signals",
        "evidence",
        "status",
        "meets_all_requirements",
    ):
        if key in extra and extra[key] is not None:
            row[key] = extra[key]

    try:
        rows = _post_pm(PM_CONTACT_CANDIDATES_TABLE, [row])
    except Exception as e:
        msg = str(e)
        # Treat uniqueness violations on email, LinkedIn, or idem_key as benign no-ops.
        if (
            "duplicate key" in msg
            or "uq_ct_email_per_company" in msg
            or "uq_ct_linkedin_per_company" in msg
            or "uq_ct_idem" in msg
        ):
            return None
        print(f"[insert_contact_candidate] error: {e}")
        raise
    return rows[0] if rows else None


def update_pm_run_status(*, run_id: str, status: str, error: Optional[str] = None) -> Dict[str, Any]:
    """Update the status (and optional notes) of a pm_pipeline run."""

    body: Dict[str, Any] = {"status": status}
    if error:
        body["notes"] = error
    rows = _patch_pm(PM_RUNS_TABLE, {"id": f"eq.{run_id}"}, body)
    return rows[0] if rows else {}


def get_pm_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single pm_pipeline.runs row by id."""

    if not run_id:
        return None
    params: Dict[str, Any] = {
        "select": "*",
        "id": f"eq.{run_id}",
        "limit": "1",
    }
    try:
        rows = _get_pm(PM_RUNS_TABLE, params)
        return rows[0] if rows else None
    except SupabaseError:
        return None


def get_pm_company_gap(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the gap metrics for a run from v_company_gap."""

    if not run_id:
        return None
    params: Dict[str, Any] = {
        "select": "*",
        "run_id": f"eq.{run_id}",
        "limit": "1",
    }
    try:
        rows = _get_pm(PM_COMPANY_GAP_VIEW, params)
        return rows[0] if rows else None
    except SupabaseError:
        return None


def get_run_resume_plan(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch stage + gap information for a run from v_run_resume_plan."""

    if not run_id:
        return None
    params: Dict[str, Any] = {
        "select": "*",
        "run_id": f"eq.{run_id}",
        "limit": "1",
    }
    try:
        rows = _get_pm(PM_RUN_RESUME_PLAN_VIEW, params)
        return rows[0] if rows else None
    except SupabaseError:
        return None


def set_run_stage(*, run_id: str, stage: str, status: Optional[str] = None) -> Dict[str, Any]:
    """Update the stage (and optionally status) of a pm_pipeline run."""

    body: Dict[str, Any] = {"stage": stage}
    if status:
        body["status"] = status
    rows = _patch_pm(PM_RUNS_TABLE, {"id": f"eq.{run_id}"}, body)
    return rows[0] if rows else {}


def has_company_research_queue(run_id: str) -> bool:
    """Return True when there are still companies awaiting research for a run."""

    if not run_id:
        return False
    try:
        rows = _get_pm(
            PM_COMPANY_RESEARCH_QUEUE_VIEW,
            {"run_id": f"eq.{run_id}", "limit": "1"},
        )
        return bool(rows)
    except SupabaseError:
        return True


def get_contact_gap_summary(run_id: str) -> Optional[Dict[str, Any]]:
    """Return aggregate contact gap for a run from v_contact_gap."""

    if not run_id:
        return None
    try:
        rows = _get_pm(
            PM_CONTACT_GAP_VIEW,
            {"run_id": f"eq.{run_id}", "limit": "1"},
        )
        return rows[0] if rows else None
    except SupabaseError:
        return None


def get_contact_gap_for_top_companies(
    run_id: str,
    target_quantity: int,
) -> Optional[Dict[str, Any]]:
    """Return contact gap limited to the top N companies for a run.

    This prevents oversampled companies from inflating the contact gap.
    """
    if not run_id or target_quantity <= 0:
        return None
    sql = """
    SELECT COALESCE(v.contacts_min_gap, 0) AS contacts_min_gap
    FROM pm_pipeline.v_contact_gap_per_company v
    JOIN pm_pipeline.company_candidates c ON c.id = v.company_id
    WHERE c.run_id = %s
    ORDER BY c.created_at ASC, c.id ASC
    LIMIT %s
    """
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, (run_id, target_quantity))
        rows = cur.fetchall()
    if not rows:
        return None
    gaps = [int(r.get("contacts_min_gap") or 0) for r in rows]
    ready = sum(1 for g in gaps if g <= 0)
    gap_total = sum(max(0, g) for g in gaps)
    return {"ready_companies": ready, "gap_total": gap_total}


def get_contact_gap_for_company(run_id: str, company_id: str) -> Optional[Dict[str, Any]]:
    """Return contact gap info for a single company."""

    if not run_id or not company_id:
        return None
    try:
        rows = _get_pm(
            PM_CONTACT_GAP_PER_COMPANY_VIEW,
            {
                "run_id": f"eq.{run_id}",
                "company_id": f"eq.{company_id}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None
    except SupabaseError:
        return None


def claim_company_for_research(
    worker_id: str,
    lease_seconds: int = 300,
) -> Optional[Dict[str, Any]]:
    """Lease a company candidate that still needs research for the worker.

    When RUN_FILTER_ID is set in the environment, only companies belonging
    to that pm_pipeline.runs.id will be considered. This is primarily used
    for targeted testing and does not affect normal multi-run operation.
    """

    import os

    interval_literal = f"{int(lease_seconds)} seconds"
    run_filter_id = os.getenv("RUN_FILTER_ID", "").strip()
    run_filter_clause = ""
    # Parameter order must match placeholder order in SQL:
    # 1) optional r.id = %s in CTE
    # 2) worker_id and lease interval in UPDATE.
    params: list[Any] = []
    if run_filter_id:
        run_filter_clause = " AND r.id = %s"
        params.append(run_filter_id)
    params.extend([worker_id, interval_literal])

    sql = f"""
WITH candidate AS (
    SELECT c.id
    FROM pm_pipeline.company_candidates c
    JOIN pm_pipeline.runs r ON r.id = c.run_id
    LEFT JOIN pm_pipeline.company_research cr ON cr.company_id = c.id
    WHERE r.stage = 'company_research'
      AND c.status = ANY (ARRAY['validated','promoted'])
      AND (c.worker_id IS NULL OR c.worker_lease_until < now())
      AND cr.id IS NULL
      {run_filter_clause}
    ORDER BY c.created_at
    LIMIT 1
)
UPDATE pm_pipeline.company_candidates AS c
SET worker_id = %s,
    worker_lease_until = now() + %s::interval
FROM candidate
WHERE c.id = candidate.id
RETURNING c.id, c.run_id, c.name, c.domain, c.website, c.state;
"""
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row


def release_company_lease(company_id: str) -> None:
    """Clear worker lease info for a company candidate."""

    if not company_id:
        return
    sql = """
UPDATE pm_pipeline.company_candidates
SET worker_id = NULL,
    worker_lease_until = NULL
WHERE id = %s
"""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (company_id,))


def claim_company_for_contacts(
    worker_id: str,
    lease_seconds: int = 300,
) -> Optional[Dict[str, Any]]:
    """Lease a company that still needs contacts for the worker.

    When RUN_FILTER_ID is set in the environment, only companies belonging
    to that pm_pipeline.runs.id will be considered. This is primarily used
    for targeted testing and does not affect normal multi-run operation.
    """

    import os

    interval_literal = f"{int(lease_seconds)} seconds"
    run_filter_id = os.getenv("RUN_FILTER_ID", "").strip()
    run_filter_clause = ""
    # Parameter order must match placeholder order in SQL:
    # 1) optional r.id = %s in CTE
    # 2) worker_id and lease interval in UPDATE.
    params: list[Any] = []
    if run_filter_id:
        run_filter_clause = " AND r.id = %s"
        params.append(run_filter_id)
    params.extend([worker_id, interval_literal])

    sql = f"""
WITH ranked AS (
    SELECT
        c.id,
        r.id AS run_id,
        r.target_quantity,
        v.contacts_min_gap,
        ROW_NUMBER() OVER (
            PARTITION BY r.id
            ORDER BY c.created_at ASC, c.id ASC
        ) AS rn
    FROM pm_pipeline.v_contact_gap_per_company v
    JOIN pm_pipeline.company_candidates c ON c.id = v.company_id
    JOIN pm_pipeline.runs r ON r.id = c.run_id
    WHERE r.stage = 'contact_discovery'
      AND v.contacts_min_gap > 0
      AND (c.worker_id IS NULL OR c.worker_lease_until < now())
      {run_filter_clause}
),
candidate AS (
    SELECT id
    FROM ranked
    WHERE (target_quantity IS NULL OR target_quantity <= 0) OR rn <= target_quantity
    ORDER BY contacts_min_gap DESC
    LIMIT 1
)
UPDATE pm_pipeline.company_candidates AS c
SET worker_id = %s,
    worker_lease_until = now() + %s::interval
FROM candidate
WHERE c.id = candidate.id
RETURNING c.id, c.run_id, c.name, c.domain, c.website, c.state;
"""
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row


def insert_company_research(
    *,
    run_id: str,
    company_id: str,
    facts: Dict[str, Any],
    signals: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
    status: str = "complete",
) -> Dict[str, Any]:
    """Upsert company research facts for a run/company pair."""

    sql = """
INSERT INTO pm_pipeline.company_research
    (run_id, company_id, facts, signals, confidence, status)
VALUES
    (%s, %s, %s::jsonb, %s::jsonb, %s, %s)
ON CONFLICT (run_id, company_id)
DO UPDATE SET
    facts = EXCLUDED.facts,
    signals = EXCLUDED.signals,
    confidence = EXCLUDED.confidence,
    status = EXCLUDED.status,
    updated_at = now()
RETURNING *;
"""
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            sql,
            (
                run_id,
                company_id,
                Json(facts or {}),
                Json(signals or {}),
                confidence,
                status,
            ),
        )
        row = cur.fetchone()
        return row or {}
    params: Dict[str, Any] = {
        "select": "*",
        "run_id": f"eq.{run_id}",
        "limit": "1",
    }
    try:
        rows = _get_pm(PM_COMPANY_GAP_VIEW, params)
        return rows[0] if rows else None
    except SupabaseError:
        return None


def get_pms_subdomain_seeds(
    *,
    pms: str,
    city: Optional[str] = None,
    state: Optional[str] = None,
    status: str = "alive",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return PMS subdomain seed rows from public.pms_subdomains.

    This is used to quickly seed candidate pools for hard PMS requirements
    (e.g. Buildium/AppFolio) before running expensive web discovery.
    """

    sql = f"SELECT * FROM {PMS_SUBDOMAINS_TABLE} WHERE pms = %s"
    params: List[Any] = [pms]
    if status:
        sql += " AND status = %s"
        params.append(status)
    if state:
        sql += " AND state = %s"
        params.append(state)
    if city:
        sql += " AND city ILIKE %s"
        params.append(city)
    if limit:
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(int(limit))
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def get_focus_account_metrics(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch focus account metrics from the public.mv_focus_account_metrics view."""

    params: Dict[str, Any] = {
        "select": "owner_email,role,target,current,gap,status",
        "order": "owner_email.asc",
    }
    if limit:
        params["limit"] = str(int(limit))
    return _get(FOCUS_ACCOUNT_METRICS_VIEW, params) or []


def get_blocked_domains() -> List[str]:
    """Return a list of blocked/suppressed domains from v_blocked_domains."""

    try:
        rows = _get_pm(PM_BLOCKED_DOMAINS_VIEW, {"select": "domain"})
    except SupabaseError:
        return []
    domains: List[str] = []
    for row in rows or []:
        d = str(row.get("domain") or "").strip().lower()
        if d:
            domains.append(d)
    return domains


def insert_hubspot_suppression(
    *,
    domain: str,
    company_name: Optional[str] = None,
    hubspot_company_id: Optional[str] = None,
    suppression_reason: str,
    lifecycle_stage: Optional[str] = None,
    last_contact_date: Optional[str] = None,
    last_contact_type: Optional[str] = None,
    engagement_count: Optional[int] = None,
) -> None:
    """Insert a HubSpot suppression record.

    Args:
        domain: Company domain (required)
        company_name: Company name from HubSpot
        hubspot_company_id: HubSpot company ID
        suppression_reason: Reason for suppression ('customer' or 'recently_contacted')
        lifecycle_stage: HubSpot lifecycle stage (e.g., 'customer')
        last_contact_date: ISO date of last contact
        last_contact_type: Type of last contact (EMAIL, CALL, MEETING)
        engagement_count: Number of engagements in the lookback period
    """
    row = {
        "domain": domain.strip().lower(),
        "company_name": company_name,
        "hubspot_company_id": hubspot_company_id,
        "suppression_reason": suppression_reason,
        "lifecycle_stage": lifecycle_stage,
        "last_contact_date": last_contact_date,
        "last_contact_type": last_contact_type,
        "engagement_count": engagement_count,
    }

    try:
        _post_pm(PM_HUBSPOT_SUPPRESSION_TABLE, [row])
    except SupabaseError:
        # Idempotent - ignore duplicates
        pass


def is_domain_in_hubspot_suppression(domain: str) -> bool:
    """Check if a domain is in the HubSpot suppression table."""
    try:
        rows = _get_pm(
            PM_HUBSPOT_SUPPRESSION_TABLE,
            {"domain": f"eq.{domain.strip().lower()}", "select": "domain"}
        )
        return bool(rows)
    except SupabaseError:
        return False


# ---------------------------
# Staging companies (PMS fan-out) helpers
# ---------------------------

def fetch_eligible_staging_companies(
    *,
    search_run_id: str,
    pms_required: str | None = None,
    min_pms_confidence: float | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch eligible staging companies for a given search_run.

    This is used to promote PMS-qualified candidates from
    ``pm_pipeline.staging_companies`` into ``pm_pipeline.company_candidates``.
    """

    if not search_run_id:
        return []

    clauses: list[str] = ["run_id = %s", "status = 'eligible'"]
    values: list[Any] = [search_run_id]

    if pms_required:
        clauses.append("lower(coalesce(pms_detected, '')) = lower(%s)")
        values.append(pms_required)

    if min_pms_confidence is not None:
        clauses.append("coalesce(pms_confidence, 0) >= %s")
        values.append(float(min_pms_confidence))

    where_sql = " AND ".join(clauses)
    limit_sql = f" LIMIT {int(limit)}" if limit and limit > 0 else ""
    sql = f"SELECT * FROM {PM_STAGING_COMPANIES_TABLE} WHERE {where_sql} ORDER BY created_at{limit_sql};"

    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, values)
        return list(cur.fetchall())


def promote_staging_companies_to_run(
    *,
    search_run_id: str,
    pm_run_id: str,
    pms_required: str | None = None,
    min_pms_confidence: float | None = 0.7,
    max_companies: int | None = None,
) -> int:
    """Promote eligible staging companies into pm_pipeline.company_candidates.

    Args:
        search_run_id: id from pm_pipeline.search_runs (linked to staging_companies.run_id)
        pm_run_id: id from pm_pipeline.runs to receive company_candidates
        pms_required: optional PMS filter; when provided, only matching rows are promoted.
        min_pms_confidence: minimum confidence threshold for PMS analyzer.
        max_companies: optional hard cap on how many companies to promote.

    Returns:
        Number of company_candidates rows successfully inserted (non-duplicate).
    """

    if not search_run_id or not pm_run_id:
        return 0

    candidates = fetch_eligible_staging_companies(
        search_run_id=search_run_id,
        pms_required=pms_required,
        min_pms_confidence=min_pms_confidence,
        limit=max_companies,
    )
    if not candidates:
        return 0

    try:
        blocked = set(d.lower().strip() for d in get_blocked_domains())
    except Exception:
        blocked = set()

    inserted = 0
    for row in candidates:
        domain = (row.get("normalized_domain") or row.get("raw_domain") or "").strip().lower()
        if not domain or "." not in domain:
            continue
        if domain in blocked:
            continue

        name = (row.get("normalized_name") or row.get("raw_name") or domain).strip() or domain
        website = (row.get("raw_website") or "").strip() or f"https://{domain}"
        state = (row.get("normalized_state") or row.get("raw_state") or "NA").strip() or "NA"

        extra: dict[str, Any] = {
            "discovery_source": "staging_companies",
            "pms_detected": row.get("pms_detected"),
            "units_estimate": row.get("units_estimate"),
            "evidence": row.get("evidence") or [],
            "status": "validated",
        }

        try:
            res = insert_company_candidate(
                run_id=pm_run_id,
                name=name,
                website=website,
                domain=domain,
                state=state,
                **extra,
            )
            if res:
                inserted += 1
        except Exception:
            # Any hard failure should be surfaced, but duplicates are
            # already treated as benign inside insert_company_candidate.
            raise

    return inserted


# ============================================================================
# Worker Heartbeat / Health Monitoring (Phase 2.2)
# ============================================================================


def upsert_worker_heartbeat(
    worker_id: str,
    worker_type: str,
    status: str = "active",
    current_run_id: Optional[str] = None,
    current_task: Optional[str] = None,
    lease_expires_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Update or insert a worker heartbeat record.

    This function is called periodically by workers to indicate they are alive
    and processing tasks. It updates the last_heartbeat_at timestamp and
    current task information.

    Args:
        worker_id: Unique identifier for the worker (e.g., 'lead-list-uuid')
        worker_type: Type of worker ('lead_list', 'company_research', 'contact_research')
        status: Worker status ('active', 'idle', 'processing', 'stopped')
        current_run_id: ID of run being processed (if any)
        current_task: Description of current task (if any)
        lease_expires_at: When the current lease expires
        metadata: Additional worker metadata (JSON)
    """
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pm_pipeline.upsert_worker_heartbeat(
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    worker_id,
                    worker_type,
                    status,
                    current_run_id,
                    current_task,
                    lease_expires_at,
                    Json(metadata) if metadata else None,
                ),
            )


def stop_worker(worker_id: str) -> None:
    """Mark a worker as stopped (graceful shutdown).

    This should be called by workers during graceful shutdown to indicate
    they are no longer processing tasks.

    Args:
        worker_id: Unique identifier for the worker
    """
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pm_pipeline.stop_worker(%s)",
                (worker_id,),
            )


def get_active_workers() -> List[Dict[str, Any]]:
    """Get list of active workers (heartbeat within last 5 minutes).

    Returns:
        List of worker dicts with heartbeat info
    """
    from decimal import Decimal

    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    worker_id,
                    worker_type,
                    last_heartbeat_at,
                    status,
                    current_run_id,
                    current_task,
                    lease_expires_at,
                    started_at,
                    seconds_since_heartbeat,
                    metadata
                FROM pm_pipeline.v_active_workers
                """
            )
            columns = [desc[0] for desc in cur.description]
            rows = []
            for row in cur.fetchall():
                # Convert Decimal to float for numeric values
                row_dict = {}
                for col, val in zip(columns, row):
                    if isinstance(val, Decimal):
                        row_dict[col] = float(val)
                    else:
                        row_dict[col] = val
                rows.append(row_dict)
            return rows


def get_dead_workers() -> List[Dict[str, Any]]:
    """Get list of dead workers (no heartbeat in last 5 minutes).

    Returns:
        List of worker dicts that appear to have crashed
    """
    from decimal import Decimal

    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    worker_id,
                    worker_type,
                    last_heartbeat_at,
                    status,
                    current_run_id,
                    current_task,
                    lease_expires_at,
                    started_at,
                    seconds_since_heartbeat,
                    metadata
                FROM pm_pipeline.v_dead_workers
                """
            )
            columns = [desc[0] for desc in cur.description]
            rows = []
            for row in cur.fetchall():
                # Convert Decimal to float for numeric values
                row_dict = {}
                for col, val in zip(columns, row):
                    if isinstance(val, Decimal):
                        row_dict[col] = float(val)
                    else:
                        row_dict[col] = val
                rows.append(row_dict)
            return rows


def get_worker_stats() -> List[Dict[str, Any]]:
    """Get worker statistics by type.

    Returns:
        List of stats dicts with counts for each worker type:
        - worker_type
        - total_workers
        - active_workers
        - idle_workers
        - processing_workers
        - dead_workers
    """
    from decimal import Decimal

    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pm_pipeline.get_worker_stats()")
            columns = [desc[0] for desc in cur.description]
            rows = []
            for row in cur.fetchall():
                # Convert Decimal to float for numeric values
                row_dict = {}
                for col, val in zip(columns, row):
                    if isinstance(val, Decimal):
                        row_dict[col] = float(val)
                    else:
                        row_dict[col] = val
                rows.append(row_dict)
            return rows


def cleanup_stale_workers(stale_threshold_minutes: int = 60) -> List[Dict[str, Any]]:
    """Clean up old stopped workers from heartbeat table.

    Args:
        stale_threshold_minutes: Remove workers stopped for this many minutes

    Returns:
        List of removed worker dicts
    """
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM pm_pipeline.cleanup_stale_workers(%s)
                """,
                (stale_threshold_minutes,),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def release_dead_worker_leases() -> int:
    """Release leases held by dead workers.

    This should be called periodically (e.g., every minute) to clean up
    leases from crashed workers so other workers can claim them.

    Returns:
        Number of leases released
    """
    dead_workers = get_dead_workers()
    released = 0

    for worker in dead_workers:
        worker_id = worker.get("worker_id", "")
        current_run_id = worker.get("current_run_id")

        # Release company research leases
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pm_pipeline.company_candidates
                    SET claimed_by = NULL,
                        claim_expires_at = NULL
                    WHERE claimed_by = %s
                    RETURNING id
                    """,
                    (worker_id,),
                )
                released += cur.rowcount

        # Release contact research leases
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pm_pipeline.company_candidates
                    SET contacts_claimed_by = NULL,
                        contacts_claim_expires_at = NULL
                    WHERE contacts_claimed_by = %s
                    RETURNING id
                    """,
                    (worker_id,),
                )
                released += cur.rowcount

    return released


def get_active_and_recent_runs(limit: int = 10) -> List[Dict[str, Any]]:
    """Get active and recently completed runs for UI display.

    Returns runs that are:
    - Currently active (stage != 'done')
    - Completed in the last 48 hours

    Args:
        limit: Maximum number of runs to return (default: 10)

    Returns:
        List of run dicts sorted by created_at desc (newest first)
    """
    # Use psycopg row factory so we always get dict-like rows without relying on
    # adapter-specific cursor classes (e.g. RealDictCursor).
    with _pg_conn() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM pm_pipeline.runs
            WHERE stage != 'done'
               OR (stage = 'done' AND created_at > NOW() - INTERVAL '48 hours')
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return list(cur.fetchall())
