# MERIT — Managing Evolving Requirements & Intent Tracking

**Design specification**
Status: draft for review · Date: 2026-10-18 (rev 5) · Horizon: Builders Day demo

---

## 1. Thesis

It would be easy to build something that sounds like *"AI drafts our requirements."* That is neither the defensible product nor the one that survives scrutiny. MERIT's actual claim is narrower and much harder to dismiss:

> **A requirement cannot exist in the repo unless a deterministic, non-LLM checker can resolve its evidence to a verbatim span in an immutable, hash-pinned source bundle.**

Drafting, conflict spotting and clarification questions are the model being useful on top of a floor that the model cannot lower. That floor is ordinary Python. It is the entire answer to the project's governing constraint.

The claim holds without qualification because ideas not yet grounded in evidence are **candidates**, a separate artefact kind (§5.5). They live in `candidates/`, they are never called requirements, and they cannot enter `requirements/` without evidence.

---

## 2. Problem

Four failure modes in current requirements practice motivate this work:

| Challenge | Description |
|---|---|
| Manual consolidation | Requirements are extracted and reconciled by hand across transcripts, minutes and informal discussion |
| Uncertain provenance | Requirements lose their speaker, source and context, so user intent cannot be verified |
| Conflicts in requirements | Contradictions within or between users accumulate undetected across sessions |
| Unclear assumptions | Success conditions and constraints go unstated; developers infer what users will accept |

### The six-stage loop

MERIT implements a six-stage loop. These stage numbers are used throughout this document and nowhere depend on an external reference.

| Stage | Name | What happens |
|---|---|---|
| **1** | **Evidence intake** | Choose the sources in scope; extract, freeze and label them with session metadata |
| **2** | **Draft requirements** | Derive statements and acceptance criteria, each labelled *stated / inferred* and tied to evidence; ungrounded ideas become candidates |
| **3** | **Review evidence and gaps** | Compare statements within and across speakers and sessions; raise issues carrying competing evidence |
| **4** | **Clarify with users** | Put open questions to users; record their answers and the record that supports each |
| **5** | **Confirm the baseline** | Select what is ready, defer the rest with reasons, approve, merge and tag |
| **6** | **Incorporate new inputs** | Assess new sources against a baseline; repeat 1–4 for what changed; tag the next baseline |

As a state machine: stages 1–4 form a **working draft that cycles** — intake feeds drafting, drafting feeds review, and review either returns corrections to drafting or escalates to clarification. Clarification returns either clarified requirements or a demand for more evidence, re-entering intake. The draft leaves that cycle only at **stage 5, on human approval**, producing a tagged baseline. New discussion or a change request enters **stage 6**, which opens a fresh working draft against the existing baseline and re-enters the cycle.

---

## 3. Goals and non-goals

### Goals

- **G1.** Every requirement traces to a verbatim, speaker-attributed span in an immutable source.
- **G2.** Fabricated or paraphrased evidence is rejected by code, not by reviewer vigilance.
- **G3.** Detected conflicts block baselining until a decision is recorded against an attributable record.
- **G4.** A non-technical user operates the whole loop through chat plus a read-only web report.
- **G5.** Any agent harness meeting a minimal capability floor can drive it; no vendor lock-in.
- **G6.** Approval is reviewable by someone who never leaves the browser, **including creating the baseline tag**.
- **G7.** A reviewer can re-derive every canonical source from the original file it came from.
- **G8.** No agent or user ever hand-writes a governed artefact. All writes go through one transactional command, `merit apply`, with `merit ingest` as the single narrow, create-only exception (§5.15, D19).

### Non-goals (this POC)

- Multi-project or multi-tenant operation. One project, one repo.
- Authentication, user accounts, or any running service.
- Redaction or declassification tooling.
- Import from Jira, Confluence, DOORS or similar.
- Guaranteeing complete recall of requirements or conflicts. See §14.
- Guaranteeing that a cited line *means* what the requirement claims. See §5.12 and §14.

---

## 4. Locked decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Horizon is a **Builders Day demo** (days, not weeks) | Scope ruthlessly; optimise for a credible end-to-end slice |
| D2 | **Python** CLI, stdlib + `pyyaml` + `jsonschema` only | Present on target laptops and CI; readable by non-developer reviewers |
| D3 | **GitHub + Actions**; Pages optional | Chosen over GitLab; Actions is the enforcement point |
| D4 | Git is **visible and agent-narrated**, never hidden | User requirement: teach while doing |
| D5 | Validation is **deterministic code**, never prompt-enforced | Governing constraint: cannot be left to users or arbitrary agent instructions |
| D6 | Agent may `push` and open PRs; **never merge, never tag** | Those stay a human action, §5.16 |
| D7 | Decision records require a hash-matched source record **and an attributable record type**. A self-written note is *permitted* and yields a **provisional** decision that V8 bars from a baseline | §5.6. Forbidding it in V9 would make the provisional state unreachable |
| D8 | **Two repos**: tool and project, separate | Prevents prompt and schema drift, §5.1. Not by itself an enforcement boundary, §5.8 |
| D9 | Prompts and schemas ship **in the package**, not the project repo | Prevents drift and local weakening, §5.2 |
| D10 | Demo corpus unconfirmed; **dogfood fallback** confirmed | Removes data clearance from the critical path, §12 |
| D11 | Sources are **immutable as a bundle**: canonical text, retained original, and metadata together | §5.4. Protecting the canonical text alone leaves the attribution fields editable |
| D12 | Original input files are **retained** alongside their canonical extraction | G7. Extraction becomes verifiable rather than trusted |
| D13 | Ungrounded ideas are **candidates**, a separate artefact kind | Keeps §1 true without qualification |
| D14 | **One transactional write path** (`merit apply`) for every governed artefact | G8. Without it, "never hand-edit" is an instruction with no mechanism |
| D15 | Supersession is an **append-only artefact**, never a mutation of a source sidecar | Follows from D11: a pinned sidecar cannot be edited later |
| D16 | Enforcement tier is **verified by identity through the GitHub API, or reported as `unverified`** | §5.8. A required check says nothing about *what* is required |
| D17 | `merit apply` validates the staged tree against the **real repository's config and git history** | §5.15. A bare temp dir degrades V2/V4 to no-ops at the moment of writing |
| D18 | The baseline manifest records **no target commit**; the tag is applied after merge to the commit containing it | §5.16. A manifest naming its own commit is self-referential |
| D19 | `merit ingest` is the **sole exception** to the single write path, and may only *create* under `sources/` | §5.15. Sources originate as binary files outside the repo |
| D20 | A confirmation requires **anchored evidence**, not a name | §5.5. A bare `confirmed_by` is self-attestation in a different field |
| D21 | The manifest records a **`governed_tree_hash`** and the tag is pinned to the merge commit by prefilled link | §5.16. Targeting `main` races with later merges |

