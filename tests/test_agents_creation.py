import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rv_agentic.agents.company_researcher_agent import create_company_researcher_agent
from rv_agentic.agents.contact_researcher_agent import create_contact_researcher_agent
from rv_agentic.agents.lead_list_agent import create_lead_list_agent
from rv_agentic.agents.sequence_enroller_agent import create_sequence_enroller_agent


def _ensure_openai_env() -> None:
    # Use a dummy key so Settings() can initialize without hitting the API.
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")


def test_company_researcher_agent_initializes() -> None:
    _ensure_openai_env()
    agent = create_company_researcher_agent()
    assert agent.name == "Company Researcher"
    # Ensure we are pinned to the GPT-5 family in production.
    assert agent.model == "gpt-5-mini"


def test_contact_researcher_agent_initializes() -> None:
    _ensure_openai_env()
    agent = create_contact_researcher_agent()
    assert agent.name == "Contact Researcher"
    assert agent.model == "gpt-5-mini"


def test_lead_list_agent_initializes() -> None:
    _ensure_openai_env()
    agent = create_lead_list_agent()
    assert agent.name == "Lead List Agent"
    assert agent.model == "gpt-5-mini"


def test_sequence_enroller_agent_initializes() -> None:
    _ensure_openai_env()
    agent = create_sequence_enroller_agent()
    assert agent.name == "Sequence Enroller"
    assert agent.model == "gpt-5-nano"
