"""
ldraw-mcp-setup — install the LDraw parts library and the ImportLDraw
Blender addon so the renderer can run.

What it does:
  1. Download the LDraw parts library (complete.zip) into ~/.ldraw if the
     library is not already present.
  2. Download the latest ImportLDraw addon release from GitHub and install
     it into every detected Blender addons directory under
     ~/.config/blender/<version>/scripts/addons/.

Blender itself is a prerequisite and is NOT installed by this script.
When automatic detection fails, clear manual instructions are printed.
"""

import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

LDRAW_LIBRARY_URL = "https://library.ldraw.org/library/updates/complete.zip"
IMPORTLDRAW_LATEST_API = (
    "https://api.github.com/repos/TobyLobster/ImportLDraw/releases/latest"
)
IMPORTLDRAW_RELEASES_PAGE = "https://github.com/TobyLobster/ImportLDraw/releases/latest"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _contained(root: Path, rel: str) -> Path:
    """Where a zip member named `rel` lands under `root`; ValueError if outside it.

    Member names come from a downloaded archive, so they are untrusted input: a
    `..` component or an absolute name would otherwise write anywhere the
    process can (zip-slip). Every member is checked before anything is written.
    """
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"zip member {rel!r} would extract outside {root}")
    return target


def ldraw_library_present(ldraw_dir: Path) -> bool:
    return (ldraw_dir / "parts").is_dir()


def install_ldraw_library(ldraw_dir: Path, force: bool = False) -> bool:
    """Download + unzip the LDraw parts library. Returns True if installed."""
    if ldraw_library_present(ldraw_dir) and not force:
        _log(f"[ldraw] library already present at {ldraw_dir} — skipping")
        return True

    ldraw_dir.mkdir(parents=True, exist_ok=True)
    _log(f"[ldraw] downloading {LDRAW_LIBRARY_URL} ...")
    try:
        with urllib.request.urlopen(LDRAW_LIBRARY_URL, timeout=120) as resp:  # nosec B310 - fixed https URL
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        _log(f"[ldraw] download FAILED: {exc}")
        _log(
            "[ldraw] manual step: download complete.zip from\n"
            f"          {LDRAW_LIBRARY_URL}\n"
            f"        and unzip it so that {ldraw_dir}/parts/ exists."
        )
        return False

    _log(f"[ldraw] unzipping ({len(data) // (1024 * 1024)} MB) ...")
    # complete.zip contains a top-level 'ldraw/' directory; extract its
    # contents directly into ldraw_dir.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            plan = []
            for member in zf.namelist():
                parts = member.split("/", 1)
                if parts[0] == "ldraw" and len(parts) == 2:
                    if not parts[1]:
                        continue  # the ldraw/ directory entry itself
                    rel = parts[1]
                else:
                    rel = member
                plan.append((member, _contained(ldraw_dir, rel)))
            for member, target in plan:
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
    except (zipfile.BadZipFile, ValueError) as exc:
        _log(f"[ldraw] refusing the downloaded archive: {exc}")
        _log(
            "[ldraw] manual step: download complete.zip from\n"
            f"          {LDRAW_LIBRARY_URL}\n"
            f"        and unzip it so that {ldraw_dir}/parts/ exists."
        )
        return False

    if ldraw_library_present(ldraw_dir):
        _log(f"[ldraw] installed at {ldraw_dir}")
        return True
    _log(f"[ldraw] extraction finished but {ldraw_dir}/parts/ not found — check layout")
    return False


def detect_blender_addon_dirs() -> list[Path]:
    """Find Blender addons dirs under ~/.config/blender/<version>/scripts/addons."""
    base = Path.home() / ".config" / "blender"
    dirs: list[Path] = []
    if base.is_dir():
        for version_dir in sorted(base.iterdir()):
            if version_dir.is_dir():
                dirs.append(version_dir / "scripts" / "addons")
    return dirs


