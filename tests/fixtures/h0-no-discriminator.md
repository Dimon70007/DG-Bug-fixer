## Task
After a rewrite the cache sometimes serves a stale value.

## Clarifications
Q: On what input does it reproduce?  A: overwrite a key, then read
Q: What is the correct behaviour?  A: a read right after a write returns the new value
Q: Which test will show it?  A: test_cache_ttl

FAILING TEST: pytest tests/test_cache.py -> 1 failed
BASELINE: 1652/1654, red: test_cache_ttl

### F2 — route
| Field | Value |
|---|---|
| Kind | route |
| Scope | task |
| Outcome | ROUTED |

Working on: H0

### H0 — the invalidate call runs before the write commits
| Field | Value |
|---|---|
| Rank | 1 |
| Status | derived |
| Proposed | F2 |
| Origin | description |
| Mechanism | the cache is emptied, then the write fills it again with the old row |
| Prediction | the stale value disappears when invalidation moves after the commit |

#### Records
- F2 → PROPOSED
