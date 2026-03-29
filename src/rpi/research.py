"""Research types and markdown serialization."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field


class CodeReference(BaseModel):
    path: str = Field(description="File path, optionally with :LINE suffix")
    description: str = Field(description="What this file/location does")


class Finding(BaseModel):
    area: str = Field(description="Component or area name")
    description: str = Field(description="What exists, how it works, how it connects")
    key_files: list[CodeReference] = Field(description="Key files for this area")


class Research(BaseModel):
    title: str = Field(description="Research topic name")
    question: str = Field(description="Original research query")
    summary: str = Field(description="2-4 paragraph high-level overview")
    findings: list[Finding] = Field(description="Detailed findings by component/area")
    architecture: str = Field(description="How the pieces fit together — data flow, relationships")
    patterns: str = Field(description="Patterns and conventions found in the codebase")
    code_references: list[CodeReference] = Field(description="Consolidated list of important files")
    open_questions: list[str] = Field(description="Anything unresolved that may need human input")


def serialize_to_markdown(research: Research, date: str) -> str:
    """Serialize a Research object to markdown with YAML frontmatter."""
    lines: list[str] = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f"date: {date}")
    lines.append(f"topic: {research.title}")
    lines.append("status: complete")
    lines.append("---")
    lines.append("")

    # Research Question
    lines.append("## Research Question")
    lines.append("")
    lines.append(research.question)
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(research.summary)
    lines.append("")

    # Detailed Findings
    lines.append("## Detailed Findings")
    lines.append("")
    for finding in research.findings:
        lines.append(f"### {finding.area}")
        lines.append("")
        lines.append(finding.description)
        lines.append("")
        for ref in finding.key_files:
            lines.append(f"- **{ref.path}**: {ref.description}")
        lines.append("")

    # Architecture / Data Flow
    lines.append("## Architecture / Data Flow")
    lines.append("")
    lines.append(research.architecture)
    lines.append("")

    # Patterns and Conventions
    lines.append("## Patterns and Conventions")
    lines.append("")
    lines.append(research.patterns)
    lines.append("")

    # Code References
    lines.append("## Code References")
    lines.append("")
    for ref in research.code_references:
        lines.append(f"- **{ref.path}**: {ref.description}")
    lines.append("")

    # Open Questions
    lines.append("## Open Questions")
    lines.append("")
    for q in research.open_questions:
        lines.append(f"- {q}")
    lines.append("")

    return "\n".join(lines)


def research_file_path(title: str, date: str) -> Path:
    """Convert a research title and date into a file path under .claude/research/."""
    # Lowercase
    slug = title.lower()
    # Strip non-alphanumeric characters except spaces and hyphens
    slug = re.sub(r"[^a-z0-9 -]", "", slug)
    # Replace spaces with hyphens
    slug = slug.replace(" ", "-")
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")

    return Path(f".claude/research/{date}-{slug}.md")
