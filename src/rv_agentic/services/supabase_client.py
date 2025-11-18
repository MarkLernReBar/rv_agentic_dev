import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import json


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
