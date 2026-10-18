# MERIT

MERIT turns meeting transcripts into a git-backed requirements baseline in which
every requirement traces to a verbatim, speaker-attributed span in an immutable,
hash-pinned source. Validation is deterministic Python with no LLM involved.

This is the **tool** repository. It holds no requirements data; a project repository
holds that, and installs this package at a pinned version.

## Install

To set up a project repository and run the loop end to end, see
[`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md).

To develop MERIT itself (requires Python 3.11 or later):

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\merit --help
```

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | success |
| 1 | validation failure |
| 2 | usage error |
| 3 | anchor failure |
| 4 | environment error |

## Design

See `docs/superpowers/specs/2026-10-18-merit-design.md` and the implementation plan in
`docs/superpowers/plans/2026-10-18-merit.md`.
