"""The addon zip URL comes out of GitHub's release JSON, so https must be enforced,
not assumed, before it reaches urlopen.

Positive control first in the same test: a normal https asset URL is returned, so a
harness that returns None for everything cannot pass as "refused".
"""

import io
import json

from ldraw_mcp import setup_cli


def _serve(monkeypatch, release):
    def fake_urlopen(req, timeout):
        return io.BytesIO(json.dumps(release).encode())

    monkeypatch.setattr(setup_cli.urllib.request, "urlopen", fake_urlopen)


def test_release_asset_url_must_be_https(monkeypatch):
    ok = "https://github.com/TobyLobster/ImportLDraw/releases/download/v1/a.zip"
    _serve(monkeypatch, {"assets": [{"name": "a.zip", "browser_download_url": ok}]})
    assert setup_cli._find_addon_asset_url() == ok

    _serve(
        monkeypatch,
        {"assets": [{"name": "a.zip", "browser_download_url": "file:///etc/passwd"}]},
    )
    assert setup_cli._find_addon_asset_url() is None

    _serve(monkeypatch, {"assets": [], "zipball_url": "ftp://example.org/src.zip"})
    assert setup_cli._find_addon_asset_url() is None
