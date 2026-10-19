# Draft requirements

Read `merit help-agent`, `merit schema proposal` and `merit schema requirement`.
Read the canonical sources and sidecars. Separate stated claims from inference;
write atomic statements with acceptance criteria and supporting evidence.
Use `add_requirement` for grounded claims and `add_candidate` for unsupported ideas.
The proposal never allocates a new ID or chooses a governed file path. To point a
later operation at an artefact the same proposal creates, give the creating
operation `ref: <name>` and use `"@<name>"` where the ID belongs (for example in
`affects`, `superseded_by` or `id`); never guess the next ID.
A quote is one or more whole canonical lines, never part of a line.
Run `merit apply --check <proposal>` first; it runs every check and writes nothing.

The only write path for governed artefacts is a proposal under `proposals/`
followed by `merit apply <proposal>`. Never edit governed files directly.
Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade
the claim to `inferred` with an explicit rationale and retain only valid evidence.
Ideas without support belong in candidates, not requirements. Correct a rejected
proposal using the reported fix, run `merit apply` again, then `merit validate`.
Continue with `merit prompt review`.
