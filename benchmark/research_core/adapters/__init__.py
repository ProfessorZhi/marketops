"""Adapter contract and registry.

An adapter is the only place a provider is named. Cases never are.
"""

from .base import (
    AdapterError,
    AdapterInfo,
    AdapterRegistry,
    AdapterTimeout,
    ContractViolation,
    HTTPError,
    NotConfigured,
    ParseError,
    StageAdapter,
    TransportError,
)

__all__ = [
    "AdapterError", "AdapterInfo", "AdapterRegistry", "AdapterTimeout",
    "ContractViolation", "HTTPError", "NotConfigured", "ParseError",
    "StageAdapter", "TransportError",
]