---

## 5. Architecture

### 5.1 Repo topology

Two repositories.

| | `merit` (tool) | `<project>-requirements` |
|---|---|---|
| Contains | CLI, schemas, prompts, report templates, tests | Sources, requirements, candidates, issues, decisions, supersessions, baselines |
| Visibility | Public or org-internal — holds no data | Private — holds transcripts |
| Changes | When the method changes | Every working session |
| CI | Unit tests over the validator | `merit validate` on every PR |

**What the split does.** Schemas and prompts live in the installed package, so twelve project repos cannot become twelve dialects of MERIT, and neither a user nor an agent can edit a rule into something weaker mid-session. It also lets the tool repo be public — no data in it — giving free Actions, free Pages and tokenless `pip install` in CI, while project repos stay private.

**What the split does not do.** It is **not**, by itself, an enforcement boundary. The project repo still contains `.github/workflows/merit.yml` and `merit.toml`, and an agent with write access can change either in the same pull request that changes data. GitHub runs workflows from the pull request's own head for same-repo PRs, and required status checks match by name, so a job renamed into compliance reports green. Enforcement requires a mechanism outside the project repo's write scope: §5.8.

### 5.2 Where prompts and schemas live

In the installed package, exposed through commands:

```
merit help-agent          # the operating manual, for the agent to read
merit prompt <stage>      # a stage playbook
merit schema <artefact>   # a JSON Schema
```

Three reasons: prompts cannot drift into per-project dialects; the agent always receives the playbook matching the installed validator; and neither user nor agent can edit them into something weaker mid-session. Transparency is preserved — the commands print them, and the tool repo is readable.

**One deliberate escape hatch.** A project may keep `.agent/context.md` holding project vocabulary (*"the portal" means the staff-facing web console*). This is **additive context, never rules**. The line holds because validation rules are Python: nothing written in a Markdown file can relax a check.

### 5.3 Project repo layout

Examples throughout use a placeholder project named *Sample*, ID prefix `SMP`.

```
AGENTS.md              # ~20 lines, bootstrap only
CLAUDE.md              # one line: @AGENTS.md
GEMINI.md              # same pointer
merit.toml             # project id, ID prefix, tool version (advisory), classification policy
.agent/context.md      # optional, additive vocabulary
CODEOWNERS             # §5.8 tier T2 — only meaningful with required review configured
.github/workflows/merit.yml
sources/
  raw/
    sample_interview_120926.vtt          # the original file, as supplied
  2026-09-12-sample-interview.md         # canonical extraction
  2026-09-12-sample-interview.meta.yaml  # includes bundle_sha256 over all three
proposals/             # UNGOVERNED scratch — gitignored, never validated, §5.15
requirements/REQ-SMP-0007.yaml
candidates/CAN-0002.yaml
issues/ISS-0001.yaml
decisions/DEC-0001.yaml
supersessions/SUP-0001.yaml
baselines/sample-b1.yaml
```

`AGENTS.md` content, in substance:

> This is a MERIT requirements repo. Run `merit help-agent` for your operating instructions and follow them exactly. **Never write or edit a file under `sources/`, `requirements/`, `candidates/`, `issues/`, `decisions/`, `supersessions/` or `baselines/`. To change anything, write a proposal under `proposals/` and run `merit apply`.** If `merit` is not on PATH: `pipx install git+https://github.com/<org>/merit@v0.3.1`.

**Do not symlink `CLAUDE.md` to `AGENTS.md`.** Git symlinks on Windows require `core.symlinks` and developer mode and will fail on a locked-down laptop. Use a one-line import pointer.

> ⚠️ **`sources/raw/` inherits the highest classification of anything inside it.** An original `.docx` carries author names, revision history and tracked changes a redacted canonical extraction may deliberately have dropped. `merit ingest --inspect-metadata` reports embedded metadata before anything is written.

### 5.4 The source bundle

`merit ingest` converts `.vtt`, `.docx` and `.txt` into one canonical form, **retaining the original** under `sources/raw/`. One utterance per line, never wrapped:

```
[0001] # Minutes of Sample user interview, 12 Sep 2026
[0002] Jordan Reyes: If the connection drops we still need to work for a couple of days, maybe three.
[0003] Sam Ellis: We must be able to export to a spreadsheet.
```

Line numbers are zero-padded to four digits. Speaker `#` marks a structural or unattributed line. Speaker attribution is **structural, not inferred**.

Each source gets a `.meta.yaml` sidecar:

```yaml
source: sources/2026-09-12-sample-interview.md
sha256: 9f2c8a1e...                               # canonical text, after normalisation
origin: sources/raw/sample_interview_120926.vtt
origin_sha256: 41bd0c7e...                        # original bytes, as supplied
extractor_version: 0.3.1
record_type: transcript | minutes | email | self_recorded_note
authored_by: alex                                 # who produced this record, not who speaks in it
session_date: 2026-09-12
participants:
  - { name: Jordan Reyes, role: user }
  - { name: Sam Ellis, role: ops lead }
classification: INTERNAL
ingested_on: 2026-10-18
ingested_by: alex
bundle_sha256: c4e0...                            # binds all of the above together
```

**The bundle, not the file, is the unit of integrity (D11).** Protecting only the canonical text leaves the attribution fields editable, and flipping `record_type` from `self_recorded_note` to `transcript` would silently dissolve the confirmation requirement in V9. So:

```
bundle_sha256 = sha256( sha256 || origin_sha256 || canonical_json(immutable_fields) )
```

where `immutable_fields` is every key above except `bundle_sha256` itself, serialised with sorted keys and no whitespace. **Nothing in a sidecar is editable after ingest.** There are no mutable fields.

**Sources are immutable.** A committed source is never edited — not even by appending. Whole-file hashing makes appending incoherent: it changes the hash, invalidating every evidence entry already citing it. A correction, a re-transcription, or a later session is a **new source**, and the relationship is recorded as a separate append-only artefact (§5.5, D15) rather than by editing either sidecar.

**Ingest is atomic and create-only.** Work happens in a temporary directory and is moved into place only when every file is written and hashed. A failed ingest leaves the repo untouched. A raw filename or canonical stem that already exists is an error naming both paths, resolvable with `--as <name>`. Per D19, `ingest` may **only create** files under `sources/` — it has no code path that modifies or deletes an existing source, original or sidecar, which is what makes the exception to the single write path (§5.15) narrow enough to be safe.

### 5.5 Artefact schemas

`schema_version: 1` throughout.

**Requirement** — `requirements/REQ-<PREFIX>-<NNNN>.yaml`

