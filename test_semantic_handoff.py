#!/usr/bin/env python3
"""
test_semantic_handoff.py --- tests for the layer-2 semantic validator.

Covers:
  * green band (synonyms) -> no finding
  * warning band (re-summary) -> SEMANTIC_DRIFT_WARNING
  * error band (contradiction) -> SEMANTIC_DRIFT_ERROR
  * unavailable graceful degradation (no model) -> INFO finding, no crash
  * backward compat (4 minimum-viable heuristics)

The model-loading tests are gated by pytest.skipif so they pass cleanly
when the operator has not yet pre-staged the model.

Run:
    cd ~/bleaknarratives/Syntax-Intelligence
    pytest test_semantic_handoff.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import semantic_handoff as sh


# Locating the pre-staged model. sentence-transformers' default HF cache is
# ~/.cache/torch/sentence_transformers/
DEFAULT_MODEL_CACHE = Path.home() / '.cache' / 'torch' / 'sentence_transformers'


def _model_loaded() -> bool:
    """True iff the all-MiniLM-L6-v2 model is staged in the default cache."""
    if not DEFAULT_MODEL_CACHE.exists():
        return False
    return any(DEFAULT_MODEL_CACHE.rglob('config.json'))


@pytest.fixture
def validator_with_model():
    snaps = list(DEFAULT_MODEL_CACHE.rglob('config.json'))
    assert snaps, 'pre-staged model not found'
    model_path = str(snaps[0].parent)
    return sh.SemanticHandoffValidator(
        enabled_heuristics={'proper'},
        model_path=model_path,
    )


@pytest.mark.skipif(not _model_loaded(), reason="all-MiniLM-L6-v2 model not pre-staged; run scripts/fetch_minilm.py to enable")
def test_proper_semantic_green_band(validator_with_model):
    verdict = validator_with_model.compare(
        prior={'reason': 'Server is down'},
        current={'reason': 'The server is offline'},
    )
    drift_findings = [f for f in verdict.findings if 'DRIFT' in f.code]
    assert not drift_findings, (
        f'Green-band synonyms should not drift; got: '
        f'{[(f.code, f.severity, f.message) for f in drift_findings]}'
    )


@pytest.mark.skipif(not _model_loaded(), reason="all-MiniLM-L6-v2 model not pre-staged; run scripts/fetch_minilm.py to enable")
def test_proper_semantic_warning_band(validator_with_model):
    verdict = validator_with_model.compare(
        prior={'reason': 'Server is down'},
        current={'reason': 'System experiencing minor lag'},
    )
    drift_findings = [f for f in verdict.findings if f.code == 'SEMANTIC_DRIFT_WARNING']
    assert drift_findings, (
        f'Warning-band re-summary should trigger SEMANTIC_DRIFT_WARNING; '
        f'got: {[(f.code, f.severity) for f in verdict.findings]}'
    )
    assert all(f.severity == sh.Severity.WARNING for f in drift_findings)


@pytest.mark.skipif(not _model_loaded(), reason="all-MiniLM-L6-v2 model not pre-staged; run scripts/fetch_minilm.py to enable")
def test_proper_semantic_error_band(validator_with_model):
    verdict = validator_with_model.compare(
        prior={'reason': 'Critical database failure'},
        current={'reason': 'Everything is working perfectly'},
    )
    drift_findings = [f for f in verdict.findings if f.code == 'SEMANTIC_DRIFT_ERROR']
    assert drift_findings, (
        f'Error-band contradiction should trigger SEMANTIC_DRIFT_ERROR; '
        f'got: {[(f.code, f.severity) for f in verdict.findings]}'
    )
    assert all(f.severity == sh.Severity.ERROR for f in drift_findings)
    assert not verdict.passed


def test_proper_disabled_graceful_degradation():
    v = sh.SemanticHandoffValidator(
        enabled_heuristics={'proper'},
        model_path=None,
    )
    assert not v._proper_available
    verdict1 = v.compare(
        prior={'reason': 'irrelevant'},
        current={'reason': 'totally different'},
    )
    unavailable = [f for f in verdict1.findings if f.code == 'SEMANTIC_PROPER_UNAVAILABLE']
    assert len(unavailable) == 1, f'expected 1, got {len(unavailable)}'
    assert unavailable[0].severity == sh.Severity.INFO
    verdict2 = v.compare(prior={'reason': 'a'}, current={'reason': 'b'})
    assert not [f for f in verdict2.findings if f.code == 'SEMANTIC_PROPER_UNAVAILABLE'], (
        'emit-once gate failed'
    )


def test_legacy_heuristics_still_work():
    v = sh.SemanticHandoffValidator(
        enabled_heuristics={'omission', 'numeric', 'class', 'polarity'},
    )
    verdict = v.compare(
        prior={'reason': 'critical bug', 'tags': 'alpha'},
        current={'reason': 'minor issue', 'tags': ['alpha']},
    )
    codes = {f.code for f in verdict.findings}
    assert 'SEMANTIC_POLARITY_SHIFT' in codes
    assert 'SEMANTIC_CLASS_FLIP' in codes
