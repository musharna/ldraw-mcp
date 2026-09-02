"""What a client is told when a tool REFUSES, as opposed to when it works.

`test_server.py` already drives a real client over in-memory transport, but
only along the happy path: it lists tools and calls `check_renderer`, which
answers whatever the machine's state is and never fails. That left the failing
half of the surface untested through the protocol, and the failing half is
where this server does its explaining - "run `ldraw-mcp-setup`", "the model may
be empty, hidden, or outside the camera frustum", the path that did not exist.

mcp 2.1 replaces the message of any exception that is not a ToolError with
`Error executing tool <name>` and leaves the original in the server log. So
every one of those sentences stopped reaching the caller the moment this
package resolved 2.1 - which it does today, since the pin is `mcp>=2,<3` and
there is no lockfile - and nothing here failed, because a masked refusal is
still an error and no test read the text.
"""

import asyncio

import pytest
from mcp.client import Client

from ldraw_mcp import render as ldraw_render
from ldraw_mcp.server import _REFUSALS, _surfaces_refusals, mcp


async def _call(name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _text(result):
    return "".join(b.text for b in result.content if hasattr(b, "text"))


def test_a_missing_file_is_named_through_the_protocol():
    """The refusal names the path, which is the only way a caller can tell a
    typo from an unmounted directory. Asserting the TEXT rather than
    `is_error`: a masked refusal is still an error, so `is_error` is true
    either way and cannot tell the two apart."""
    result = asyncio.run(
        _call("render_ldraw_file", {"path": "/nonexistent/definitely-not-here.ldr"})
    )

    assert result.is_error
    assert "definitely-not-here.ldr" in _text(result)


def test_an_unparseable_azimuth_says_what_it_could_not_read():
    """`float(a)` over the comma-separated list, reached through the tool. The
    caller passed a string and needs to know which part of it was rejected."""
    result = asyncio.run(
        _call(
            "render_ldraw_text",
            {"ldr": "0 test\n", "azimuths": "-60,not-a-number"},
        )
    )

    assert result.is_error
    assert "not-a-number" in _text(result)


def test_an_unavailable_renderer_still_says_how_to_install_it(monkeypatch):
    """The one refusal that is pure instruction: without Blender or the parts
    library there is nothing to render, and `ldraw-mcp-setup` is the answer.
    Masked, this tool becomes unusable with no way to find out why.

    `find_blender` rather than `is_available`, because `render_ldraw` calls
    the former and never the latter - patching `is_available` here left the
    real Blender on this machine to run the model, which is how that got
    noticed. Patching it also makes the test answer the same on a machine
    with a working renderer as on one without.
    """
    monkeypatch.setattr(ldraw_render, "find_blender", lambda: None)

    result = asyncio.run(
        _call("render_ldraw_file", {"path": __file__})
    )

    assert result.is_error
    assert "ldraw-mcp-setup" in _text(result)


def test_every_tool_surfaces_its_refusals():
    """The scan that stops the NEXT tool being added without the wrapper.

    Nothing else here would notice: a masked refusal is still a refusal, so the
    tool still "fails correctly" - it just stops saying why.
    """
    import ast
    import inspect

    from ldraw_mcp import server as server_mod

    tree = ast.parse(inspect.getsource(server_mod))
    unsurfaced = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        names = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            names.append(ast.unparse(target))

        if "mcp.tool" in names and "_surfaces_refusals" not in names:
            unsurfaced.append(node.name)

    assert not unsurfaced, (
        f"{unsurfaced} refuse without `@_surfaces_refusals` - under mcp 2.1 the "
        "caller gets `Error executing tool <name>` and the instruction in the "
        "refusal never leaves the server log"
    )


def test_a_real_bug_is_still_masked_rather_than_read_as_a_refusal():
    """The negative, with its positive control in the same test.

    Surfacing refusals must not become surfacing everything: `Error executing
    tool X` is the right answer to a TypeError, which carries internals the
    caller can do nothing with. If this fails while the tests above pass,
    `_REFUSALS` has been widened to `Exception` and the boundary no longer
    tells a refusal from a crash.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    @_surfaces_refusals
    def refuses():
        raise ldraw_render.LDrawRenderError("run `ldraw-mcp-setup`")

    @_surfaces_refusals
    def crashes():
        raise TypeError("unsupported operand type(s)")

    with pytest.raises(ToolError) as refusal:
        refuses()
    assert "ldraw-mcp-setup" in str(refusal.value)

    with pytest.raises(TypeError):
        crashes()

    assert TypeError not in _REFUSALS