```yaml
id: REQ-SMP-0007                 # ^REQ-[A-Z]{2,6}-\d{4}$
statement: Audit logs shall be retained for 7 years.
type: functional | constraint | quality | interface
provenance: stated | inferred    # no third value; ungrounded ideas are candidates
status: draft | clarifying | baselined | deferred | withdrawn
rationale: ...                   # required when provenance == inferred
evidence: [...]                  # min 1, always
acceptance_criteria: [...]       # min 1 required to reach baselined
owner: ops-lead                  # optional
promoted_from: CAN-0002          # optional
deferred_reason: ...             # required when status == deferred
withdrawn_reason: ...            # required when status == withdrawn
```

Requirements are **never deleted**. Removal is `status: withdrawn` plus a reason.

> `open_issues` is **not** a stored field. It is derived from the issue files on every read (§5.6, V8). Storing it would let an agent clear a block by editing the requirement.

**Candidate** — `candidates/CAN-<NNNN>.yaml`

```yaml
id: CAN-0002                     # ^CAN-\d{4}$
statement: The system should offer a dark theme.
rationale: Raised in passing by the facilitator; no user stated this.
raised_by: alex
raised_on: 2026-10-18
status: open | promoted | withdrawn
promoted_to: REQ-SMP-0021        # required when status == promoted
withdrawn_reason: ...            # required when status == withdrawn
```

A candidate carries **no evidence field at all** — the schema sets `additionalProperties: false`, so an evidence-bearing candidate is a structural error. It never appears in a baseline, and the report renders it in a section labelled *Candidates — not requirements*.

**Evidence entry**

```yaml
source: sources/2026-09-12-sample-interview.md
bundle_sha256: c4e0...           # pins the whole bundle, not just the text
line: 142
end_line: 143                    # optional; inclusive range, single speaker only
speaker: Jordan Reyes
quote: "We need the logs kept for seven years, that's the audit requirement."
```

A multi-line span must lie within **one speaker's** contiguous lines and contain no structural line. A span crossing a speaker change is rejected at anchor time: one `speaker` field cannot honestly label two people.

**Issue** — `issues/ISS-<NNNN>.yaml`

```yaml
id: ISS-0001
kind: contradiction | revision | scenario_difference | misunderstanding
summary: Export constraint may or may not cover aggregate exports.
evidence: [...]                  # min 2 — the competing statements
affects: [REQ-SMP-0011, REQ-SMP-0012]
question: Does the no-export constraint cover aggregate exports, or all exports?
status: open | resolved | dismissed      # RECORDED status — see effective status below
owner: alex
resolved_by: DEC-0001            # required when status == resolved
dismissed_reason: ...            # required when status == dismissed
```

The four `kind` values are the distinctions stage 3 must draw: a genuine **contradiction**, a **revision** over time, a **scenario difference** where both statements hold under different conditions, or a **misunderstanding**.

**Decision** — `decisions/DEC-<NNNN>.yaml`

```yaml
id: DEC-0001
resolves: [ISS-0001]
decided_on: 2026-09-24
decided_by: Jordan Reyes
outcome: Aggregate exports permitted; raw data export prohibited.
record: { ...evidence entry... }
confirmed_by: Sam Ellis          # optional; only meaningful with confirmation_record
confirmation_record:             # an evidence entry, REQUIRED whenever confirmed_by is set
  source: sources/2026-09-25-sample-confirmation-email.md
  bundle_sha256: b19d...
  line: 4
  speaker: Sam Ellis
  quote: "Confirming Jordan's note of the 24th — aggregate only, raw stays in."
```

**A confirmation must itself be evidenced (D20).** A bare `confirmed_by` name is self-attestation wearing a different field: an agent could type any colleague's name and clear the block. So `confirmed_by` is meaningful only alongside a `confirmation_record` that anchors like any other evidence, whose source is **not** a `self_recorded_note`, and whose speaker or `authored_by` matches `confirmed_by`. Nothing in MERIT is true because someone typed it.

**Supersession** — `supersessions/SUP-<NNNN>.yaml` *(new in rev 3, D15)*

```yaml
id: SUP-0001
supersedes: sources/2026-09-12-sample-interview.md
superseded_by: sources/2026-10-02-sample-interview-corrected.md
reason: Re-transcribed after audio clean-up; three passages corrected.
recorded_by: alex
recorded_on: 2026-10-02
```

Append-only. Recording a supersession writes one new file and mutates nothing — the only shape compatible with hash-pinned sidecars.

**Baseline manifest** — `baselines/<project>-b<N>.yaml`

```yaml
id: sample-b1
prepared_on: 2026-10-20
prepared_by: alex
governed_tree_hash: e8b1...      # every governed file EXCEPT this manifest, §5.16
merit_version: 0.3.1
schema_version: 1
requirements:
  - { id: REQ-SMP-0007, content_sha256: a71f... }
deferred:
  - { id: REQ-SMP-0014, reason: Awaiting interface specification. }
open_issues: [ISS-0005]
```

Recording the tool and schema version fixes *which rules* a baseline was approved under.

> The manifest records **no target commit**. A manifest naming the commit that will contain it is self-referential — writing the SHA changes the content, which changes the SHA. The manifest is committed in the reviewed pull request, and the tag is applied after merge to the commit that already contains it (§5.16).

### 5.5.1 Effective status

A recorded status is not always the status that matters. Two derived functions are the **single source of truth for everything user-facing** — report, `merit questions`, `merit pr-body` and `merit baseline` all call them, and none ever displays a raw `status`:

```
is_provisional(decision) =
    the record source's record_type == self_recorded_note
    and confirmed_by is absent

effective_status(issue) =
    dismissed              if status == dismissed
    open                   if status == open
    awaiting_confirmation  if status == resolved and its decision is provisional
    resolved               otherwise
```

`awaiting_confirmation` is a **derived value only** — never stored, never in the schema enum. Without this, `merit decide` writes `resolved` while V8 still blocks the baseline, and a lay user sees a resolved issue alongside an unexplained refusal to tag. V8 blocks on `open` **and** `awaiting_confirmation`, and `merit questions --open` lists both.

### 5.6 The validator

`merit validate` — zero LLM involvement, deterministic, identical locally and in CI.

