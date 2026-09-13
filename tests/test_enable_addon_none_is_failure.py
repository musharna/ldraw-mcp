"""`addon_utils.enable()` reports a missing add-on by RETURNING None, not by raising.

Probed against Blender 4.5: enabling an absent module prints "Add-on not loaded" and returns
None; enabling the installed importer returns the module. A loop that treats "no exception" as
success stops at the first name, never tries the fallback, and never reaches its own
"addon not found" error -- the render then dies later on an unrelated missing operator.

blender_script.py runs inside Blender, so bpy and addon_utils are faked in sys.modules.
"""

import importlib
import sys
import types

import pytest


def _load(monkeypatch, installed):
    calls = []

    def enable(name, default_set=False):
        calls.append(name)
        return types.ModuleType(name) if name in installed else None

    monkeypatch.setitem(sys.modules, "bpy", types.ModuleType("bpy"))
    monkeypatch.setitem(
        sys.modules, "addon_utils", types.SimpleNamespace(enable=enable)
    )
    monkeypatch.delitem(sys.modules, "ldraw_mcp.blender_script", raising=False)
    return importlib.import_module("ldraw_mcp.blender_script"), calls


def test_first_name_installed_stops_there(monkeypatch):
    mod, calls = _load(monkeypatch, {"io_scene_importldraw"})
    mod.enable_addon()
    assert calls == ["io_scene_importldraw"]


def test_falls_back_when_first_name_returns_none(monkeypatch):
    mod, calls = _load(monkeypatch, {"importldraw"})
    mod.enable_addon()
    assert calls == ["io_scene_importldraw", "importldraw"]


def test_raises_when_every_name_returns_none(monkeypatch):
    mod, calls = _load(monkeypatch, set())
    with pytest.raises(RuntimeError, match="ImportLDraw addon not found"):
        mod.enable_addon()
    assert calls == ["io_scene_importldraw", "importldraw"]
