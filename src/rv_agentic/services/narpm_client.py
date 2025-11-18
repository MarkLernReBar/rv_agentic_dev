import os
from typing import Any, Dict, List, Optional

import requests


class NarpmError(Exception):
    pass


BASE_URL = "https://api.blankethomes.com/narpm-members"


def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    timeout = float(os.getenv("HTTP_TIMEOUT", "20"))
    try:
        resp = requests.get(BASE_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except requests.RequestException as e:
        raise NarpmError(str(e))


def search_narpm(full_name: str, limit: int = 12, offset: int = 0) -> List[Dict[str, Any]]:
    """Search the public NARPM members API by full_name (person or company).

    Returns a list of member dicts. No API key required.
    """
    if not full_name or not str(full_name).strip():
        return []
    params: Dict[str, Any] = {
        "full_name": str(full_name).strip(),
        "offset": max(0, int(offset)),
        "limit": max(1, int(limit)),
    }
    data = _get(params)
    # API may return list or an object with items/results
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("items") or []
    return []


def quick_company_membership(company_name: str) -> Optional[Dict[str, Any]]:
    """Return the first NARPM record for the given company name, or None."""
    items = search_narpm(company_name, limit=1, offset=0)
    return items[0] if items else None


def quick_person_membership(person_name: str, company_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the best-matching NARPM record for a person, optionally biased by company token overlap."""
    items = search_narpm(person_name, limit=5, offset=0)
    if not items:
        return None
    if not company_hint:
        return items[0]
    hint = company_hint.lower()
    def score(item: Dict[str, Any]) -> int:
        s = 0
        comp = (item.get("company") or item.get("company_name") or "").lower()
        if comp and any(t for t in hint.split() if t and t in comp):
            s += 1
        return s
    items.sort(key=score, reverse=True)
    return items[0]

