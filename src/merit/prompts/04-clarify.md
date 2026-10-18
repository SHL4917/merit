# Clarify with users

Read `merit help-agent`, `merit schema decision` and `merit schema proposal`.
Ask focused questions tied to open issues and record the actual answers.
Use `merit ingest` to retain the answer record as a new source bundle.
Propose `add_decision` with its record and resolved issues. A self-recorded note
without confirmation remains provisional; a name alone is not confirmation.
Use `confirm_decision` only with an anchored confirmation record.

The only write path for governed artefacts is a proposal under `proposals/`
followed by `merit apply <proposal>`. Never edit governed files directly.
Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade
the claim to `inferred` with an explicit rationale and retain only valid evidence.
Route unsupported ideas to candidates. Run `merit validate` and return to
`merit prompt draft`, or to `merit prompt intake` when more evidence is needed.
