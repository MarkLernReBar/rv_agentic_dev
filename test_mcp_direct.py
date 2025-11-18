#!/usr/bin/env python3
"""Direct test of MCP client to diagnose event loop issue."""

import sys
sys.path.insert(0, "src")

from rv_agentic.tools import mcp_client

print("Testing MCP client call...")
try:
    result = mcp_client.call_tool("hubspot_find_company", {"domain_or_name": "test.com"})
    print(f"Success! Result: {result}")
except Exception as exc:
    import traceback
    print(f"\n❌ Error occurred:\n")
    traceback.print_exc()
    print(f"\nError type: {type(exc).__name__}")
    print(f"Error message: {exc}")
