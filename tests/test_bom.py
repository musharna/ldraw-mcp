"""bill_of_materials / lookup_color / search_parts.

`tests/data/ldraw/` is a slice of the REAL LDraw library, copied verbatim from
complete.zip (LDConfig.ldr UPDATE 2026-05-29, parts 3001/3002/3004; CC BY 4.0,
see ATTRIBUTION.txt there). CI has no full library, so these files are what
lets the header and LDConfig parsers meet the real format on every run. The
full-library tests at the bottom run wherever `ldraw-mcp-setup` has been run.

The MPD fixtures are hand-written because their whole point is a count known
in advance.
"""

import asyncio
from pathlib import Path

import pytest
from mcp.client import Client

from ldraw_mcp import bom
from ldraw_mcp import render as ldraw_render
from ldraw_mcp.server import mcp

_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_LIBRARY = _ROOT / "tests" / "data" / "ldraw"


def _line(colour, name, x=0):
    return f"1 {colour} {x} 0 0 1 0 0 0 1 0 0 0 1 {name}"


# S holds 2x 3001 in colour 16 and 1x 3002 in colour 4; main references S three
# times: once in colour 1, twice in colour 14.
KNOWN_ANSWER_MPD = "\n".join(
    [
        "0 FILE main.ldr",
        "0 Main",
        _line(1, "s.ldr"),
        _line(14, "s.ldr", 40),
        _line(14, "s.ldr", 80),
        "0 NOFILE",
        "0 FILE s.ldr",
        "0 Sub",
        _line(16, "3001.dat"),
        _line(16, "3001.dat", 40),
        "0 STEP",
        _line(4, "3002.dat"),
        "2 24 0 0 0 1 1 1",
        "",
    ]
)


def _rows(result):
    return {(r["part"], r["color_code"]): r["quantity"] for r in result["rows"]}


def test_submodel_multiplicity_and_colour_inheritance_known_answer():
    result = bom.bill_of_materials(text=KNOWN_ANSWER_MPD)

    assert _rows(result) == {
        ("3001.dat", 1): 2,
        ("3001.dat", 14): 4,
        ("3002.dat", 4): 3,
    }
    assert result["total_parts"] == 9
    assert result["model"] == "main.ldr"


def test_inheritance_passes_through_two_levels_and_top_level_16_stays_16():
    text = "\n".join(
        [
            "0 FILE top.ldr",
            _line(2, "mid.ldr"),
            _line(16, "3004.dat"),
            "0 FILE mid.ldr",
            _line(16, "leaf.ldr"),
            _line(16, "leaf.ldr"),
            "0 FILE leaf.ldr",
            _line(16, "3001.dat"),
            _line(24, "3002.dat"),
        ]
    )
    assert _rows(bom.bill_of_materials(text=text)) == {
        ("3001.dat", 2): 2,
        ("3002.dat", 24): 2,
        ("3004.dat", 16): 1,
    }


def test_reference_names_are_case_insensitive_and_accept_backslashes():
    text = "\n".join(
        [
            "0 FILE Main.LDR",
            _line(1, "SUB\\Wing Left.LDR"),
            _line(1, "3001.DAT"),
            "0 FILE sub/wing left.ldr",
            _line(16, "3001.dat"),
            _line(16, "S\\3002s01.DAT"),
        ]
    )
    assert _rows(bom.bill_of_materials(text=text)) == {
        ("3001.dat", 1): 2,
        ("s/3002s01.dat", 1): 1,
    }


def test_reference_cycle_raises_the_named_error_and_the_acyclic_variant_counts():
    def model(b_refers_to):
        return "\n".join(
            [
                "0 FILE main.ldr",
                _line(1, "a.ldr"),
                "0 FILE a.ldr",
                _line(16, "b.ldr"),
                "0 FILE b.ldr",
                _line(16, b_refers_to),
            ]
        )

    with pytest.raises(bom.LDrawReferenceCycleError) as cycle:
        bom.bill_of_materials(text=model("A.LDR"))
    assert "a.ldr -> b.ldr -> a.ldr" in str(cycle.value)

    assert _rows(bom.bill_of_materials(text=model("3001.dat"))) == {("3001.dat", 1): 1}


def test_a_shared_submodel_is_not_a_cycle():
    """A diamond (main -> a, main -> b, a -> c, b -> c) revisits c without a
    cycle. A cycle check on "seen before" rather than "on the current path"
    refuses this model."""
    text = "\n".join(
        [
            "0 FILE main.ldr",
            _line(1, "a.ldr"),
            _line(4, "b.ldr"),
            "0 FILE a.ldr",
            _line(16, "c.ldr"),
            "0 FILE b.ldr",
            _line(16, "c.ldr"),
            "0 FILE c.ldr",
            _line(16, "3001.dat"),
        ]
    )
    assert _rows(bom.bill_of_materials(text=text)) == {
        ("3001.dat", 1): 1,
        ("3001.dat", 4): 1,
    }


