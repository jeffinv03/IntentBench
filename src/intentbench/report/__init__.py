"""Rendering runs for humans, machines, and CI."""

from intentbench.report.json_out import write_json
from intentbench.report.markdown import render_markdown, write_markdown
from intentbench.report.terminal import (
    console_for,
    render_catalog_table,
    render_diff,
    render_run,
)

__all__ = [
    "console_for",
    "render_catalog_table",
    "render_diff",
    "render_markdown",
    "render_run",
    "write_json",
    "write_markdown",
]
