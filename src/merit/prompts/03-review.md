# Review evidence and gaps

Read `merit help-agent`, run `merit validate`, and inspect `merit schema issue`
and `merit schema proposal`. Compare claims across sources, speakers and sessions.
Distinguish contradiction, revision, scenario difference and misunderstanding.
Use `add_issue` with the affected requirements and competing evidence. Identify
missing information without manufacturing a resolution or confirmation.
An issue needs at least two evidence entries (rule V7), each of which bears on
the question itself. If only one line does, never pad it with an unrelated
quote to pass V7: report the gap to the user and leave it out of the proposal.
Run `merit apply --check <proposal>` before `merit apply`.

The only write path for governed artefacts is a proposal under `proposals/`
followed by `merit apply <proposal>`. Never edit governed files directly.
Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade
the claim to `inferred` with an explicit rationale and retain only valid evidence.
Route unsupported ideas to candidates. Run `merit validate` after application;
return to `merit prompt draft` for corrections or `merit prompt clarify` for questions.
