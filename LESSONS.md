# Lessons

One line per miss: date, the class of miss, the mechanism that now catches it.

- 2026-09-17 — A function's early-return branch (library already present) had no test at all; three mutants in it survived the nightly for a week. Caught now by `tests/test_setup_cli.py` (both arms in one test); the class is caught by the nightly mutation job's Haiku triage naming the missing test per gap (`nightly-guardrails.yml`).
