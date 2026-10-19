# AGENTS.md

This is a MERIT requirements repo. Run `merit help-agent` for your operating instructions and follow them exactly.

**Never write or edit a file under `sources/`, `requirements/`, `candidates/`, `issues/`, `decisions/`, `supersessions/` or `baselines/`. To change anything, write a proposal under `proposals/` and run `merit apply`.**

- `merit ingest` is the one exception: it alone adds files under `sources/`, and only creates them.
- `proposals/` is gitignored scratch. It is never validated and must never be committed.
- `sources/raw/` inherits the highest classification of anything inside it. An original `.docx` carries author names, revision history and tracked changes that a redacted canonical extraction may have dropped.

If `merit` is not on PATH, look for `$HOME\merit-venv\Scripts\merit.exe` first; otherwise install it: `pipx install "git+{source_url}@v{merit_version}"`
