"""The runner's use of the cache, repeats, and usage accounting. No network, ever."""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path

import pytest

from intentbench.corpus import load_corpus
from intentbench.corpus.schema import CorpusFile
from intentbench.judge import ResponseCache
from intentbench.judge.base import cost_usd
from intentbench.models import Catalog, JudgeResult, RenderedCatalog, RunReport, ToolSchema
from intentbench.runner import count_uncached, run_corpus
from tests.conftest import CORPORA

MODEL = "claude-sonnet-4-6"
INPUT_TOKENS = 100
OUTPUT_TOKENS = 10

#: What the synthetic corpus expects, so a stable judge passes every case.
EXPECTED: dict[str, str | None] = {
    "add milk to my shopping list": "AddItemIntent",
    "make a new list called camping": "CreateShoppingListIntent",
    "what's the weather in tokyo": None,
    "sync everything": "TitleOnlyIntent",
}


class CountingJudge:
    """Counts every call, and can answer a phrase differently on each call."""

    name = "counting"
    model_id = MODEL

    def __init__(self, sequences: dict[str, list[str | None]] | None = None) -> None:
        self.sequences = sequences or {}
        self.calls: Counter[str] = Counter()
        self._lock = threading.Lock()

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def select(self, phrase: str, tools: list[ToolSchema], locale: str) -> JudgeResult:
        key = phrase.lower()
        with self._lock:
            nth = self.calls[key]
            self.calls[key] += 1
        sequence = self.sequences.get(key)
        selected = sequence[nth % len(sequence)] if sequence else EXPECTED[key]
        return JudgeResult(
            selected_intent=selected,
            input_tokens=INPUT_TOKENS,
            output_tokens=OUTPUT_TOKENS,
        )


@pytest.fixture
def corpus() -> CorpusFile:
    return load_corpus(CORPORA / "synthetic.yaml")


@pytest.fixture
def cache(tmp_path: Path) -> ResponseCache:
    return ResponseCache(tmp_path / "cache")


def _run(
    synthetic: Catalog,
    rendered: RenderedCatalog,
    corpus: CorpusFile,
    judge: CountingJudge,
    cache: ResponseCache | None,
    *,
    repeat: int = 1,
    concurrency: int = 1,
) -> RunReport:
    return run_corpus(
        catalog=synthetic,
        rendered=rendered,
        corpus=corpus,
        cases=corpus.cases,
        judge=judge,
        intents=synthetic.intents,
        cache=cache,
        concurrency=concurrency,
        repeat=repeat,
    )


def test_single_runs_use_the_cache(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
) -> None:
    cases = len(corpus.cases)

    first = CountingJudge()
    cold = _run(synthetic, synthetic_rendered, corpus, first, cache)
    assert first.total_calls == cases
    assert (cold.usage.api_calls, cold.usage.cached_calls) == (cases, 0)
    assert cold.usage.input_tokens == cases * INPUT_TOKENS

    second = CountingJudge()
    warm = _run(synthetic, synthetic_rendered, corpus, second, cache)
    assert second.total_calls == 0
    assert (warm.usage.api_calls, warm.usage.cached_calls) == (0, cases)
    assert all(case.from_cache for case in warm.cases)


def test_a_fully_cached_run_costs_nothing(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
) -> None:
    _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache)
    warm = _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache)
    assert warm.usage.input_tokens == 0
    assert warm.usage.output_tokens == 0
    assert warm.usage.estimated_cost_usd == 0.0


@pytest.mark.parametrize("concurrency", [1, 8])
def test_repeats_make_a_fresh_call_every_time(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
    concurrency: int,
) -> None:
    # Warm the cache first: repeats must ignore it rather than replay it.
    _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache)

    judge = CountingJudge()
    report = _run(
        synthetic, synthetic_rendered, corpus, judge, cache, repeat=3, concurrency=concurrency
    )

    assert judge.calls == Counter({phrase: 3 for phrase in EXPECTED})
    assert all(not case.from_cache and case.repeats == 3 for case in report.cases)


def test_repeat_usage_counts_every_attempt(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
) -> None:
    cases = len(corpus.cases)
    report = _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache, repeat=3)

    assert report.usage.api_calls == cases * 3
    assert report.usage.cached_calls == 0
    assert report.usage.input_tokens == cases * 3 * INPUT_TOKENS
    assert report.usage.output_tokens == cases * 3 * OUTPUT_TOKENS
    assert report.usage.estimated_cost_usd == pytest.approx(
        cost_usd(MODEL, cases * 3 * INPUT_TOKENS, cases * 3 * OUTPUT_TOKENS)
    )


def test_repeats_do_not_write_the_cache(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
) -> None:
    _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache, repeat=3)
    assert cache.count() == 0


def test_differing_selections_are_unstable(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
) -> None:
    flaky = "add milk to my shopping list"
    # Warm the cache with the stable answer; a cached replay would hide the flip.
    _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache)

    judge = CountingJudge({flaky: ["AddItemIntent", "CreateShoppingListIntent", "AddItemIntent"]})
    report = _run(synthetic, synthetic_rendered, corpus, judge, cache, repeat=3)

    by_phrase = {case.phrase.lower(): case for case in report.cases}
    case = by_phrase[flaky]
    assert case.unstable
    assert case.agreement == 2
    assert case.observed_selections == [
        "AddItemIntent",
        "CreateShoppingListIntent",
        "AddItemIntent",
    ]
    assert report.metrics.unstable == 1
    assert not any(c.unstable for phrase, c in by_phrase.items() if phrase != flaky)


def test_count_uncached_ignores_the_cache_for_repeats(
    synthetic: Catalog,
    synthetic_rendered: RenderedCatalog,
    corpus: CorpusFile,
    cache: ResponseCache,
) -> None:
    cases = len(corpus.cases)

    def uncached(repeat: int) -> int:
        return count_uncached(
            cases=corpus.cases,
            corpus=corpus,
            rendered=synthetic_rendered,
            judge=CountingJudge(),
            cache=cache,
            repeat=repeat,
        )

    assert uncached(1) == cases
    assert uncached(3) == cases * 3

    _run(synthetic, synthetic_rendered, corpus, CountingJudge(), cache)
    assert uncached(1) == 0
    assert uncached(3) == cases * 3
