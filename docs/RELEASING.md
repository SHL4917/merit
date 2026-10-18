# Releasing MERIT

The bootstrap order is mandatory:

1. Commit the release-ready package and `.github/workflows/merit-validate.yml`
   together. Before committing, build the final package deterministically with
   `SOURCE_DATE_EPOCH=315532800`, setuptools 84.0.0 and wheel 0.48.0. Measure the
   wheel's SHA-256 and embed it as the literal `MERIT_WHEEL_SHA256` in that
   external workflow. Match `pyproject.toml` and `merit.__version__`.
   The external workflow is excluded from the wheel; the packaged wrapper
   contract has a placeholder, so this ordering has no circular hash.
   Run `python -m pytest -q -W error`.
2. Tag that commit, for example `git tag -a v0.1.0 -m "MERIT 0.1.0"`.
   Resolve it with `git rev-parse 'v0.1.0^{commit}'`, then confirm the resolved
   commit contains `.github/workflows/merit-validate.yml` with
   `git cat-file -e 'v0.1.0:.github/workflows/merit-validate.yml'`.
3. Publish the wheel through a GitHub release attached to that existing tag.
   After publication is authorized, push the commit and tag to the intended
   MERIT remote. The `release.yml` workflow checks the tag against both package
   versions, verifies the workflow exists in its tree, runs
   `python -m build --wheel --no-isolation` with the same timestamp and build
   tools and normalized LF package text. It canonicalizes ZIP entry order,
   timestamp and permissions with uncompressed entries so Windows and Linux
   produce the same wheel bytes, refuses a digest mismatch against the tagged
   workflow, computes and
   checks `dist/SHA256SUMS`, and attaches
   the wheel and checksum file before publishing the release. It never creates
   or moves a tag, and never overwrites an existing release asset.
4. Record the release asset's explicit HTTPS URL and its 64-hex SHA-256 digest.
   `merit init` resolves the tag's commit SHA at scaffold time and substitutes
   that SHA into the project workflow's `uses:` reference; no guessed SHA belongs
   in a shipped template. Configure only the HTTPS `wheel-url` transport location
   in the caller. The external wrapper owns the digest; changing a caller URL
   cannot change the accepted validator bytes. Scaffold and tier verification
   refuse legacy wrappers that accept a caller-supplied digest. The pre-R78
   local `v0.1.0` tag is such a legacy wrapper and is not moved by this change.

The reusable interface is `workflow_call`, with one required string input,
`wheel-url`. The caller grants `contents: read`. A GitHub
release is an asset host, so installation uses its explicit wheel URL. A
versioned URL identifies a location; the pinned digest identifies the bytes.

The validator downloads to `merit.whl` in runner temporary storage and verifies
it with `sha256sum -c` before installation. The filename comes from the URL's
path basename, excluding any query or fragment; it must parse as a valid wheel
filename for MERIT and contain only safe filename characters. Only after the
digest check succeeds does the workflow rename the verified bytes to that
validated basename and install the resulting local wheel. Pip requires version
and platform tags and rejects the design's illustrative `merit.whl`
installation name.
Checkout includes complete history so immutability and baseline checks retain
their Git comparison points.

For an offline preflight, when setuptools and wheel are already installed:

```text
python -m pip wheel --no-index --no-build-isolation --no-deps --wheel-dir dist .
```

Inspect the wheel's templates, schemas, prompts and report assets, and record its
SHA-256 (PowerShell: `Get-FileHash dist/*.whl -Algorithm SHA256`). Set the timestamp
first in PowerShell with `$env:SOURCE_DATE_EPOCH='315532800'` and use the pinned
build tools above. Normalize packaged text to LF before building, as release CI
does, and canonicalize the wheel's ZIP metadata using the release workflow's
recipe. The packaging
test performs an isolated offline build when local build tools are available;
CI installs those tools so that test executes there. `MERIT_BUILD_PYTHON` can
select an already provisioned Python interpreter for that offline test.

Local preparation ends after the commit, tag, wheel and digest checks. Pushing
the tag, creating the remote release and uploading assets require publication
authorization. MERIT's project CLI never merges or tags; the release tag above
is a maintainer operation in this tool repository.