| # | Check | What it prevents |
|---|---|---|
| V1 | Each source has a sidecar whose `sha256` matches the canonical text, whose `origin` file **exists** and matches `origin_sha256`, whose `classification` is permitted, whose `record_type` and `authored_by` are set, and whose **`bundle_sha256` matches the recomputed bundle** | Silent edits to text, deletion or swapping of the original, and tampering with attribution metadata |
| V2 | Every source bundle present at the comparison point has an **identical `bundle_sha256` now** | Any mutation of a cited source, its original, or its metadata |
| V3 | Every artefact validates against its JSON Schema; unparseable YAML is a finding naming file and line | Structure as a matter of opinion; tracebacks |
| V4 | IDs match their pattern, are unique within their kind, and are never reused or renumbered relative to the comparison point | Identity drift across baselines |
| V5 | **Each evidence `quote` is sliced from the cited source at the cited line(s) and compared verbatim** after whitespace normalisation; `speaker` must match; the span must not cross a speaker change or include a structural line; `bundle_sha256` must be current | **Fabricated and paraphrased evidence; misattributed spans** |
| V6 | `stated` requires ≥1 evidence; `inferred` requires ≥1 evidence **and** `rationale`; `withdrawn`/`deferred` require reasons; a `promoted` candidate must name an existing requirement | Assumptions quietly becoming requirements |
| V7 | Each issue cites ≥2 evidence entries and ≥1 existing requirement ID | Conflicts asserted without showing both sides |
| V8 | No `baselined` requirement has an affecting issue whose **effective status** is `open` or `awaiting_confirmation` | Known conflicts shipping unresolved; self-attested resolutions |
| V9 | Each decision cites ≥1 existing issue, `decided_on`, `decided_by`, and a `record` passing V5. A `self_recorded_note` record **may** omit `confirmed_by` — that is a valid *provisional* decision. When `confirmed_by` **is** present it requires a `confirmation_record` that passes V5, whose source is **not** a `self_recorded_note`, whose speaker or `authored_by` equals `confirmed_by`, and which is not authored by the original note's `authored_by` | Decisions with nothing behind them; **invented confirmers and self-confirmation** |
| V10 | `baseline` refuses if any error-severity finding exists, any `deferred` item lacks a reason, or any `baselined` requirement lacks acceptance criteria | A tag that doesn't mean anything |
| V11 | No `baselined` requirement cites a source named by a supersession record | Requirements resting on corrected transcripts |
| V12 | Canonical text **re-extracts** from the retained original and matches, when `extractor_version` equals the installed version | Trusted, unverifiable extraction (G7) |

Whitespace normalisation for V5: collapse runs of whitespace to a single space and strip ends. No other transformation. Case and punctuation must match exactly.

**Finding severity.** Every finding is `error` or `advisory`. Only errors fail the run and block a baseline. Advisories exist for exactly two situations, both cases where the tool genuinely cannot decide rather than cases where a rule is soft: **V12 when the recorded extractor differs from the installed one**, and **V11 when a non-baselined requirement cites a superseded source**. A missing or altered original is a V1 **error**, never a V12 advisory — the three failures (raw deleted/tampered, metadata tampered, extractor drift) are separately detected and separately tested.

**Comparison point for V2 and V4.** The most recent baseline tag reachable from the current branch; failing that, the merge-base with `main`; failing that, `none`, in which case V2 passes trivially and V4 enforces pattern and uniqueness only.

**D7, restated — and why V9 must permit what V8 blocks.** A Requirements Lead may write and commit a note themselves; it is ingested as `record_type: self_recorded_note`, and a decision resting on it is *provisional* until a second named person confirms. Validation proves the quote is in the note; it cannot prove the note is true.

The division of labour between the two checks is load-bearing and was wrong in rev 2 and 3:

- **V9 permits** a self-note decision with no `confirmed_by`. Forbidding it would make the provisional state unreachable — `merit apply` validates transactionally, so a decision V9 rejects is rolled back before anyone can see it — which in turn made `awaiting_confirmation` dead code and V8's handling of it unreachable.
- **V8 blocks** baselining while any affecting issue is `awaiting_confirmation`.

So the record can exist, is visible, and is honestly labelled; it simply cannot carry a baseline. V9's remaining job is to reject a confirmation that is not one — which means **evidence, not a name** (D20). `confirm_decision` (§5.15) supplies both `confirmed_by` and the `confirmation_record` behind it; without the record the block stands.

### 5.7 The anchoring mechanic

LLMs are poor at character offsets and will confidently invent them. **The agent therefore never produces one.**

The agent emits only the quote text. `merit anchor` — plain code — searches the cited source:

- **not found verbatim** → error. The agent must find the real line or reclassify; paraphrasing is forbidden.
- **found more than once** → error listing candidate lines; the agent must extend the quote.
- **spans a structural line or a speaker change** → error naming the offending line.
- **found exactly once** → code writes `line`, `end_line`, `speaker` and `bundle_sha256`.

The model selects the relevant sentence; code performs exact anchoring. This is the single most important mechanic in the build.

**The failure is a downgrade, not a wall.** When a quote cannot support a stated requirement, the item is re-anchored to what was actually said and downgraded to `inferred` with a rationale, raising a clarification issue. When no quote supports it at all, it becomes a **candidate**. The design principle:

> The validator never silently fixes and never silently blocks. It converts an unsupported claim into a visible open question.

### 5.8 Enforcement model

Three layers, deepening — but only the third is structural, and the tool must report the tier it can **verify**, never the tier it hopes for.

1. **Pre-commit hook** — fast feedback, bypassable.
2. **GitHub Actions on the PR** — runs the checks, but the workflow and version pin live in the repo under review. On its own a *reviewer-controlled convention*, not enforcement.
3. **A validating workflow outside the project repo's write scope** — the only structural layer.

**Every condition is mandatory, and each must be verified by identity, not by shape** (D16). A required check says nothing about *what* is being required; a `CODEOWNERS` file says nothing about whether review is enforced or whether its patterns cover anything that matters.

| Tier | Every condition must hold |
|---|---|
| **T1 — Structural** | API confirms an org ruleset targets this repo's default branch **and** requires a workflow whose owner, repo, path **and** ref identify MERIT's `merit-validate.yml` |
| **T2 — Strong convention** | API confirms a required status check **and** `require_code_owner_reviews` on `main`; **and** the project workflow `uses:` MERIT's reusable workflow at a 40-hex SHA; **and** `CODEOWNERS` patterns actually match `.github/` **and** `merit.toml` |
| **T3 — Convention** | API confirms a required status check, and nothing above holds |
| **unverified** | Any condition cannot be read — no `gh`, no permission, API error | 

Failing any condition for a tier drops to the next, never to a hopeful one. Where the API cannot confirm, the honest report is `unverified`, and the deck must say "reviewer-controlled convention" rather than "structurally enforced". Overstating this is the one claim that would genuinely embarrass the project under scrutiny.

**Release bootstrapping order.** Two constraints bind here and rev 3 got the first backwards. A tag cannot contain a file committed after it, so tagging before committing the reusable workflow produces a `uses: …@<sha>` that resolves to a tree without the workflow. And a wheel attached to a GitHub release is **not a package index**: `pip install merit==0.1.0` has nothing to resolve against. The order is therefore:

