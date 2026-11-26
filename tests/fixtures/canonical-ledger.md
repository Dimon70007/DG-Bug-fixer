## Task
После перезаписи кеш иногда отдаёт старое значение; в выдаче бывают дубли.

## Clarifications
Q: На каком входе воспроизводится?  A: overwrite ключа, затем read
Q: Как выглядит правильное поведение?  A: read сразу после write — новое значение, без дублей
Q: Каким тестом проверите?  A: test_cache_ttl + test_no_duplicates

## Iteration 1

### F1 — baseline
| Field | Value |
|---|---|
| Kind | research |
| Scope | task |
| Responds to | — |
| Supersedes | — |
| Iteration | 1 |
| Outcome | BASELINE recorded |

FAILING TEST: pytest tests/test_cache_ttl.py -> failed
FAILING TEST: pytest tests/test_no_duplicates.py -> failed
BASELINE: 1652/1654, красные: test_cache_ttl, test_no_duplicates; test_b (флейкует)

### F2 — route
| Field | Value |
|---|---|
| Kind | route |
| Scope | task |
| Responds to | F1 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | ROUTED: H0 создана |

### H0 — Лок не освобождается после write
| Field | Value |
|---|---|
| Rank | 1 |
| Status | derived (не редактируется) |
| Proposed | F2 |
| Origin | description |
| Mechanism | ветка ошибки пропускает unlock |
| Prediction | в логах нет unlock на пути overwrite |
| Discriminator | — (пока единственная) |

#### Records
- F2 → PROPOSED
- F3 → REFUTED

### F3 — Проба H0: unlock есть
| Field | Value |
|---|---|
| Kind | probe |
| Scope | H0 |
| Responds to | F2 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | REFUTED |

| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| test_cache_ttl | stale после write | stale_hits | 7 | 7 | 0 | без изменений |

ФАКТ (RED): pytest tests/test_cache_ttl.py tests/test_no_duplicates.py -> 2 failed
ФАКТ (логи): unlock вызывается на всех четырёх ветках — механизм H0 отсутствует
Bug: stable
Attempt 1 consumed
Secrets scan: clean
ФАКТ (GREEN): pytest -> 1652 passed, 2 failed (те же)
Revert: src/lock.py
NEW INFORMATION: лок не при чём; дубли и stale остаются — нужно исследование

### F4 — research
| Field | Value |
|---|---|
| Kind | research |
| Scope | task |
| Responds to | F3 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | RESEARCH: H1, H2 |

Merge: none — Prediction H1 и H2 различаются

### H1 — Инвалидация до commit
| Field | Value |
|---|---|
| Rank | 2 |
| Status | derived (не редактируется) |
| Proposed | F4 |
| Origin | logs+search |
| Mechanism | invalidate() до commit, читатель видит старое |
| Prediction | перенос invalidate после commit уберёт stale_hits |
| Discriminator | против H2: чинит stale, не порядок в выдаче |

#### Records
- F4 → PROPOSED
- F5 → REFUTED

### H2 — Второй вызывающий инвалидирует чужой ключ
| Field | Value |
|---|---|
| Rank | 3 |
| Status | derived (не редактируется) |
| Proposed | F4 |
| Origin | logs+search |
| Mechanism | caller B сбрасывает ключ A при своём write |
| Prediction | изоляция ключей уберёт и stale, и дубли |
| Discriminator | против H1: чинит оба симптома через изоляцию, не порядок invalidate |

#### Records
- F4 → PROPOSED
- F6 → CONFIRMED

### F5 — Проба H1: stale упал, дубли нет
| Field | Value |
|---|---|
| Kind | probe |
| Scope | H1 |
| Responds to | F4 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | REFUTED |

| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| test_cache_ttl | stale | stale_hits | 7 | 7 | 0 | без изменений |

ФАКТ (RED): pytest tests/test_cache_ttl.py tests/test_no_duplicates.py -> 2 failed
ФАКТ (логи): invalidate уже после commit — механизм H1 отсутствует
Bug: stable
Attempt 2 consumed
Secrets scan: clean
ФАКТ (GREEN): pytest -> 1652 passed, 2 failed
Revert: src/cache.py
Working on: H2

### F6 — Проба H2: изоляция ключей
| Field | Value |
|---|---|
| Kind | probe |
| Scope | H2 |
| Responds to | F5 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | CONFIRMED: stale_hits 7 → 0, duplicates 4 → 0 |

| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| test_cache_ttl | stale | stale_hits | 7 | 0 | 0 | исправлено |
| test_no_duplicates | дубли | dup_count | 4 | 0 | 0 | исправлено |

ФАКТ (RED): pytest tests/test_cache_ttl.py tests/test_no_duplicates.py -> 2 failed
ФАКТ (логи): caller B больше не трогает ключ A
Bug: stable
Attempt 3 consumed
Secrets scan: clean
ФАКТ (GREEN): pytest -> 1654 passed
Corner cases: none — два целевых покрывают оба симптома

STATUS: DONE

## Report
Итог: DONE — оба симптома закрыты, suite зелёный.
Корневая причина: caller B сбрасывал чужой ключ — см. F6 / H2.
Что изменено: src/cache.py — изоляция ключей при invalidate.
Таблица результатов: в F6.
Опровергнутые гипотезы: H0 (F3), H1 (F5).
Остаток: —
Что нужно от оператора: —
Уроки: e2e на два ключа поймал бы раньше.
