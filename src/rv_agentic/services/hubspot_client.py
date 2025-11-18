import hashlib
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import requests


class HubSpotError(Exception):
    pass


def _base_url() -> str:
    return os.getenv("HUBSPOT_BASE_URL", "https://api.hubspot.com").rstrip("/")


def _headers() -> Dict[str, str]:
    token = os.getenv("HUBSPOT_PRIVATE_APP_TOKEN") or os.getenv("HUBSPOT_TOKEN")
    if not token:
        raise HubSpotError("HUBSPOT_PRIVATE_APP_TOKEN (or HUBSPOT_TOKEN) is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    url = f"{_base_url()}/{path.lstrip('/')}"
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    max_retries = int(os.getenv("HUBSPOT_ENROLL_RETRY_MAX", "3"))
    backoff_base_ms = int(os.getenv("HUBSPOT_ENROLL_BACKOFF_BASE_MS", "500"))
    attempt = 0
    while True:
        attempt += 1
        hdrs = _headers()
        if extra_headers:
            hdrs.update(extra_headers)
        r = requests.request(
            method,
            url,
            headers=hdrs,
            params=params or {},
            json=json_body,
            timeout=timeout,
        )
        if r.ok:
            try:
                return r.json()
            except Exception:
                return {"ok": True}
        status = r.status_code
        text = r.text or ""
        # Retry on 429 and 5xx
        if status == 429 or (500 <= status < 600):
            if attempt <= max_retries:
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except Exception:
                        delay = 0.0
                else:
                    delay = min(15.0, (backoff_base_ms / 1000.0) * (2 ** (attempt - 1)))
                time.sleep(max(0.1, delay))
                continue
        lowered = (text or "").lower()
        if status in (401, 403) or any(
            k in lowered for k in ("scope", "permission", "forbidden")
        ):
            msg = (
                "Missing scopes. Required: crm.objects.sequences.read, crm.objects.sequences.write, "
                "crm.objects.owners.read, sales-email-read, sales-email-write."
            )
            raise HubSpotError(f"{method} {url} failed: {status} {msg}")
        raise HubSpotError(f"{method} {url} failed: {status} {text}")


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _request("GET", path, params=params)


def _post(
    path: str,
    json_body: Dict[str, Any],
    *,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    return _request("POST", path, json_body=json_body, extra_headers=extra_headers)


def _patch(path: str, json_body: Dict[str, Any]) -> Dict[str, Any]:
    return _request("PATCH", path, json_body=json_body)


# --- Helpers ---

HUBSPOT_SEARCH_MAX_RESULTS = int(os.getenv("HUBSPOT_SEARCH_MAX_RESULTS", "5000"))


def _to_epoch_millis(date_iso: str) -> int:
    """Convert an ISO8601/date string to epoch milliseconds (UTC)."""

    try:
        if "T" in date_iso:
            dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _hubspot_search(
    obj: str, since_date: str, properties: List[str]
) -> List[Dict[str, Any]]:
    """Generic HubSpot CRM search paging by last-modified date."""

    timestamp = _to_epoch_millis(since_date)
    payload: Dict[str, Any] = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "hs_lastmodifieddate",
                        "operator": "GTE",
                        "value": str(timestamp),
                    }
                ]
            }
        ],
        "properties": properties,
        "limit": 100,
    }

    results: List[Dict[str, Any]] = []
    after: Optional[str] = None
    path = f"crm/v3/objects/{obj}/search"

    while True:
        body = dict(payload)
        if after is not None:
            body["after"] = after
        response = _request("POST", path, json_body=body)
        results.extend(response.get("results", []))
        paging = (response.get("paging") or {}).get("next") or {}
        after = paging.get("after")
        if not after:
            break
        if len(results) >= HUBSPOT_SEARCH_MAX_RESULTS:
            break
    return results


# --- CRM: Recent activity search ---


def search_companies_recent_activity(since_date: str) -> List[Dict[str, Any]]:
    """Return companies with HubSpot activity since the provided ISO date."""

    props = ["domain", "name", "hs_lastmodifieddate", "hubspot_owner_id"]
    rows = _hubspot_search("companies", since_date, props)
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        properties = row.get("properties") or {}
        normalized.append(
            {
                "hubspot_id": row.get("id"),
                "domain": properties.get("domain"),
                "name": properties.get("name"),
                "hs_lastmodifieddate": properties.get("hs_lastmodifieddate"),
                "hubspot_owner_id": properties.get("hubspot_owner_id"),
        }
    )
    return normalized


