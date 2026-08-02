# Contributing

Thanks for helping improve `ldraw-mcp`. It is an MCP server that renders
LDraw/LEGO models to images with real part geometry, via headless Blender and the
ImportLDraw addon.

## Dev setup

Requires Python >=3.10.

```bash
pip install -e ".[test]"
```

This is a **pip/hatchling** project and has no `uv.lock` — that is deliberate, not
an omission, and CI installs it exactly the way above. Do not add a lockfile
without changing CI to match.

Rendering additionally needs Blender, the ImportLDraw addon and an LDraw parts
library. The bundled setup CLI wires those up:

```bash
ldraw-mcp-setup
```

`check_renderer` reports all three by name, and is the first thing to run when a
render fails.

## Running the tests

```bash
pytest -q
```

**The suite must pass on a machine with no Blender.** Tests that genuinely need a
renderer are skipped there — but see the rule below, because that skip is exactly
where this repo has been bitten.

## Testing rules

These share one idea: **a test is worth only what it can fail on.**

### 1. A module-level skip can hide a control that never runs

The blank-frame detector's control test sat under a module-level skip and
therefore **never executed in CI**, while reading as a passing suite. `numpy` is
declared in the `test` extra specifically so that control can run on a
Blender-less runner. If you add a guard, make sure the test proving the guard can
fail actually runs somewhere.

### 2. Never trust a test you have not seen fail

Run a new test against the broken state — for a fix, the pre-fix code you still
have — and confirm it fails *for the stated reason*. "Some exception was raised"
passes against broken code too.

### 3. A negative result needs a positive control

A test asserting something is refused must also assert, in the same test, that
the legitimate path still succeeds. Otherwise a harness that fails on everything
reads as a working guard.

### 4. Renders need an output postcondition

A render that produces no image, or an entirely blank one, must fail loudly. A
subprocess exiting 0 is not evidence that a frame was produced — that is the
gap the blank-frame guard exists to close.

## Pull requests

- Update `CHANGELOG.md` for any user-facing change (Keep a Changelog format).
- Update the README if tool signatures or configuration change.
- Version lives in `pyproject.toml`, `server.json` and `CITATION.cff`; a release
  PR moves them together.
- Dependency bounds here are **deliberate**. `mcp>=2,<3` moves rather than widens:
  a range spanning both majors can resolve to a version that cannot import the
  server, which is exactly what a dependabot PR once proposed and why it broke
  the build. Do not widen a bound to make a resolver happy.
- Fail loud. No silent fallbacks, no swallowed errors.
