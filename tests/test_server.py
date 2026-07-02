"""In-memory MCP server tests: list tools and call check_renderer."""

import asyncio

from mcp.shared.memory import create_connected_server_and_client_session as connect

from ldraw_mcp.server import mcp


async def _exercise():
    async with connect(mcp._mcp_server) as client:
        await client.initialize()

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
