# POC Friction Fixes Plan

**Goal:** Remove the failed-apply and recovery detours seen in the first end-to-end
user session (ES2002a/ES2002b AMI transcripts, 2026-10-19), with the smallest
changes that keep every invariant in `AGENTS.md`.

**Scope rule:** POC. High value, low effort. Each fix is local to one or two
modules, adds tests next to the existing ones, and changes no check ID, schema
contract for governed artefacts, or exit-code meaning.

**Evidence:** the session transcript showed 5 failed `merit apply` runs, 1 refused
re-ingest, 1 failed `merit save`, and an agent reading `site-packages` source to
learn the `.txt` speaker rule and the whole-utterance quote rule.

---

## Picks and what each one fixes

| # | Fix | Session friction it removes | Effort |
|---|---|---|---|
| 1 | Ingest attribution gate and parse summary | ES2002a ingested with 0 attributed lines, exit 0. The failure only surfaced at apply, about 5 minutes later. | S |
| 5 | `merit apply --check` | The agent wrote its own Python against private `merit.anchor` to pre-check quotes. V8 and V9 failures surfaced only on a real apply. | S |
| 6 | Diagnostic anchor errors | "quote not found verbatim; closest line 40" pointed at the wrong line. The real causes were a fragment quote and an unattributed source. | S |
| 15 | Git identity check and quieter `save` errors | `merit save` failed with exit 4, and the cause ("Author identity unknown") was buried under about 20 LF/CRLF warnings. | S |
| 7 | Proposal-local references (`ref` / `@name`) | The agent guessed future IDs (REQ-TPI-0007/0008, 0009–0013) and split one change into two proposals. | M |
| 9 | Confirmation rules up front | V9 rejected a self-recorded confirmation only at apply. The rules were not in the clarify playbook. | S |

---

## 1. Ingest attribution gate (`merit/ingest/__init__.py`, `merit/ingest/txt.py`, `cli.py`)

**Behaviour**
- After extraction on the create path only (not the identical-bundle no-op path,
  and not in `_canonical_text`, which V12 re-extraction shares), count attributed and
  structural (`#`) lines.
- **Zero attributed lines → refuse** with `ValidationFailed` (exit 1). Nothing is
  written. The message names the count and gives format hints. `--allow-unattributed`
  overrides this for a deliberately structural source.
- Otherwise succeed. `IngestResult` gains `attributed` and `structural` counts,
  and the CLI prints `lines: N attributed, M structural (unattributed; cannot be cited)`.
- When more than half the lines are structural, the CLI prints a `warning:` with the
  same hints.

**Hints** (`txt.unattributed_hints(lines)`, read-only and outside the speaker predicate):
- `[0001] ` style line-number prefixes → "strip the leading line numbers".
- `NAME (ROLE):` labels → "drop the parenthesised role; record roles with `--participant NAME=ROLE`".
- lower-case labels (`sh2:`) → "speaker labels must start with a capital letter (`Sh2:`)".

**Not doing:** widening the speaker predicate. `txt.py` warns that predicate edits
are risky (an invented attribution is the one failure the product cannot tolerate).
The hints tell the user how to fix the input instead.

**Tests:** zero-attributed refusal writes nothing; the override flag ingests; the counts
appear in the result; each hint fires for its shape; the identical re-ingest no-op is
unchanged.

## 5. `merit apply --check` (`apply/transaction.py`, `cli.py`)

**Behaviour:** `apply_proposal(..., check=True)` runs the exact same staging,
handlers, anchoring and full `run_checks(staging, context_root=root)`, then
returns **before `_promote`**. The proposal file is not moved. The CLI prints
`Check passed: would write <paths>` plus any provisional-decision warnings, and exits 0.
A failure gives the same error and exit code as a real apply.

**Why this shape:** it reuses the transaction boundary, so `--check` and `apply`
cannot disagree. It needs no new validation path.

**Tests:** a passing check writes nothing and leaves the proposal in place; a failing
quote returns exit 3 under `--check`; the paths reported equal those a real apply then writes.

## 6. Diagnostic anchor errors (`anchor.py`)

When `find_quote` finds no whole-line match, it adds the first diagnostic that applies,
before the existing closest-line detail:
- **Unattributed source:** every line is `#` → "source has no attributed lines
  (ingested without speaker labels); evidence needs a `Name: text` source".
