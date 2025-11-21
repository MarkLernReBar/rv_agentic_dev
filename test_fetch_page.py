import asyncio
import sys
sys.path.insert(0, 'src')

from rv_agentic.tools.mcp_client import mcp_client

async def test_fetch_page():
    """Test what fetch_page returns for ipropertymanagement.com"""
    
    url = "https://ipropertymanagement.com/companies/boulder-co"
    
    print(f"Testing fetch_page with URL: {url}")
    print("=" * 60)
    
    result = await mcp_client.call_tool_async("fetch_page", {"url": url})
    
    print(f"\nResult type: {type(result)}")
    print(f"Result length: {len(result) if isinstance(result, (list, dict, str)) else 'N/A'}")
    print("\nResult content:")
    print(result)
    
    return result

if __name__ == "__main__":
    asyncio.run(test_fetch_page())
