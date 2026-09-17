"""Discovery seams in render.py: env override, PATH lookup, home fallback.

Nothing else in the suite drives `find_blender`, `ldraw_library_dir` or
`is_available` through their branches: every server test runs with the
renderer disabled, so the 28 mutants inside these three functions survived
(nightly 2026-09-17). Each test here pins ONE branch of the search order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ldraw_mcp import render


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.delenv("LDRAW_MCP_BLENDER", raising=False)
    monkeypatch.delenv("LDRAW_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LDRAW_MCP_DISABLE", raising=False)
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    return fake_home


# --- find_blender -----------------------------------------------------------


def test_find_blender_prefers_an_existing_env_override(home, tmp_path, monkeypatch):
    exe = tmp_path / "custom-blender"
    exe.write_text("")
    monkeypatch.setenv("LDRAW_MCP_BLENDER", str(exe))
    monkeypatch.setattr(render.shutil, "which", lambda name: "/should/not/win")
    assert render.find_blender() == str(exe)


def test_find_blender_ignores_an_env_override_that_does_not_exist(
    home, tmp_path, monkeypatch
):
    monkeypatch.setenv("LDRAW_MCP_BLENDER", str(tmp_path / "missing"))
    assert render.find_blender() is None


def test_find_blender_asks_which_for_exactly_blender(home, monkeypatch):
    asked = []

    def which(name):
        asked.append(name)
        return "/usr/bin/blender" if name == "blender" else None

    monkeypatch.setattr(render.shutil, "which", which)
    assert render.find_blender() == "/usr/bin/blender"
    assert asked == ["blender"]


def test_find_blender_falls_back_to_local_bin_under_home(home):
    local = home / ".local" / "bin" / "blender"
    local.parent.mkdir(parents=True)
    local.write_text("")
    assert render.find_blender() == str(local)


def test_find_blender_returns_none_when_nothing_is_installed(home):
    assert render.find_blender() is None


# --- ldraw_library_dir ------------------------------------------------------


def _library(root: Path) -> Path:
    (root / "parts").mkdir(parents=True)
    return root


def test_library_dir_prefers_env_path_when_it_has_parts(home, tmp_path, monkeypatch):
    env_lib = _library(tmp_path / "env-lib")
    _library(home / ".ldraw")  # present too, must lose
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(env_lib))
    assert render.ldraw_library_dir() == env_lib


def test_library_dir_skips_an_env_path_without_parts(home, tmp_path, monkeypatch):
    (tmp_path / "env-lib").mkdir()  # no parts/ inside
    home_lib = _library(home / ".ldraw")
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(tmp_path / "env-lib"))
    assert render.ldraw_library_dir() == home_lib


def test_library_dir_falls_back_to_dot_ldraw_under_home(home):
    home_lib = _library(home / ".ldraw")
    assert render.ldraw_library_dir() == home_lib


# --- is_available -----------------------------------------------------------


@pytest.fixture
def both_present(home, monkeypatch):
    _library(home / ".ldraw")
    monkeypatch.setattr(render.shutil, "which", lambda name: "/usr/bin/blender")


def test_is_available_when_blender_and_library_are_both_found(both_present):
    assert render.is_available() is True


def test_is_available_is_false_without_blender(both_present, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    assert render.is_available() is False


def test_is_available_is_false_without_a_library(home, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda name: "/usr/bin/blender")
    assert render.is_available() is False


def test_disable_switch_only_fires_on_the_literal_one(both_present, monkeypatch):
    monkeypatch.setenv("LDRAW_MCP_DISABLE", "1")
    assert render.is_available() is False
    monkeypatch.setenv("LDRAW_MCP_DISABLE", "0")
    assert render.is_available() is True


def test_library_dir_falls_back_to_the_system_share_path(home, tmp_path, monkeypatch):
    """The last candidate is the distro path. Redirect that one literal into
    tmp so the test can observe it without a real /usr/share/ldraw."""
    system_lib = _library(tmp_path / "usr-share-ldraw")

    real_path = render.Path

    class _Redirected:
        """Stand-in for the module's Path: same class for everything except
        the one distro literal (a plain callable, so it works on 3.10+)."""

        home = staticmethod(real_path.home)

        def __new__(cls, *args):
            if args == ("/usr/share/ldraw",):
                return real_path(system_lib)
            return real_path(*args)

    monkeypatch.setattr(render, "Path", _Redirected)
    assert render.ldraw_library_dir() == system_lib
