import re
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urlparse


def normalize_domain(domain: str) -> str:
    """Normalize domain to standard format"""
    if not domain:
        return ""

    # Remove protocol if present
    domain = re.sub(r"^https?://", "", domain)

    # Remove www prefix
    domain = re.sub(r"^www\.", "", domain)

    # Remove trailing slash and path
    domain = domain.split("/")[0]

    # Remove port if present
    domain = domain.split(":")[0]

    # Convert to lowercase
    domain = domain.lower().strip()

    return domain


def validate_domain(domain: str) -> bool:
    """Validate domain format"""
    if not domain:
        return False

    # Basic domain pattern validation
    domain_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"

    return bool(re.match(domain_pattern, domain))


def extract_company_name(text: str) -> Optional[str]:
    """Extract company name from domain or text"""

    # If it's a domain, extract company name from it
    if "." in text and " " not in text:
        domain_parts = text.split(".")
        if len(domain_parts) >= 2:
            # Take the main part before TLD
            company_part = domain_parts[0]

            # Clean up common patterns
            company_part = re.sub(
                r"(property|properties|management|mgmt|pm|rental|rentals)$",
                "",
                company_part,
                flags=re.IGNORECASE,
            )

            # Convert to title case
            return company_part.title()

    # If it's text, try to extract company name
    # Look for patterns like "ABC Property Management", "XYZ Rentals", etc.
    patterns = [
        r"([A-Z][a-zA-Z\s&]+)(?:Property Management|Properties|Management|Rentals?|Real Estate)",
        r"([A-Z][a-zA-Z\s&]+)(?:PM|PMC)",
        r"(?:analyze|research)\s+([A-Z][a-zA-Z\s&]+?)(?:\s|$)",
        r"^([A-Z][a-zA-Z\s&]+?)(?:\s+(?:company|corp|llc|inc))?$",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            company_name = match.group(1).strip()
            # Clean up the name
            company_name = re.sub(r"\s+", " ", company_name)
            return company_name

    return None


def format_number(value) -> str:
    """Format numbers for display"""
    if value is None or value == "Unknown":
        return "Unknown"

    try:
        num = int(value)
        if num >= 1000:
            return f"{num:,}"
        return str(num)
    except (ValueError, TypeError):
        return str(value)


def clean_text(text: str) -> str:
    """Clean and normalize text content"""
    if not text:
        return ""

    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text)

    # Remove special characters that might break formatting
    text = re.sub(r"[^\w\s\-\.\,\!\?\:\;]", "", text)

    return text.strip()


def extract_domain_from_url(url: str) -> str:
    """Extract domain from full URL"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        return normalize_domain(domain)
    except Exception:
        return normalize_domain(url)


def is_property_management_related(text: str) -> bool:
    """Check if text is related to property management"""
    pm_keywords = [
        "property management",
        "property manager",
        "rental management",
        "real estate management",
        "apartment management",
        "residential management",
        "property services",
        "rental services",
        "leasing",
        "tenant",
        "landlord",
        "portfolio management",
        "property portfolio",
    ]

    text_lower = text.lower()
    return any(keyword in text_lower for keyword in pm_keywords)


# --- Freshness helpers ---

COMPANY_STALE_DAYS = 180
CONTACT_STALE_DAYS = 90


def parse_iso_datetime(value) -> Optional[datetime]:
    """Parse an ISO8601 datetime string (or datetime) to a timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            s = value.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def days_since(updated_at) -> Optional[int]:
    dt = parse_iso_datetime(updated_at)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).days


def is_stale(updated_at, threshold_days: int) -> Tuple[bool, Optional[int]]:
    """Return (is_stale, age_days) given a timestamp and a threshold in days."""
    age = days_since(updated_at)
    if age is None:
        return True, None
    return age > threshold_days, age


def freshness_summary(updated_at, threshold_days: int) -> str:
    stale, age = is_stale(updated_at, threshold_days)
    if age is None:
        return "unknown"
    return f"{'stale' if stale else 'fresh'} ({age} days old)"


# --- Parsing helpers ---


def extract_person_name(text: str) -> Optional[str]:
    """Extract a likely person full name from input like 'Jane Doe at Company'."""
    if not text:
        return None
    # Common pattern: "Name at Company"
    m = re.search(
        r"^\s*([A-Z][a-zA-Z'\-]+\s+[A-Z][a-zA-Z'\-]+)\s+at\s+.+", text, re.IGNORECASE
    )
    if m:
        return " ".join(part.capitalize() for part in m.group(1).split())
    # Fallback: "First Last, ..." at start of string
    m2 = re.search(r"^\s*([A-Z][a-zA-Z'\-]+)\s+([A-Z][a-zA-Z'\-]+)\b", text)
    if m2:
        return f"{m2.group(1).capitalize()} {m2.group(2).capitalize()}"
    # Fallback: try to find two capitalized words at the start
    m3 = re.search(r"^\s*([A-Z][a-zA-Z'\-]+\s+[A-Z][a-zA-Z'\-]+)\b", text)
    if m3:
        return " ".join(part.capitalize() for part in m3.group(1).split())
    return None
