"""An attention queue of substantive source lines needing requirement citations.

A citation says nothing about whether a requirement represents its source well.
"""

from dataclasses import dataclass
from pathlib import Path

from merit.anchor import anchor_evidence
from merit.config import Config
from merit.ingest.canonical import parse_canonical
from merit.model import load_repo


@dataclass(frozen=True)
class UncitedLine:
    source: str
    line: int
    speaker: str
    text: str


@dataclass(frozen=True)
class CitationCoverage:
    # The actionable worklist precedes the secondary figures in data and text.
    uncited: tuple[UncitedLine, ...]
    substantive_count: int
    cited_count: int

    @property
    def percentage(self) -> float:
        return 100 * self.cited_count / self.substantive_count if self.substantive_count else 0.0

    def __str__(self) -> str:
        rows = [f"{len(self.uncited)} substantive lines are not cited by any requirement"]
        rows.extend(f"{item.source}:{item.line} {item.speaker}: {item.text}"
                    for item in self.uncited)
        rows.append(f"{self.percentage:.1f}% of substantive lines cited "
                    f"({self.cited_count}/{self.substantive_count}).")
        return "\n".join(rows)


def citation_coverage(repo_root: Path, config: Config) -> CitationCoverage:
    """List uncited substantive canonical lines in source-path/line order.

    Only requirement evidence counts. Anchoring verifies the cited bundle and
    resolves whole canonical spans, including evidence awaiting filled anchors.
    Empty queues with no substantive lines report zero percent.
    """
    root = Path(repo_root)
    repo = load_repo(root)
    substantive = {}
    for path in sorted((root / "sources").glob("*.md")):
        source = path.relative_to(root).as_posix()
        for line in parse_canonical(path.read_text(encoding="utf-8")):
            if line.speaker != "#" and len(line.text) >= config.coverage_min_chars:
                substantive[(source, line.number)] = UncitedLine(
                    source, line.number, line.speaker, line.text)
    cited = set()
    for requirement in repo.requirements.values():
        for evidence in requirement.evidence:
            anchored = anchor_evidence(evidence, root)
            cited.update((anchored.source, number) for number in range(
                anchored.line, (anchored.end_line or anchored.line) + 1))
    uncited = tuple(substantive[key] for key in sorted(substantive) if key not in cited)
    return CitationCoverage(uncited, len(substantive), len(substantive.keys() & cited))