1. Commit the release-ready package **and** `merit-validate.yml` together.
2. **Tag that commit.** Its SHA now contains the workflow the project repo will pin.
3. Publish the distribution through a declared channel — for the POC, the wheel attached to that release, fetched by explicit URL and **verified against a pinned SHA-256** before installation. A real index can replace this later without changing anything else.
4. **`merit init` resolves that tag's SHA at scaffold time** and substitutes it into the project workflow. The SHA is never hardcoded in a shipped template.

**A versioned URL is not an immutable artefact.** A GitHub release asset can be deleted and re-uploaded under the same name, so the URL guarantees a location, not bytes. The reusable workflow therefore downloads the wheel and checks its digest before installing:

```
curl -fsSL -o merit.whl "$MERIT_WHEEL_URL"
echo "$MERIT_WHEEL_SHA256  merit.whl" | sha256sum -c -
pip install ./merit.whl
```

Both the pinned digest and the resolved tag SHA containing the referenced workflow are tested, not assumed. Without the digest check, release-asset immutability would be a trusted GitHub control rather than a verified one — a dependency worth not taking silently in a tool whose entire claim is that nothing is trusted on assertion.

### 5.9 Model-agnostic harness contract

The capability floor is: **read a file, write a file, run a shell command.**

- `AGENTS.md` is the single operating manual; `CLAUDE.md` and `GEMINI.md` point at it.
- Playbooks are served by `merit prompt`, versioned with the tool.
- The agent's only write is a proposal under `proposals/`; everything else is a shell call (§5.15).

**Degraded path** (no CLI agent): `merit pack --stage draft` emits one paste-able Markdown bundle. The reply is saved as a proposal and run through `merit apply` — the *same* command the CLI agent uses. The browser route is not a special case; it is the normal write path with a human carrying the payload.

### 5.10 Git workflow

| Git | Means |
|---|---|
| branch | start a working draft |
| commit | save a checkpoint |
| pull request | submit for review |
| tag | approve this baseline |

Per D6 the agent may create branches, commit, push and open PRs. It may **not** merge or tag. See §5.16 for how a browser-only approver performs the tag.

### 5.11 The report

`merit report` emits a **single self-contained HTML file** with repo data embedded as JSON. No server, no build chain, works offline, emailable. Read-only — input stays in chat.

**Embedding is an injection surface.** A transcript quote containing `</script>` would terminate the script element and break out of the payload. All embedded JSON escapes `<`, `>` and `&` as `<`, `>`, `&`, and all rendered text is escaped on insertion. This is a correctness bug and a security bug in a file people forward by email.

Testing this by string-splitting around `</script>` is worthless — that cannot detect an early close, which is exactly the attack. The requirement is verified by **parsing** the output: the document contains exactly one `<script>` element, the payload inside it round-trips through `json.loads` to a value equal to the original quote character-for-character, and the parsed document contains no element the generator did not emit.

**Acceptance criteria.** "Emits HTML" is not the requirement; G6 is. The report is complete only when all of the following hold, each an automated structural assertion plus a manual browser pass before the demo:

| # | Must be present |
|---|---|
| R1 | A **blocker summary** at the top: what currently prevents baselining, in plain language, by requirement |
| R2 | Requirement cards showing status and provenance as distinct badges |
| R3 | Working **filter controls** for status and for provenance |
| R4 | Each card **expands** to its evidence: quote, speaker, source, session date, line number |
| R5 | Candidates in their own section, labelled *not requirements* |
| R6 | Issue board: each issue shows **effective status**, owner, drafted question, and both competing quotes |
| R7 | Decision register in **chronological order**, each showing record type and a provisional badge where applicable |
| R8 | Traceability matrix, requirement × source |
| R9 | Citation attention queue leading with uncited lines, percentage secondary |
| R10 | The baseline tag handoff block (§5.16) when a baseline is ready |

**The per-PR report preview is a build requirement.** Minimum viable: an Actions step that builds `public/`, uploads it as an artifact and posts a PR comment linking it. This works on any GitHub plan; Pages is a bonus.

### 5.12 Citation coverage

`merit citations` reports deterministically: *"these 29 substantive lines are not cited by any requirement"*, with the percentage as a secondary figure. A substantive line is any line whose speaker is not `#` and whose text is at least `coverage.min_chars` characters (`merit.toml`, default 40).

**This is an attention queue, not a completeness metric.** It measures whether a line has been *cited*, not whether it has been *understood*: a requirement can cite a line and misrepresent it entirely, and a correctly-ignored pleasantry counts against the percentage. §14 states the limit plainly.

### 5.13 How an agent discovers the CLI

1. **`merit help-agent` is the single entry point**, and step one of every session per `AGENTS.md`.
2. **The manual is generated from the argparse tree at runtime**, so it cannot describe a flag that does not exist nor omit one that does.
3. **`merit help-agent --json`** emits the same catalogue machine-readably.
4. **Playbooks are just-in-time** via `merit prompt <stage>`.
5. **Errors teach** — every failure names what is wrong, which check it violated, and the exact next command.

**Exit codes are a contract:** `0` success · `1` validation failure · `2` usage error · `3` anchor failure · `4` environment error.

`merit doctor` checks the environment — installed version against the pin, git configuration, `gh` presence, repo validity, **the verified enforcement tier (§5.8)**, and **whether the latest baseline manifest has a matching tag (§5.16)**.

### 5.14 Tool repo structure

```
merit/
  AGENTS.md                   # how to develop MERIT itself — see §5.17
  pyproject.toml
  src/merit/
    cli.py                    # argparse tree — single source of truth for the surface
    config.py  model.py  errors.py
    ingest/                   # vtt, docx, txt, canonical, bundle, metadata
    anchor.py
    apply/                    # proposal parsing, operations, transaction, ID allocation
    validate/
      runner.py               # check registry, Finding, severity
      checks.py               # V1-V12, each registered under its own ID
    citations.py  baseline.py  questions.py  doctor.py  pack.py  git_ops.py
    report/ (build.py, template.html)
    schemas/*.schema.json     # package data
    prompts/*.md              # package data
    templates/                # what `merit init` writes
  tests/fixtures/<name>/
  .github/workflows/
    test.yml                  # the tool's own tests
    merit-validate.yml        # the SHA-pinnable reusable workflow
```

**Schemas and prompts are package data**, the mechanism that makes §5.2 true. **Each check is registered under its own ID**, so tests assert the right rule fired and errors name the rule violated.

### 5.15 The write path

**No agent and no user ever writes a governed artefact (G8, D14).** The instruction "never hand-edit" is worthless without a mechanism that makes compliance possible; before rev 3 there was no command to create a requirement, an issue, or a status change, so the only way to work was the behaviour the design forbids.

The agent writes a **proposal** — an ordinary file under `proposals/`, which is ungoverned scratch — and runs `merit apply`.

