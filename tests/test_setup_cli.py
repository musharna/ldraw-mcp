"""install_ldraw_library's skip-when-present contract.

Mutation triage 2026-09-17 (issue #40): three survivors in this function
(default ``force`` flipped to True, ``not force`` inverted, the skip path's
``return True`` flipped) were all invisible because no test checked what
happens when the library is ALREADY there. One test, both arms.
"""

from pathlib import Path

from ldraw_mcp import setup_cli


def _present(tmp_path: Path) -> Path:
    lib = tmp_path / "ldraw"
    (lib / "parts").mkdir(parents=True)
    return lib


def test_install_ldraw_skips_download_when_present_unless_forced(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_urlopen(url, *a, **kw):
        calls.append(url)
        raise OSError("offline: the network must not be touched on the skip path")

    monkeypatch.setattr(setup_cli.urllib.request, "urlopen", fake_urlopen)
    lib = _present(tmp_path)

    # Negative arm: present + default force -> True, and NO download attempt.
    assert setup_cli.install_ldraw_library(lib) is True
    assert calls == []

    # Positive control, same test: force=True must reach the download and,
    # since the fake fails it, report False. Proves the skip above was a
    # decision, not a harness that never reaches urlopen.
    assert setup_cli.install_ldraw_library(lib, force=True) is False
    assert calls == [setup_cli.LDRAW_LIBRARY_URL]
