"""
Read-only queries over LDraw models and the LDraw library: bill of materials,
colour lookup, part search. No Blender involved; only the parts library, and
only for names - a BOM is still counted correctly without one.

The LDraw rules this module leans on (https://www.ldraw.org/article/218.html,
https://www.ldraw.org/article/47.html):

  - a type-1 line is `1 <colour> x y z a b c d e f g h i <file>`; the file
    name is everything after the 14th token and may contain spaces;
  - file names are case-insensitive and may use `\\` as the separator;
  - an MPD holds several files, each opened by `0 FILE <name>`; the first is
    the main model;
  - colour 16 means "the colour of the line that referenced me"; 24 is the
    matching edge colour and is left as 24 in the rows, since it names no
    colour of its own.

A reference is a sub-model when an MPD section (or, for a model read from disk,
a sibling .ldr/.mpd file) carries that name; anything else is a part and is a
leaf - part files are never descended into.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MAIN_COLOUR = 16
EDGE_COLOUR = 24

# The render path has no input caps to be consistent with (Blender reads the
# file itself), so these are set here. Quantities are summed per sub-model and
# multiplied up, never expanded instance by instance, so nesting depth is the
# only thing that can grow without bound: 64 is far above any real model (the
# deepest OMR sets nest under 10) and far below Python's recursion limit.
MAX_MODEL_BYTES = 32 * 1024 * 1024
MAX_SUBMODEL_DEPTH = 64
MAX_SEARCH_RESULTS = 200

_MODEL_SUFFIXES = (".ldr", ".mpd")


class LDrawModelError(Exception):
    """The model (or the library) cannot be read as asked."""


class LDrawReferenceCycleError(LDrawModelError):
    """A sub-model references itself, directly or through other sub-models."""


class LDrawLibraryError(LDrawModelError):
    """The LDraw library, or the file needed from it, is not there."""


def normalise_name(name: str) -> str:
    """LDraw file names compare case-insensitively, with either separator."""
    return name.strip().replace("\\", "/").lower()


def parse_colour_code(token: str) -> int:
    """A colour token: decimal code, or a direct colour `0x2RRGGBB`."""
    text = token.strip().lower()
    if text.startswith("0x"):
        return int(text, 16)
    return int(text)


def direct_colour_rgb(code: int) -> str | None:
    """`#RRGGBB` for a direct colour (0x2RRGGBB), else None."""
    if 0x2000000 <= code <= 0x2FFFFFF:
        return f"#{code & 0xFFFFFF:06X}"
    return None


# --------------------------------------------------------------------------
# model parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ref:
    colour: int
    name: str  # normalised
    line: int


def _split_sections(text: str, origin: str) -> tuple[str, dict[str, list[_Ref]]]:
    """Return (main section name, {section name: its type-1 references})."""
    sections: dict[str, list[_Ref]] = {}
    main = normalise_name(origin)
    current: list[_Ref] | None = None
    in_data = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        tokens = raw.split()
        if not tokens:
            continue
        if tokens[0] == "0":
            meta = tokens[1].upper() if len(tokens) > 1 else ""
            if meta == "FILE" or meta == "!DATA":
                name = normalise_name(raw.split(None, 2)[2]) if len(tokens) > 2 else ""
                if not name:
                    raise LDrawModelError(
                        f"{origin}:{lineno}: `0 {meta}` without a file name"
                    )
                in_data = meta == "!DATA"
                if in_data:
                    current = None
                    continue
                if name in sections:
                    raise LDrawModelError(
                        f"{origin}:{lineno}: sub-model {name!r} is defined twice"
                    )
                if not sections:
                    main = name
                current = sections.setdefault(name, [])
            elif meta == "NOFILE":
                current, in_data = None, False
            continue
        if tokens[0] != "1" or in_data:
            continue
        parts = raw.split(None, 14)
        if len(parts) < 15:
            raise LDrawModelError(
                f"{origin}:{lineno}: type-1 line has {len(parts)} fields, needs 15"
            )
        try:
            colour = parse_colour_code(parts[1])
        except ValueError:
            raise LDrawModelError(
                f"{origin}:{lineno}: colour {parts[1]!r} is neither a code nor 0x2RRGGBB"
            ) from None
        if current is None:
            if sections:
                # after `0 NOFILE`: belongs to no file
                raise LDrawModelError(
                    f"{origin}:{lineno}: part reference outside any `0 FILE` section"
                )
            current = sections.setdefault(main, [])
        current.append(_Ref(colour, normalise_name(parts[14]), lineno))

    if not sections:
        sections[main] = []
    return main, sections


def _read_model(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_MODEL_BYTES:
        raise LDrawModelError(
            f"{path} is {size} bytes; the limit for a model is {MAX_MODEL_BYTES}"
        )
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LDrawModelError(f"{path} is not UTF-8 text: {exc}") from exc


class _Model:
    """Sections of one model plus, for a model on disk, its sibling files."""

    def __init__(self, text: str, origin: str, directory: Path | None):
        if len(text.encode("utf-8")) > MAX_MODEL_BYTES:
            raise LDrawModelError(
                f"model text exceeds the {MAX_MODEL_BYTES}-byte limit for a model"
            )
        self.directory = directory.resolve() if directory else None
        self.main, self.sections = _split_sections(text, origin)
        self._memo: dict[str, Counter] = {}
        self._scopes: dict[str, tuple[str, str]] = {}
        self._loaded_siblings: dict[str, str | None] = {}

    def _sibling(self, name: str) -> str | None:
        """Section key for a sibling model file named `name`, loading it once.

        Only files inside the model's own directory tree are read: a reference
        is data from the model, and `..\\..\\x.ldr` must not walk out of it.
        """
        if self.directory is None or not name.endswith(_MODEL_SUFFIXES):
            return None
        if name in self._loaded_siblings:
            return self._loaded_siblings[name]
        found = self.directory
        for segment in name.split("/"):
            if segment in ("", ".", ".."):
                raise LDrawModelError(
                    f"reference {name!r} leaves the model's directory; refusing to follow it"
                )
            matches = (
                [c for c in found.iterdir() if c.name.lower() == segment]
                if found.is_dir()
                else []
            )
            if not matches:
                self._loaded_siblings[name] = None
                return None
            found = matches[0]
        if not found.resolve().is_relative_to(self.directory):
            raise LDrawModelError(
                f"reference {name!r} resolves outside the model's directory; refusing to follow it"
            )
        key = f"file:{name}"
        sub_main, sub_sections = _split_sections(_read_model(found), name)
        # The sibling's own MPD sections are visible only under its prefix, so
        # they cannot shadow (or be shadowed by) the referencing model's.
        for sec_name, refs in sub_sections.items():
            self.sections[f"{key}#{sec_name}"] = refs
        self._scopes[key] = (f"{key}#", sub_main)
        self._loaded_siblings[name] = key
        return key

    def count(self) -> Counter:
        return self._count(self.main, "", [])

    def _resolve(self, name: str, scope: str) -> tuple[str, str] | None:
        """(section key, scope for that section's own references), or None = part."""
        if scope + name in self.sections:
            return scope + name, scope
        key = self._sibling(name)
        if key is None:
            return None
        prefix, sub_main = self._scopes[key]
        return prefix + sub_main, prefix

    def _count(self, section: str, scope: str, stack: list[str]) -> Counter:
        """{(part, colour): quantity} for one section, colour 16 left symbolic."""
        if section in stack:
            chain = " -> ".join([*stack[stack.index(section) :], section])
            raise LDrawReferenceCycleError(f"sub-model reference cycle: {chain}")
        if section in self._memo:
            return self._memo[section]
        if len(stack) >= MAX_SUBMODEL_DEPTH:
            raise LDrawModelError(
                f"sub-models nest deeper than {MAX_SUBMODEL_DEPTH}: {' -> '.join(stack[:4])} ..."
            )
        total: Counter = Counter()
        for ref in self.sections[section]:
            target = self._resolve(ref.name, scope)
            if target is None:
                total[(ref.name, ref.colour)] += 1
                continue
            child = self._count(target[0], target[1], [*stack, section])
            for (part, colour), qty in child.items():
                inherited = ref.colour if colour == MAIN_COLOUR else colour
                total[(part, inherited)] += qty
        self._memo[section] = total
        return total


# --------------------------------------------------------------------------
# library: colours and part headers
# --------------------------------------------------------------------------


def parse_ldconfig(text: str) -> dict[int, dict]:
    """`0 !COLOUR` lines of an LDConfig.ldr, keyed by code."""
    colours: dict[int, dict] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        tokens = raw.split()
        if len(tokens) < 3 or tokens[0] != "0" or tokens[1].upper() != "!COLOUR":
            continue
        fields = {}
        for key in ("CODE", "VALUE", "EDGE", "ALPHA", "LUMINANCE"):
            if key in tokens[3:]:
                idx = tokens.index(key, 3)
                if idx + 1 >= len(tokens):
                    raise LDrawLibraryError(
                        f"LDConfig.ldr:{lineno}: {key} has no value"
                    )
                fields[key] = tokens[idx + 1]
        if "CODE" not in fields or "VALUE" not in fields:
            raise LDrawLibraryError(
                f"LDConfig.ldr:{lineno}: !COLOUR without CODE and VALUE"
            )
        finish = next(
            (
                t
                for t in tokens[3:]
                if t
                in (
                    "CHROME",
                    "PEARLESCENT",
                    "RUBBER",
                    "MATTE_METALLIC",
                    "METAL",
                    "MATERIAL",
                )
            ),
            None,
        )
        code = int(fields["CODE"])
        colours[code] = {
            "code": code,
            "name": tokens[2],
            "rgb": fields["VALUE"].upper(),
            "edge": fields.get("EDGE", "").upper() or None,
            "alpha": int(fields["ALPHA"]) if "ALPHA" in fields else None,
            "finish": finish,
        }
    return colours


@lru_cache(maxsize=4)
def _colours_cached(config_path: str, mtime_ns: int) -> dict[int, dict]:
    return parse_ldconfig(Path(config_path).read_text(encoding="utf-8-sig"))


def load_colours(library: Path) -> dict[int, dict]:
    config = library / "LDConfig.ldr"
    if not config.is_file():
        raise LDrawLibraryError(
            f"no LDConfig.ldr in the LDraw library at {library}; run `ldraw-mcp-setup`"
        )
    return _colours_cached(str(config), config.stat().st_mtime_ns)


def _require_library(library: Path | None) -> Path:
    if library is None:
        raise LDrawLibraryError(
            "no LDraw library found (run `ldraw-mcp-setup` or set LDRAW_LIBRARY_PATH)"
        )
    return library


def _header_description(part_file: Path) -> str:
    """First line of a part file, without its leading `0`."""
    with part_file.open(encoding="utf-8-sig", errors="replace") as fh:
        first = fh.readline().strip()
    return first[1:].strip() if first.startswith("0") else first


def part_description(library: Path, name: str) -> str | None:
    """Description of `parts/<name>`, or None when the library has no such part."""
    rel = normalise_name(name)
    if any(seg in ("", ".", "..") for seg in rel.split("/")):
        return None
    candidate = library / "parts" / rel
    return _header_description(candidate) if candidate.is_file() else None


# --------------------------------------------------------------------------
# the three queries
# --------------------------------------------------------------------------


def _colour_fields(code: int, colours: dict[int, dict] | None) -> dict:
    direct = direct_colour_rgb(code)
    if direct:
        return {"color_name": None, "color_rgb": direct, "color_known": True}
    if colours is None:
        return {"color_name": None, "color_rgb": None, "color_known": None}
    entry = colours.get(code)
    if entry is None:
        return {"color_name": None, "color_rgb": None, "color_known": False}
    return {"color_name": entry["name"], "color_rgb": entry["rgb"], "color_known": True}


def bill_of_materials(
    *,
    path: Path | None = None,
    text: str | None = None,
    library: Path | None = None,
) -> dict:
    """Part x colour x quantity rows for a model file or inline model text."""
    if (path is None) == (text is None):
        raise ValueError("give exactly one of `path` and `ldr`")
    if path is not None:
        model = _Model(_read_model(path), path.name, path.parent)
    else:
        model = _Model(text or "", "inline.ldr", None)
    counts = model.count()

    colours = load_colours(library) if library is not None else None
    rows = []
    for (part, code), qty in sorted(counts.items()):
        description = part_description(library, part) if library is not None else None
        rows.append(
            {
                "part": part,
                "description": description,
                "in_library": (description is not None)
                if library is not None
                else None,
                "color_code": code,
                **_colour_fields(code, colours),
                "quantity": qty,
            }
        )
    return {
        "model": model.main,
        "library": str(library) if library is not None else None,
        "rows": rows,
        "total_parts": sum(counts.values()),
        "unknown_color_codes": sorted(
            {r["color_code"] for r in rows if r["color_known"] is False}
        ),
        "parts_not_in_library": sorted(
            {r["part"] for r in rows if r["in_library"] is False}
        ),
    }


def _squash(name: str) -> str:
    return name.lower().replace("_", "").replace(" ", "").replace("-", "")


def lookup_colour(query: str, library: Path | None) -> dict:
    """Colours by code (`4`, `0x2FF8800`) or by name substring (`dark blue`)."""
    wanted = query.strip()
    if not wanted:
        raise ValueError("empty colour query; give a code or part of a name")
    colours = load_colours(_require_library(library))
    try:
        code = parse_colour_code(wanted)
    except ValueError:
        needle = _squash(wanted)
        matches = [c for c in colours.values() if needle in _squash(c["name"])]
        matches.sort(key=lambda c: (_squash(c["name"]) != needle, c["code"]))
        return {"query": query, "by": "name", "matches": matches}
    direct = direct_colour_rgb(code)
    if direct:
        entry = {
            "code": code,
            "name": None,
            "rgb": direct,
            "edge": None,
            "alpha": None,
            "finish": None,
            "direct": True,
        }
        return {"query": query, "by": "code", "matches": [entry]}
    return {
        "query": query,
        "by": "code",
        "matches": [colours[code]] if code in colours else [],
    }


# {part file path: (mtime_ns, size, description)}. Keyed per FILE, not on the
# directory's mtime: a part rewritten in place under the same name (what
# `ldraw-mcp-setup --force` does) leaves the directory's mtime untouched, and
# this process outlives such an update. Every search stats every part (one
# scandir, ~24k entries) and re-reads only the headers that changed.
_HEADER_CACHE: dict[str, tuple[int, int, str]] = {}


def _part_index(parts_dir: Path) -> list[tuple[str, str]]:
    entries = []
    with os.scandir(parts_dir) as listing:
        for entry in listing:
            if not entry.name.lower().endswith(".dat") or not entry.is_file():
                continue
            stat = entry.stat()
            cached = _HEADER_CACHE.get(entry.path)
            if cached is None or cached[:2] != (stat.st_mtime_ns, stat.st_size):
                cached = (
                    stat.st_mtime_ns,
                    stat.st_size,
                    _header_description(Path(entry.path)),
                )
                _HEADER_CACHE[entry.path] = cached
            entries.append((entry.name.lower(), cached[2]))
    return sorted(entries)


def search_parts(query: str, library: Path | None, limit: int = 50) -> dict:
    """Parts whose header description (or file name) contains the query."""
    needle = " ".join(query.lower().split())
    if not needle:
        raise ValueError("empty part query; give one or more words from a description")
    if not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise ValueError(
            f"limit must be between 1 and {MAX_SEARCH_RESULTS}, got {limit}"
        )
    parts_dir = _require_library(library) / "parts"
    index = _part_index(parts_dir)
    hits = [
        {"part": name, "description": desc}
        for name, desc in index
        # descriptions pad numbers ("Brick  2 x  4"); compare on collapsed spaces
        if needle in name or needle in " ".join(desc.lower().split())
    ]
    return {
        "query": query,
        "total_matches": len(hits),
        "truncated": len(hits) > limit,
        "parts": hits[:limit],
    }