```yaml
schema_version: 1
operations:
  - op: add_requirement
    statement: Audit logs shall be retained for 7 years.
    type: constraint
    provenance: stated
    acceptance_criteria: ["Records older than 7 years are purged automatically."]
    evidence:
      - source: sources/2026-09-12-sample-interview.md
        quote: "We need the logs kept for seven years, that's the audit requirement."
  - op: add_candidate
    statement: The system should offer a dark theme.
    rationale: Raised by the facilitator; no user stated this.
  - op: set_status
    id: REQ-SMP-0014
    status: deferred
    reason: Awaiting interface specification.
```

Operations: `add_requirement` · `add_candidate` · `add_issue` · `add_decision` · `confirm_decision` · `promote_candidate` · `set_status` · `dismiss_issue` · `add_acceptance_criterion` · `record_supersession`.

`confirm_decision` sets `confirmed_by` **and** its `confirmation_record` on an existing provisional decision, anchoring the record like any other evidence (D20). Without the operation a provisional decision could never be made binding except by hand-editing; without the record requirement, confirming would be free.

`merit apply` owns everything the agent must not: **ID allocation** (the proposal never names a new ID), file paths, state transitions, anchoring of every quote, and validation. It is **transactional** — staged, with every operation applied, every quote anchored and the full check suite run before anything moves into the working tree. Any failure leaves the repo byte-identical and reports which operation failed and why.

**The staged tree must be validated in the real repository's context.** A bare temporary directory has no `merit.toml` and no `.git`, so configuration cannot load and the comparison point (§5.6) degrades to `none` — which would silently disable V2 and V4 at the exact moment writes are committed. The check runner therefore takes an explicit **context root**: artefacts are read from the staged tree while configuration and git history come from the real repository. An `apply` that attempts a historical source mutation or an ID removal must fail V2 or V4 like any other change.

**`proposals/` is ignored scratch.** It is excluded by `.gitignore` at scaffold time and never validated. Agent output there is unreviewed and may quote sensitive material; it must not reach a pull request. `merit apply` removes a proposal once it has been applied successfully, and `merit doctor` warns if any file under `proposals/` is tracked.

This is also the degraded path (§5.9). One mechanism, two ways of carrying the payload.

**The one exception: `merit ingest`.** Sources enter the repository through `ingest`, not through a proposal, because they originate as binary files outside the repo. The exception is narrow and enforced: **`ingest` may only create new files under `sources/`; it may never modify or delete an existing one** (§5.4). Every other governed directory is writable only by `apply`.

### 5.16 The tag handoff

D6 forbids the tool from tagging, and G6 requires a browser-only approver to complete a baseline. Both hold only if the handoff is specified — and it cannot be specified by naming a commit in advance, because the commit that carries the manifest does not exist until the manifest is committed.

**The order is therefore: prepare → commit → review → merge → tag.**

1. `merit baseline` writes `baselines/sample-b1.yaml` into the working tree, including `governed_tree_hash` over every governed file **except the manifest itself**, and prints the **handoff block**: the tag name and what happens next.
2. The manifest is committed and pushed as part of the pull request under review. The approver sees it alongside everything it attests to.
3. On merge, a CI step detects the newly added manifest and posts a **prefilled release link** naming both the tag and the exact merge commit:
   `https://github.com/<org>/<repo>/releases/new?tag=baseline/sample-b1&target=<merge-sha>`
   The approver clicks it and presses Publish.

**Targeting `main` would be a race.** Telling the approver to select `main` means the tag lands on whatever the branch tip happens to be when they get to it — which may include commits merged after the one under review, silently pulling unrelated governed artefacts into the tagged snapshot. The prefilled link pins the target to the merge commit, and the approver still never types a SHA.

The handoff block appears in the report (R10) and the PR body, so the approver meets it where they already are; the prefilled link appears after merge, which is the first moment the commit exists.

**`merit doctor` verifies the tag by content, never by a recorded SHA:**

- a tag named `baseline/<id>` exists;
- the commit it points at **contains** that manifest file;
- the manifest's per-requirement content hashes match the requirements at that commit;
- the manifest's **`governed_tree_hash` matches the governed tree at that commit**, excluding the manifest.

The last one is what closes the race: a tag on a later commit carrying extra issues, sources or decisions fails even when every recorded requirement still matches. It also catches drift between preparing the baseline and merging it, in which case the answer is to re-run `merit baseline`.

### 5.17 Curating the tool's own `AGENTS.md`

The tool repo carries its own `AGENTS.md` for developing MERIT. Its purpose is **context economy**: a future session should recover the project's non-obvious invariants without re-reading the codebase or re-deriving decisions already made.

**What belongs in it** — only things that are true, load-bearing, and *not* discoverable by reading one file:

- Invariants that span modules: schemas cover structure only and never cross-field rules; every check registers under its own ID; `open_issues` and `awaiting_confirmation` are derived and never stored; the tool never merges or tags.
- Decisions that reverse an earlier approach, with one line of why — a future session that doesn't know *why* append-only was rejected will propose it again.
- Where the non-obvious things live: schemas and prompts are package data; the argparse tree is the source of truth for the agent manual.
- Commands to verify work: the test command, and which fixtures must pass versus fail.

**What does not belong**: anything a `grep` answers (function locations, file lists), narration of what the code does, status updates, or a changelog. Those rot and consume context without earning it.

**When to update it** — four triggers, each a real signal rather than a schedule:

1. **After adding or renumbering a check** — the ID registry is the most consulted fact in the repo.
2. **After changing an interface consumed by more than one module** (`Finding`, `ComparisonPoint`, `effective_status`, the proposal operation set).
3. **After a decision that reverses an earlier one** — record the reversal and the reason, or it will be re-litigated.
4. **When a session had to re-derive something that was already settled** — that is direct evidence of a gap, and the fix is a line in `AGENTS.md`, not a longer prompt.

**Keep it short.** Past roughly 200 lines it stops being context economy and becomes context cost; when it grows beyond that, the fix is to delete what the code now says plainly, not to add a summary. Review it at the end of each build stage (§9) and cut as readily as you add.

---

## 6. CLI surface

