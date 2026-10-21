"""Provider transport parsing under a hostile upstream (no network).

Targets, all driven through their injectable ``opener`` so the only "upstream"
is a synthetic local decoy:

* ``dual_search_mcp._request`` / ``_normalize`` / ``NoRedirect``
* ``research_provider_extensions._http_json``

Contract sources: ``docs/architecture/
MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md``
§2 table row "Local human authorization" (§4: *"make the security level of each
deployment topology explicit"* — a credential boundary that leaks the key
through error text is not a boundary), §2 "SerpApi duplicate paths" and
"Provider pricing" (one active path, fail closed on an unknown upstream
answer), Appendix D P1-01 (egress failure paths) and P2-04 (*"save sanitized
request metadata"*). The modules themselves state the same guarantee: a SerpApi
query URL carries ``api_key``, so no failure message may stringify a request URL
or an upstream body, and a redirect must never be followed.

A *synthetic* secret is injected so a leak assertion has something to search
for; it is not a credential and grants no access. Tests whose contract the
candidate does not meet are deliberately left **red**.
"""
from __future__ import annotations

import json
import urllib.error

import pytest

import dual_search_mcp as dsm
import research_provider_extensions as ext

# Synthetic marker: never a real credential, only a string to search for.
KEY = "qa-gate-SYNTHETICKEY-DEADBEEF"
SERPAPI_URL = "https://serpapi.com/search.json?engine=google&q=hello&api_key=" + KEY
SERPER_URL = "https://google.serper.dev/search"


# --- synthetic transport ----------------------------------------------------


