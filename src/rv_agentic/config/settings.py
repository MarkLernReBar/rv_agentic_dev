"""Configuration and settings for the rv_agentic package.

Centralizes environment-variable based configuration using Pydantic
for type safety and discoverability.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    This is intentionally minimal; we can extend it as the
    architecture evolves (e.g. MCP/n8n endpoints, Supabase URLs).
    """

    openai_api_key: str = Field(..., env="OPENAI_API_KEY")

    # n8n HTTP endpoint that proxies MCP tools for search/fetch via MCP.
    # Example: http://10.0.131.72:5678/mcp/<uuid>
    n8n_mcp_server_url: Optional[str] = Field(None, env="N8N_MCP_SERVER_URL")
    n8n_mcp_server_label: str = Field("default-server", env="N8N_MCP_SERVER_LABEL")
    n8n_mcp_auth_token: Optional[str] = Field(None, env="N8N_MCP_AUTH_TOKEN")

    # Backwards-compatible HTTP endpoint for custom non-MCP calls (if needed).
    n8n_mcp_base_url: Optional[str] = Field(None, env="N8N_MCP_BASE_URL")

    # Supabase / Postgres-style connection values for pm_pipeline tables.
    supabase_url: Optional[str] = Field(None, env="SUPABASE_URL")
    supabase_anon_key: Optional[str] = Field(None, env="SUPABASE_ANON_KEY")

    class Config:
        env_file = ".env.local"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Using an accessor keeps imports cheap and avoids repeated parsing.
    """

    settings = Settings()  # type: ignore[arg-type]
    # Propagate critical settings into process env so libraries that
    # rely on environment variables (OpenAI SDK, direct Supabase calls)
    # can see them even if they don't use this Settings object.
    import os as _os

    if settings.openai_api_key and "OPENAI_API_KEY" not in _os.environ:
        _os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    # Supabase URL/key
    if settings.supabase_url and "SUPABASE_URL" not in _os.environ:
        _os.environ["SUPABASE_URL"] = settings.supabase_url
    # Keep existing NEXT_PUBLIC_* envs if already set; otherwise derive from supabase_url/anon
    if settings.supabase_url and "NEXT_PUBLIC_SUPABASE_URL" not in _os.environ:
        _os.environ["NEXT_PUBLIC_SUPABASE_URL"] = settings.supabase_url
    if settings.supabase_anon_key and "NEXT_PUBLIC_SUPABASE_ANON_KEY" not in _os.environ:
        _os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"] = settings.supabase_anon_key
    return settings
