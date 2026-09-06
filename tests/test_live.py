"""One optional integration test against the real Anthropic API.

Deselected by default (``addopts = -m 'not live'``). Run it deliberately:

    pytest -m live

It costs a few cents and needs ``ANTHROPIC_API_KEY``. Everything else in this
suite runs offline.
"""

from __future__ import annotations

import os

import pytest

from intentbench.catalog import load_catalog, render_catalog
from intentbench.judge.anthropic import DEFAULT_MODEL, AnthropicJudge
from intentbench.models import NO_MATCH
from tests.conftest import catalog_path

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"),
]


@pytest.fixture(scope="module")
def weather_tools():
    catalog = load_catalog(catalog_path("weather"))
    return render_catalog(catalog).tools


def test_live_selects_a_plausible_intent(weather_tools) -> None:
    judge = AnthropicJudge(model_id=DEFAULT_MODEL)
    result = judge.select("what's the air quality in Los Angeles", weather_tools, "en-US")
    assert result.error is None
    assert result.selected_intent == "OpenWeatherAirQualityIntent"
    assert "los angeles" in str(result.parameters.get("location", "")).casefold()
    assert result.input_tokens > 0


def test_live_abstains_on_an_out_of_scope_request(weather_tools) -> None:
    """The escape hatch has to actually work under forced tool choice."""
    judge = AnthropicJudge(model_id=DEFAULT_MODEL)
    result = judge.select("add milk to my shopping list", weather_tools, "en-US")
    assert result.error is None
    assert result.selected_intent == NO_MATCH
