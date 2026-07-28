# Changelog

## 0.1.2

- **Fix broken installs against the `mcp` 2.0.0 SDK.** The dependency was an
  unbounded `"mcp"`. The MCP spec revision `2026-07-28` shipped alongside
  `mcp` 2.0.0 on the same day, so a clean `pip install ldraw-mcp` resolved the
  new major, in which `mcp.server.fastmcp` no longer exists (`FastMCP` is now
  `MCPServer`) — the server failed at import with
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. Now pinned
  `mcp<2`. Migrating to the 2.x API is separate work; the new spec's
  deprecations carry a twelve-month minimum window, and this server is stdio,
  which the stateless-transport changes do not touch.

## 0.1.1

- Publish to the official MCP registry (`server.json` + OIDC workflow) and MCP
  directories (Glama, Cursor via `.mcp.json`).
- `mcp-name` marker in README for PyPI-ownership verification.

## 0.1.0

- Initial release: `render_ldraw_file`, `render_ldraw_text`, `check_renderer`
  MCP tools; `ldraw-mcp-setup` installer for the LDraw library + ImportLDraw addon.
