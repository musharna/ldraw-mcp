"""The render tools and the parts list follow the same references.

Audit 2026-11-10 (L-M3): `bill_of_materials` refused `..\\outside\\secret.ldr`,
while `render_ldraw_file` followed it and drew the outside model, and
`render_ldraw_text` followed an absolute path to an existing file. ImportLDraw
joins each reference onto its search folders, so `..`, an absolute path or a
`~` reaches any file the server can read.

One rule now, in `bom.check_reference` and `bom.check_inside`: a reference may
not name a path that leaves the folder it is looked up in (no `..`, `.` or
empty segment, no absolute path, drive or `~`), and a file found in the model's
folder may not resolve outside it (a symlink). The parts list calls them while
it walks the model; the renderer calls the same two functions from inside
Blender, on every file the importer looks up.

Inline text has no folder of its own. The rule still applies, because the
importer still resolves inline references against the library folders and a
temporary folder, and follows an absolute path from either. No real LDraw
reference needs to leave those folders.
"""

import asyncio
import json

import pytest
from mcp.client import Client

from ldraw_mcp import bom
from ldraw_mcp import render as ldraw_render
from ldraw_mcp.server import mcp


def _line(colour, name):
    return f"1 {colour} 0 0 0 1 0 0 0 1 0 0 0 1 {name}\n"


async def _acall(name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _call(name, args):
    return asyncio.run(_acall(name, args))


def _text(result):
    return "".join(b.text for b in result.content if b.type == "text")


def test_the_rule_refuses_every_way_out_and_passes_library_names():
    for name in (
        "..\\outside\\secret.ldr",
        "../x.dat",
        "sub/../../x.ldr",
        "/etc/passwd",
        "\\\\server\\share\\x.ldr",
        "~/secret.ldr",
        "~root/x.dat",
        "c:\\x.ldr",
        "./sub.ldr",
        "s\\\\x.dat",
    ):
        with pytest.raises(bom.LDrawModelError, match="leaves the model's directory"):
            bom.check_reference(name)
    # Positive control: real library and sub-model names pass, normalised.
    for name, normal in (
        ("3001.dat", "3001.dat"),
        ("s\\3001s01.dat", "s/3001s01.dat"),
        ("48\\4-4cyli.dat", "48/4-4cyli.dat"),
        ("Sub Model.LDR", "sub model.ldr"),
        ("models/wheel.ldr", "models/wheel.ldr"),
    ):
        assert bom.check_reference(name) == normal


def test_the_parts_list_now_refuses_what_it_used_to_count(tmp_path):
    """An absolute path in inline text, and a `..` path to a part, used to be
    counted as parts not in the library while the renderer followed them."""
    secret = tmp_path / "secret.ldr"
    secret.write_text(_line(1, "3001.dat"))
    for text in (_line(16, str(secret)), _line(4, "..\\x.dat")):
        with pytest.raises(bom.LDrawModelError, match="leaves the model's directory"):
            bom.bill_of_materials(text=text)
    # Positive control: an ordinary part in inline text is still counted.
    rows = bom.bill_of_materials(text=_line(4, "s\\3001s01.dat"))["rows"]
    assert [(r["part"], r["quantity"]) for r in rows] == [("s/3001s01.dat", 1)]


def test_a_refusal_reported_by_blender_is_a_refusal(monkeypatch, tmp_path):
    """Without Blender: the script's report of a refused reference becomes
    the same error the parts list raises, not a render failure."""
    import subprocess

    monkeypatch.setattr(ldraw_render, "find_blender", lambda: "/fake/blender")
    monkeypatch.setattr(ldraw_render, "ldraw_library_dir", lambda: tmp_path)
    message = "reference '../x.ldr' leaves the model's directory; refusing to follow it"

    def fake_run(cmd, **kwargs):
        report = json.dumps({"missing": [], "refused": [message]})
        return subprocess.CompletedProcess(
            cmd, 3, ldraw_render.REPORT_PREFIX + report, ""
        )

    monkeypatch.setattr(ldraw_render.subprocess, "run", fake_run)
    with pytest.raises(bom.LDrawModelError) as exc:
        ldraw_render.render_ldraw_report(
            str(tmp_path / "m.ldr"), str(tmp_path / "o.png")
        )
    assert str(exc.value) == message


@pytest.mark.skipif(
    not ldraw_render.is_available(), reason="blender/LDraw stack not installed"
)
def test_render_and_parts_list_refuse_the_same_references(tmp_path):
    """Real Blender through the MCP client, beside the parts list, with the
    sibling reference both must still follow."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.ldr"
    secret.write_text("0 secret\n" + _line(1, "3001.dat"))
    models = tmp_path / "models"
    models.mkdir()
    (models / "sub.ldr").write_text("0 sub\n" + _line(4, "3001.dat"))
    (models / "link.ldr").symlink_to(secret)
    args = {"azimuths": "-60", "resolution": 64, "samples": 2}

    def model(name, ref):
        path = models / name
        path.write_text("0 main\n" + _line(16, ref))
        return str(path)

    escapes = {
        "dotdot": {"path": model("dotdot.ldr", "..\\outside\\secret.ldr")},
        "symlink": {"path": model("symlink.ldr", "link.ldr")},
        "absolute": {"ldr": "0 t\n" + _line(16, str(secret))},
    }
    for case, source in escapes.items():
        tool = "render_ldraw_file" if "path" in source else "render_ldraw_text"
        rendered = _call(tool, {**source, **args})
        listed = _call("bill_of_materials", source)
        assert rendered.is_error, (case, [b.type for b in rendered.content])
        assert listed.is_error, case
        assert "refusing to follow it" in _text(rendered), (case, _text(rendered))
        # The same check produced both messages.
        assert _text(rendered).split(": ", 1)[1] == _text(listed).split(": ", 1)[1], (
            case
        )

    # Positive control: a sibling file is followed by both.
    sibling = {"path": model("sibling.ldr", "sub.ldr")}
    rendered = _call("render_ldraw_file", {**sibling, **args})
    assert not rendered.is_error, _text(rendered)
    assert [b.type for b in rendered.content] == ["image"], _text(rendered)
    listed = _call("bill_of_materials", sibling)
    assert not listed.is_error, _text(listed)
    assert listed.structured_content["total_parts"] == 1
