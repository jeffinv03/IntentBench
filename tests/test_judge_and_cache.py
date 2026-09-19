"""Judge protocol, mock judge, and the response cache. No network, ever."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from intentbench.catalog.render import canonical_tools_json
from intentbench.judge import JudgeError, MockJudge, ResponseCache, get_judge
from intentbench.judge.anthropic import AnthropicJudge
from intentbench.judge.base import PROMPT_VERSION, SYSTEM_PROMPT, Judge, cost_usd
from intentbench.judge.cache import cache_dir, make_key
from intentbench.models import JudgeResult, RenderedCatalog, ToolSchema
from tests.conftest import CORPORA


def test_mock_judge_satisfies_the_protocol() -> None:
    judge = MockJudge(responses={"a": "AddItemIntent"})
    assert isinstance(judge, Judge)


def test_mock_judge_scripted_selection(synthetic_rendered: RenderedCatalog) -> None:
    judge = MockJudge(script_path=CORPORA / "synthetic-mock.yaml")
    result = judge.select("add milk to my shopping list", synthetic_rendered.tools, "en-US")
    assert result.selected_intent == "AddItemIntent"
    assert result.parameters == {"item": "Milk", "list": "shopping"}


def test_mock_judge_abstains(synthetic_rendered: RenderedCatalog) -> None:
    judge = MockJudge(script_path=CORPORA / "synthetic-mock.yaml")
    result = judge.select("what's the weather in Tokyo", synthetic_rendered.tools, "en-US")
    assert result.selected_intent is None


def test_mock_judge_default_for_unscripted_phrases(synthetic_rendered: RenderedCatalog) -> None:
    judge = MockJudge(script_path=CORPORA / "synthetic-mock.yaml")
    result = judge.select("something nobody scripted", synthetic_rendered.tools, "en-US")
    assert result.selected_intent is None


def test_mock_judge_phrase_matching_is_case_insensitive(
    synthetic_rendered: RenderedCatalog,
) -> None:
    judge = MockJudge(responses={"Add Milk": "AddItemIntent"})
    result = judge.select("  add milk  ", synthetic_rendered.tools, "en-US")
    assert result.selected_intent == "AddItemIntent"


def test_mock_judge_can_simulate_an_error(synthetic_rendered: RenderedCatalog) -> None:
    judge = MockJudge(responses={"a": {"error": "simulated 500"}})
    result = judge.select("a", synthetic_rendered.tools, "en-US")
    assert result.error == "simulated 500"
    assert result.selected_intent is None


def test_mock_judge_rejects_an_intent_outside_the_catalog(
    synthetic_rendered: RenderedCatalog,
) -> None:
    """The likeliest scripting mistake must not silently score as a wrong intent."""
    judge = MockJudge(responses={"a": "NotARealIntent"})
    result = judge.select("a", synthetic_rendered.tools, "en-US")
    assert result.error is not None
    assert "not in the tool list" in result.error


def test_mock_judge_missing_script(tmp_path: Path) -> None:
    with pytest.raises(JudgeError, match="No mock script"):
        MockJudge(script_path=tmp_path / "nope.yaml")


def test_get_judge_unknown_name() -> None:
    with pytest.raises(JudgeError, match="Unknown judge"):
        get_judge("gemini")


def test_get_judge_mock() -> None:
    assert get_judge("mock").name == "mock"


# --- cache ----------------------------------------------------------------


def test_cache_dir_respects_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_dir() == tmp_path / "intentbench"


def test_cache_dir_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert cache_dir() == Path.home() / ".cache" / "intentbench"


def _key(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "model_id": "m",
        "prompt_version": PROMPT_VERSION,
        "tools_json": "[]",
        "phrase": "add milk",
        "locale": "en-US",
    }
    base.update(overrides)
    return make_key(**base)


@pytest.mark.parametrize(
    "override",
    [
        {"model_id": "other"},
        {"prompt_version": 99},
        {"tools_json": '[{"name":"X"}]'},
        {"phrase": "add bread"},
        {"locale": "fr-FR"},
    ],
    ids=["model", "prompt", "tools", "phrase", "locale"],
)
def test_every_key_component_changes_the_hash(override: dict[str, Any]) -> None:
    assert _key() != _key(**override)


def test_key_is_stable() -> None:
    assert _key() == _key()


def test_key_fields_cannot_run_together() -> None:
    """A field separator prevents 'ab'+'c' colliding with 'a'+'bc'."""
    assert make_key(
        model_id="ab", prompt_version=1, tools_json="c", phrase="p", locale="l"
    ) != make_key(model_id="a", prompt_version=1, tools_json="bc", phrase="p", locale="l")


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    result = JudgeResult(selected_intent="AddItemIntent", parameters={"item": "milk"})
    key = _key()

    assert cache.get(key) is None
    cache.put(key, result)

    hit = cache.get(key)
    assert hit is not None
    assert hit.selected_intent == "AddItemIntent"
    assert hit.from_cache is True, "a hit must be labelled, so the report can say so"
    assert cache.count() == 1


def test_cache_never_stores_failures(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put(_key(), JudgeResult(error="429 rate limited"))
    assert cache.get(_key()) is None
    assert cache.count() == 0


def test_disabled_cache_is_inert(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, enabled=False)
    cache.put(_key(), JudgeResult(selected_intent="X"))
    assert cache.get(_key()) is None


def test_corrupt_entry_is_a_miss_not_an_error(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    key = _key()
    cache.put(key, JudgeResult(selected_intent="X"))
    path = next(tmp_path.rglob("*.json"))
    path.write_text("{{{ not json", encoding="utf-8")
    assert cache.get(key) is None


def test_cache_clear(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    for phrase in ("a", "b", "c"):
        cache.put(_key(phrase=phrase), JudgeResult(selected_intent="X"))
    assert cache.count() == 3
    assert cache.clear() == 3
    assert cache.count() == 0


# --- Anthropic judge, exercised with a fake client -----------------------


class FakeBlock:
    type = "tool_use"

    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.name = name
        self.input = payload


class FakeUsage:
    input_tokens = 1200
    output_tokens = 40


class FakeResponse:
    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.content = [FakeBlock(name, payload)]
        self.usage = FakeUsage()

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {"content": [{"type": "tool_use", "name": self.content[0].name}]}


class FakeMessages:
    def __init__(self, behavior: Any) -> None:
        self.behavior = behavior
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.behavior(len(self.calls), kwargs)


class FakeClient:
    def __init__(self, behavior: Any) -> None:
        self.messages = FakeMessages(behavior)


def _tools() -> list[ToolSchema]:
    return [ToolSchema(name="AddItemIntent", description="Adds an item.", input_schema={})]


def test_anthropic_judge_sends_the_right_request() -> None:
    client = FakeClient(lambda n, kw: FakeResponse("AddItemIntent", {"item": "milk"}))
    judge = AnthropicJudge(client=client)
    result = judge.select("add milk", _tools(), "en-US")

    sent = client.messages.calls[0]
    # The 1.x SDK has no `temperature` argument; it rides in the body instead.
    assert sent["extra_body"] == {"temperature": 0.0}, "temperature is 0, not configurable"
    assert sent["system"] == SYSTEM_PROMPT
    # Forced choice is safe because the escape hatch is in the tool list.
    assert sent["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    # The user turn is the phrase and nothing else.
    assert sent["messages"] == [{"role": "user", "content": "add milk"}]

    assert result.selected_intent == "AddItemIntent"
    assert result.parameters == {"item": "milk"}
    assert result.input_tokens == 1200
    assert result.raw_response, "raw responses are always retained"


class Boom(Exception):
    status_code = 429


def test_anthropic_judge_retries_then_succeeds() -> None:
    def behavior(n: int, kw: dict[str, Any]) -> Any:
        if n < 3:
            raise Boom("rate limited")
        return FakeResponse("AddItemIntent", {})

    client = FakeClient(behavior)
    judge = AnthropicJudge(client=client, sleep=lambda _s: None)
    result = judge.select("add milk", _tools(), "en-US")
    assert result.error is None
    assert len(client.messages.calls) == 3


def test_anthropic_judge_surfaces_failure_per_case_rather_than_aborting() -> None:
    client = FakeClient(lambda n, kw: (_ for _ in ()).throw(Boom("always down")))
    judge = AnthropicJudge(client=client, max_attempts=2, sleep=lambda _s: None)
    result = judge.select("add milk", _tools(), "en-US")
    assert result.error is not None
    assert "Boom" in result.error


class NotFound(Exception):
    status_code = 400


def test_anthropic_judge_does_not_retry_client_errors() -> None:
    client = FakeClient(lambda n, kw: (_ for _ in ()).throw(NotFound("bad model")))
    judge = AnthropicJudge(client=client, sleep=lambda _s: None)
    result = judge.select("add milk", _tools(), "en-US")
    assert result.error is not None
    assert len(client.messages.calls) == 1


def test_anthropic_judge_falls_back_when_forced_choice_is_rejected() -> None:
    """Some newer models reject tool_choice `any`; fall back rather than fail."""

    def behavior(n: int, kw: dict[str, Any]) -> Any:
        if kw["tool_choice"]["type"] == "any":
            raise NotFound('tool_choice: type "tool" and "any" are not supported')
        return FakeResponse("AddItemIntent", {})

    client = FakeClient(behavior)
    judge = AnthropicJudge(client=client, sleep=lambda _s: None)
    result = judge.select("add milk", _tools(), "en-US")
    assert result.selected_intent == "AddItemIntent"
    assert client.messages.calls[-1]["tool_choice"]["type"] == "auto"


def test_anthropic_judge_request_fits_the_installed_sdk() -> None:
    """The fake client takes any kwargs, so check them against the real signature."""
    import inspect

    from anthropic.resources.messages import Messages

    client = FakeClient(lambda n, kw: FakeResponse("AddItemIntent", {}))
    AnthropicJudge(client=client).select("add milk", _tools(), "en-US")

    accepted = inspect.signature(Messages.create).parameters
    assert set(client.messages.calls[0]) <= set(accepted)


def test_anthropic_judge_drops_temperature_when_the_model_rejects_it() -> None:
    def behavior(n: int, kw: dict[str, Any]) -> Any:
        if "temperature" in kw.get("extra_body", {}):
            raise NotFound("temperature: is not supported for this model")
        return FakeResponse("AddItemIntent", {})

    client = FakeClient(behavior)
    judge = AnthropicJudge(client=client, sleep=lambda _s: None)
    assert judge.select("add milk", _tools(), "en-US").selected_intent == "AddItemIntent"
    assert judge.select("add eggs", _tools(), "en-US").error is None
    # One rejected call, then the setting stays off for the rest of the run.
    assert len(client.messages.calls) == 3
    assert "extra_body" not in client.messages.calls[-1]


def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(JudgeError, match="No Anthropic API key"):
        AnthropicJudge()


def test_cost_estimation() -> None:
    assert cost_usd("claude-sonnet-4-6", 1_000_000, 0) == pytest.approx(3.00)
    assert cost_usd("claude-sonnet-4-6", 0, 1_000_000) == pytest.approx(15.00)
    # An unknown model yields 0.0 and the report says the estimate is unavailable.
    assert cost_usd("some-unreleased-model", 1_000_000, 1_000_000) == 0.0


def test_tools_json_feeds_the_key(synthetic_rendered: RenderedCatalog) -> None:
    payload = canonical_tools_json(synthetic_rendered.tools)
    assert _key(tools_json=payload) != _key(tools_json="[]")
