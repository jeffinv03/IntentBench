"""Scoring — turning judge selections into outcomes and run metrics."""

from intentbench.scoring.aggregate import aggregate, resolve_repeats
from intentbench.scoring.match import (
    compare_parameters,
    normalize_value,
    score_case,
    values_match,
)

__all__ = [
    "aggregate",
    "compare_parameters",
    "normalize_value",
    "resolve_repeats",
    "score_case",
    "values_match",
]