class _Resp:
    """A bounded response object; records every ``read`` size it is asked for."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
        self.read_sizes: list[int] = []

    def read(self, n: int = -1):
        self.read_sizes.append(n)
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _returns(response):
    def opener(req, timeout=None):
        return response
    return opener


def _raises(exc):
    def opener(req, timeout=None):
        raise exc
    return opener


def _contract_text(exc: BaseException) -> str:
    """Exactly what the contract bounds: ``str`` and ``repr`` of the raised error."""
    return str(exc) + "\n" + repr(exc)


def _chain_text(exc: BaseException) -> str:
    """Everything reachable by walking the suppressed ``__cause__``/``__context__``."""
    parts = [_contract_text(exc)]
    seen, node = {id(exc)}, exc
    for _ in range(8):
        nxt = None
        for attr in ("__cause__", "__context__"):
            candidate = getattr(node, attr, None)
            if candidate is not None and id(candidate) not in seen:
                nxt = candidate
                break
        if nxt is None:
            break
        seen.add(id(nxt))
        parts.append(_contract_text(nxt))
        node = nxt
    return "\n".join(parts)


def _assert_no_leak(exc: BaseException, *needles: str):
    text = _contract_text(exc)
    for needle in needles:
        assert needle not in text, (
            "a credential or request URL surfaced in a raised message: "
            f"needle={needle!r} message={str(exc)!r}")


# An HTTP reason phrase is attacker-controlled (it is the server's status-line
# text). Realistic failures carry the standard phrase; a hostile server may put
# anything there. ``realistic`` is used for the contract tests, ``hostile`` for
# the exception-chain hardening test.
def _http_error(url: str, code: int, reason: str, location: str = "") -> urllib.error.HTTPError:
    headers = {"Location": location} if location else {}
    return urllib.error.HTTPError(url, code, reason, headers, None)


def _env_for(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", KEY)
    monkeypatch.setenv("SERPER_API_KEY", KEY)


# --- 1. the key never surfaces in any failure message -----------------------


@pytest.mark.parametrize("provider", ["serpapi", "serper"])
def test_an_http_error_never_surfaces_the_key_or_the_request_url(clean_env, monkeypatch, provider):
    """The SerpApi request URL contains ``api_key``, so an ``HTTPError`` must never
    be stringified into the raised message: neither ``str`` nor ``repr`` of the
    refusal may carry the key or the request URL."""
    _env_for(monkeypatch)
    url = SERPAPI_URL if provider == "serpapi" else SERPER_URL
    error = _http_error(url, 500, "Internal Server Error")
    with pytest.raises(ValueError) as caught:
        dsm._request(provider, "web", "hello", "US", "en", 5, opener=_raises(error))
    assert "PROVIDER_HTTP_ERROR_500" in str(caught.value)
    _assert_no_leak(caught.value, KEY, SERPAPI_URL, url)


@pytest.mark.parametrize("provider", ["serpapi", "serper"])
def test_a_connection_error_never_surfaces_the_key(clean_env, monkeypatch, provider):
    """A real ``URLError`` carries a socket reason (an ``OSError``), not a URL; the
    refusal must stay a bare code regardless."""
    _env_for(monkeypatch)
    reason = OSError(111, "Connection refused")
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_ERROR") as caught:
        dsm._request(provider, "web", "hello", "US", "en", 5,
                     opener=_raises(urllib.error.URLError(reason)))
    _assert_no_leak(caught.value, KEY, SERPAPI_URL)


@pytest.mark.parametrize("provider", ["serpapi", "serper"])
def test_a_non_json_body_never_surfaces_the_body(clean_env, monkeypatch, provider):
    """A malformed body may echo the key; the refusal is a code, not the body."""
    _env_for(monkeypatch)
    body = ("<html>error: bad request, see https://example.invalid/log?"
            + "k=" + KEY + "</html>").encode()
    with pytest.raises(ValueError, match="PROVIDER_INVALID_JSON") as caught:
        dsm._request(provider, "web", "hello", "US", "en", 5, opener=_returns(_Resp(body)))
    _assert_no_leak(caught.value, KEY)


@pytest.mark.parametrize("provider", ["serpapi", "serper"])
def test_a_declared_upstream_error_never_surfaces_the_body(clean_env, monkeypatch, provider):
    """An upstream error object that echoes the request key must be reduced to a
    code: the body is attacker-controlled text."""
    _env_for(monkeypatch)
    body = json.dumps({"error": f"invalid api_key {KEY}",
                       "request_url": SERPAPI_URL}).encode()
    with pytest.raises(ValueError, match="PROVIDER_DECLARED_ERROR") as caught:
        dsm._request(provider, "web", "hello", "US", "en", 5, opener=_returns(_Resp(body)))
    _assert_no_leak(caught.value, KEY, SERPAPI_URL)


def test_an_oversized_body_never_surfaces_the_body(clean_env, monkeypatch):
    _env_for(monkeypatch)
    body = (KEY.encode() * (dsm.MAX_BODY // len(KEY) + 2))
    with pytest.raises(ValueError, match="PROVIDER_BODY_TOO_LARGE") as caught:
        dsm._request("serpapi", "web", "hello", "US", "en", 5, opener=_returns(_Resp(body)))
    _assert_no_leak(caught.value, KEY)


@pytest.mark.parametrize("provider", ["serpapi", "serper"])
def test_an_http_error_never_surfaces_the_key_on_the_extensions_transport(
        clean_env, monkeypatch, provider):
    """``research_provider_extensions._http_json`` sends a key too; the same
    no-stringify rule applies to its ``HTTPError`` branch."""
    for status in (500, 401, 403, 429):
        error = _http_error(SERPAPI_URL, status, "Upstream Error")
        with pytest.raises(ValueError) as caught:
            ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_raises(error))
        assert str(caught.value).startswith("UPSTREAM_"), str(caught.value)
        _assert_no_leak(caught.value, KEY, SERPAPI_URL)


def test_a_declared_upstream_error_never_surfaces_the_key_on_the_extensions_transport(
        clean_env, monkeypatch):
    body = json.dumps({"error": {"code": "bad_key", "message": f"key {KEY} rejected"}}).encode()
    with pytest.raises(ValueError, match="UPSTREAM_DECLARED_ERROR") as caught:
        ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_returns(_Resp(body)))
    _assert_no_leak(caught.value, KEY, SERPAPI_URL)


def test_an_invalid_json_body_never_surfaces_the_body_on_the_extensions_transport(
        clean_env, monkeypatch):
    body = ("not json " + KEY).encode()
    with pytest.raises(ValueError, match="UPSTREAM_NOT_JSON") as caught:
        ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_returns(_Resp(body)))
    _assert_no_leak(caught.value, KEY)


# --- 2. the size ceiling is enforced with a bounded read --------------------


def test_the_search_transport_reads_a_bounded_number_of_bytes(clean_env, monkeypatch):
    """A hostile upstream must not be allowed to make the client allocate an
    unbounded body; the read is capped and one byte past the cap is refused."""
    _env_for(monkeypatch)
    response = _Resp(b"x" * (dsm.MAX_BODY + 1))
    with pytest.raises(ValueError, match="PROVIDER_BODY_TOO_LARGE"):
        dsm._request("serpapi", "web", "hello", "US", "en", 5, opener=_returns(response))
    assert response.read_sizes == [dsm.MAX_BODY + 1], response.read_sizes


def test_a_body_at_the_ceiling_is_still_accepted(clean_env, monkeypatch):
    """The cap is a ceiling, not an off-by-one: exactly ``MAX_BODY`` parses."""
    _env_for(monkeypatch)
    payload = {"organic_results": [{"title": "ok"}], "pad": "a" * 64}
    encoded = json.dumps(payload).encode()
    padding = dsm.MAX_BODY - len(encoded)
    assert padding >= 0
    body = json.dumps({"organic_results": [{"title": "ok"}], "pad": "a" * (64 + padding)}).encode()
    assert len(body) == dsm.MAX_BODY
    result = dsm._request("serpapi", "web", "hello", "US", "en", 5,
                          opener=_returns(_Resp(body)))
    assert result["organic_results"] == [{"title": "ok"}]


def test_the_extensions_transport_reads_a_bounded_number_of_bytes(clean_env, monkeypatch):
    response = _Resp(b"x" * (ext.MAX_OUTPUT_BYTES + 1))
    with pytest.raises(ValueError, match="UPSTREAM_RESULT_TOO_LARGE"):
        ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_returns(response))
    assert response.read_sizes == [ext.MAX_OUTPUT_BYTES + 1], response.read_sizes


# --- 3. error shapes --------------------------------------------------------


def test_a_non_dict_search_body_is_refused(clean_env, monkeypatch):
    """A top-level JSON array is not a search response."""
    _env_for(monkeypatch)
    with pytest.raises(ValueError, match="PROVIDER_INVALID_BODY"):
        dsm._request("serpapi", "web", "hello", "US", "en", 5,
                     opener=_returns(_Resp(b'[{"title": "x"}]')))


@pytest.mark.parametrize("status", ["Processing", "Error"])
def test_a_serpapi_search_that_is_not_successful_is_refused(clean_env, monkeypatch, status):
    _env_for(monkeypatch)
    body = json.dumps({"search_metadata": {"status": status},
                       "organic_results": [{"title": "x"}]}).encode()
    with pytest.raises(ValueError, match="PROVIDER_NOT_SUCCESSFUL"):
        dsm._request("serpapi", "web", "hello", "US", "en", 5, opener=_returns(_Resp(body)))


def test_the_extensions_transport_maps_provider_error_codes(clean_env, monkeypatch):
    cases = {
        "insufficient_credits": "UPSTREAM_INSUFFICIENT_CREDITS",
        "rate_limited": "UPSTREAM_RATE_LIMITED",
        "invalid_api_key": "UPSTREAM_AUTH_REJECTED",
        "something_else": "UPSTREAM_DECLARED_ERROR",
    }
    for code, expected in cases.items():
        body = json.dumps({"error": {"code": code}}).encode()
        with pytest.raises(ValueError, match=expected):
            ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_returns(_Resp(body)))


def test_a_non_dict_body_is_refused_on_the_extensions_transport(clean_env, monkeypatch):
    """The extensions transport must fail closed on a body whose shape is not an
    object: a top-level JSON array is returned verbatim today and flows on into
    provider artifact storage, so an upstream can hand the pipeline a shape no
    caller expects. The contract is a bounded ``UPSTREAM_*`` refusal (or a
    normalized object), never a silent passthrough.
    """
    body = b'[{"url": "https://example.invalid/", "content": "anything"}]'
    try:
        result = ext._http_json(SERPAPI_URL, method="GET", key=KEY,
                                opener=_returns(_Resp(body)))
    except ValueError as exc:
        assert str(exc).startswith("UPSTREAM_"), str(exc)
        return
    pytest.fail(
        "a top-level JSON array was accepted as a successful upstream body and "
        f"returned as {type(result).__name__}: {result!r}; the extensions "
        "transport validates no body shape, so an unknown upstream shape is "
        "stored as if it were a provider response")


# --- 4. redirects are not followed and leak nothing -------------------------


def test_the_redirect_handler_refuses_and_keeps_the_location_out_of_the_message():
    """Following a redirect would hand an auth header (and, for SerpApi, a query
    key) to another origin. The handler must refuse — not return a redirect — and
    must not carry the ``Location`` value into the message.
    """
    import urllib.request

    location = "https://evil.invalid/collect?" + "k=" + KEY
    handler = dsm.NoRedirect()
    request = urllib.request.Request(SERPAPI_URL)
    with pytest.raises(ValueError, match="PROVIDER_REDIRECT_BLOCKED") as caught:
        handler.redirect_request(request, None, 302, "Found", {}, location)
    _assert_no_leak(caught.value, KEY, location)


def test_a_redirect_response_is_refused_without_the_location(clean_env, monkeypatch):
    """Same guarantee through the real code path: a 3xx reaches the raised
    message as a code only."""
    _env_for(monkeypatch)
    location = "https://evil.invalid/collect?" + "k=" + KEY
    error = _http_error(SERPAPI_URL, 302, "Found", location)
    with pytest.raises(ValueError) as caught:
        dsm._request("serpapi", "web", "hello", "US", "en", 5, opener=_raises(error))
    assert "PROVIDER_HTTP_ERROR_302" in str(caught.value)
    _assert_no_leak(caught.value, KEY, location, SERPAPI_URL)


def test_a_redirect_response_is_refused_without_the_location_on_the_extensions_transport(
        clean_env, monkeypatch):
    location = "https://evil.invalid/collect?" + "k=" + KEY
    error = _http_error(SERPAPI_URL, 302, "Found", location)
    with pytest.raises(ValueError, match="UPSTREAM_INVALID_RESPONSE") as caught:
        ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_raises(error))
    _assert_no_leak(caught.value, KEY, location)


@pytest.mark.parametrize("transport", ["dual_search", "provider_extensions"])
def test_a_hostile_http_reason_phrase_is_not_reachable_through_the_exception_chain(
        clean_env, monkeypatch, transport):
    """HARDENING beyond the stated ``str``/``repr`` contract — currently RED.

    The raised error is a bare code and its ``str``/``repr`` are clean (asserted
    below), so the contract as written holds. But the status line is
    attacker-controlled and the refused ``HTTPError`` is still attached as the
    *suppressed* ``__context__`` of the replacement, so its reason phrase stays
    reachable to any consumer that walks the exception chain rather than using
    the traceback (this repository's own group-unwrapping helpers do exactly
    that kind of walk). Severity is bounded — ``__suppress_context__`` is set, so
    a normal traceback does not print it — but a credential-bearing status line
    should not survive the refusal at all.
    """
    _env_for(monkeypatch)
    hostile_reason = "internal error; api_key=" + KEY
    error = _http_error(SERPAPI_URL, 500, hostile_reason)
    with pytest.raises(ValueError) as caught:
        if transport == "dual_search":
            dsm._request("serpapi", "web", "hello", "US", "en", 5, opener=_raises(error))
        else:
            ext._http_json(SERPAPI_URL, method="GET", key=KEY, opener=_raises(error))
    exc = caught.value
    # The stated contract: the raised error itself carries nothing.
    assert KEY not in str(exc) and KEY not in repr(exc)
    assert exc.__cause__ is None
    if KEY in _chain_text(exc):
        pytest.fail(
            "the refused HTTPError is still attached to the raised ValueError as "
            f"a suppressed __context__, so its attacker-controlled reason phrase "
            f"({hostile_reason!r}) remains reachable by walking the exception "
            "chain, even though str()/repr() of the raised error are clean")


# --- 5. normalization is hostile-input safe ---------------------------------


def test_a_non_list_result_field_is_refused(clean_env):
    with pytest.raises(ValueError, match="PROVIDER_INVALID_RESULT_LIST"):
        dsm._normalize("serper", "web", {"organic": {"title": "not a list"}}, 10)
    with pytest.raises(ValueError, match="PROVIDER_INVALID_RESULT_LIST"):
        dsm._normalize("serpapi", "web", {"organic_results": "nope"}, 10)


def test_a_hostile_result_row_yields_a_well_formed_entry_or_is_dropped(clean_env):
    """Rows with the wrong types, missing fields or ``None`` must never produce an
    entry that asserts anything about the upstream text.
    """
    rows = [
        None,
        42,
        "a bare string",
        [],
        {"title": 12345, "link": {"not": "a url"}, "position": "first"},
        {"name": None, "snippet": {"a": 1}, "gps_coordinates": "not a dict"},
        {"title": "ok", "link": "javascript:alert(1)", "position": True},
        {"title": "ok2", "link": "https://example.invalid/x",
         "sourceUrl": "https://example.invalid/y"},
    ]
    entries = dsm._normalize("serper", "web", {"organic": rows}, 10)
    assert entries, "no row survived normalization at all"
    for entry in entries:
        assert entry["status"] == "discovered"
        assert entry["provider"] == "serper"
        assert entry["kind"] == "search_candidate"
        # No entry may assert verification of upstream text.
        assert "verified" not in entry
        assert "business_evidence" not in entry
        assert isinstance(entry["title"], str) and len(entry["title"]) <= 500
        assert isinstance(entry["url"], str) and len(entry["url"]) <= 2000
        assert isinstance(entry["snippet"], str) and len(entry["snippet"]) <= 1000
        assert entry["rank"] >= 1
        url = entry["url"]
        assert url == "" or url.startswith(("http://", "https://"))
    # A javascript: link is not a usable candidate URL.
    assert all(not e["url"].startswith("javascript:") for e in entries)


def test_an_upstream_self_declared_verification_is_not_honoured(clean_env):
    """A vendor row that declares itself verified must not become verified
    evidence; the normalizer's own vocabulary wins."""
    row = {"title": "x", "link": "https://example.invalid/",
           "verified": True, "business_evidence": True,
           "status": "verified", "source_status": "verified"}
    entry = dsm._normalize("serper", "web", {"organic": [row]}, 10)[0]
    assert entry["status"] == "discovered"
    assert "verified" not in entry
    assert entry.get("business_evidence") is None


