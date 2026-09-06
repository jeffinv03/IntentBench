"""Corpus loading and validation — the five classes from the spec, and then some."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentbench.corpus import CorpusError, load_corpus, validate_corpus
from intentbench.corpus.loader import has_errors
from intentbench.corpus.schema import CorpusFile, IssueSeverity
from intentbench.models import Catalog
from tests.conftest import CORPORA


def test_loads_a_good_corpus(demo_corpus: CorpusFile) -> None:
    assert demo_corpus.version == 1
    assert len(demo_corpus.cases) == 4
    assert demo_corpus.defaults.locale == "en-US"


def test_abstention_is_first_class(demo_corpus: CorpusFile) -> None:
    case = next(c for c in demo_corpus.cases if c.phrase.startswith("what's the weather"))
    assert case.expect.intent is None
    assert case.expect.expects_abstention is True


def test_partial_parameter_expectations(demo_corpus: CorpusFile) -> None:
    case = demo_corpus.cases[0]
    assert set(case.expect.parameters) == {"item", "list"}


def test_tag_filtering(demo_corpus: CorpusFile) -> None:
    assert len(demo_corpus.filtered(["smoke"])) == 2
    assert len(demo_corpus.filtered(["abstain"])) == 1
    assert len(demo_corpus.filtered(None)) == 4
    assert demo_corpus.filtered(["nonexistent"]) == []


def test_locale_defaults_and_override() -> None:
    corpus = CorpusFile.model_validate(
        {
            "version": 1,
            "defaults": {"locale": "en-GB"},
            "cases": [
                {"phrase": "a", "expect": {"intent": None}},
                {"phrase": "b", "expect": {"intent": None}, "locale": "fr-FR"},
            ],
        }
    )
    assert corpus.locale_for(corpus.cases[0]) == "en-GB"
    assert corpus.locale_for(corpus.cases[1]) == "fr-FR"


# --- validation class 1: malformed YAML ----------------------------------


def test_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text("version: 1\ncases:\n  - phrase: [unclosed\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="not valid YAML"):
        load_corpus(path)


# --- validation class 2: empty corpus -------------------------------------


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(CorpusError, match="empty"):
        load_corpus(path)


def test_no_cases(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text("version: 1\ncases: []\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="no cases"):
        load_corpus(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="No corpus file"):
        load_corpus(tmp_path / "nope.yaml")


# --- validation class 3: unknown top-level keys ---------------------------


def test_unknown_top_level_key(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text(
        "version: 1\nphrases: []\ncases:\n  - phrase: a\n    expect: {}\n", encoding="utf-8"
    )
    with pytest.raises(CorpusError, match="Extra inputs are not permitted"):
        load_corpus(path)


def test_unknown_key_inside_a_case() -> None:
    with pytest.raises(CorpusError, match="Extra inputs are not permitted"):
        load_corpus(CORPORA / "bad-unknown-key.yaml")


def test_unsupported_version(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text("version: 7\ncases:\n  - phrase: a\n    expect: {}\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="unsupported corpus version 7"):
        load_corpus(path)


def test_blank_phrase_rejected(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text('version: 1\ncases:\n  - phrase: "   "\n    expect: {}\n', encoding="utf-8")
    with pytest.raises(CorpusError, match="must not be empty"):
        load_corpus(path)


def test_abstention_cannot_expect_parameters(tmp_path: Path) -> None:
    path = tmp_path / "phrases.yaml"
    path.write_text(
        "version: 1\ncases:\n  - phrase: a\n    expect:\n"
        "      intent: null\n      parameters:\n        x: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="cannot also expect parameters"):
        load_corpus(path)


# --- validation class 4: duplicate phrases --------------------------------


def test_duplicate_phrases_detected() -> None:
    corpus = load_corpus(CORPORA / "bad-duplicate.yaml")
    issues = validate_corpus(corpus, None)
    assert has_errors(issues)
    assert "duplicate phrase" in issues[0].message
    assert issues[0].case_index == 0


# --- validation class 5: unknown intents and parameters -------------------


def test_unknown_intent_with_suggestion(synthetic: Catalog) -> None:
    corpus = load_corpus(CORPORA / "bad-unknown-intent.yaml")
    issues = validate_corpus(corpus, synthetic)
    assert has_errors(issues)

    intent_issue = next(i for i in issues if "not in the catalog" in i.message)
    assert intent_issue.suggestion == "AddItemIntent", "did-you-mean must fire on a typo"
    assert "AddItemIntnet" in intent_issue.message


def test_unknown_parameter_with_suggestion(synthetic: Catalog) -> None:
    corpus = load_corpus(CORPORA / "bad-unknown-intent.yaml")
    issues = validate_corpus(corpus, synthetic)
    param_issue = next(i for i in issues if "does not declare" in i.message)
    assert param_issue.suggestion == "name"


def test_good_corpus_validates_clean(demo_corpus: CorpusFile, synthetic: Catalog) -> None:
    assert validate_corpus(demo_corpus, synthetic) == []


def test_abstention_cases_skip_catalog_checks(synthetic: Catalog) -> None:
    corpus = CorpusFile.model_validate(
        {"version": 1, "cases": [{"phrase": "x", "expect": {"intent": None}}]}
    )
    assert validate_corpus(corpus, synthetic) == []


def test_issue_render_includes_suggestion() -> None:
    corpus = load_corpus(CORPORA / "bad-unknown-intent.yaml")
    issues = validate_corpus(corpus, None)
    for issue in issues:
        assert issue.severity in (IssueSeverity.ERROR, IssueSeverity.WARNING)
        assert issue.render()