def test_nesting_beyond_the_cap_is_refused_and_at_the_cap_is_counted():
    def chain(depth):
        lines = []
        for i in range(depth):
            lines += [f"0 FILE m{i}.ldr", _line(16, f"m{i + 1}.ldr")]
        lines += [f"0 FILE m{depth}.ldr", _line(16, "3001.dat")]
        return "\n".join(lines)

    with pytest.raises(bom.LDrawModelError, match="nest deeper than 64"):
        bom.bill_of_materials(text=chain(bom.MAX_SUBMODEL_DEPTH + 1))

    assert _rows(bom.bill_of_materials(text=chain(bom.MAX_SUBMODEL_DEPTH - 1))) == {
        ("3001.dat", 16): 1
    }


def test_exponential_multiplicity_is_counted_without_expanding_instances():
    """30 levels, each referencing the next twice: 2**30 bricks. Counted per
    sub-model and multiplied, this is 60 additions; expanded per instance it
    does not finish."""
    lines = []
    for i in range(30):
        lines += [
            f"0 FILE m{i}.ldr",
            _line(16, f"m{i + 1}.ldr"),
            _line(16, f"m{i + 1}.ldr"),
        ]
    lines += ["0 FILE m30.ldr", _line(16, "3001.dat")]

    assert _rows(bom.bill_of_materials(text="\n".join(lines))) == {
        ("3001.dat", 16): 2**30
    }


def test_a_malformed_type_1_line_is_refused_with_its_line_number():
    good = "0 model\n" + _line(4, "3001.dat") + "\n"
    assert _rows(bom.bill_of_materials(text=good)) == {("3001.dat", 4): 1}

    with pytest.raises(
        bom.LDrawModelError, match=r"inline\.ldr:3: type-1 line has 5 fields"
    ):
        bom.bill_of_materials(text=good + "1 4 0 0 0\n")
    with pytest.raises(bom.LDrawModelError, match=r"inline\.ldr:3: colour 'red'"):
        bom.bill_of_materials(text=good + _line("red", "3001.dat") + "\n")


def test_sibling_model_files_are_followed_but_not_out_of_the_directory(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "Wheel.LDR").write_text(
        _line(16, "3002.dat") + "\n" + _line(0, "3004.dat") + "\n"
    )
    (tmp_path / "outside.ldr").write_text(_line(16, "3001.dat") + "\n")
    main = models / "car.ldr"

    main.write_text(_line(4, "wheel.ldr") + "\n" + _line(4, "wheel.ldr", 40) + "\n")
    assert _rows(bom.bill_of_materials(path=main)) == {
        ("3002.dat", 4): 2,
        ("3004.dat", 0): 2,
    }

    main.write_text(_line(4, "..\\outside.ldr") + "\n")
    with pytest.raises(bom.LDrawModelError, match="leaves the model's directory"):
        bom.bill_of_materials(path=main)