def _find_addon_asset_url() -> str | None:
    req = urllib.request.Request(
        IMPORTLDRAW_LATEST_API, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - fixed https URL
        release = json.load(resp)
    for asset in release.get("assets", []):
        if asset.get("name", "").endswith(".zip"):
            url = asset.get("browser_download_url")
            break
    else:
        # fall back to the auto-generated source zip
        url = release.get("zipball_url")
    # the URL is data from the API response: enforce https rather than trust provenance
    if url and not url.startswith("https://"):
        _log(f"[addon] refusing non-https asset URL from the release API: {url!r}")
        return None
    return url


def _addon_root_in_zip(zf: zipfile.ZipFile) -> str | None:
    """Return the path prefix inside the zip that contains the addon package.

    The addon package is the directory containing __init__.py that also
    holds loadldraw/ (or is named importldraw/io_scene_importldraw).
    """
    names = zf.namelist()
    # Look for an __init__.py at depth 1 or 2 whose sibling tree looks like
    # the addon.
    inits = [n for n in names if n.endswith("__init__.py")]
    # Prefer the shallowest __init__.py.
    inits.sort(key=lambda n: n.count("/"))
    for init in inits:
        prefix = init[: -len("__init__.py")]
        return prefix
    return None


def install_importldraw_addon(addon_dirs: list[Path], force: bool = False) -> bool:
    """Download the latest ImportLDraw addon and install into each addon dir."""
    if not addon_dirs:
        _log(
            "[addon] no Blender addons directory detected under "
            "~/.config/blender/<version>/scripts/addons/.\n"
            "[addon] manual step: launch Blender once (creates the config dir), "
            "then re-run ldraw-mcp-setup, OR install the addon by hand:\n"
            f"          download the latest release zip from {IMPORTLDRAW_RELEASES_PAGE}\n"
            "          and use Blender > Edit > Preferences > Add-ons > Install."
        )
        return False

    target_name = "io_scene_importldraw"
    already = [d for d in addon_dirs if (d / target_name).is_dir()]
    if already and not force:
        _log(
            f"[addon] {target_name} already installed in: "
            + ", ".join(str(d) for d in already)
            + " — skipping (use --force to reinstall)"
        )
        return True

    _log(f"[addon] querying {IMPORTLDRAW_LATEST_API} ...")
    try:
        asset_url = _find_addon_asset_url()
    except Exception as exc:  # noqa: BLE001
        _log(f"[addon] could not query GitHub release: {exc}")
        _log(
            f"[addon] manual step: download the addon zip from {IMPORTLDRAW_RELEASES_PAGE}\n"
            "          and install via Blender > Preferences > Add-ons > Install."
        )
        return False

    if not asset_url:
        _log("[addon] no downloadable zip asset found in the latest release")
        return False

    _log(f"[addon] downloading {asset_url} ...")
    try:
        req = urllib.request.Request(asset_url, headers={"User-Agent": "ldraw-mcp"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - https enforced in _find_addon_asset_url
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        _log(f"[addon] download FAILED: {exc}")
        return False

    ok = True
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            prefix = _addon_root_in_zip(zf)
            if prefix is None:
                _log("[addon] could not locate the addon package inside the zip")
                return False
            rels = [
                (n, n[len(prefix) :])
                for n in zf.namelist()
                if n.startswith(prefix) and not n.endswith("/") and n[len(prefix) :]
            ]
            for addon_dir in addon_dirs:
                dest = addon_dir / target_name
                plan = [(member, _contained(dest, rel)) for member, rel in rels]
                addon_dir.mkdir(parents=True, exist_ok=True)
                _log(f"[addon] installing into {dest}")
                for member, target in plan:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as out:
                        out.write(src.read())
                if not (dest / "__init__.py").exists():
                    _log(f"[addon] WARNING: {dest}/__init__.py missing after install")
                    ok = False
    except (zipfile.BadZipFile, ValueError) as exc:
        _log(f"[addon] refusing the downloaded archive: {exc}")
        _log(
            f"[addon] manual step: download the addon zip from {IMPORTLDRAW_RELEASES_PAGE}\n"
            "          and install via Blender > Preferences > Add-ons > Install."
        )
        return False
    if ok:
        _log(
            "[addon] installed. Enable it in Blender > Preferences > Add-ons if needed."
        )
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ldraw-mcp-setup",
        description="Install the LDraw parts library and ImportLDraw Blender addon.",
    )
    parser.add_argument(
        "--ldraw-dir",
        default=os.environ.get("LDRAW_LIBRARY_PATH") or str(Path.home() / ".ldraw"),
        help="Where to install the LDraw parts library (default: ~/.ldraw)",
    )
    parser.add_argument(
        "--skip-library", action="store_true", help="Don't install the LDraw library"
    )
    parser.add_argument(
        "--skip-addon", action="store_true", help="Don't install the ImportLDraw addon"
    )
    parser.add_argument(
        "--force", action="store_true", help="Reinstall even if present"
    )
    args = parser.parse_args()

    ldraw_dir = Path(args.ldraw_dir).expanduser()
    ok = True

    if not args.skip_library:
        ok = install_ldraw_library(ldraw_dir, force=args.force) and ok

    if not args.skip_addon:
        ok = (
            install_importldraw_addon(detect_blender_addon_dirs(), force=args.force)
            and ok
        )

    _log("")
    if ok:
        _log("Setup complete. Verify with:  ldraw-mcp   (then call check_renderer)")
        _log(
            'Or:  python -c "from ldraw_mcp.render import is_available; print(is_available())"'
        )
    else:
        _log("Setup finished with warnings — see messages above for manual steps.")
        sys.exit(1)


if __name__ == "__main__":
    main()
