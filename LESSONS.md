# Lessons

One line per miss: date, the class of miss, the mechanism that now catches it.

- 2026-09-17 — A function's early-return branch (library already present) had no test at all; three mutants in it survived the nightly for a week. Caught now by `tests/test_setup_cli.py` (both arms in one test); the class is caught by the nightly mutation job's Haiku triage naming the missing test per gap (`nightly-guardrails.yml`).
- 2026-09-17 — A pass-through function (server._render → render_ldraw) had 19 survivors: every test ran with the renderer disabled, so the call was never observed. Caught now by `tests/test_server_render_call.py` (a spy asserting every argument and the returned bytes). Class: when the real dependency is stubbed out, at least one test must assert the CALL, not just the result.
