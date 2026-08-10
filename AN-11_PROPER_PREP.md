# AN-11 PROPER — Prep Brief

> Forward-prep for the semantic-validator layer-2 arc. Pre-staged by an
> un-attended doc-pass on 2026-07-21 (Agent / Buffy, with Thinker
> recommendation). Designed so the operator can wake, read, and answer
> decision points in minutes.

## 1. Model Pick & Budget Recommendation

**Recommendation:** `sentence-transformers/all-MiniLM-L6-v2`.
**Deployment Pattern:** Pre-staged local cache (`local_files_only=True`), no runtime network calls.

**Justification:**
- **RAM vs. RSS ceiling.** At ~150 MB RAM, MiniLM safely fits within the 1.5 GiB `autoclaw` RSS ceiling, leaving >1.3 GiB for the Python runtime, bus orchestration, and actual agent payloads. `all-mpnet-base-v2` (~400 MB) is too heavy and risks OOM spikes under concurrent task load. `Ollama llama3.1` (4.5 GB) is disqualified.
- **Embed-speed vs. bus throughput.** MiniLM executes in ~50 ms per payload pair on CPU. This preserves the 100-200 ms bus throughput budget. Anything heavier creates an unacceptable bottleneck on the synchronous event bus.
- **Model quality on small-text handoffs.** We are evaluating small-text structural handoffs (QRDs, task summaries, fits-in-2k-tokens). MiniLM is optimized for sentence-level cosine similarity. `bge-small-en-v1.5` is heavily biased toward asymmetrical search/retrieval (query-to-document) rather than **symmetric** semantic similarity — MiniLM is the superior technical fit for `compare(prior, current)`.

## 2. Pre-Specified Heuristic Set (default values)

