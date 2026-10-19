# Bootstrap: install MERIT and run a project repo

Every command below was executed and verified against `v0.1.4`. Commands are written
for Windows PowerShell; on Windows PowerShell 5.1 `curl` is an alias for
`Invoke-WebRequest`, so call `curl.exe` by filename if you need real curl.

MERIT uses **two** repositories. This one is the **tool** — it holds no requirements
data. Each project gets its own **requirements repo**, created by `merit init`, which
holds the sources, requirements, candidates, issues, decisions, supersessions and
baselines. Keep project repos private: that is where transcripts live.

---

## 1. Install the tool

Requires Python 3.11 or later. Install from a pinned tag, never from `main`, so the
version that validated your baseline is identifiable later.

```powershell
py -3.12 -m venv "$HOME\merit-venv"
& "$HOME\merit-venv\Scripts\python.exe" -m pip install "git+https://github.com/SHL4917/merit@v0.1.4"
```

Check it:

```powershell
& "$HOME\merit-venv\Scripts\merit.exe" --help
```

To type `merit` anywhere instead of the full path, use pipx:

```powershell
py -3.12 -m pip install --user pipx
py -3.12 -m pipx install "git+https://github.com/SHL4917/merit@v0.1.4"
py -3.12 -m pipx ensurepath
```

The rest of this guide writes `merit`. Substitute the full path if you skipped pipx.

To upgrade a project scaffolded by an earlier version, install the new tag the same
way, then set `[tool] merit_version` in that project's `merit.toml` to the installed
version, run `merit validate`, and commit `merit.toml`. Until then `merit doctor`
reports the pin mismatch and names both choices.

---

## 2. Scaffold the project repo

The target may be an empty folder or a new path. `init` creates the tree, `merit.toml`,
the agent pointers, `.gitattributes` and `.gitignore`, and runs `git init`.

```powershell
merit init "C:\path\to\your-project" --project-id acme --id-prefix ACM
```

`--id-prefix` is 2–6 uppercase letters and becomes part of every requirement ID
(`REQ-ACM-0001`). IDs are never renumbered once allocated, so choose deliberately.

`init` is non-destructive: it appends to an existing `.gitignore` or `.gitattributes`
and refuses, naming every collision, if `merit.toml`, `AGENTS.md`, `CLAUDE.md` or
`GEMINI.md` already exist.

```powershell
merit doctor "C:\path\to\your-project"
```

`doctor` will report `enforcement tier: unverified` until GitHub protections are
configured and readable. That is correct behaviour, not a fault — see section 7.

---

## 3. Ingest your raw files

`.vtt`, `.docx` and `.txt` are supported. **For a `.docx`, look before you ingest**:
the original is retained, and a `.docx` carries author names, revision history and
tracked changes a redacted extraction may deliberately have dropped. This writes
nothing:

```powershell
merit ingest "C:\path\to\raw\interview.docx" --inspect-metadata
```

**`ingest` takes no path argument — run it from inside the project repo.** Most other
commands accept an optional path; this one does not.

```powershell
cd "C:\path\to\your-project"
```

```powershell
merit ingest "C:\path\to\raw\interview.txt" --record-type transcript --authored-by alex --session-date 2026-10-19 --participant "Jordan Reyes=user" --participant "Sam Ellis=ops lead" --classification INTERNAL --ingested-by alex
```

Every value here enters the hash-pinned sidecar and **cannot be edited afterwards**.
Get them right at ingest time; a correction is a new source plus a supersession record,
never an edit.

`--record-type` is `transcript`, `minutes`, `email` or `self_recorded_note`, and it is
load-bearing rather than bookkeeping: a decision resting on a `self_recorded_note` is
*provisional* and cannot carry a baseline until a second named person confirms it with
anchored evidence from a different, attributable source.

`--classification` must be one of the levels in `merit.toml`. `sources/raw/` inherits
the highest classification of anything inside it.

A re-ingest of the same bytes with the same metadata is a no-op that writes nothing. A
re-ingest with *different* metadata is an error, because silently keeping the original
attribution would discard what you just said.

```powershell
merit validate
```

---

## 4. Read the canonical source, then draft

Open `sources\<name>.md`. One utterance per line, numbered, speaker-attributed:

```
[0001] Jordan Reyes: If the connection drops we still need to keep working for a couple of days, maybe three.
[0002] Sam Ellis: We must be able to export the whole register to a spreadsheet for the auditors.
```

A line whose speaker is `#` is structural or unattributed and cannot be cited as
evidence.

