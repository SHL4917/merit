# Confirm the baseline

Read `merit help-agent`, `merit schema baseline` and `merit schema proposal`.
Run `merit validate --strict`. Resolve blocking issues with evidence and obtain
human approval of the selected requirements; defer the rest with reasons using
the proposal's `set_status` operation. Provisional decisions cannot unblock a baseline.
Consult the runtime catalogue for available baseline commands; do not invent CLI
commands. The tool never merges or tags: those actions belong to the human.

The only write path for governed artefacts is a proposal under `proposals/`
followed by `merit apply <proposal>`. Never edit governed files directly.
Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade
the claim to `inferred` with an explicit rationale and retain only valid evidence.
Route unsupported ideas to candidates. Run `merit validate` after application.
New inputs re-enter `merit prompt intake` and repeat drafting, review and clarification.