| Command | Purpose |
|---|---|
| `merit init` | Scaffold a governed project repo, resolving the reusable-workflow SHA at scaffold time |
| `merit init --github` | Also create the remote and configure the strongest **verifiable** enforcement tier |
| `merit doctor` | Environment, version pin, git/`gh`, repo validity, verified tier, baseline tag presence |
| `merit ingest` | Convert to canonical form, retain the original, compute the bundle hash, write the sidecar |
| `merit ingest --inspect-metadata` | Report embedded metadata in an original before anything is committed |
| `merit apply <proposal>` | **The only write path for governed artefacts** (§5.15) |
| `merit anchor <file>` | Resolve unanchored quotes; verify anchored ones |
| `merit validate [--strict]` | Run V1–V12 |
| `merit citations` | Attention queue of uncited substantive lines |
| `merit report` | Generate the self-contained HTML report |
| `merit questions --open` | Clarification brief, including `awaiting_confirmation` issues |
| `merit baseline [--dry-run]` | Readiness check, manifest, and the tag handoff block (§5.16) |
| `merit assess --against <tag>` | Change assessment (stretch) |
| `merit pack --stage <s>` | Emit the paste-able bundle for the browser route |
| `merit pr-body` | Generate PR description from repo state |
| `merit help-agent [--json]` | Operating manual and command catalogue, generated from the CLI definitions |
| `merit prompt <stage>` / `merit schema <artefact>` | Serve stage playbooks and JSON Schemas |

`decide`, `confirm_decision`, `promote` and `supersede` are **operations inside a proposal**, not separate commands — one write path, not several (D14). `ingest` is the sole create-only exception (D19).

---

## 7. User journeys

Six journeys were validated in design during the conversation of 2026-10-17/18. Stage numbers refer to §2.

| | Journey | Covers | Primary actor |
|---|---|---|---|
| A | Cold start — a pile of files, nothing else | Stages 1–2 | P1 |
| B | The guardrail encounter | V5, anchoring, provenance downgrade | P1 |
| C | Two people said opposite things | Stages 3–4 | P1 → P3 |
| D | Locking a baseline | Stage 5, PR review, tag handoff (§5.16) | P1 + P2 |
| E | New inputs land weeks later | Stage 6 | P1 |
| F | No agent at all, browser chat only | Degraded path (§5.9) | P1 |

### Cast

| | Who | Technical level | Where they work |
|---|---|---|---|
| P1 | Requirements Lead — owns repo content, runs the loop | Non-technical; follows prompting | Chat + the HTML report |
| P2 | Approver — signs off a baseline and creates its tag | Non-technical | Browser only |
| P3 | Interviewee — whose words become evidence | n/a | Meetings and email |
| P4 | Developer — consumes the tagged baseline | Technical | `git checkout baseline/sample-b1` |

### Cross-cutting UX principles

1. The agent narrates git in domain terms, then shows the real command. Never silent.
2. Unsupported claims are downgraded and surfaced, never deleted or forced through.
3. P1 never reads or writes YAML — not even a proposal, which the agent composes.
4. Nothing is deleted, only recorded with a reason.
5. Every open question is attributable to a named person and a date.
6. P2 never leaves the browser, including for the tag.

---

## 8. Scope

**In scope.** Stages 1, 2, 3 and 5 in full; stage 4 via proposal operations; the validator (V1–V12); anchoring; the transactional write path; candidates; source bundles, immutability and supersession; original retention and re-extraction; the HTML report meeting R1–R10; `merit citations`; the SHA-pinned reusable workflow with verified tier detection; the tag handoff; the browser route.

**Stretch.** Stage 6 change assessment (`merit assess`).

**Explicitly out.** Multi-project support · authentication · any live service · redaction tooling · Jira/Confluence import · a terminal-free variant where the web UI itself writes proposals.

---

## 9. Build order

Logical stages, not a calendar. Each has an exit criterion that must hold before the next begins.

| | Stage | Exit criterion |
|---|---|---|
| **B1** | **Foundations and scaffold** — packaging, config, minimal `merit init`, `merit doctor` | `merit init` produces a repo whose structure `doctor` reports healthy |
| **B2** | **Evidence intake** — ingest, canonical format, bundle hashing, original retention, atomicity | A transcript round-trips, is bundle-pinned, and re-extracts from its retained original |
| **B3** | **The gate** — schemas, anchoring, V1–V12, full fixture suite | Every fixture fails with its own check ID; all must-pass fixtures pass |
| **B4** | **The write path** — proposals, operations, transaction, ID allocation | A proposal creates requirements, candidates and issues with no direct YAML write; a failing proposal leaves the repo byte-identical |
| **B5** | **Agent interface** — `AGENTS.md`, `help-agent`, playbooks | A cold agent drives intake → draft end-to-end writing only proposals |
| **B6** | **The report** — HTML generator meeting R1–R10, citations | Manual browser pass: an approver reviews a draft baseline without seeing YAML |
| **B7** | **Baseline and handoff** — `merit baseline`, manifest, tag handoff | A conflict goes open → awaiting_confirmation → confirmed → baselined → merged → tagged by a browser-only approver, with `doctor` verifying the tag by content |
| **B8** | **Enforcement** — release bootstrap, reusable workflow, verified tier detection | A PR with a fabricated quote is blocked by CI; `doctor` reports a verified tier or `unverified` |
| **B9** | **Browser route** — `pack` | The same artefacts are produced with no CLI agent |
| **S** | **Stretch** — `merit assess` | New source against a baseline yields new / changed / contradicted / unaffected |

**Hard ordering constraints:** B3 before B4 (the gate must exist before the write path that depends on it); B4 before B5 (an agent cannot be told to use a command that does not exist).

---

## 10. Testing strategy

**The validator is the product.** Fixtures are written before the checks they exercise.

Each must fail with the named check:

| Fixture | Must fail |
|---|---|
| `hallucinated_quote` · `paraphrased_quote` · `wrong_speaker` | V5 |
| `quote_spanning_speaker_change` · `quote_spanning_structural_line` | V5 / anchor |
| `ambiguous_quote` | anchor, listing candidates |
| `tampered_canonical_text` · `meta_missing_record_type` | V1 |
| `raw_original_deleted` · `raw_original_tampered` | V1 |
| `meta_record_type_tampered` · `meta_authored_by_tampered` | V1 (bundle hash) |
| `source_modified` · `source_appended` | V2 |
| `malformed_yaml` | V3, naming file and line |
| `renumbered_id` | V4 |
| `inferred_without_rationale` · `candidate_promoted_to_missing_requirement` | V6 |
| `issue_with_one_quote` | V7 |
| `baselined_with_open_issue` · `provisional_decision_closing_issue` | V8 |
| `decision_without_record` · `decision_record_hash_mismatch` | V9 |
| `self_note_confirmed_by_its_own_author` | V9 — self-confirmation |
| `invented_confirmer_without_record` | V9 — `confirmed_by` with no `confirmation_record` |
| `confirmation_record_from_a_self_note` | V9 — confirmation must come from an attributable source |
| `confirmation_record_speaker_mismatch` | V9 — confirmer does not match the record |
| `tag_on_commit_with_extra_governed_files` | `doctor` — `governed_tree_hash` mismatch |
| `apply_mutating_a_historical_source` · `apply_removing_an_id` | V2 / V4 **through `apply`** |
| `deferred_without_reason` · `baselined_without_acceptance_criteria` | V10 |
| `baselined_citing_superseded_source` | V11 |
| `canonical_not_matching_original` | V12 |

