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
- V3 validates raw YAML before model loading; model validation failures become located V3 findings. Its structural gate precedes semantic checks. The runner reads artefacts from `artefact_root`, configuration/history from `context_root` (defaulting to the former).
- ID schemas reuse `model.ID_PATTERNS` exactly, including the portable strict `(?![\s\S])` terminator. Neither `$` nor Python-only `\Z` is equivalent across consumers.
- Decision record absence belongs to V9, so the schema/model permit it; display paths treat missing source metadata as provisional. Evidence cardinality and conditional reasons likewise belong to numbered checks, not V3.
- V1 owns missing source integrity metadata. The sidecar schema rejects unknown fields and validates present fields, but permits absent fields so V1 reports the integrity failure under its own ID.
- `open_issues` and `awaiting_confirmation` are derived on read and never stored. Stored, an agent could clear a block by editing the thing being blocked.
- The tool never merges and never tags. Those stay human actions.
- `merit apply` owns governed changes; `merit baseline` computes only its manifest through the same staging/validation/rollback machinery, without a public proposal operation. `merit ingest` is the create-only exception owning `sources/`.
- A provisional decision is a valid state: V9 permits a self-recorded note with no `confirmed_by`, and V8 blocks it from a baseline.
- A confirmation requires anchored evidence, not a name.
- V1 integrity, V2 immutability, V3 structure, V4 IDs, V5 anchors, V6 provenance/status, V7 issues, V8 derived issue blocks, V9 decisions, V10 readiness, V11 supersession, V12 re-extraction each keep their own check ID.
- V9 calls `model.confirmation_defect` for D20 metadata; both decision records pass through `anchor_evidence` in V5 and V9. Display predicates read metadata only; anchoring belongs to validation.
- Python `re` and ECMA-262 regex syntax differ: Python `$` admits a trailing newline and `\Z` is invalid under ECMA Unicode mode. Share the portable strict lookahead in `ID_PATTERNS`, never substitute either anchor.
- V11 reads `supersessions/`, never mutable source metadata. Non-baselined citations are advisory; baselined citations are errors. `baseline_blockers` returns all errors, never advisories.
- Raw validation order is V3, V1, V4 before the model gate; all remaining checks require safe models. Context/history reads use the real repository even when artefacts are staged.
- Apply handlers write only to staging. ID allocation reads staging; rollback consumes no IDs. The transaction calls `run_checks(staging, context_root=real_repo)` so V2/V4 retain their historical boundary and configuration. Never replace that call with validation of a bare temporary repo.
- Baselines select only requirements already marked `baselined`; draft/clarifying items block preparation, deferred items retain reasons, withdrawn items are excluded. Hash UTF-8 governed text with normalized line endings, but retained `sources/raw/` originals as exact bytes. Doctor enumerates tagged files with `files_at`, including extra files, and reads binary blobs through `file_bytes_at`.
- `add_decision` records issue status `resolved` and `resolved_by`; derived status still reads `awaiting_confirmation` for a provisional decision. Supersession writes a new artefact and never changes a sidecar.
- All file I/O is explicitly UTF-8 (`encoding="utf-8"`). Never create a symlink: the target is Windows. Use `pathlib`.

## Non-obvious placement

- Schemas (`schemas/*.json`) and prompts (`prompts/*.md`) ship as package data, declared in `pyproject.toml`. Load them with `importlib.resources`, never a repo-relative path.
- The argparse tree in `cli.py` is the single source of truth for the agent manual. `help-agent` is generated from it, so never hand-write a flag list elsewhere.
- Browser bundles are read-only context. Save the browser's bare YAML reply under `proposals/` and run the ordinary `merit apply`; there is no browser-specific parser or transaction.

## Reversals (do not re-propose the earlier approach)

- **Append-only sources became immutable sources.** A committed source is never edited, not even by appending: whole-file hashing makes appending incoherent, because the hash changes and invalidates every evidence entry already citing it. A correction or re-transcription is a new source; the link is a separate append-only supersession artefact, never an edit to either sidecar.
- **A `proposed` provenance value became candidates.** Provenance is `stated` or `inferred`, with no third value. An ungrounded idea is a candidate, a separate artefact kind with no `evidence` field at all, so the central claim (every requirement resolves to a verbatim, speaker-attributed span) holds without a qualifying clause.
- **V9 forbidding a provisional decision became V9 permitting it.** Earlier revisions had V9 reject a self-recorded note with no `confirmed_by`. That made the state unreachable: `merit apply` validates transactionally and would roll the decision back before anyone saw it, leaving `awaiting_confirmation` dead code and V8's handling of it unreachable. V9 permits the record; V8 blocks it from a baseline.

## When to update this file

1. After adding or renumbering a check: the ID registry is the most consulted fact in the repo.
2. After changing an interface consumed by more than one module (`Finding`, `ComparisonPoint`, `effective_status`, the proposal operation set).
3. After a decision that reverses an earlier one: record the reversal and the reason, or it will be re-litigated.
4. When a session had to re-derive something already settled: that is evidence of a gap, and the fix is a line here, not a longer prompt.

Keep it under 200 lines; past that it costs more context than it saves. Never add a file inventory.
