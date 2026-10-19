# Evidence intake

Start with `merit help-agent` and `merit doctor`. Inspect source metadata using
`merit ingest <origin> --inspect-metadata`, then run `merit ingest` with the
record type, author, session date, participants, classification and ingester.
Ingest is the narrow create-only exception for immutable source bundles.
A `.txt` line is attributed only as `Name: text`, with a capitalised label of one
to four words; anything else (`[0001]` prefixes, `NAME (ROLE):`, lower-case labels)
is structural and cannot be cited. Check the `lines:` summary ingest prints, and if
labels are wrong, fix them in a copy of the file before ingesting.

When an email, document or self-recorded note has no speaker labels, its text
cannot be cited as it stands. Suggest author attribution to the user: ask who
wrote it, and only with their answer re-run `merit ingest` with
`--attribute-to-author` and `--authored-by "<that person>"`, under `--as` if a
bundle was already created. Never guess or infer the author. Quoted replies,
email headers and forwarded text stay uncredited. Never use it for a transcript
or minutes: label each speaker's lines instead.
Read the canonical source and its sidecar before drafting; do not alter either.

For governed artefacts, the only write path is a proposal under `proposals/`
followed by `merit apply <proposal>`. Use `merit schema proposal` for the shape.
Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade
the claim to `inferred` with an explicit rationale and retain only valid evidence.
Route unsupported ideas to candidates, never invent evidence or user approval.
Run `merit validate` and proceed with `merit prompt draft`.