**You never write a governed file directly.** Write a proposal under `proposals\` and
let `merit apply` allocate IDs, resolve every quote and validate before anything moves:

```yaml
schema_version: 1
operations:
  - op: add_requirement
    statement: The system shall operate offline for at least 48 hours.
    type: constraint
    provenance: stated
    acceptance_criteria:
      - "Offline operation verified for 48 hours without connectivity."
    evidence:
      - source: sources/2026-10-19-interview.md
        quote: "If the connection drops we still need to keep working for a couple of days, maybe three."
```

```powershell
merit apply proposals\draft.yaml
```

The `quote` must be **verbatim**. Copy it from the canonical file; do not retype it. You
supply no line number and no speaker — code fills those in. A paraphrase is rejected,
the whole proposal rolls back, no IDs are consumed, and the error names the closest line:

```
error: operation index 0: quote not found verbatim; closest line 2: 'We must be able to export the whole register to a spreadsheet for the auditors.'
```

That rejection is the product. Exit code `3` is an anchor failure. A successful apply
deletes the proposal; a failed one leaves it in place for correction.

Operations available in a proposal: `add_requirement`, `add_candidate`, `add_issue`,
`add_decision`, `confirm_decision`, `promote_candidate`, `set_status`, `dismiss_issue`,
`add_acceptance_criterion`, `record_supersession`.

An idea nobody actually said becomes a **candidate**, which carries no evidence field at
all and never enters a baseline. That is what keeps the central claim true without a
qualifying clause.

---

## 5. Review, clarify, baseline

```powershell
merit citations
```

Lists the substantive source lines no requirement cites, worklist first and the
percentage second. A substantive line has a real speaker and at least
`coverage.min_chars` characters (40 by default). **This is an attention queue, never a
score**: it proves a line was looked at, not that the requirement citing it represents
what the speaker meant.

```powershell
merit report
```

Writes `public\index.html` — one self-contained, offline, read-only file you can email.
It leads with what currently blocks a baseline, and issues show their *effective*
status, so a conflict resolved only by a self-recorded note reads as
*awaiting confirmation* rather than *resolved*.

```powershell
merit questions
```

```powershell
merit baseline --dry-run
```

When it reports ready, drop `--dry-run` to write the manifest and print the tag handoff
block. Then use `merit start-draft`, `merit save`, `merit push` and `merit open-pr`.

**A human merges and tags.** The tool never does either, by design — no code path
invokes `git merge` or `git tag`. The handoff block gives the approver a prefilled
release link pinned to the merge commit, so they never type a SHA.

---

## 6. Browser route, if you have no CLI agent

```powershell
merit pack --stage draft
```

Prints one paste-able Markdown bundle: the stage playbook, the proposal schema and the
source excerpts. Paste the reply into `proposals\browser-reply.yaml` and run the ordinary
`merit apply`. There is no browser-specific parser — it lands on the same transaction,
producing the same artefacts and the same governed tree hash as the agent path.

---

## 7. Enforcement tiers, and what they do and do not mean

`doctor` reports the tier it can **verify by identity**, never the tier you hope for:

| Tier | Requires |
| --- | --- |
| T1 structural | An **organisation ruleset** on the default branch requiring a workflow whose owner, repo, path and ref identify MERIT's `merit-validate.yml` |
| T2 strong convention | A required status check **and** `require_code_owner_reviews` on `main`; **and** the project workflow `uses:` the reusable workflow at a 40-hex SHA; **and** `CODEOWNERS` patterns that actually match `.github/` and `merit.toml` |
| T3 convention | A required status check, and nothing above |
| unverified | Any condition cannot be read — no `gh`, no permission, API error |

**The tier does not govern whether the validator works. It governs whether someone with
write access can remove it.** A fabricated quote fails `merit apply` before it can be
committed, locally, with no GitHub involved. A low tier means a motivated actor with
write access could weaken the validating workflow in the same pull request that exploits
the weakness; the mitigation there is a second reviewer, which is a social control rather
than a mechanical one.

T1 needs a GitHub organisation — a personal account cannot satisfy it. T2 needs branch
protection and code-owner reviews on the project repo, which a free private repository
cannot have. Reporting `unverified` honestly is the design working.

---

## Agent setup

The scaffold writes `AGENTS.md`, with `CLAUDE.md` and `GEMINI.md` pointing at it. An
agent's first step in any session is:

```powershell
merit help-agent
```

That manual is generated from the CLI definitions at runtime, so it cannot describe a
flag that does not exist or omit one that does. `merit prompt <stage>` serves the stage
playbooks (`intake`, `draft`, `review`, `clarify`, `baseline`) and `merit schema
<artefact>` serves the JSON Schemas.

The agent's only write is a proposal under `proposals/`. Everything else is a shell call.
`proposals/` is gitignored scratch and must never reach a pull request.
