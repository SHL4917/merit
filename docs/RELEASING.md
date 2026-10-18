# Releasing MERIT

The bootstrap order is mandatory:

1. Commit the release-ready package and `.github/workflows/merit-validate.yml`
   together. Match `pyproject.toml`, `merit.__version__`, and the reusable
   workflow's versioned wheel filename. Run `python -m pytest -q -W error`.
2. Tag that commit, for example `git tag -a v0.1.0 -m "MERIT 0.1.0"`.
   Resolve it with `git rev-parse 'v0.1.0^{commit}'`, then confirm the resolved
   commit contains `.github/workflows/merit-validate.yml` with
   `git cat-file -e 'v0.1.0:.github/workflows/merit-validate.yml'`.
3. Publish the wheel through a GitHub release attached to that existing tag.
   After publication is authorized, push the commit and tag to the intended
   MERIT remote. The `release.yml` workflow checks the tag against both package
   versions, verifies the workflow exists in its tree, runs
   `python -m build --wheel`, computes and checks `dist/SHA256SUMS`, and attaches
   the wheel and checksum file before publishing the release. It never creates
   or moves a tag, and never overwrites an existing release asset.
4. Record the release asset's explicit HTTPS URL and its 64-hex SHA-256 digest.
   `merit init` resolves the tag's commit SHA at scaffold time and substitutes
   that SHA into the project workflow's `uses:` reference; no guessed SHA belongs
   in a shipped template. Configure `wheel-url` and `wheel-sha256` from the
   reviewed release. Keep the digest pinned in the caller rather than fetching
   a replacement digest at validation time.

The reusable interface is `workflow_call`, with required string inputs
`wheel-url` and `wheel-sha256`. The caller grants `contents: read`. A GitHub
release is an asset host, so installation uses its explicit wheel URL. A
versioned URL identifies a location; the pinned digest identifies the bytes.

The validator downloads to `merit.whl` in runner temporary storage and verifies
it with `sha256sum -c` before installation. It then renames those verified bytes
to `merit-0.1.0-py3-none-any.whl`: pip requires a valid wheel filename containing
version and platform tags and rejects the design's illustrative `merit.whl`
installation name. The install command uses only that verified local wheel.
Checkout includes complete history so immutability and baseline checks retain
their Git comparison points.

For an offline preflight, when setuptools and wheel are already installed:

```text
python -m pip wheel --no-index --no-build-isolation --no-deps --wheel-dir dist .
```

Inspect the wheel's templates, schemas, prompts and report assets, and record its
SHA-256 (PowerShell: `Get-FileHash dist/*.whl -Algorithm SHA256`). The packaging
test performs an isolated offline build when local build tools are available;
CI installs those tools so that test executes there. `MERIT_BUILD_PYTHON` can
select an already provisioned Python interpreter for that offline test.

Local preparation ends after the commit, tag, wheel and digest checks. Pushing
the tag, creating the remote release and uploading assets require publication
authorization. MERIT's project CLI never merges or tags; the release tag above
is a maintainer operation in this tool repository.
