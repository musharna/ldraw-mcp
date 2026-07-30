"""In-memory MCP server tests: list tools and call check_renderer.

Connected with `Client`, which mcp 2.x accepts an `MCPServer` directly. The 1.x
spelling was:

    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        await c.initialize()

That helper is genuinely gone in 2.x — the only removal in this whole migration
that needed new code rather than a rename. The replacement is better on two
counts: it takes the server object itself instead of reaching through the
private `_mcp_server` attribute, and the context manager initialises the
session, so the explicit `initialize()` call goes away.

Still a real client over in-memory transport, not a direct call into the tool
functions. That distinction is the point of this file: it exercises
registration, schema generation and serialisation the way a client does.
"""

import asyncio

from mcp.client import Client

from ldraw_mcp.server import mcp


async def _exercise():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert {"render_ldraw_file", "render_ldraw_text", "check_renderer"} <= names

        result = await client.call_tool("check_renderer", {})
        assert result.content
        text = result.content[0].text
        assert "blender:" in text
        assert "available:" in text


def test_list_tools_and_check_renderer():
    asyncio.run(_exercise())
