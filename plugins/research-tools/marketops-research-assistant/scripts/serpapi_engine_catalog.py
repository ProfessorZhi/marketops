#!/usr/bin/env python3
"""SerpApi's engine catalog, read one resource at a time and pinned by hash.

SerpApi's official MCP does not describe its engines in the ``search`` tool
schema. It publishes them as **MCP resources**: an index at
``serpapi://engines`` and one parameter schema per engine at
``serpapi://engines/<engine>``. Those two URIs are the whole contract this
module depends on, and they were read from the vendor's own MCP README rather
than inferred.

**Why laziness is the design and not an optimisation.** The product brief's
failure mode is "blindly exposing hundreds of vendor methods, or hiding
advanced methods entirely". A catalog of ~100+ engine schemas flattened into a
tool result is the first half of that sentence: it spends context on engines
the current research question will never touch, and it hands the host agent a
pile of vendor-authored text (which the convergence review classifies as
*untrusted input*) with no review step. So the index is read once, the engine
*names* are cheap, and a schema body is fetched only when a caller names one.

**Why the cache is hash-pinned.** The cache exists so a second call in the same
run does not re-read the vendor. A cache that trusts whatever is on disk is
then a way for a later edit — by another process, another agent, or a person —
to become "the vendor's schema". Each entry stores the sha256 of the exact
bytes it was read at, and a read-back recomputes it. A mismatch is
``SERPAPI_ENGINE_CACHE_INTEGRITY``, not a silent serve.

**What this module deliberately cannot say.** It never claims the catalog is
current, and it never claims an engine is *entitled* on this account. It
records ``observed_at`` per entry, reports staleness instead of hiding it, and
leaves cost alone entirely — reading a schema is not a search and does not
prove the account may run one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .research_event_journal import _root as data_root
except ImportError:
    from research_event_journal import _root as data_root

INDEX_URI = "serpapi://engines"
SCHEMA_URI_TEMPLATE = "serpapi://engines/{engine}"
#: The engine a search uses when the caller names none.
#:
#: ``google_light`` answers with Google redirect links
#: (``https://www.google.com/url?q=<opaque protobuf>``), which carry no
#: readable destination: a crawler cannot follow one and the allowlist refuses
#: google.com, so every result of that engine is undiscoverable material. The
#: reviewed ``google`` engine answers with the same fields and a plain ``link``,
#: which is what the rest of the pipeline needs in order to fetch a page.
DEFAULT_ENGINE = "google"
CACHE_SCHEMA = "marketops.serpapi.engine-cache.v1"
DEFAULT_ENDPOINT = "https://mcp.serpapi.com/mcp"

#: The refusal vocabulary of this module. Every one of these travels to a caller,
#: so they are named rather than inlined, and the adapter that consumes them
#: asserts it knows all of them.
#: Errors the **adapter dispatch path** raises and must classify. The coupling
#: test asserts the adapter knows every one of them.
CATALOG_ERROR_CODES: frozenset[str] = frozenset({
    # pre-dispatch: the session never listed this resource, so nothing was read
    "SERPAPI_RESOURCE_NOT_ADVERTISED",
    # post-dispatch: the read left and did not come back with usable content
    "SERPAPI_RESOURCE_UNAVAILABLE",
    "SERPAPI_RESOURCE_EMPTY",
    "SERPAPI_SCHEMA_TOO_LARGE",
    "SERPAPI_ENGINE_SCHEMA_UNREADABLE",
    # cache-side
    "SERPAPI_ENGINE_CACHE_UNREADABLE",
    "SERPAPI_ENGINE_SCHEMA_NOT_CACHED",
    "SERPAPI_ENGINE_CACHE_INTEGRITY",
    "SERPAPI_ENGINE_NAME_INVALID",
})

#: Errors the **discovery probe** raises. They close the probe; they are not
#: adapter refusals, because ``discover_surface`` never dispatches a research
#: operation — it reads a surface and closes. Keeping the two sets apart is what
#: stops a probe failure being reported as a capability refusal.
DISCOVERY_ERROR_CODES: frozenset[str] = frozenset({
    "SERPAPI_SESSION_CANNOT_NOTIFY_INITIALIZED",
    "SERPAPI_SURFACE_TOOL_COUNT_CHANGED",
    "SERPAPI_SURFACE_UNTRUSTED_INSTRUCTIONS",
    "SERPAPI_RESOURCE_URI_INVALID",
})

#: Every code this module can raise. The declaration test reads the source and
#: asserts this set covers it, so a new refusal cannot be added without naming it.
ALL_CATALOG_ERROR_CODES: frozenset[str] = (CATALOG_ERROR_CODES
                                           | DISCOVERY_ERROR_CODES)

#: Bounds one engine's schema resource. A schema is a parameter description, not
#: a payload; a body past this is refused before it is parsed.
MAX_SCHEMA_BYTES = 512 * 1024

#: Whether anybody has **reviewed** the live engine-resource surface against this
#: contract. Reading the vendor's MCP README tells us the URIs exist; it does not
#: tell us the account's server serves them, nor that the shapes match. So the
#: default is ``False`` and it stays ``False`` until a separately authorized
#: read-only handshake actually looks — "unknown" must not read as "fine".
REVIEWED_ENGINE_RESOURCES = False
RESOURCE_REVIEW_NOTE = ("NOT_REVIEWED: the URIs come from the vendor's MCP README; no "
                        "resources/list or resources/read has been performed against "
                        "mcp.serpapi.com on this host.")

#: The reviewed maximum for one discovery read. Same bound as a schema body: a
#: surface probe is not a bulk export, and an unbounded read is how a probe turns
#: into a memory event.
DISCOVERY_MAX_BYTES = MAX_SCHEMA_BYTES

SURFACE_SCHEMA = "marketops.serpapi.mcp-surface.v1"

#: The exact methods a discovery may call, in the order it may call them. The
#: list is the contract; `tools/call` is absent on purpose — a discovery that can
#: invoke a tool is a billed call wearing a different name.
DISCOVERY_METHODS: tuple[str, ...] = (
    "initialize", "notifications/initialized", "tools/list",
    "resources/list", "resources/read",
)

#: Instruction-like text in vendor documentation. A tool description is
#: untrusted input; one that addresses the host agent is a finding, not a hint.
#: Kept deliberately narrow: each pattern is a sentence shape that has no place
#: in a parameter description, so a normal "use this tool to search" does not
#: trip it.
_UNTRUSTED_MARKERS = (
    re.compile(r"\bignore\b.{0,60}\binstructions\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bsystem\s*:", re.IGNORECASE),
    re.compile(r"\btools?/call\b", re.IGNORECASE),
    re.compile(r"\bprint\b.{0,60}\b(api[_-]?key|token|secret|password)\b",
               re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(endpoint|url|host)\s*=", re.IGNORECASE),
)


def _field(value, *names):
    """Read a field off an SDK object or a plain mapping, over both spellings.

    The MCP SDK hands us attribute-bearing objects; a snapshot read from a file
    or a test fixture is a dict. Both are the same descriptor, so neither is
    privileged here.
    """
    for name in names:
        found = (value.get(name) if isinstance(value, dict)
                 else getattr(value, name, None))
        if found is not None:
            return found
    return None


def tool_descriptor(item) -> dict:
    """One tool's descriptor, hashed over everything that describes it.

    **A name-only hash cannot see a schema change.** The tool a research call is
    allowed to invoke is defined by its input schema, its description and its
    annotations — not by the string `search`. So the descriptor digest covers all
    four fields, and a changed `inputSchema` under an unchanged name produces a
    different digest (which is what makes the drift check meaningful).
    """
    name = str(_field(item, "name") or "")
    description = str(_field(item, "description") or "")
    schema = _field(item, "inputSchema", "input_schema") or {}
    annotations = _field(item, "annotations") or {}
    return {
        "name": name,
        "description_sha256": _digest(description),
        "input_schema_sha256": _digest(schema),
        "annotations_sha256": _digest(annotations),
        "descriptor_sha256": _digest({"name": name, "description": description,
                                      "inputSchema": schema,
                                      "annotations": annotations}),
    }


def surface_digest(descriptors) -> str:
    """One hash over the whole set, independent of the order it arrived in.

    Order independence is the property that matters: an MCP server may reorder
    its tool list between two identical sessions, and a digest that moved for
    that reason would report drift on every call.
    """
    normalised = [item if isinstance(item, dict) and "descriptor_sha256" in item
                  else tool_descriptor(item) for item in descriptors]
    digests = sorted(str(item["descriptor_sha256"]) for item in normalised)
    return _digest({"tools": digests})


def untrusted_descriptors(items) -> list[str]:
    """Names of tools whose own description reads like an instruction."""
    flagged: list[str] = []
    for item in items:
        name = str(_field(item, "name") or "")
        text = " ".join(str(_field(item, f) or "") for f in ("description", "name"))
        if any(marker.search(text) for marker in _UNTRUSTED_MARKERS):
            flagged.append(name)
    return flagged


def _checked_resource_uri(uri: str) -> str:
    """Only the two reviewed URI shapes are readable.

    A discovery that will read whatever URI it is handed is a request to a route
    nobody chose. The index and one engine schema are the whole contract.
    """
    if not isinstance(uri, str) or not uri:
        raise ValueError("SERPAPI_RESOURCE_URI_INVALID")
    if uri == INDEX_URI:
        return uri
    prefix = "serpapi://engines/"
    if uri.startswith(prefix):
        try:
            engine_uri(uri[len(prefix):])   # validates the segment's alphabet
        except ValueError:
            # A bad segment makes the *URI* unreadable, which is the finding the
            # caller needs; naming it as a bad engine would suggest the caller
            # had already been talking about engines.
            raise ValueError("SERPAPI_RESOURCE_URI_INVALID") from None
        return uri
    raise ValueError("SERPAPI_RESOURCE_URI_INVALID")


async def _notify_initialized(session) -> str:
    """Send ``notifications/initialized``, over whichever spelling the session has."""
    direct = getattr(session, "notify_initialized", None)
    if callable(direct):
        await direct()
        return "notifications/initialized"
    generic = getattr(session, "send_notification", None)
    if callable(generic):
        try:
            await generic("notifications/initialized")
        except TypeError:
            await generic()
        return "notifications/initialized"
    raise ValueError("SERPAPI_SESSION_CANNOT_NOTIFY_INITIALIZED")


async def _bounded_read(session, uri: str) -> str:
    text = await _read_resource(session, uri)
    if len(text.encode("utf-8")) > DISCOVERY_MAX_BYTES:
        raise ValueError("SERPAPI_SCHEMA_TOO_LARGE")
    return text


async def discover_surface(session, *, catalog: EngineCatalog | None = None,
                           expected_tool_count: int | None = None,
                           resources: tuple[str, ...] = ()) -> dict:
    """A bounded, read-only probe of one MCP surface. **Never calls a tool.**

    The value of this function is what it refuses to do. It performs exactly the
    methods in :data:`DISCOVERY_METHODS`, reads only URIs the session advertised
    and only in the two reviewed shapes, bounds every body, and closes on a
    changed tool count or instruction-like vendor text.

    It does **not** promote anything. ``reviewed`` is ``False`` and ``live`` is
    ``False`` no matter how clean the surface looks: a synthetic or in-process
    session is evidence that the contract behaves, not evidence about the vendor.
    """
    methods: list[str] = []

    await session.initialize()
    methods.append("initialize")

    methods.append(await _notify_initialized(session))

    listed = await session.list_tools()
    methods.append("tools/list")
    items = list(getattr(listed, "tools", None) or ())

    flagged = untrusted_descriptors(items)
    if flagged:
        raise ValueError("SERPAPI_SURFACE_UNTRUSTED_INSTRUCTIONS")

    descriptors = [tool_descriptor(item) for item in items]
    if expected_tool_count is not None and len(descriptors) != expected_tool_count:
        raise ValueError("SERPAPI_SURFACE_TOOL_COUNT_CHANGED")

    advertised = await advertised_uris(session)
    methods.append("resources/list")

    read: list[str] = []
    for uri in resources:
        checked = _checked_resource_uri(uri)
        _require_advertised(advertised, _advertisement_form(checked))
        await _bounded_read(session, checked)
        methods.append("resources/read")
        read.append(checked)

    return {
        "schema": SURFACE_SCHEMA,
        "methods": methods,
        "resources_read": read,
        "tools": descriptors,
        "tool_count": len(descriptors),
        "surface_sha256": surface_digest(descriptors),
        "untrusted_descriptors": flagged,
        "engine_count": len(catalog.engine_names()) if catalog is not None else None,
        "reviewed": REVIEWED_ENGINE_RESOURCES,
        "live": False,
        "evidence": "SYNTHETIC_SURFACE_NOT_VENDOR_VERIFIED",
        "provenance": "NOT_LIVE_IN_MEMORY_OR_INJECTED_SESSION",
    }


def _advertisement_form(uri: str) -> str:
    """How an advertised resource names this URI.

    The index is advertised as a literal ``uri``; a per-engine schema is
    advertised as a ``uriTemplate`` (``serpapi://engines/{engine}`` here, and
    ``serpapi://engines/{engine_name}`` on the live vendor server). A caller that
    reads a concrete engine URI is checked against the template it belongs to,
    because that is what the server actually listed; the variable's spelling is
    the server's, and :func:`_require_advertised` compares shapes for that
    reason.
    """
    return INDEX_URI if uri == INDEX_URI else SCHEMA_URI_TEMPLATE


# Engine names become URI path segments, so the accepted alphabet is the one
# that survives that trip unchanged. A name outside it is dropped from the
# index rather than repaired: `google maps` is not a name we should invent a
# spelling for.
_ENGINE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class EngineCatalogError(ValueError):
    """Kept as a ValueError so callers of the MCP surface see one error class."""


def engine_uri(engine: str) -> str:
    if not isinstance(engine, str) or not _ENGINE_NAME.fullmatch(engine):
        raise ValueError("SERPAPI_ENGINE_NAME_INVALID")
    return SCHEMA_URI_TEMPLATE.format(engine=engine)


def _canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_of(value) -> str | None:
    """The readable body of a resource read, over either SDK attribute case."""
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)) or not value:
        return None
    body = getattr(value[0], "text", None)
    if body is None:
        body = getattr(value[0], "blob", None)
    return body if isinstance(body, str) else None


def parse_resource_content(result) -> tuple[str | None, str | None]:
    """``(text, mime_type)`` from an MCP ``ReadResourceResult``.

    The MCP Python SDK uses camelCase on the wire and snake_case on some
    versions of its objects; both are read, and neither is guessed at.
    """
    contents = getattr(result, "contents", None)
    if contents is None:
        contents = getattr(result, "content", None)
    text = _text_of(contents)
    mime = None
    if isinstance(contents, (list, tuple)) and contents:
        mime = (getattr(contents[0], "mimeType", None)
                or getattr(contents[0], "mime_type", None))
    return text, (mime if isinstance(mime, str) else None)


def parse_engine_index(text: str) -> tuple[str, ...]:
    """Engine names from whatever shape the index resource arrived in.

    Four shapes are accepted because the resource is vendor-authored and the
    wrapper has changed before: a JSON object with an ``engines`` key, a JSON
    array, a list of objects carrying ``name``/``engine``, and a newline- or
    comma-separated string. Names that fail the URI alphabet are dropped — an
    unparseable index must not become a catalog of nothing, and it must not
    become a catalog of guesses either.
    """
    if not isinstance(text, str) or not text.strip():
        return ()
    candidates: object
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        candidates = re.split(r"[\s,]+", stripped)
    else:
        if isinstance(payload, dict):
            candidates = payload.get("engines", payload.get("engine", []))
        else:
            candidates = payload
        if isinstance(candidates, str):
            candidates = re.split(r"[\s,]+", candidates)
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    names: list[str] = []
    for item in candidates:
        name = item
        if isinstance(item, dict):
            name = item.get("name", item.get("engine", item.get("id")))
        if isinstance(name, str):
            name = name.strip()
            if _ENGINE_NAME.fullmatch(name) and name not in names:
                names.append(name)
    return tuple(names)


def parse_engine_schema(text: str) -> dict:
    """One engine's parameter schema, or a refusal that says why not.

    A schema is **data**, and this function does not try to police its prose: a
    vendor-authored description that reads like an instruction is still a
    description. What it does police is the shape (must be an object) and the
    size, because an unbounded resource body is how a read turns into a memory
    event. The adapter is what marks the result untrusted; here it is simply
    parsed and bounded.
    """
    if isinstance(text, str) and len(text.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise ValueError("SERPAPI_SCHEMA_TOO_LARGE")
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("SERPAPI_ENGINE_SCHEMA_UNREADABLE") from None
    if not isinstance(payload, dict):
        raise ValueError("SERPAPI_ENGINE_SCHEMA_UNREADABLE")
    return payload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EngineCatalog:
    """A hash-pinned on-disk view of the vendor's engine resources.

    ``clock`` is injectable so staleness is testable without sleeping, and
    ``ttl_seconds`` is a *reporting* threshold here — a stale index is still
    readable, it is just not allowed to look fresh.
    """

    def __init__(self, path: str | Path, *, clock=None, ttl_seconds: int = 86400):
        self.path = Path(path)
        self._clock = clock or _utc_now
        self.ttl_seconds = ttl_seconds

    @classmethod
    def default(cls) -> "EngineCatalog":
        """The catalog beside the run ledger, under the customer data root."""
        return cls(data_root(None) / "state" / "serpapi-engine-cache.json")

    # -- storage ---------------------------------------------------------
    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt file must not read as "no engines cached": those two
            # states lead to different actions (re-read the vendor vs. trust
            # an empty catalog), and collapsing them is how an empty answer
            # gets reported as a complete one.
            raise ValueError("SERPAPI_ENGINE_CACHE_UNREADABLE") from None
        if not isinstance(payload, dict):
            raise ValueError("SERPAPI_ENGINE_CACHE_UNREADABLE")
        return payload

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, self.path)

    def _blank(self) -> dict:
        return {"schema": CACHE_SCHEMA,
                "source": {"endpoint": DEFAULT_ENDPOINT,
                           "index_uri": INDEX_URI,
                           "schema_uri_template": SCHEMA_URI_TEMPLATE},
                "index": {}, "engines": {}}

    # -- reads -----------------------------------------------------------
    def engine_names(self) -> tuple[str, ...]:
        index = self._read().get("index") or {}
        names = index.get("engines") or []
        return tuple(n for n in names if isinstance(n, str))

    def cached_schema(self, engine: str) -> dict | None:
        """The cached entry, or None. Never raises for a miss."""
        entry = (self._read().get("engines") or {}).get(engine)
        return entry if isinstance(entry, dict) else None

    def schema(self, engine: str) -> dict:
        """One pinned schema body, refusing both a miss and a tampered cache."""
        engine_uri(engine)  # validate before touching the file
        entry = self.cached_schema(engine)
        if entry is None:
            raise ValueError("SERPAPI_ENGINE_SCHEMA_NOT_CACHED")
        body = entry.get("schema")
        pinned = entry.get("sha256")
        if not isinstance(pinned, str) or pinned != _digest(body):
            raise ValueError("SERPAPI_ENGINE_CACHE_INTEGRITY")
        return body

    # -- drift ---------------------------------------------------------------
    def schema_verdict(self, engine: str, observed_text: str) -> str:
        """Compare an observed body to the accepted pin, in §2's vocabulary.

        ``UNVERIFIED`` when there is no accepted pin (nothing was compared),
        ``MATCH`` when the bytes hash the same, ``DRIFT`` when they do not, and
        ``ABSENT`` when the body did not parse — a mismatch we cannot describe is
        not the same finding as a mismatch we can.
        """
        try:
            catalog_module = engine_uri(engine)  # noqa: F841 - name validation
        except ValueError:
            return "ABSENT"
        entry = self.cached_schema(engine)
        if entry is None:
            return "UNVERIFIED"
        try:
            observed = json.loads(observed_text)
        except (TypeError, json.JSONDecodeError):
            return "ABSENT"
        if not isinstance(observed, dict):
            return "ABSENT"
        return "MATCH" if _digest(observed) == entry.get("sha256") else "DRIFT"

    def note_drift(self, engine: str, observed: Any, *,
                   source_uri: str | None = None,
                   observed_at: str | None = None) -> dict:
        """Record that the vendor now serves a different body. Accept nothing.

        The accepted pin is deliberately **not** overwritten: a body that changed
        without review must not become the thing the next call is validated
        against, or the drift would launder itself into the baseline.
        """
        engine_uri(engine)
        entry = {
            "observed_sha256": _digest(observed),
            "accepted_sha256": (self.cached_schema(engine) or {}).get("sha256"),
            "source_uri": source_uri or engine_uri(engine),
            "observed_at": self._stamp(source_uri=source_uri, observed_at=observed_at),
        }
        state = self._read() or self._blank()
        state.setdefault("drift", {})[engine] = entry
        self._write(state)
        return dict(entry)

    def drift(self, engine: str) -> dict | None:
        entry = (self._read().get("drift") or {}).get(engine)
        return entry if isinstance(entry, dict) else None

    def drifted(self, engine: str) -> bool:
        return self.drift(engine) is not None

    def clear_drift(self, engine: str) -> bool:
        """Forget a drift. Only a deliberate accept should call this."""
        state = self._read()
        drift = state.get("drift") or {}
        if engine not in drift:
            return False
        del drift[engine]
        state["drift"] = drift
        self._write(state)
        return True

    def is_stale(self) -> bool:
        observed = (self._read().get("index") or {}).get("observed_at")
        return self._age_seconds(observed) > self.ttl_seconds

    def _age_seconds(self, observed) -> float:
        if not isinstance(observed, str):
            return float("inf")
        try:
            when = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        if when.tzinfo is None:
            return float("inf")
        return (self._clock() - when.astimezone(timezone.utc)).total_seconds()

    def summary(self) -> dict:
        """Counts and freshness — never a schema body.

        This is the shape that may travel into an agent's context.
        """
        state = self._read()
        index = state.get("index") or {}
        engines = sorted((state.get("engines") or {}).keys())
        stale = self.is_stale()
        drift = state.get("drift") or {}
        return {
            "index_uri": INDEX_URI,
            "schema_uri_template": SCHEMA_URI_TEMPLATE,
            "reviewed": REVIEWED_ENGINE_RESOURCES,
            "review_note": RESOURCE_REVIEW_NOTE,
            "drift": sorted(name for name in drift),
            "engine_count": len(self.engine_names()),
            "engines": sorted(self.engine_names()),
            "cached_schema_count": len(engines),
            "cached_engines": engines,
            "observed_at": index.get("observed_at"),
            "stale": stale,
            "freshness_note": ("INDEX_STALE_FALLBACK_TO_LAST_OBSERVED" if stale
                               else "INDEX_FRESH_AS_OBSERVED"),
        }

    # -- writes ----------------------------------------------------------
    def _stamp(self, *, source_uri: str, observed_at: str | None) -> str:
        if observed_at is None:
            return self._clock().isoformat()
        return observed_at

    def record_index(self, engines, *, source_uri: str = INDEX_URI,
                     observed_at: str | None = None) -> dict:
        names = [n for n in engines if isinstance(n, str) and _ENGINE_NAME.fullmatch(n)]
        state = self._read() or self._blank()
        entry = {"engines": names, "engine_count": len(names),
                 "sha256": _digest(names), "source_uri": source_uri,
                 "observed_at": self._stamp(source_uri=source_uri,
                                            observed_at=observed_at)}
        state["index"] = entry
        self._write(state)
        return dict(entry)

    def record_schema(self, engine: str, schema: dict, *,
                      source_uri: str | None = None,
                      observed_at: str | None = None) -> dict:
        engine_uri(engine)  # never store a body under a name we would not read
        if not isinstance(schema, dict):
            raise ValueError("SERPAPI_ENGINE_SCHEMA_UNREADABLE")
        uri = source_uri or engine_uri(engine)
        state = self._read() or self._blank()
        entry = {"schema": schema, "sha256": _digest(schema), "source_uri": uri,
                 "observed_at": self._stamp(source_uri=uri, observed_at=observed_at)}
        state.setdefault("engines", {})[engine] = entry
        self._write(state)
        return dict(entry)


def resolve_engine(engine: str, *, catalog: "EngineCatalog") -> dict:
    """Is this engine one we may ask for, decided locally and without a call?

    Four outcomes, and the reason codes come from the frozen contract's closed
    set, so a caller can act on them without parsing prose:

    - the name is not a name this adapter would ever put in a URI
      → ``CAPABILITY_PARAM_NOT_ALLOWED``;
    - the index was never read, so *no* engine can be shown to exist
      → ``CAPABILITY_UNKNOWN`` with ``index_state = INDEX_NOT_READ``;
    - the index was read and this engine is not in it
      → ``CAPABILITY_UNKNOWN`` with ``index_state = INDEX_READ``;
    - the engine is real but its schema has not been fetched yet
      → ``CAPABILITY_NOT_AVAILABLE``.

    The last two are deliberately different. Collapsing them would tell a
    caller to go re-read the index when the right move is to read one
    resource, and it would let "we have never looked" read as "the vendor does
    not have it".
    """
    if not isinstance(engine, str) or not _ENGINE_NAME.fullmatch(engine):
        return {"status": "BLOCKED", "reason": "CAPABILITY_PARAM_NOT_ALLOWED",
                "engine": None, "upstream_calls": 0}
    base = {"engine": engine, "upstream_calls": 0}
    names = catalog.engine_names()
    if not names:
        return {**base, "status": "BLOCKED", "reason": "CAPABILITY_UNKNOWN",
                "index_state": "INDEX_NOT_READ"}
    if engine not in names:
        return {**base, "status": "BLOCKED", "reason": "CAPABILITY_UNKNOWN",
                "index_state": "INDEX_READ", "index_engine_count": len(names)}
    try:
        body = catalog.schema(engine)
    except ValueError as exc:
        if str(exc) == "SERPAPI_ENGINE_CACHE_INTEGRITY":
            return {**base, "status": "BLOCKED", "reason": "CAPABILITY_NOT_AVAILABLE",
                    "integrity": "CACHE_INTEGRITY_FAILURE",
                    "next_step": "RE_READ_ENGINE_SCHEMA_RESOURCE"}
        return {**base, "status": "BLOCKED", "reason": "CAPABILITY_NOT_AVAILABLE",
                "next_step": "INSERT_ENGINE_SCHEMA_RESOURCE_READ_PENDING"}
    entry = catalog.cached_schema(engine) or {}
    return {**base, "status": "READY", "index_state": "INDEX_READ",
            "sha256": entry.get("sha256"), "source_uri": entry.get("source_uri"),
            "schema": body}


async def _read_resource(session, uri: str) -> str:
    try:
        result = await session.read_resource(uri)
    except Exception:
        # The request went out and did not come back with content. The detail is
        # dropped rather than forwarded: an upstream error string is untrusted
        # text, and this one would travel into a receipt.
        raise ValueError("SERPAPI_RESOURCE_UNAVAILABLE") from None
    text, _mime = parse_resource_content(result)
    if text is None:
        raise ValueError("SERPAPI_RESOURCE_EMPTY")
    return text


async def advertised_uris(session) -> tuple[str, ...]:
    """Every URI and URI template the session says it serves.

    Read *before* a resource read, so a read for something nobody advertised is
    refused locally instead of being sent to a route that does not exist.
    """
    try:
        listed = await session.list_resources()
    except Exception:
        raise ValueError("SERPAPI_RESOURCE_UNAVAILABLE") from None
    uris: list[str] = []
    for resource in getattr(listed, "resources", None) or ():
        for name in ("uri", "uriTemplate", "uri_template"):
            value = getattr(resource, name, None)
            if isinstance(value, str) and value and value not in uris:
                uris.append(value)
    # MCP advertises resource templates through a separate standard method.
    # Older in-process fixtures put templates in resources; retain that shape,
    # but a real server must not be required to duplicate its advertisements.
    template_method = getattr(session, "list_resource_templates", None)
    if callable(template_method) and SCHEMA_URI_TEMPLATE not in uris:
        try:
            templates = await template_method()
        except Exception:
            raise ValueError("SERPAPI_RESOURCE_UNAVAILABLE") from None
        for resource in (getattr(templates, "resource_templates", None)
                         or getattr(templates, "resourceTemplates", None) or ()):
            value = getattr(resource, "uri_template", None) or getattr(resource, "uriTemplate", None)
            if isinstance(value, str) and value and value not in uris:
                uris.append(value)
    return tuple(uris)


#: One RFC 6570-style variable inside a URI template. SerpApi names its own
#: variables (the live server advertises ``serpapi://engines/{engine_name}``
#: where this module spells the same route ``.../{engine}``), so the spelling is
#: compared only as a shape.
_TEMPLATE_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _template_shape(uri: str) -> str:
    """The URI with every ``{placeholder}`` reduced to ``{}``.

    The route is the contract; the variable's name is the server's to choose. Two
    templates name one resource when their shapes are equal, and a literal URI
    never contains ``{}``, so a literal is only ever matched exactly.
    """
    if not isinstance(uri, str):
        return ""
    return _TEMPLATE_PLACEHOLDER.sub("{}", uri)


def _require_advertised(advertised: tuple[str, ...], wanted: str) -> None:
    if wanted in advertised:
        return
    shape = _template_shape(wanted)
    if "{}" in shape and any(_template_shape(uri) == shape for uri in advertised):
        return
    raise ValueError("SERPAPI_RESOURCE_NOT_ADVERTISED")


async def discover_index(session, *, catalog: EngineCatalog) -> dict:
    """Read the index resource once and record it. No per-engine read."""
    _require_advertised(await advertised_uris(session), INDEX_URI)
    text = await _read_resource(session, INDEX_URI)
    names = parse_engine_index(text)
    recorded = catalog.record_index(names, source_uri=INDEX_URI)
    recorded["source"] = "RESOURCE_READ"
    return recorded


async def discover_schema(session, engine: str, *, catalog: EngineCatalog,
                          refresh: bool = False) -> dict:
    """One engine schema: from the pinned cache, or a single resource read."""
    entry = None if refresh else catalog.cached_schema(engine)
    if entry is not None:
        body = catalog.schema(engine)  # re-validates the pin
        return {"engine": engine, "schema": body, "sha256": entry.get("sha256"),
                "source_uri": entry.get("source_uri"), "source": "CACHE_HIT"}
    uri = engine_uri(engine)
    _require_advertised(await advertised_uris(session), SCHEMA_URI_TEMPLATE)
    text = await _read_resource(session, uri)
    schema = parse_engine_schema(text)
    verdict = catalog.schema_verdict(engine, text)
    if verdict == "DRIFT":
        # Fail closed: report the drift and keep the accepted pin, rather than
        # recording the new body as if it had been reviewed.
        drift = catalog.note_drift(engine, schema, source_uri=uri)
        return {"engine": engine, "schema": schema, "sha256": drift["observed_sha256"],
                "accepted_sha256": drift["accepted_sha256"], "source_uri": uri,
                "source": "RESOURCE_READ", "schema_verdict": "DRIFT"}
    recorded = catalog.record_schema(engine, schema, source_uri=uri)
    return {"engine": engine, "schema": schema, "sha256": recorded["sha256"],
            "source_uri": uri, "source": "RESOURCE_READ",
            "schema_verdict": verdict}


__all__ = [
    "CACHE_SCHEMA", "CATALOG_ERROR_CODES", "DEFAULT_ENDPOINT", "EngineCatalog",
    "EngineCatalogError", "INDEX_URI", "MAX_SCHEMA_BYTES", "SCHEMA_URI_TEMPLATE",
    "advertised_uris", "discover_index", "discover_schema", "engine_uri",
    "parse_engine_index", "parse_engine_schema", "parse_resource_content",
    "resolve_engine",
]
