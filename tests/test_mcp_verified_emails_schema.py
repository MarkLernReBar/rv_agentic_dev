def test_company_researcher_verified_emails_payload_includes_domain(monkeypatch):
    """Company researcher wrapper must send person_name, company_name, and domain."""
    from rv_agentic.agents.company_researcher_agent import _build_verified_emails_payload

    payload = _build_verified_emails_payload(
        person_name="Test Person",
        company_name="Test Company",
        domain="https://example.com",
    )
    assert payload == {
        "person_name": "Test Person",
        "company_name": "Test Company",
        "domain": "example.com",
    }


def test_contact_researcher_verified_emails_payload_includes_domain(monkeypatch):
    """Contact researcher wrapper must send person_name, company_name, and normalized domain."""
    from rv_agentic.agents.contact_researcher_agent import _build_verified_emails_payload

    payload = _build_verified_emails_payload(
        person_name="Test Person",
        company_name="Test Company",
        domain="https://example.com/path",
    )
    assert payload["domain"] == "example.com"


def test_lead_list_agent_verified_emails_payload_includes_domain(monkeypatch):
    """Lead list agent wrapper must send person_name, company_name, and normalized domain."""
    from rv_agentic.agents.lead_list_agent import _build_verified_emails_payload

    payload = _build_verified_emails_payload(
        person_name="Test Person",
        company_name="Test Company",
        domain="example.com",
    )
    assert payload["domain"] == "example.com"
