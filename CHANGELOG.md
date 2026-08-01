# Changelog

## 0.2.1

- **Fixed: every render was blank.** 0.2.0 produced a uniform grey frame with no
  model in it, for every input. The camera _distance_ was derived from the scene
  bounds but the camera _clip planes_ were left at Blender's defaults.
  ImportLDraw imports at real-world metre scale — two 2x4 bricks genuinely span
  0.032 m — so `distance = span * 2.2` placed the camera 0.07 m away, **inside
  the default `clip_start` of 0.1 m**. Every mesh was clipped away before
  shading and the frame rendered as the bare white world, which AgX maps to a
  plausible grey.

  Nothing failed on the way there: the import succeeded (472 vertices per brick,
  nothing hidden from rendering), Blender exited 0, and the PNG was well-formed.
  The fault was a parameter that was never set, so no line of code read wrong.

  Both clip planes are now derived from the same `distance` the camera position
  already uses, which fixes it at any model scale rather than special-casing
  small models with a fixed tiny near plane. Measured on an otherwise identical
  scene, max per-pixel deviation from the background goes from **6** (0 of 40000
  pixels differing) to **195** (13010 of 40000). (#14, #16)

- **The blank-frame eval's control had never run in CI.** It sat under a
  module-level `skipif(not is_available())`, and CI runners have no Blender — so
  the one check that proves the detector can tell a blank frame from a drawn one
  was skipped everywhere it mattered, while its own docstring claimed it ran
  without a renderer. The skip now applies only to the test that drives Blender.
  That control also imports `numpy`, which was in neither `dependencies` nor the
  `test` extra; it stayed green only by being skipped, so `numpy` joins the
  `test` extra. (#16)

- **`server.json`'s fourth version copy is now guarded.**
  `_meta.publisher-provided.version` was not covered by the version-sync test
  that exists precisely because unchecked version sources drift. It was correct
  through two releases only because it was bumped by hand each time.

## 0.2.0

- **Migrated to the `mcp` 2.x API** — the "separate work" 0.1.2 deferred below.
  `FastMCP` → `mcp.server.mcpserver.MCPServer`, `Image` moved with it. The
  dependency **moves** to `mcp>=2,<3` rather than widening to `<3`: this package
  imports `mcp.server.mcpserver`, absent in 1.x, so a range spanning both majors
  can resolve to a version that cannot import the server. That is precisely how
  dependabot's `mcp<3` proposal (#7) broke the build, and why it was closed
  rather than merged. mcp 2.x requires Python >=3.10, exactly this package's
  floor, so the support matrix is unchanged.

- **Rewrote the in-memory client test.** `create_connected_server_and_client_session`
  is genuinely gone from `mcp.shared.memory` — the one removal in this migration
  that needed new code rather than a rename, and the reason #7's CI failed for a
  _second_ reason beyond the server break.

  The replacement is `mcp.client.Client`, which accepts an `MCPServer` directly.
  It is better than what it replaces on two counts: no reaching through the
  private `_mcp_server` attribute, and the context manager initialises the
  session so the explicit `initialize()` call disappears. It remains a real
  client over in-memory transport rather than a direct call into the tool
  functions, which is the point of that file.

- **`__version__` now reads from installed metadata** instead of being a fourth
  hand-maintained copy. `test_packaging.py` already guarded all four sources —
  it exists because this repo shipped `__version__` 0.1.0 while everything else
  said 0.1.1 — so this removes a source of drift rather than replacing the check
  that catches it. `server.json` and `CITATION.cff` cannot be derived and are
  still asserted.

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
