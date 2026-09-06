"""The machine-readable report — and the baseline format.

One schema, not two. What ``run --json`` writes is what ``diff`` reads.
"""

from __future__ import annotations

from pathlib import Path

from intentbench.baseline import save_report
from intentbench.models import RunReport


def write_json(report: RunReport, path: Path) -> None:
    save_report(report, path)
