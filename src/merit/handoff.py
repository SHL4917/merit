"""Shared, side-effect-free baseline handoff for reports and pull requests."""

import re
from urllib.parse import quote


def handoff_block(manifest_id: str | None, commit_sha: str | None = None) -> str:
    """Describe the human handoff, pinning its release link only after merge.

    The release path is relative to the GitHub repository because this formatter
    has no repository context. Callers supply the exact merge commit when known.
    """
    if not manifest_id:
        return ""

    tag = f"baseline/{manifest_id}"
    # Code spans must use a delimiter longer than any embedded backtick run.
    # Render line breaks visibly to prevent blank lines ending the paragraph.
    visible_tag = tag.replace("\r", "\\r").replace("\n", "\\n")
    fence = "`" * (max((len(run) for run in re.findall(r"`+", tag)), default=0) + 1)
    display_tag = f"{fence} {visible_tag} {fence}"
    if commit_sha:
        url = (f"releases/new?tag={quote(tag, safe='')}"
               f"&target={quote(commit_sha, safe='')}")
        return (f"Baseline tag: {display_tag}\n\n"
                f"[Create the baseline release]({url}), then press Publish. "
                "The link selects the exact merge commit reviewed for this baseline.")

    return (f"Baseline tag: {display_tag}\n\n"
            "Follow this order: prepare -> commit -> review -> merge -> tag.\n\n"
            "Prepare the baseline manifest, then commit and push it with the pull "
            "request. Review the manifest alongside the artefacts it attests to, "
            "then merge the pull request. After merge, CI posts a prefilled release "
            "link selecting the exact merge commit. Open that link and press Publish "
            f"to create {display_tag}.")