`proper_semantic` heuristic calculates text-embedding cosine similarity on `reason`, `description`, and `title` fields (per the existing polarity heuristic's targeting).

| Band | Cos-sim range | Severity | Code |
|---|---|---|---|
| Green | `> 0.95` | (no finding) | — |
| Warning | `0.80 – 0.95` | WARNING | `SEMANTIC_DRIFT_WARNING` |
| Error | `< 0.80` | ERROR (blocking) | `SEMANTIC_DRIFT_ERROR` |

Default sliding-window depth: **2 handoffs minimum** (compare prior to current + 1 hop back).

No hedging or dynamic sliding scales; we start with these rigid gates. Operators adjust thresholds in operation, not at arm-wrist.

## 3. Class API Extension & Dependency Strategy

`semantic_handoff.py` currently states a NON-GOAL: "pure stdlib + zero new dependencies." PROPER **breaks this** — adding standard semantic vectors requires `torch` and `sentence-transformers`.

**Pure-numpy alternative? A bespoke SVD/TF-IDF baseline would save ~300 MB of pip overhead but fails at detecting semantic synonyms (e.g., "broken" vs "non-functional" surfaces as zero overlap, false-positive ERROR). Reject.** Accept the `sentence-transformers` dependency, isolated in a `try/except ImportError` block that gracefully disables `proper_semantic` if the packages are absent — keeps the core stdlib fallback intact for lightweight runners.

**API Extension:**
```python
# semantic_handoff.py

VALID_HEURISTICS = frozenset({"omission", "numeric", "class", "polarity", "proper"})

class SemanticHandoffValidator:
    def __init__(
        self,
        enabled_heuristics: Optional[Set[str]] = None,
        model_path: str = None,  # new arg for PROPER
    ):
        ...
        # __init__ attempts to load the model_path inside a try/except.
        # On ImportError or load failure, sets self._proper_available = False
        # and "proper" heuristic becomes a no-op (logged at WARN level).
```

## 4. Integration Point (drop-in for existing wire)

PROPER wires directly into the existing `ValidatingBusProxy` without structural changes:

```python
from semantic_handoff import SemanticHandoffValidator
from bus_validator import ValidatingBusProxy

v = SemanticHandoffValidator(
    enabled_heuristics={"omission", "numeric", "class", "polarity", "proper"},
    model_path="./models/all-MiniLM-L6-v2",
)
proxy = ValidatingBusProxy(bus, semantic_validator=v)
```

Since `ValidatingBusProxy._dispatch` already feeds prior + current payloads to the bound semantic validator, PROPER is a seamless drop-in: enable `proper` in the `enabled_heuristics` set, point at the local cache, ship.

## 5. Operator Decision Questions (yes/no, default-valued)

1. **Accept `all-MiniLM-L6-v2` footprint (~150 MB RAM at runtime)?** [Default: YES]
2. **Accept `torch`/`sentence-transformers` as production dependencies?** [Default: YES]
3. **Enforce hard block (Severity.ERROR) on cosine similarity < 0.80?** [Default: YES]
4. **Require model loading from explicit local disk path (`local_files_only=True`)?** [Default: YES]
5. **Two-anchor threshold (sliding-window depth) is enough — or do you want N-anchor with baseline drift detection?** [Default: 2-anchor only; baseline-drift is a v3 candidate.]

## 6. Implementation Steps (decreasing risk)

1. **Pre-flight download script.** Create `Syntax-Intelligence/scripts/fetch_minilm.py` to pull the HF model to a committed `models/` dir. **Risk:** HuggingFace API connection/timeouts.
2. **Dependency + loading isolation.** Implement the `import torch` block inside `semantic_handoff.py` with fail-safes — module doesn't crash for lightweight runners. **Risk:** breaking the existing 4 minimum-viable heuristics; mitigate via tests covering all 5 modes (each heuristic alone + all together + backwards-compat without PROPER enabled).
3. **Core logic.** Implement `detect_semantic_distance(prior, current, model) -> List[SemanticFinding]` mapping fields to the cos-sim bands above.
4. **Test set.** Land the band-based test suite (below) against the cached model fixture.
5. **Integration wire.** Push `model_path` arg through `ValidatingBusProxy.__init__` and `validating_semantic_validator` factory pattern.
6. **Anomaly update.** Flip `anomalies.md` AN-11 status from `FIXED (minimum-viable)` to `FIXED (proper — embeddings via all-MiniLM-L6-v2)`.

## 7. Minimal Test Set (illustrative bands; assertions may vary on real embed)

```python
def test_proper_semantic_green_band(validator_with_model):
    res = validator_with_model.compare(
        prior={"reason": "Server is down"},
        current={"reason": "The server is offline"},
    )
    assert res.similarity > 0.95  # synonyms shouldn't trigger warnings
    assert not [f for f in res.findings if f.code == "SEMANTIC_DRIFT_WARNING"]

def test_proper_semantic_warning_band(validator_with_model):
    res = validator_with_model.compare(
        prior={"reason": "Server is down"},
        current={"reason": "System experiencing minor lag"},
    )
    assert 0.80 <= res.similarity < 0.95
    assert res.findings[0].severity == Severity.WARNING
    assert res.findings[0].code == "SEMANTIC_DRIFT_WARNING"

def test_proper_semantic_error_band(validator_with_model):
    res = validator_with_model.compare(
        prior={"reason": "Critical database failure"},
        current={"reason": "Everything is working perfectly"},
    )
    assert res.similarity < 0.80
    assert res.findings[0].severity == Severity.ERROR
    assert res.findings[0].code == "SEMANTIC_DRIFT_ERROR"

def test_proper_disabled_graceful_degradation(validator_without_model):
    # Module loaded without sentence-transformers → "proper" heuristic is no-op.
    res = validator_without_model.compare(
        prior={"reason": "irrelevant"},
        current={"reason": "totally different"},
    )
    assert not [f for f in res.findings if "DRIFT" in f.code]
```

*Assumes `validator_with_model` is a fixture loading `./models/all-MiniLM-L6-v2` once per test session. Tests run in <3s for the cache-hit case.*

---

## What this isn't solving

- Cross-session memory of THRESHOLDS (we pick bands per-session; if operators want persisting bands across sessions, that's a follow-on arc that lands in `swarm_charter.py` Article X-style territory).
- LLM-as-judge integration — explicitly rejected at current RAM budget. If operator ever bumps to 4+ GiB RSS headroom, that becomes option B.
- Bootstrap-time model download — the prep script `fetch_minilm.py` must run once before this layer can be tested; the brief does not bundle the download itself.

## Cross-references this brief depends on

- `bleaknarratives/Syntax-Intelligence/semantic_handoff.py` — current minimum-viable heuristics.
- `bleaknarratives/syntax-ai-architecture-spec.md §4` — "Per-handoff-type definition of 'important parameters'" open item (this brief closes it).
- `bleaknarratives/anomalies.md AN-11` — this brief's target entry; flips from "minimum-viable" to "PROPER" once the implementation lands.

---

*Pre-staged by Agent (Buffy / Freebuff) and the un-attended doc-pass of 2026-07-21. Pending: operator yes/no on the five decision questions in §5.*
