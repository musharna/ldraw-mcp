"""Fuzz issue #29: six inputs that escaped as raw tracebacks, two of them zip-slip.

Every negative case below sits beside a positive control in the same test: a
well-formed archive still installs, an existing path still reaches the renderer.
Without that, a broken harness (or an installer that refuses everything) would
read as "blocked".
"""

import asyncio
import io
import json
import zipfile

from mcp.client import Client

from ldraw_mcp import server, setup_cli


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _serve(monkeypatch, payload_for):
    def fake_urlopen(req, *a, **kw):
        url = getattr(req, "full_url", req)
        return io.BytesIO(payload_for(str(url)))

    monkeypatch.setattr(setup_cli.urllib.request, "urlopen", fake_urlopen)


# ------------------------------------------------------------ parts library


def test_library_zip_slip_is_refused_and_nothing_escapes(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"

    good = _zip({"ldraw/parts/3001.dat": "0 brick"})
    _serve(monkeypatch, lambda url: good)
    assert setup_cli.install_ldraw_library(tmp_path / "ok") is True
    assert (tmp_path / "ok" / "parts" / "3001.dat").read_text() == "0 brick"

    for name in ("ldraw/../../outside/marker.txt", "../outside/marker.txt"):
        evil = _zip({"ldraw/parts/3001.dat": "0 brick", name: "pwned"})
        _serve(monkeypatch, lambda url, evil=evil: evil)
        dest = tmp_path / "lib"
        assert setup_cli.install_ldraw_library(dest, force=True) is False, name
        assert not marker.exists(), name
        # refused before writing anything, not halfway through
        assert not (dest / "parts").exists(), name


def test_library_non_zip_download_is_a_logged_failure(monkeypatch, tmp_path):
    _serve(monkeypatch, lambda url: b"<html>502 Bad Gateway</html>")
    assert setup_cli.install_ldraw_library(tmp_path / "lib") is False


# ------------------------------------------------------------ importer addon


def _addon_server(archive):
    def payload(url):
        if "api.github.com" in url:
            asset = {"name": "a.zip", "browser_download_url": "https://x/a.zip"}
            return json.dumps({"assets": [asset]}).encode()
        return archive

    return payload


def test_addon_zip_slip_is_refused_and_nothing_escapes(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"

    good = _zip({"addon-1.0/__init__.py": "x = 1", "addon-1.0/loadldraw/a.py": ""})
    _serve(monkeypatch, _addon_server(good))
    ok_dir = tmp_path / "ok"
    assert setup_cli.install_importldraw_addon([ok_dir]) is True
    assert (ok_dir / "io_scene_importldraw" / "__init__.py").exists()

    evil = _zip(
        {
            "addon-1.0/__init__.py": "x = 1",
            "addon-1.0/../../../outside/marker.txt": "pwned",
        }
    )
    _serve(monkeypatch, _addon_server(evil))
    bad_dir = tmp_path / "addons"
    assert setup_cli.install_importldraw_addon([bad_dir], force=True) is False
    assert not marker.exists()
    assert not (bad_dir / "io_scene_importldraw").exists()


def test_addon_non_zip_download_is_a_logged_failure(monkeypatch, tmp_path):
    _serve(monkeypatch, _addon_server(b"not a zip"))
    assert setup_cli.install_importldraw_addon([tmp_path / "addons"]) is False


# ------------------------------------------------------------ MCP path refusals


def _call(name, args):
    async def go():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, args)

    return asyncio.run(go())


def _text(result):
    return "".join(b.text for b in result.content if hasattr(b, "text"))


def test_unusable_paths_are_refusals_that_name_the_problem(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(server, "_render", lambda p, *a: seen.append(p) or "rendered")

    real = tmp_path / "model.ldr"
    real.write_text("0 model\n")
    assert server.render_ldraw_file(str(real)) == "rendered"
    assert seen == [str(real)]

    long_path = "A" * 5000
    result = _call("render_ldraw_file", {"path": long_path})
    assert result.is_error
    # masked = the bare "Error executing tool <name>" with no reason after it
    assert _text(result).strip() != "Error executing tool render_ldraw_file"
    # 3.10-3.13 raise ENAMETOOLONG from exists(); 3.14 returns False. Either way a refusal.
    assert "too long" in _text(result).lower() or "no such LDraw file" in _text(result)

    result = _call("render_ldraw_file", {"path": "~nonexistentuser123/model.ldr"})
    assert result.is_error
    # masked = the bare "Error executing tool <name>" with no reason after it
    assert _text(result).strip() != "Error executing tool render_ldraw_file"
    assert "~nonexistentuser123" in _text(result)
    assert seen == [str(real)]