def test_a_symlink_out_of_the_model_directory_is_refused(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (tmp_path / "secret.ldr").write_text(_line(16, "3001.dat") + "\n")
    (models / "inside.ldr").write_text(_line(16, "3002.dat") + "\n")
    (models / "link.ldr").symlink_to(tmp_path / "secret.ldr")
    main = models / "main.ldr"

    main.write_text(_line(4, "link.ldr") + "\n")
    with pytest.raises(
        bom.LDrawModelError, match="resolves outside the model's directory"
    ):
        bom.bill_of_materials(path=main)

    main.write_text(_line(4, "inside.ldr") + "\n")
    assert _rows(bom.bill_of_materials(path=main)) == {("3002.dat", 4): 1}


def test_sibling_file_cycle_is_the_named_error(tmp_path):
    (tmp_path / "a.ldr").write_text(_line(16, "b.ldr") + "\n")
    (tmp_path / "b.ldr").write_text(_line(16, "a.ldr") + "\n")
    with pytest.raises(bom.LDrawReferenceCycleError):
        bom.bill_of_materials(path=tmp_path / "a.ldr")

    (tmp_path / "b.ldr").write_text(_line(16, "3001.dat") + "\n")
    assert _rows(bom.bill_of_materials(path=tmp_path / "a.ldr")) == {
        ("3001.dat", 16): 1
    }


# --------------------------------------------------------------------------
# real library data (the verbatim slice in tests/data/ldraw)
# --------------------------------------------------------------------------


def test_names_come_from_the_real_ldconfig_and_part_headers():
    result = bom.bill_of_materials(text=KNOWN_ANSWER_MPD, library=FIXTURE_LIBRARY)
    by_key = {(r["part"], r["color_code"]): r for r in result["rows"]}

    blue = by_key[("3001.dat", 1)]
    assert (blue["description"], blue["color_name"], blue["color_rgb"]) == (
        "Brick  2 x  4",
        "Blue",
        "#1E5AA8",
    )
    assert by_key[("3001.dat", 14)]["color_name"] == "Yellow"
    red = by_key[("3002.dat", 4)]
    assert (red["description"], red["color_name"]) == ("Brick  2 x  3", "Red")
    assert result["unknown_color_codes"] == []
    assert result["parts_not_in_library"] == []


def test_the_repos_own_model_against_the_real_slice():
    """tools/models/asymmetric.ldr is the model the azimuth measurements use."""
    result = bom.bill_of_materials(
        path=_ROOT / "tools" / "models" / "asymmetric.ldr", library=FIXTURE_LIBRARY
    )
    assert [
        (r["part"], r["description"], r["color_name"], r["quantity"])
        for r in result["rows"]
    ] == [
        ("3001.dat", "Brick  2 x  4", "Red", 1),
        ("3002.dat", "Brick  2 x  3", "Blue", 1),
        ("3004.dat", "Brick  1 x  2", "Green", 1),
    ]


def test_an_unknown_colour_and_a_missing_part_are_reported_not_dropped():
    text = "\n".join(
        [
            _line(4, "3001.dat"),
            _line(9999, "3001.dat"),
            _line("0x2FF8800", "3001.dat"),
            _line(4, "nosuchpart.dat"),
        ]
    )
    result = bom.bill_of_materials(text=text, library=FIXTURE_LIBRARY)
    by_key = {(r["part"], r["color_code"]): r for r in result["rows"]}

    assert result["total_parts"] == 4
    assert result["unknown_color_codes"] == [9999]
    assert by_key[("3001.dat", 9999)]["color_known"] is False
    assert by_key[("3001.dat", 9999)]["color_name"] is None
    assert by_key[("3001.dat", 4)]["color_known"] is True
    direct = by_key[("3001.dat", 0x2FF8800)]
    assert (direct["color_known"], direct["color_rgb"]) == (True, "#FF8800")
    assert result["parts_not_in_library"] == ["nosuchpart.dat"]
    assert by_key[("nosuchpart.dat", 4)]["quantity"] == 1


def test_without_a_library_counts_are_the_same_and_names_are_null():
    result = bom.bill_of_materials(text=KNOWN_ANSWER_MPD, library=None)
    assert _rows(result) == {
        ("3001.dat", 1): 2,
        ("3001.dat", 14): 4,
        ("3002.dat", 4): 3,
    }
    assert {r["description"] for r in result["rows"]} == {None}
    assert {r["color_known"] for r in result["rows"]} == {None}
    assert result["library"] is None


def test_the_real_ldconfig_parses_whole():
    colours = bom.load_colours(FIXTURE_LIBRARY)
    config = (FIXTURE_LIBRARY / "LDConfig.ldr").read_text()
    declared = sum(1 for ln in config.splitlines() if ln.startswith("0 !COLOUR"))

    assert declared > 200  # the real file, not a stub
    assert len(colours) == declared
    assert colours[16]["name"] == "Main_Colour"
    assert colours[24]["name"] == "Edge_Colour"
    assert colours[36] == {
        "code": 36,
        "name": "Trans_Red",
        "rgb": "#C91A09",
        "edge": "#660D05",
        "alpha": 128,
        "finish": None,
    }
    assert colours[61]["finish"] == "CHROME"


def test_lookup_colour_by_code_by_name_and_unknown():
    by_code = bom.lookup_colour("4", FIXTURE_LIBRARY)
    assert [(m["code"], m["name"], m["rgb"]) for m in by_code["matches"]] == [
        (4, "Red", "#B40000")
    ]

    by_name = bom.lookup_colour("dark blue", FIXTURE_LIBRARY)
    names = [m["name"] for m in by_name["matches"]]
    assert by_name["by"] == "name"
    assert names[0] == "Dark_Blue"  # the exact name sorts first
    assert "Dark_Blue_Violet" in names and "Red" not in names

    assert bom.lookup_colour("9999", FIXTURE_LIBRARY)["matches"] == []
    assert bom.lookup_colour("no such colour name", FIXTURE_LIBRARY)["matches"] == []
    assert (
        bom.lookup_colour("0x2FF8800", FIXTURE_LIBRARY)["matches"][0]["rgb"]
        == "#FF8800"
    )


def test_lookup_and_search_refuse_without_a_library_and_work_with_one():
    with pytest.raises(bom.LDrawLibraryError, match="ldraw-mcp-setup"):
        bom.lookup_colour("4", None)
    with pytest.raises(bom.LDrawLibraryError, match="ldraw-mcp-setup"):
        bom.search_parts("brick", None)

    assert bom.lookup_colour("4", FIXTURE_LIBRARY)["matches"][0]["name"] == "Red"
    assert bom.search_parts("brick", FIXTURE_LIBRARY)["total_matches"] == 3


def test_search_parts_matches_descriptions_with_collapsed_spaces_and_truncates():
    hit = bom.search_parts("BRICK 2 x 4", FIXTURE_LIBRARY)
    assert hit["parts"] == [{"part": "3001.dat", "description": "Brick  2 x  4"}]
    assert hit["truncated"] is False

    by_file = bom.search_parts("3004", FIXTURE_LIBRARY)
    assert [p["part"] for p in by_file["parts"]] == ["3004.dat"]

    capped = bom.search_parts("brick", FIXTURE_LIBRARY, limit=2)
    assert (capped["total_matches"], len(capped["parts"]), capped["truncated"]) == (
        3,
        2,
        True,
    )

    assert bom.search_parts("windscreen", FIXTURE_LIBRARY)["parts"] == []
    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        bom.search_parts("brick", FIXTURE_LIBRARY, limit=201)


# --------------------------------------------------------------------------
# through the protocol
# --------------------------------------------------------------------------


async def _call(name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _text(result):
    return "".join(b.text for b in result.content if hasattr(b, "text"))


def test_tools_are_registered_and_answer_through_a_real_client(monkeypatch):
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(FIXTURE_LIBRARY))

    async def listed():
        async with Client(mcp) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert {"bill_of_materials", "lookup_color", "search_parts"} <= asyncio.run(
        listed()
    )

    result = asyncio.run(_call("bill_of_materials", {"ldr": KNOWN_ANSWER_MPD}))
    assert not result.is_error, _text(result)
    assert _rows(result.structured_content) == {
        ("3001.dat", 1): 2,
        ("3001.dat", 14): 4,
        ("3002.dat", 4): 3,
    }
    assert result.structured_content["library"] == str(FIXTURE_LIBRARY)

    colour = asyncio.run(_call("lookup_color", {"query": "14"}))
    assert colour.structured_content["matches"][0]["name"] == "Yellow"

    parts = asyncio.run(_call("search_parts", {"query": "brick 1 x 2"}))
    assert [p["part"] for p in parts.structured_content["parts"]] == ["3004.dat"]


def test_refusals_carry_their_text_through_the_protocol(monkeypatch, tmp_path):
    monkeypatch.setattr(ldraw_render, "ldraw_library_dir", lambda: None)
    cyclic = "0 FILE a.ldr\n" + _line(16, "a.ldr") + "\n"

    cycle = asyncio.run(_call("bill_of_materials", {"ldr": cyclic}))
    assert cycle.is_error
    assert "reference cycle: a.ldr -> a.ldr" in _text(cycle)

    missing = asyncio.run(
        _call("bill_of_materials", {"path": str(tmp_path / "nope.mpd")})
    )
    assert missing.is_error and "nope.mpd" in _text(missing)

    neither = asyncio.run(_call("bill_of_materials", {}))
    assert neither.is_error and "exactly one of" in _text(neither)

    no_library = asyncio.run(_call("lookup_color", {"query": "4"}))
    assert no_library.is_error and "ldraw-mcp-setup" in _text(no_library)

    # positive control: the same client, the same tool, a model that is fine
    fine = asyncio.run(_call("bill_of_materials", {"ldr": _line(4, "3001.dat")}))
    assert not fine.is_error
    assert _rows(fine.structured_content) == {("3001.dat", 4): 1}


# --------------------------------------------------------------------------
# the installed library, where there is one
# --------------------------------------------------------------------------


def _installed_library():
    library = ldraw_render.ldraw_library_dir()
    if (
        library
        and (library / "LDConfig.ldr").is_file()
        and (library / "parts" / "3001.dat").is_file()
    ):
        return library
    return None


needs_full_library = pytest.mark.skipif(
    _installed_library() is None,
    reason="no installed LDraw library (run ldraw-mcp-setup)",
)


@needs_full_library
def test_search_and_bom_against_the_installed_library():
    library = _installed_library()

    found = bom.search_parts("brick 2 x 4", library, limit=200)
    assert found["total_matches"] > 3  # the full library has many 2 x 4 variants
    assert {"part": "3001.dat", "description": "Brick  2 x  4"} in found["parts"]

    result = bom.bill_of_materials(
        path=_ROOT / "tools" / "models" / "symmetric.ldr", library=library
    )
    assert result["parts_not_in_library"] == []
    assert result["unknown_color_codes"] == []
    assert all(r["description"] for r in result["rows"])