Must **pass** (exit 0): `known_good` · `first_run_no_baseline` · `extractor_version_drift` (V12 advisory, not failure) · **`self_note_decision_without_confirmation`** — a provisional decision is a *valid* state that V9 permits and V8 blocks from baselining (§5.6). This fixture passing is what proves `awaiting_confirmation` is reachable at all.

A fixture that fails for the wrong reason counts as a failing test. **Assert on the check ID**, never on a non-zero exit alone.

Separately: `test_help_agent.py` asserts every command and flag in the argparse tree appears in `merit help-agent` output; `test_apply_transaction.py` asserts a failing proposal leaves the repo byte-identical; `test_report_acceptance.py` asserts R1–R10 structurally; and a **manual browser UAT checklist** is run once before the demo.

---

## 11. Demo script

1. Two sources go in, bundle-pinned, originals retained.
2. The agent writes a *proposal*; `merit apply` allocates IDs and anchors every quote. One ungrounded idea lands in **Candidates**, visibly not a requirement.
3. **Have the agent cite a plausible paraphrase. The proposal is rejected and the repo is untouched.** Then show the same thing reaching CI red on a PR.
4. The conflict surfaces with both quotes side by side; the requirement holds at `clarifying`.
5. **Resolve it with a self-written note. The decision is accepted and recorded — and the issue reads *awaiting confirmation*, not resolved, while the baseline still refuses to tag.** Then a second named person confirms via `confirm_decision`, and the block clears.
6. The approver reviews in the browser, merges, and tags the merge commit from the handoff block.
7. *(stretch)* A third source arrives → change report.

Steps 3 and 5 are what people remember. Step 5 is the stronger: the tool refusing to accept its own operator's word.

---

## 12. Data strategy

The primary corpus is cleared, redacted transcripts from a live project. Candidates are identified but unconfirmed; per D10 this is **off the critical path**.

**Fallback, confirmed: dogfood.** The MERIT design conversation of 2026-10-17/18 is itself a requirements corpus with genuine provenance and real decisions — including several reversals recorded mid-conversation, which exercise supersession and `revision`-kind issues better than a clean transcript would. It needs no clearance, so a working demo exists regardless of approval timelines.

---

## 13. Risks and items to verify

| Risk | Mitigation |
|---|---|
| **Org-level required workflows (T1) may need a plan tier the org lacks** | `doctor` reports the verified tier or `unverified`; never overstate |
| **Release bootstrap ordering** — template cannot pin a SHA that does not exist | `init` resolves the tag's SHA at scaffold time (§5.8) |
| Branch protection / Pages on private repos may need a paid plan | Artifact-plus-PR-comment path works on any plan |
| **Retained originals may carry metadata the canonical text dropped** | `sources/raw/` inherits highest classification; `--inspect-metadata` pre-commit |
| Retained originals grow the repo | Acceptable at POC scale; revisit with Git LFS beyond a few hundred MB |
| `gh` absent on the target laptop | `doctor` detects it; tier reports `unverified` |
| Cleared data may not arrive | §12 fallback |
| Minutes are human paraphrase | §14.3 |
| Stage 6 overruns the demo | Stretch; cut without hesitation |

---

## 14. Honest limits

1. **MERIT guarantees no fabricated evidence. It does not guarantee complete recall.** Detection is LLM judgment; the guardrail forces a *claimed* conflict to cite two real anchored quotes, so the model cannot invent — but it can overlook.
2. **Citation is not comprehension.** `merit citations` proves a line was *looked at*, not that the requirement citing it represents what the speaker meant. A worklist, never a score.
3. **Fidelity is to the record, not to reality.** With a minute rather than a verbatim transcript, the chain runs to what the minute-taker wrote. MERIT makes that chain explicit, tamper-evident and re-derivable; it cannot reach behind it. `record_type` keeps the distinction visible.
4. **Conflict detection is a judgment; conflict *citation* is enforced.**
5. **Enforcement is only as structural as the verified tier.** At T2, T3 or `unverified`, a motivated actor with write access can weaken the validating workflow in the same PR that uses the weakness. The mitigation is a second reviewer — a social control, not a mechanical one.

---

## 15. Open questions

None blocking. Items to confirm during build are in §13.

---

## Appendix A — Source material

- Design conversation, 2026-10-17 to 2026-10-18, including the rev 2 and rev 3 reviews. This is the only external reference; everything required to implement MERIT is stated here.

## Appendix B — Revision history

| Rev | Change |
|---|---|
| 1 | Initial specification |
| 2 | Enforcement tiers replace the false "the split is the boundary" claim · sources immutable, supersession replaces append-only · originals retained and re-extraction verified · candidates split from requirements · decision records gain `record_type`/`authored_by`/`confirmed_by` · coverage renamed and reframed |
| 3 | **Transactional write path** (§5.15, D14, G8) — there was previously no compliant way to create a record · **effective status** unifies provisional state (§5.5.1) · **source bundle** extends integrity to the original and the metadata (§5.4, D11, D15) · **tier verification** required before claiming T1/T2, plus release bootstrap ordering (§5.8, D16) · **report acceptance criteria R1–R10** and the **tag handoff** (§5.11, §5.16, G6) · cross-speaker spans rejected · ingest atomicity and collision rules · HTML escaping in the report · tool `AGENTS.md` curation guidance (§5.17) |
| 4 | **V9 permits provisional decisions** — rev 2–3 forbade the state V8 and `effective_status` depend on, making `awaiting_confirmation` unreachable under transactional apply (§5.6, D7) · **`confirm_decision` operation** added, without which a provisional decision could never become binding (§5.15) · **apply validates in the real repo's context** (D17) — a bare temp tree degraded V2/V4 to no-ops at the moment of writing · **`target_commit` removed** as self-referential; tag applied after merge and verified by content (D18, §5.16) · **release order corrected** — commit package and workflow, then tag, then publish; install by immutable wheel URL, since a release asset is not an index (§5.8) · **tier conditions all mandatory and identity-checked** (§5.8, D16) · **ingest named as the sole create-only exception** to the write path (D19) · `proposals/` gitignored and cleaned · injection verified by parsing, not string-splitting (§5.11) |
| 5 | **Confirmation requires evidence** (D20) — `confirm_decision` previously wrote only a name, so an invented confirmer cleared the block; now needs an anchored `confirmation_record` from a non-self-note source matching the confirmer · **tag race closed** (D21) — handoff pins the merge commit via a prefilled release link instead of `main`, and `governed_tree_hash` lets `doctor` reject a tag carrying unrelated governed artefacts · **wheel digest pinned** — a versioned release URL guarantees a location, not bytes |
