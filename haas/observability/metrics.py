"""Low-cardinality metrics (specs/observability/README.md §4)."""

from __future__ import annotations

import re

# Metric names are fixed, snake_case identifiers. Runtime values (session ids,
# user ids, provider names) MUST be tags/labels, never part of the name;
# baking them in would explode series cardinality (P2-16).
_STATIC_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class Metrics:
    """In-memory counter/gauge collection with cardinality guard."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}

    @staticmethod
    def _check_name(name: str) -> None:
        if not _STATIC_METRIC_NAME.match(name):
            raise ValueError(f"dynamic metric name not allowed: {name!r}")

    def incr(self, name: str, amount: int = 1) -> None:
        self._check_name(name)
        self._counters[name] = self._counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: float) -> None:
        self._check_name(name)
        self._gauges[name] = value

    def counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def snapshot(self) -> dict[str, int | float]:
        return {**self._gauges, **self._counters}