def search_contacts_recent_activity(since_date: str) -> List[Dict[str, Any]]:
    """Return contacts with HubSpot activity since the provided ISO date."""

    props = [
        "email",
        "firstname",
        "lastname",
        "hs_lastmodifieddate",
        "hubspot_owner_id",
    ]
    rows = _hubspot_search("contacts", since_date, props)
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        properties = row.get("properties") or {}
        normalized.append(
            {
                "hubspot_id": row.get("id"),
                "email": properties.get("email"),
                "firstname": properties.get("firstname"),
                "lastname": properties.get("lastname"),
                "hs_lastmodifieddate": properties.get("hs_lastmodifieddate"),
                "hubspot_owner_id": properties.get("hubspot_owner_id"),
        }
    )
    return normalized


# --- Candidate-scoped suppression helpers ---


def _company_has_recent_activity(domain: str, since_date: str) -> bool:
    timestamp = _to_epoch_millis(since_date)
    path = "crm/v3/objects/companies/search"
    filters = [
        {
            "filters": [
                {"propertyName": "domain", "operator": "EQ", "value": domain},
                {
                    "propertyName": "lastactivitydate",
                    "operator": "GTE",
                    "value": str(timestamp),
                },
            ]
        },
        {
            "filters": [
                {"propertyName": "domain", "operator": "EQ", "value": domain},
                {
                    "propertyName": "hs_last_sales_activity_timestamp",
                    "operator": "GTE",
                    "value": str(timestamp),
                },
            ]
        },
    ]
    body = {
        "filterGroups": filters,
        "properties": ["domain", "lastactivitydate", "hs_last_sales_activity_timestamp"],
        "limit": 1,
    }
    resp = _request("POST", path, json_body=body)
    return bool(resp.get("total"))


def _contact_has_recent_activity(email: str, since_date: str) -> bool:
    timestamp = _to_epoch_millis(since_date)
    path = "crm/v3/objects/contacts/search"
    filters = [
        {
            "filters": [
                {"propertyName": "email", "operator": "EQ", "value": email},
                {
                    "propertyName": "lastactivitydate",
                    "operator": "GTE",
                    "value": str(timestamp),
                },
            ]
        },
        {
            "filters": [
                {"propertyName": "email", "operator": "EQ", "value": email},
                {
                    "propertyName": "hs_last_sales_activity_timestamp",
                    "operator": "GTE",
                    "value": str(timestamp),
                },
            ]
        },
    ]
    body = {
        "filterGroups": filters,
        "properties": ["email", "lastactivitydate", "hs_last_sales_activity_timestamp"],
        "limit": 1,
    }
    resp = _request("POST", path, json_body=body)
    return bool(resp.get("total"))


def companies_recent_for_domains(domains: List[str], since_date: str) -> Dict[str, bool]:
    results: Dict[str, bool] = {}
    for domain in domains:
        key = (domain or "").strip().lower()
        if not key:
            continue
        try:
            results[key] = _company_has_recent_activity(domain, since_date)
        except Exception:
            results[key] = False
    return results


def contacts_recent_for_emails(emails: List[str], since_date: str) -> Dict[str, bool]:
    results: Dict[str, bool] = {}
    for email in emails:
        key = (email or "").strip().lower()
        if not key:
            continue
        try:
            results[key] = _contact_has_recent_activity(email, since_date)
        except Exception:
            results[key] = False
    return results


# --- Suppression set builders ---

def _parse_owner_ids_from_env() -> List[str]:
    ids_env = os.getenv("HUBSPOT_OWNER_USER_IDS", "").strip()
    if ids_env:
        return [x.strip() for x in ids_env.split(",") if x.strip()]
    email_map_env = os.getenv("HUBSPOT_OWNER_EMAIL_MAP", "").strip()
    if email_map_env:
        try:
            import json as _json

            mapping = _json.loads(email_map_env)
            if isinstance(mapping, dict):
                return [str(v).strip() for v in mapping.values() if v]
        except Exception:
            pass
    return []


