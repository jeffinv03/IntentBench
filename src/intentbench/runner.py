"""Execute a corpus against a judge and score the results.

Concurrency is bounded (default 8). Retries live inside each judge, so a single
flaky phrase produces one ERROR case rather than aborting a run that already
cost money.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from intentbench import __version__
from intentbench.catalog.render import canonical_tools_json
from intentbench.corpus.schema import CorpusCase, CorpusFile
from intentbench.judge.base import PROMPT_VERSION, Judge, cost_usd
from intentbench.judge.cache import ResponseCache, make_key
from intentbench.models import (
    CaseResult,
    Catalog,
    IntentDefinition,
    JudgeResult,
    RenderedCatalog,
    RunMetadata,
    RunReport,
    UsageSummary,
)
from intentbench.scoring.aggregate import aggregate, resolve_repeats
from intentbench.scoring.match import score_case

DEFAULT_CONCURRENCY = 8
#: Above this many uncached calls, confirm before spending.
COST_CONFIRM_THRESHOLD = 50


def catalog_hash(rendered: RenderedCatalog) -> str:
    """A stable fingerprint of the tool list the judge actually saw.

    Recorded in every report so a diff can tell "the model got worse" apart from
    "the catalog changed underneath you".
    """
    return hashlib.sha256(canonical_tools_json(rendered.tools).encode("utf-8")).hexdigest()[:16]


def _resolve_tool_name(rendered: RenderedCatalog, selected: str | None) -> str | None:
    """Map a tool name back to the intent identifier the corpus speaks in."""
    if selected is None:
        return None
    return rendered.tool_name_to_identifier.get(selected, selected)


def run_corpus(
    *,
    catalog: Catalog,
    rendered: RenderedCatalog,
    corpus: CorpusFile,
    cases: list[CorpusCase],
    judge: Judge,
    intents: list[IntentDefinition],
    cache: ResponseCache | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    repeat: int = 1,
    app_label: str | None = None,
    corpus_path: str | None = None,
    on_case_done: Callable[[CaseResult], None] | None = None,
) -> RunReport:
    """Run every case (``repeat`` times each) and return a scored report."""
    by_identifier = {intent.identifier: intent for intent in intents}
    tools_json = canonical_tools_json(rendered.tools)

    # (case index, repetition index) — flattened so one thread pool covers both.
    units = [(index, attempt) for index in range(len(cases)) for attempt in range(repeat)]

    def execute(unit: tuple[int, int]) -> tuple[int, CaseResult]:
        index, _attempt = unit
        case = cases[index]
        locale = corpus.locale_for(case)

        key = make_key(
            model_id=judge.model_id,
            prompt_version=PROMPT_VERSION,
            tools_json=tools_json,
            phrase=case.phrase,
            locale=locale,
        )

        judged: JudgeResult | None = cache.get(key) if cache else None
        if judged is None:
            judged = judge.select(case.phrase, rendered.tools, locale)
            if cache is not None:
                cache.put(key, judged)

        identifier = _resolve_tool_name(rendered, judged.selected_intent)
        result = score_case(
            case,
            locale=locale,
            selected_intent=identifier,
            selected_parameters=judged.parameters,
            intent=by_identifier.get(case.expect.intent or ""),
            error=judged.error,
        )
        result.from_cache = judged.from_cache
        result.latency_ms = judged.latency_ms
        result.input_tokens = judged.input_tokens
        result.output_tokens = judged.output_tokens
        return index, result

    collected: dict[int, list[CaseResult]] = {index: [] for index in range(len(cases))}

    if concurrency <= 1:
        for unit in units:
            index, result = execute(unit)
            collected[index].append(result)
            if on_case_done:
                on_case_done(result)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for index, result in pool.map(execute, units):
                collected[index].append(result)
                if on_case_done:
                    on_case_done(result)

    results = [resolve_repeats(collected[index]) for index in range(len(cases))]

    usage = UsageSummary(
        api_calls=sum(1 for r in results for _ in range(r.repeats) if not r.from_cache),
        cached_calls=sum(r.repeats for r in results if r.from_cache),
        input_tokens=sum(r.input_tokens for r in results),
        output_tokens=sum(r.output_tokens for r in results),
    )
    usage.estimated_cost_usd = cost_usd(judge.model_id, usage.input_tokens, usage.output_tokens)

    metadata = RunMetadata(
        intentbench_version=__version__,
        judge=judge.name,
        model_id=judge.model_id,
        prompt_version=PROMPT_VERSION,
        catalog_hash=catalog_hash(rendered),
        catalog_source=str(catalog.source_path),
        corpus_path=corpus_path,
        app_label=app_label,
        timestamp=datetime.now(UTC),
        repeat=repeat,
        tool_count=len(rendered.tools),
    )

    return RunReport(
        metadata=metadata,
        metrics=aggregate(results, catalog=catalog, intents=intents),
        cases=results,
        usage=usage,
        parse_warnings=catalog.parse_warnings,
        weak_descriptions=rendered.weak_descriptions,
        unresolved_localization=rendered.unresolved_localization,
    )


def count_uncached(
    *,
    cases: list[CorpusCase],
    corpus: CorpusFile,
    rendered: RenderedCatalog,
    judge: Judge,
    cache: ResponseCache | None,
    repeat: int,
) -> int:
    """How many billable calls a run would make. Drives the cost confirmation."""
    if cache is None or not cache.enabled:
        return len(cases) * repeat

    tools_json = canonical_tools_json(rendered.tools)
    uncached = 0
    for case in cases:
        key = make_key(
            model_id=judge.model_id,
            prompt_version=PROMPT_VERSION,
            tools_json=tools_json,
            phrase=case.phrase,
            locale=corpus.locale_for(case),
        )
        if cache.get(key) is None:
            uncached += repeat
    return uncached
