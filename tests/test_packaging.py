"""Version identity across every place ldraw-mcp records it.

Ported from data-aggregator-mcp, which is the only repo in this account that had
such a test — and it earned its keep immediately, catching two incomplete version
bumps during the v0.45.1 release. The repos without it stayed green while shipping
a stale ``__version__``; this repo was one of them (``__version__`` said 0.1.0 while
pyproject, server.json and the published PyPI artifact all said 0.1.1).

A version recorded in four places with nothing enforcing agreement will drift.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import ldraw_mcp

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text())


def test_version_is_synced_across_all_sources() -> None:
    pyproject_version = _PYPROJECT["project"]["version"]
    module_version = ldraw_mcp.__version__
    sj = json.loads((_ROOT / "server.json").read_text())

    assert module_version == pyproject_version, (
        f"__version__ {module_version!r} != pyproject version {pyproject_version!r}"
    )
    assert sj["version"] == pyproject_version, (
        f"server.json top-level version {sj['version']!r} != pyproject version {pyproject_version!r}"
    )
    assert sj["packages"][0]["version"] == pyproject_version, (
        f"server.json packages[0].version {sj['packages'][0]['version']!r} "
        f"!= pyproject version {pyproject_version!r}"
    )


def test_citation_cff_version_matches_pyproject() -> None:
    """CITATION.cff feeds GitHub's cite panel and the Zenodo DOI record.

    Stale citation metadata is worse than none: it is machine-readable and a wrong
    version propagates into other people's bibliographies, where nobody re-checks it
    against the tag.
    """
    cff = (_ROOT / "CITATION.cff").read_text()
    line = next(ln for ln in cff.splitlines() if ln.startswith("version:"))
    cff_version = line.split(":", 1)[1].strip().strip("\"'")
    assert cff_version == _PYPROJECT["project"]["version"], (
        f"CITATION.cff version {cff_version!r} != pyproject version "
        f"{_PYPROJECT['project']['version']!r}"
    )


def test_server_json_matches_package_identity() -> None:
    sj = json.loads((_ROOT / "server.json").read_text())
    assert sj["name"] == "io.github.musharna/ldraw-mcp"
    pkg = sj["packages"][0]
    assert pkg["registryType"] == "pypi"
    assert pkg["identifier"] == _PYPROJECT["project"]["name"]
    assert pkg["version"] == ldraw_mcp.__version__


def test_server_json_description_within_registry_limit() -> None:
    # The MCP registry hard-rejects (422) descriptions longer than 100 chars.
    sj = json.loads((_ROOT / "server.json").read_text())
    assert len(sj["description"]) <= 100, (
        f"server.json description is {len(sj['description'])} chars; registry limit is 100"
    )
