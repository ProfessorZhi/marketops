"""The adapter contract.

One interface for every stage and every kind of provider: a mock, a paid API, a
local GPU service or a plain HTTP reader. The runner does not know which it is
talking to — that is the point, because it makes "same case, different provider"
a fair comparison and makes an offline run structurally identical to a live one.

Failure is typed. ``NotConfigured`` is not an empty result: an empty list from a
provider that has no key would score as perfectly precise. The runner turns each
exception class into the matching ``status`` and never into ``ok``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class AdapterError(RuntimeError):
    """Base class; ``kind`` is written into the result row's ``error.kind``."""

    kind = "internal"

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status

    def as_error(self) -> dict:
        return {"kind": self.kind, "message": str(self), "http_status": self.http_status}


class NotConfigured(AdapterError):
    """No credential or endpoint. Produces ``status=not_configured``."""

    kind = "credential"


class AdapterTimeout(AdapterError):
    kind = "timeout"


class TransportError(AdapterError):
    kind = "transport"


class HTTPError(AdapterError):
    kind = "http"


class ParseError(AdapterError):
    """The provider answered with bytes that are not the agreed format."""

    kind = "parse"


class ContractViolation(AdapterError):
    """The provider answered in the agreed format but broke the field contract."""

    kind = "contract"


@dataclass
class AdapterInfo:
    """What a run manifest says about the adapter, without calling it."""

    name: str
    stage: str
    kind: str                       # mock | local | remote
    model: str | None = None
    requires: tuple[str, ...] = ()  # env vars / endpoints it needs
    notes: str = ""


@runtime_checkable
class StageAdapter(Protocol):
    info: AdapterInfo

    def availability(self) -> tuple[bool, str]:
        """``(usable, reason)``. Never raises; a missing key is a reason, not an error."""
        ...

    def run(self, case: dict) -> dict:
        """Return the stage output, or raise one of the ``AdapterError`` subclasses."""
        ...


@dataclass
class AdapterRegistry:
    """Name -> adapter. Lookups fail loudly; they never fall back to a mock."""

    _items: dict[str, StageAdapter] = field(default_factory=dict)

    def register(self, adapter: StageAdapter) -> None:
        key = adapter.info.name
        if key in self._items:
            raise ValueError(f"adapter {key!r} registered twice")
        self._items[key] = adapter

    def get(self, name: str, stage: str | None = None) -> StageAdapter:
        try:
            adapter = self._items[name]
        except KeyError:
            raise KeyError(f"unknown adapter {name!r}; known: {sorted(self._items)}") from None
        if stage is not None and adapter.info.stage != stage:
            raise KeyError(
                f"adapter {name!r} serves stage {adapter.info.stage!r}, not {stage!r}"
            )
        return adapter

    def names(self, stage: str | None = None) -> list[str]:
        return sorted(
            name for name, adapter in self._items.items()
            if stage is None or adapter.info.stage == stage
        )

    def describe(self, stage: str | None = None) -> list[dict[str, Any]]:
        out = []
        for name in self.names(stage):
            info = self._items[name].info
            usable, reason = self._items[name].availability()
            out.append({
                "name": info.name, "stage": info.stage, "kind": info.kind,
                "model": info.model, "requires": list(info.requires),
                "usable": usable, "reason": reason, "notes": info.notes,
            })
        return out
