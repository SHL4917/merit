# AGENTS.md: developing MERIT itself

This is the tool repository. It holds no requirements data. Design: `docs/superpowers/specs/`. Plan: `docs/superpowers/plans/`.

## Commands

- Python 3.11+ only (`tomllib`). Create the venv with `py -3.12 -m venv .venv`.
- Install: `.venv\Scripts\python -m pip install -e ".[dev]"`
- Test: `.venv\Scripts\python -m pytest`
- Runtime dependencies are exactly `pyyaml` and `jsonschema`. `pytest` is dev-only.

## Exit codes (a contract)

`0` success, `1` validation failure, `2` usage error, `3` anchor failure, `4` environment error. Raise a `MeritError` subclass with a `fix` that is the exact next command; `cli.main` maps it.

## Invariants that span modules

- JSON Schemas cover structure, types, enums and patterns only, never cross-field rules. Every cross-field rule is a numbered check. Otherwise a fixture built to hit V6 fails V3 first.
- Every check registers under its own ID, so tests assert the right rule fired.
- `open_issues` and `awaiting_confirmation` are derived on read and never stored. Stored, an agent could clear a block by editing the thing being blocked.
- The tool never merges and never tags. Those stay human actions.
- `merit apply` is the only write path for governed artefacts. `merit ingest` is the single narrow, create-only exception and owns `sources/`.
- A provisional decision is a valid state: V9 permits a self-recorded note with no `confirmed_by`, and V8 blocks it from a baseline.
- A confirmation requires anchored evidence, not a name.
- All file I/O is explicitly UTF-8 (`encoding="utf-8"`). Never create a symlink: the target is Windows. Use `pathlib`.

## Non-obvious placement

- Schemas (`schemas/*.json`) and prompts (`prompts/*.md`) ship as package data, declared in `pyproject.toml`. Load them with `importlib.resources`, never a repo-relative path.
- The argparse tree in `cli.py` is the single source of truth for the agent manual. `help-agent` is generated from it, so never hand-write a flag list elsewhere.
