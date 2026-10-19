# Clarify with users

Read `merit help-agent`, `merit schema decision` and `merit schema proposal`.
Ask focused questions tied to open issues and record the actual answers.
Use `merit ingest` to retain the answer record as a new source bundle.
Propose `add_decision` with its record and resolved issues. A self-recorded note
without confirmation remains provisional; a name alone is not confirmation.
Use `confirm_decision` only with an anchored confirmation record.

Confirmation rules (rule V9), before drafting `confirm_decision`:
- The confirmation source must be `minutes`, `email` or `transcript`, never a
  `self_recorded_note`.
- `confirmed_by` must be the speaker of the quoted line or the source's `authored_by`.
- If the decision rests on a self-recorded note, a different person must confirm it.
- Provisional decisions block a baseline; tell the user who must confirm, and how,
  before asking them for text.

Run `merit apply --check <proposal>` before `merit apply`; it writes nothing.

The only write path for governed artefacts is a proposal under `proposals/`
followed by `merit apply <proposal>`. Never edit governed files directly.
Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade
the claim to `inferred` with an explicit rationale and retain only valid evidence.
Route unsupported ideas to candidates. Run `merit validate` and return to
`merit prompt draft`, or to `merit prompt intake` when more evidence is needed.
