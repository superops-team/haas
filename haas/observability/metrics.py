"""Low-cardinality metrics (specs/observability/README.md §4)."""

from __future__ import annotations


class Metrics:
    """In-memory counter/gauge collection with cardinality guard."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}

    def incr(self, name: str, amount: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def snapshot(self) -> dict[str, int | float]:
        return {**self._gauges, **self._counters}