- **Fragment:** the normalised quote is a substring of exactly one line, or of a few
  lines → "quote is part of line N; quote the whole utterance: '<full line text>'"
  (plus "line N is structural" when that line is `#`).

The closest-line fallback and the unusual-character diagnostics are unchanged. It
is still an `AnchorError` (exit 3). Nothing new is accepted.

**Tests:** a fragment names the full line; a fragment in a structural line says so;
an all-structural source says so; existing anchor tests are unchanged.

## 15. Git identity and quieter `save` (`doctor.py`, `git_ops.py`)

- `doctor`: if `git config user.name` or `user.email` is unset, add a `warning:` with
  the fix `git config user.name "<name>"` / `git config user.email "<email>"`.
  It's a warning, not an error, because it is only needed for `merit save`.
- `git_ops._run`: on failure, drop the `warning: in the working copy of ...` line-ending
  noise from the reported detail. When the detail says the author identity is unknown,
  set `fix` to the same two `git config` commands.

**Not doing:** changing the scaffold `.gitattributes`, which would change
scaffold output and wheel contents for a cosmetic gain.

**Tests:** doctor flags a repo with no identity (an isolated `HOME`/`GIT_CONFIG_GLOBAL`);
a commit with no identity fails with the identity fix and no CRLF lines.

## 7. Proposal-local references (`apply/proposal.py`, `apply/transaction.py`)

**Syntax**
```yaml
operations:
  - op: add_requirement
    ref: price            # names the ID this operation will allocate
    statement: ...
  - op: record_supersession
    supersedes: REQ-TPI-0003
    superseded_by: "@price"   # replaced with the allocated ID before this op runs
```

**Rules**
- `ref` is allowed on ID-allocating ops: `add_requirement`, `add_candidate`,
  `add_issue`, `add_decision`, `promote_candidate` (allocates a requirement),
  and `record_supersession`.
- A string value that is exactly `@name` (anywhere in a later op, including
  list items such as `affects`/`resolves`) is replaced. Backward references only.
  An unknown, forward or duplicate ref is a `UsageError` naming the operation index.
- **Schema handling:** `parse_proposal` strips `ref` keys and validates a copy in
  which each `@name` is replaced by a pattern-valid placeholder of the referenced
  kind, so the packaged JSON Schema stays unchanged. The real substitution happens
  op by op inside the transaction, using the ID the earlier handler actually wrote.
  ID allocation is never predicted.

**Tests:** an add-plus-supersede pair in one proposal applies; refs across issue `affects`
work; an unknown ref, a forward ref and a duplicate ref each fail with the operation index;
a proposal without refs is byte-for-byte unaffected.

## 9. Confirmation rules up front (`prompts/04-clarify.md`, `validate/checks.py` V9 fix text)

- The clarify playbook states the D20 rules before anyone drafts:
  the confirmation source must not be a `self_recorded_note`; the confirmer must be
  its speaker or author; a self-recorded decision needs a different person to confirm.
  It also says to run `merit apply --check` first.
- The V9 finding's `fix` for the self-recorded-confirmation and same-author defects
  names the next step: ingest the confirmation as `minutes` or `email`, authored by
  or quoting the confirmer, then re-run.

**Not doing:** identity verification of the confirmer. A user who invents a second name
("Sh2") can still pass V9. Closing that needs real identity (e.g. signed commits),
which is out of POC scope. It is recorded here as a known gap.

**Tests:** the V9 finding for a self-recorded confirmation carries the new fix text;
`merit prompt clarify` mentions `--check`.

---

## Cross-cutting
- `help-agent` gains two lines: the `.txt` speaker-label rule and the
  "whole utterance" quote rule, plus `--check`. The argparse tree stays the single
  source for flags.
- `AGENTS.md`: record `apply --check` and proposal refs (an interface change to the
  proposal operation set), per its "when to update" rules.
- The wheel digest pin is not re-measured. This is unreleased POC work on main
  (see `docs/RELEASING.md`).

## Order
1 → 6 → 5 → 15 → 9 → 7. Cheapest and most independent first. 7 last because it is
the only one that touches proposal parsing.

## Deferred (not POC)
Bundle retirement (`merit source retire`), `amend_requirement`, baseline drift view,
`save --all-governed`, report auto-regeneration, optional session date.