def build_suppression_sets(
    *,
    since_days: int = 120,
    restrict_to_owner_user_ids: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """Return suppression sets based on recent HubSpot activity.

    Args:
        since_days: look-back window in days (defaults to 120)
        restrict_to_owner_user_ids: if provided, only include objects owned by these userIds.
            If None, attempts HUBSPOT_OWNER_USER_IDS or HUBSPOT_OWNER_EMAIL_MAP env fallbacks.

    Returns:
        { "domains": [..], "emails": [..], "since_date": "YYYY-MM-DD" }
    """
    since_iso = (datetime.utcnow() - timedelta(days=since_days)).date().isoformat()

    owner_filter = restrict_to_owner_user_ids
    if owner_filter is None:
        owner_filter = _parse_owner_ids_from_env()
        if not owner_filter:
            # Best-effort: fetch owners (may require scopes)
            try:
                owners = list_all_owner_user_ids(active_only=True)
                owner_filter = [o.get("userId") for o in owners if o.get("userId")]
            except Exception:
                owner_filter = []

    companies = search_companies_recent_activity(since_iso)
    contacts = search_contacts_recent_activity(since_iso)

    domains_set = set()
    emails_set = set()

    if owner_filter:
        owner_set = {str(x) for x in owner_filter}
        for c in companies:
            oid = str(c.get("hubspot_owner_id") or "")
            if c.get("domain") and (not oid or oid in owner_set):
                domains_set.add(c["domain"].strip().lower())
        for p in contacts:
            oid = str(p.get("hubspot_owner_id") or "")
            if p.get("email") and (not oid or oid in owner_set):
                emails_set.add(p["email"].strip().lower())
    else:
        domains_set.update(
            (c.get("domain") or "").strip().lower() for c in companies if c.get("domain")
        )
        emails_set.update(
            (p.get("email") or "").strip().lower() for p in contacts if p.get("email")
        )

    return {
        "domains": sorted(d for d in domains_set if d),
        "emails": sorted(e for e in emails_set if e),
        "since_date": since_iso,
    }


# --- CRM: Create/Update Objects ---


def create_company(properties: Dict[str, Any]) -> Dict[str, Any]:
    return _post("crm/v3/objects/companies", {"properties": properties})


def create_contact(properties: Dict[str, Any]) -> Dict[str, Any]:
    return _post("crm/v3/objects/contacts", {"properties": properties})


def update_company_properties(
    company_id: str, properties: Dict[str, Any]
) -> Dict[str, Any]:
    return _patch(f"crm/v3/objects/companies/{company_id}", {"properties": properties})


def update_contact_properties(
    contact_id: str, properties: Dict[str, Any]
) -> Dict[str, Any]:
    return _patch(f"crm/v3/objects/contacts/{contact_id}", {"properties": properties})


# --- Notes + Associations ---


def create_note(html_body: str, timestamp_iso: Optional[str] = None) -> Dict[str, Any]:
    ts = timestamp_iso or datetime.now(timezone.utc).isoformat()
    return _post(
        "crm/v3/objects/notes",
        {"properties": {"hs_note_body": html_body, "hs_timestamp": ts}},
    )


def associate_note_to_contact(note_id: str, contact_id: str) -> Dict[str, Any]:
    path = f"crm/v3/objects/notes/{note_id}/associations/contacts/{contact_id}/note_to_contact"
    return _put_empty(path)


def associate_note_to_company(note_id: str, company_id: str) -> Dict[str, Any]:
    path = f"crm/v3/objects/notes/{note_id}/associations/companies/{company_id}/note_to_company"
    return _put_empty(path)


def _put_empty(path: str) -> Dict[str, Any]:
    return _request("PUT", path)


def delete_note(note_id: str) -> Dict[str, Any]:
    url = f"{_base_url()}/crm/v3/objects/notes/{note_id}"
    r = requests.delete(url, headers=_headers())
    if not r.ok:
        raise HubSpotError(f"DELETE {url} failed: {r.status_code} {r.text}")
    try:
        return r.json()
    except Exception:
        return {"ok": True}


def pin_note_on_contact(contact_id: str, note_id: str) -> Dict[str, Any]:
    return update_contact_properties(
        contact_id, {"hs_pinned_engagement_id": str(note_id)}
    )


def pin_note_on_company(company_id: str, note_id: str) -> Dict[str, Any]:
    # Many portals support hs_pinned_engagement_id on companies as well
    return update_company_properties(
        company_id, {"hs_pinned_engagement_id": str(note_id)}
    )


# --- CRM: Companies/Contacts search ---


def search_company_by_domain(
    domain: str, properties: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    props = properties or [
        "name",
        "domain",
        "numberofemployees",
        "annualrevenue",
        "hs_lastmodifieddate",
    ]
    payload = {
        "limit": 1,
        "properties": props,
        "filterGroups": [
            {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
        ],
    }
    data = _post("crm/v3/objects/companies/search", payload)
    results = data.get("results", [])
    return results[0] if results else None


def search_companies_by_name(
    name: str, limit: int = 3, properties: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Search companies by name using CONTAINS_TOKEN on the "name" property.
    Returns up to `limit` results with the requested properties.
    """
    props = properties or [
        "name",
        "domain",
        "numberofemployees",
        "annualrevenue",
        "hs_lastmodifieddate",
    ]
    payload = {
        "limit": limit,
        "properties": props,
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "name",
                        "operator": "CONTAINS_TOKEN",
                        "value": name,
                    }
                ]
            }
        ],
    }
    data = _post("crm/v3/objects/companies/search", payload)
    return data.get("results", [])


def search_contact(
    email: Optional[str] = None,
    query: Optional[str] = None,
    properties: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    props = properties or [
        "firstname",
        "lastname",
        "email",
        "jobtitle",
        "phone",
        "company",
        "hs_object_id",
        "hs_lastmodifieddate",
    ]
    payload: Dict[str, Any] = {"limit": 1, "properties": props}
    if email:
        payload["filterGroups"] = [
            {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
        ]
    elif query:
        payload["q"] = query
    else:
        return None
    data = _post("crm/v3/objects/contacts/search", payload)
    results = data.get("results", [])
    return results[0] if results else None


def search_contact_by_fields(
    firstname: Optional[str] = None,
    lastname: Optional[str] = None,
    company: Optional[str] = None,
    limit: int = 1,
    properties: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search contacts using filterGroups on firstname/lastname/company.
    Uses CONTAINS_TOKEN for fuzziness when supported.
    """
    props = properties or [
        "firstname",
        "lastname",
        "email",
        "jobtitle",
        "company",
        "hs_object_id",
        "hs_lastmodifieddate",
    ]
    filters = []
    if firstname:
        filters.append(
            {
                "propertyName": "firstname",
                "operator": "CONTAINS_TOKEN",
                "value": firstname,
            }
        )
    if lastname:
        filters.append(
            {
                "propertyName": "lastname",
                "operator": "CONTAINS_TOKEN",
                "value": lastname,
            }
        )
    if company:
        filters.append(
            {"propertyName": "company", "operator": "CONTAINS_TOKEN", "value": company}
        )
    if not filters:
        return []
    payload = {
        "limit": limit,
        "properties": props,
        "filterGroups": [{"filters": filters}],
    }
    data = _post("crm/v3/objects/contacts/search", payload)
    results = data.get("results", [])
    return results


def search_contacts_by_query(
    query: str, limit: int = 5, properties: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    props = properties or [
        "firstname",
        "lastname",
        "email",
        "jobtitle",
        "company",
        "hs_object_id",
        "hs_lastmodifieddate",
    ]
    payload: Dict[str, Any] = {"limit": limit, "properties": props, "q": query}
    data = _post("crm/v3/objects/contacts/search", payload)
    return data.get("results", [])


# --- Automation Sequences (v4, BETA) ---


def list_sequences(
    user_id: str, limit: Optional[int] = None, after: Optional[str] = None
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"userId": user_id}
    if limit is not None:
        params["limit"] = limit
    if after is not None:
        params["after"] = after
    return _get("automation/v4/sequences", params)


def get_sequence(sequence_id: str, user_id: str) -> Dict[str, Any]:
    return _get(f"automation/v4/sequences/{sequence_id}", {"userId": user_id})


def enroll_contact_in_sequence(
    sequence_id: str, contact_id: str, sender_email: str
) -> Dict[str, Any]:
    # Validate ids and email
    if not str(sequence_id).isdigit() or not str(contact_id).isdigit():
        raise HubSpotError("sequenceId and contactId must be numeric strings.")
    email = (sender_email or "").strip()
    if not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
        raise HubSpotError(f"Invalid senderEmail: {sender_email}")

    sender_user_id = resolve_user_id_by_email(email)
    if not sender_user_id:
        raise HubSpotError(
            f"Unable to resolve sender_email to a HubSpot userId: {sender_email}. "
            "Verify the user exists in Owners and is active with a connected inbox."
        )

    payload = {
        "sequenceId": str(sequence_id),
        "contactId": str(contact_id),
        "senderEmail": email,
        "senderUserId": str(sender_user_id),
    }

    # Optional idempotency header to avoid double-enrollment on retries
    raw_key = f"{sequence_id}:{contact_id}:{sender_user_id}".encode("utf-8")
    idem = hashlib.sha256(raw_key).hexdigest()

    try:
        return _post(
            "automation/v4/sequences/enrollments",
            payload,
            extra_headers={"Idempotency-Key": idem},
        )
    except HubSpotError as ex:
        msg = str(ex).lower()
        if any(k in msg for k in ("scope", "permission", "forbidden", "401", "403")):
            raise HubSpotError(
                "Missing scopes. Required: crm.objects.sequences.read, crm.objects.sequences.write, "
                "crm.objects.owners.read, sales-email-read, sales-email-write."
            )
        if any(k in msg for k in ("already enrolled", "conflict", "409")):
            raise HubSpotError("Contact is already enrolled in a sequence. Skipping.")
        if "connected inbox" in msg or "mailbox" in msg:
            raise HubSpotError(f"Sender {email} must have a connected inbox.")
        raise


def list_all_sequences(
    user_id: str, page_size: int = 100, max_pages: int = 50
) -> List[Dict[str, Any]]:
    """Fetch all sequences for a user by following paging.next.after if present."""
    all_items: List[Dict[str, Any]] = []
    after: Optional[str] = None
    pages = 0
    while pages < max_pages:
        data = list_sequences(user_id=user_id, limit=page_size, after=after)
        # Results might be a list or under a key like 'results' or 'items'
        items: List[Dict[str, Any]]
        if isinstance(data, list):
            items = data
        else:
            items = data.get("results") or data.get("items") or []
        all_items.extend(items)
        paging = None if isinstance(data, list) else data.get("paging")
        next_after = None
        if paging and isinstance(paging, dict):
            nxt = paging.get("next")
            if nxt and isinstance(nxt, dict):
                next_after = nxt.get("after")
        if not next_after:
            break
        after = next_after
        pages += 1
    return all_items


# --- Owners / Users ---


def list_owners(
    limit: Optional[int] = None, after: Optional[str] = None, archived: bool = False
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"archived": str(archived).lower()}
    if limit is not None:
        params["limit"] = limit
    if after is not None:
        params["after"] = after
    return _get("crm/v3/owners", params)


def list_all_owner_user_ids(
    active_only: bool = True, page_size: int = 100, max_pages: int = 50
) -> List[Dict[str, Any]]:
    """
    Return a list of dicts with owner and user information: {ownerId, userId, email, active}.
    """
    all_items: List[Dict[str, Any]] = []
    after: Optional[str] = None
    pages = 0
    while pages < max_pages:
        data = list_owners(limit=page_size, after=after, archived=False)
        items = data.get("results") or data.get("items") or []
        for it in items:
            user_id = it.get("userId") or it.get("user_id")
            owner_id = it.get("id") or it.get("ownerId")
            email = it.get("email") or (
                it.get("user", {}).get("email")
                if isinstance(it.get("user"), dict)
                else None
            )
            active = it.get("active") if it.get("active") is not None else True
            if active_only and not active:
                continue
            if user_id:
                all_items.append(
                    {
                        "ownerId": owner_id,
                        "userId": str(user_id),
                        "email": email,
                        "active": active,
                    }
                )
        paging = data.get("paging")
        next_after = None
        if paging and isinstance(paging, dict):
            nxt = paging.get("next")
            if nxt and isinstance(nxt, dict):
                next_after = nxt.get("after")
        if not next_after:
            break
        after = next_after
        pages += 1
    # de-duplicate by userId
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for rec in all_items:
        uid = rec.get("userId")
        if uid and uid not in seen:
            seen.add(uid)
            deduped.append(rec)
    return deduped


def resolve_user_id_by_email(sender_email: str) -> Optional[str]:
    email_lc = (sender_email or "").strip().lower()
    if not email_lc:
        return None
    try:
        owners = list_all_owner_user_ids(active_only=True)
    except HubSpotError:
        owners = []
    for o in owners:
        if (o.get("email") or "").strip().lower() == email_lc:
            uid = o.get("userId")
            if uid:
                return str(uid)
    return None


# --- Lead List Suppression Helpers ---


def get_company_by_domain_with_lifecycle(domain: str) -> Optional[Dict[str, Any]]:
    """Get company by domain including lifecycle stage and other suppression-relevant fields."""
    props = [
        "name",
        "domain",
        "lifecyclestage",
        "hs_lastmodifieddate",
        "lastactivitydate",
        "hs_last_sales_activity_timestamp",
        "hs_object_id",
    ]
    payload = {
        "limit": 1,
        "properties": props,
        "filterGroups": [
            {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
        ],
    }
    data = _post("crm/v3/objects/companies/search", payload)
    results = data.get("results", [])
    return results[0] if results else None


def get_recent_engagements_for_company(
    company_id: str, days: int = 90
) -> List[Dict[str, Any]]:
    """Get recent engagements (emails, calls, meetings) for a company in the last N days.

    Args:
        company_id: HubSpot company ID
        days: Look-back window in days (default 90)

    Returns:
        List of engagement dicts with type, timestamp, and metadata
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    cutoff_ms = int(cutoff_date.timestamp() * 1000)

    try:
        # Get engagements associated with this company
        response = _get(
            f"engagements/v1/engagements/associated/COMPANY/{company_id}/paged",
            params={"limit": 100, "offset": 0}
        )

        engagements = response.get('results', [])

        # Filter by date and relevant types
        recent = []
        for e in engagements:
            engagement = e.get('engagement', {})
            timestamp = engagement.get('timestamp', 0)
            eng_type = engagement.get('type', '')

            # Only include EMAIL, CALL, MEETING within date range
            if timestamp >= cutoff_ms and eng_type in ['EMAIL', 'CALL', 'MEETING']:
                recent.append({
                    'type': eng_type,
                    'timestamp': timestamp,
                    'date': datetime.fromtimestamp(timestamp / 1000).isoformat(),
                    'engagement_id': engagement.get('id'),
                })

        return recent

    except Exception as e:
        # Log but don't fail - suppression check is best-effort
        return []


def check_company_suppression(
    domain: str, days: int = 90
) -> Dict[str, Any]:
    """Check if a company should be suppressed for lead list generation.

    Args:
        domain: Company domain
        days: Look-back window for recent contact (default 90)

    Returns:
        {
            'should_suppress': bool,
            'reason': str or None,  # 'customer' or 'recently_contacted'
            'details': dict with additional context
        }
    """
    result = {
        'should_suppress': False,
        'reason': None,
        'details': {}
    }

    try:
        # Get company with lifecycle stage
        company = get_company_by_domain_with_lifecycle(domain)

        if not company:
            return result  # Not in HubSpot, no suppression

        properties = company.get('properties', {})
        company_id = company.get('id')
        lifecycle_stage = properties.get('lifecyclestage')

        # Check 1: Is customer?
        if lifecycle_stage and lifecycle_stage.lower() == 'customer':
            result['should_suppress'] = True
            result['reason'] = 'customer'
            result['details'] = {
                'company_id': company_id,
                'lifecycle_stage': lifecycle_stage,
                'company_name': properties.get('name'),
            }
            return result

        # Check 2: Recently contacted?
        if company_id:
            engagements = get_recent_engagements_for_company(company_id, days)

            if engagements:
                result['should_suppress'] = True
                result['reason'] = 'recently_contacted'
                result['details'] = {
                    'company_id': company_id,
                    'engagement_count': len(engagements),
                    'last_contact_date': engagements[0]['date'] if engagements else None,
                    'last_contact_type': engagements[0]['type'] if engagements else None,
                }
                return result

        return result

    except Exception as e:
        # Best-effort - don't fail if HubSpot check fails
        result['details']['error'] = str(e)
        return result