def test_a_huge_result_list_is_capped_at_the_requested_limit(clean_env):
    rows = [{"title": f"r{i}", "link": "https://example.invalid/"} for i in range(5000)]
    assert len(dsm._normalize("serper", "web", {"organic": rows}, 3)) == 3
    assert len(dsm._normalize("serpapi", "web", {"organic_results": rows}, 7)) == 7


def test_maps_and_autocomplete_rows_are_normalized_without_claiming_verification(clean_env):
    places = dsm._normalize("serper", "maps", {"places": [
        {"title": "Cafe", "address": "1 Main St", "gps_coordinates": {"latitude": 1.0}},
        {"name": "Shop", "address": None, "gps_coordinates": "bad"},
    ]}, 10)
    assert [p["kind"] for p in places] == ["place_candidate", "place_candidate"]
    assert all("verified" not in p for p in places)
    suggestions = dsm._normalize("serper", "autocomplete", {"suggestions": ["a", "b", None]}, 10)
    assert [s["title"] for s in suggestions if isinstance(s, dict)]


def test_the_search_envelope_never_claims_verified_evidence(clean_env, monkeypatch, data_root):
    """End-to-end through ``search``: the stored envelope marks the result as
    discovered-not-verified and never as business evidence, even when the
    upstream row tries to declare itself verified.
    """
    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MARKETOPS_SEARCH_ALLOW_NETWORK", "1")
    monkeypatch.setenv("MARKETOPS_SEARCH_MAX_CALLS", "5")
    monkeypatch.setenv("SERPER_API_KEY", KEY)
    body = json.dumps({"organic": [
        {"title": "x", "link": "https://example.invalid/",
         "verified": True, "business_evidence": True}]}).encode()
    result = dsm.search("serper", "hello world", "US", "en",
                        run_id="runA", operation_id="op1", mode="web", limit=5,
                        opener=_returns(_Resp(body)))
    assert result["status"] == "DISCOVERED_NOT_VERIFIED"
    assert all("verified" not in entry for entry in result["results"])

    # The persisted receipt is the artifact the evidence pipeline reads.
    import hashlib

    receipt = (data_root / "search-receipts" / "runA"
               / (hashlib.sha256(b"op1").hexdigest() + ".json"))
    stored = json.loads(receipt.read_text(encoding="utf-8"))
    assert stored["schema"] == "marketops.search.discovery.v1"
    assert stored["source_status"] == "discovered_not_verified"
    assert stored["business_evidence"] is False
    assert all(entry["status"] == "discovered" for entry in stored["results"])
    assert "verified" not in json.dumps(stored["results"])
