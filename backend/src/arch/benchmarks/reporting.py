"""Stable report writing for benchmark scripts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def write_json_report(path: Path, report: Mapping[str, Any]) -> None:
    """Write a readable JSON report without allowing output outside its parent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown_summary(path: Path, title: str, report: Mapping[str, Any]) -> None:
    """Write a short human-readable report that points to its exact JSON evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered_report = json.dumps(report, ensure_ascii=False, indent=2)
    lines = [f"# {title}", "", "```json", rendered_report, "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
