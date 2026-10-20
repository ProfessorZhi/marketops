"""SerpApi: one logical query is one purchase, whichever transport carried it.

The defect this file holds closed is a *product* one, not a ledger one. The
ledger already keys a route on ``provider + account + capability + canonical
params`` and already refuses a second transport. What it could not do was see the
two paths as one operation: the REST adapter handed it
``{account, country, engine, language, limit, mode, page}`` and the embedded
official MCP handed it ``{country, engine, language, limit}``. ``route_shape``
digests ``args``, so those two mappings were two purchases no matter how correct
the ledger was.

Everything below runs **offline**: no provider key is used, no socket is opened,
and no vendor is called. The transport tests use fake openers and the ledger
tests use a throw-away data root, because the claim being proved is about the
identity and the refusal, not about anybody's account.
"""
from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dual_search_mcp as dsm  # noqa: E402
import research_provider_extensions as ext  # noqa: E402
import research_usage_ledger as ledger_module  # noqa: E402
import search_canonical as canonical  # noqa: E402
import serpapi_engine_catalog as catalog  # noqa: E402

CANARY = "zz-fixture-value-0123456789abcdef"
QUERY = "paint pool london"
TRANSPORT_REST = "rest"
TRANSPORT_MCP = "official_mcp"


# ---------------------------------------------------------------------------
# Identity fixtures: each one goes through the *adapter's own* builder.
# ---------------------------------------------------------------------------

def rest_identity(**overrides) -> dict:
    values = {"provider": "serpapi", "query": QUERY, "country": "GB",
              "language": "en", "mode": "web", "engine": "google",
              "limit": 10, "page": 1, "account": ""}
    values.update(overrides)
    return dsm._ledger_identity(**values)


def mcp_params(**overrides) -> dict:
    values = {"engine": "google", "q": QUERY, "gl": "gb", "hl": "en", "num": 10}
    values.update(overrides)
    return values


def mcp_identity(params: dict | None = None, *, wrapped: bool = False,
                 account: str = "") -> dict:
    envelope = {"params": params} if wrapped else params
    return ext._ledger_identity("serpapi_mcp_search", query="",
                                mcp_params=envelope,
                                fingerprint="adapter-local-digest",
                                account=account)


def _chain(exc: BaseException):
    """Every exception reachable from ``exc``, including ``__context__``.

    ``raise ... from None`` only sets ``__suppress_context__``; it does not clear
    ``__context__``. A test that reads ``str(exc)`` alone would miss exactly the
    leak this file exists to detect.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _leaks(exc: BaseException, secret: str = CANARY) -> bool:
    return any(secret in str(item) for item in _chain(exc))


# ---------------------------------------------------------------------------
# 1. The two adapters produce one identity
# ---------------------------------------------------------------------------

class SharedSerpApiIdentityTests(unittest.TestCase):
    def test_rest_and_mcp_produce_the_same_identity(self):
        self.assertEqual(rest_identity(), mcp_identity(mcp_params()))

    def test_the_mcp_adapter_reads_both_call_envelopes(self):
        """The real ``invoke`` path hands vendor params; tests used the envelope.

        Reading only ``{"params": ...}`` made every real call fall through to the
        adapter-local digest, which is the CD-03 gap this function closes.
        """
        raw = mcp_identity(mcp_params())
        wrapped = mcp_identity(mcp_params(), wrapped=True)
        self.assertEqual(raw, wrapped)

    def test_the_identity_has_the_reviewed_axes_and_no_mode(self):
        identity = mcp_identity(mcp_params())
        self.assertEqual({"account", "country", "engine", "language",
                          "limit", "page"}, set(identity["params"]))
        self.assertNotIn("mode", identity["params"],
                         "SerpApi has no category parameter; engine is its axis")
        self.assertEqual("search", identity["capability"])
        self.assertEqual(QUERY, identity["query"])

    def test_omitted_defaults_are_materialised(self):
        """An omitted axis and its default are one purchase, not two."""
        implicit = mcp_identity({"q": QUERY})
        explicit = rest_identity(country="US", language="en", engine="google",
                                 limit=10, page=1, account="")
        self.assertEqual(explicit, implicit)
        self.assertEqual("US", implicit["params"]["country"])
        self.assertEqual("en", implicit["params"]["language"])
        self.assertEqual("google", implicit["params"]["engine"])
        self.assertEqual(10, implicit["params"]["limit"])
        self.assertEqual(1, implicit["params"]["page"])

    def test_the_default_engine_matches_the_catalog(self):
        self.assertEqual(catalog.DEFAULT_ENGINE, canonical.SERPAPI_DEFAULT_ENGINE)

    def test_case_and_whitespace_are_one_operation(self):
        padded = rest_identity(query="  Paint   POOL london ")
        self.assertEqual(padded, mcp_identity(mcp_params()))

    def test_each_reviewed_axis_moves_the_signature(self):
        base = rest_identity()
        changes = (
            ({"query": "tiles birmingham"},
             {"q": "tiles birmingham"}),
            ({"engine": "google_scholar"},
             {"engine": "google_scholar"}),
            ({"country": "IE"}, {"gl": "ie"}),
            ({"language": "ga"}, {"hl": "ga"}),
            ({"limit": 20}, {"num": 20}),
            ({"page": 2}, {"page": 2}),
            ({"account": "team-a"}, {"account": "team-a"}),
        )
        for rest_change, mcp_change in changes:
            with self.subTest(axis=repr(rest_change)):
                rest_changed = rest_identity(**rest_change)
                mcp_changed = mcp_identity(
                    mcp_params(**{k: v for k, v in mcp_change.items()
                                  if k != "account"}),
                    account=mcp_change.get("account", ""))
                self.assertNotEqual(base, rest_changed)
                self.assertEqual(rest_changed, mcp_changed)

    def test_an_empty_query_is_not_dressed_up_as_a_shared_identity(self):
        """No canonical query means no claim; the adapter-local digest is honest."""
        identity = mcp_identity({})
        self.assertEqual("adapter-local-digest", identity["query"])
        self.assertNotEqual(rest_identity(), identity)


# ---------------------------------------------------------------------------
# 2. The durable ledger refuses the second route
# ---------------------------------------------------------------------------

class LedgerCrossTransportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ledger = ledger_module.for_root(self.root)
        self.project = ledger_module.project_for_root(self.root)
        for run in ("run-a", "run-b"):
            self.ledger.set_cap("run", f"{self.project}/{run}",
                                ceiling_calls=20, source="test-fixture")

    def tearDown(self):
        self._tmp.cleanup()

    def _reserve(self, *, operation_id, run_id="run-a", transport=TRANSPORT_REST,
                 args=None):
        return self.ledger.reserve(ledger_module.OperationRequest(
            project=self.project, run_id=run_id, operation_id=operation_id,
            provider="serpapi", capability="search", account_ref="serpapi",
            transport=transport, intent="one logical query",
            args=args if args is not None else rest_identity()))

    def test_same_query_over_a_second_transport_is_refused(self):
        self._reserve(operation_id="rest-1")
        with self.assertRaises(ledger_module.LedgerRefused) as caught:
            self._reserve(operation_id="mcp-1", transport=TRANSPORT_MCP,
                          args=mcp_identity(mcp_params()))
        self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)

    def test_same_query_across_runs_and_transports_is_refused(self):
        """``route_shape`` is project-scoped; a second run is still one purchase."""
        self._reserve(operation_id="rest-1", run_id="run-a")
        with self.assertRaises(ledger_module.LedgerRefused) as caught:
            self._reserve(operation_id="mcp-1", run_id="run-b",
                          transport=TRANSPORT_MCP,
                          args=mcp_identity(mcp_params()))
        self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)

    def test_a_semantically_different_query_is_allowed(self):
        """The anti-degradation control: same provider, different question."""
        self._reserve(operation_id="rest-1", run_id="run-a")
        self._reserve(operation_id="mcp-2", run_id="run-b",
                      transport=TRANSPORT_MCP,
                      args=mcp_identity(mcp_params(q="tiles birmingham")))
        self.assertEqual(2, len(self.ledger.operations()))

    def test_every_reviewed_axis_is_a_different_purchase(self):
        cases = (
            ("query", {"q": "tiles birmingham"}, {}),
            ("engine", {"engine": "google_scholar"}, {}),
            ("country", {"gl": "ie"}, {}),
            ("language", {"hl": "ga"}, {}),
            ("depth", {"num": 20}, {}),
            ("page", {"page": 2}, {}),
            ("account", {}, {"account": "team-a"}),
        )
        for label, mcp_change, kwargs in cases:
            with self.subTest(axis=label):
                with tempfile.TemporaryDirectory() as tmp:
                    ledger = ledger_module.for_root(Path(tmp))
                    project = ledger_module.project_for_root(Path(tmp))
                    for run in ("run-a", "run-b"):
                        ledger.set_cap("run", f"{project}/{run}",
                                       ceiling_calls=5, source="test-fixture")
                    ledger.reserve(ledger_module.OperationRequest(
                        project=project, run_id="run-a", operation_id="rest-1",
                        provider="serpapi", capability="search",
                        account_ref="serpapi", transport=TRANSPORT_REST,
                        intent="one logical query", args=rest_identity()))
                    ledger.reserve(ledger_module.OperationRequest(
                        project=project, run_id="run-b", operation_id="mcp-1",
                        provider="serpapi", capability="search",
                        account_ref="serpapi", transport=TRANSPORT_MCP,
                        intent="one logical query",
                        args=mcp_identity(mcp_params(**mcp_change), **kwargs)))
                    self.assertEqual(2, len(ledger.operations()))

    def test_an_unknown_charge_keeps_the_route_claimed(self):
        """UNKNOWN is an unresolved exposure, not a release."""
        self._reserve(operation_id="rest-1", run_id="run-a")
        self.ledger.mark_dispatch_intent("rest-1")
        self.ledger.mark_unknown("rest-1", reason="PROVIDER_TIMEOUT")
        with self.assertRaises(ledger_module.LedgerRefused) as caught:
            self._reserve(operation_id="mcp-1", run_id="run-b",
                          transport=TRANSPORT_MCP,
                          args=mcp_identity(mcp_params()))
        self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)


# ---------------------------------------------------------------------------
# 3. The same refusal holds across processes
# ---------------------------------------------------------------------------

def _race_worker(root, project, run_id, operation_id, transport, args,
                 barrier, queue):
    """One contender. Every worker is a separate process and opens its own DB."""
    try:
        ledger = ledger_module.for_root(root)
        barrier.wait(timeout=30)
        ledger.reserve(ledger_module.OperationRequest(
            project=project, run_id=run_id, operation_id=operation_id,
            provider="serpapi", capability="search", account_ref="serpapi",
            transport=transport, intent="one logical query", args=args))
        queue.put(("OK", ""))
    except ledger_module.LedgerRefused as exc:
        queue.put(("REFUSED", exc.code))
    except Exception as exc:  # pragma: no cover - diagnostic only
        queue.put(("ERROR", f"{type(exc).__name__}: {exc}"))


def _context():
    try:
        return mp.get_context("fork")
    except ValueError:  # pragma: no cover - non-Linux fallback
        return mp.get_context("spawn")


class ProcessRaceTests(unittest.TestCase):
    def _run_race(self, contenders, *, runs):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = ledger_module.for_root(root)
            project = ledger_module.project_for_root(root)
            for run in runs:
                ledger.set_cap("run", f"{project}/{run}", ceiling_calls=10,
                               source="test-fixture")
            ctx = _context()
            barrier = ctx.Barrier(len(contenders))
            queue = ctx.Queue()
            processes = [ctx.Process(target=_race_worker,
                                     args=(root, project, run, op, transport,
                                           args, barrier, queue))
                         for run, op, transport, args in contenders]
            for process in processes:
                process.start()
            results = [queue.get(timeout=90) for _ in processes]
            for process in processes:
                process.join(timeout=60)
                if process.is_alive():  # pragma: no cover - failure hygiene
                    process.terminate()
                    process.join(timeout=10)
                    self.fail("a race worker did not finish")
        return results

    def test_one_rest_process_and_one_mcp_process_yield_one_reservation(self):
        results = self._run_race([
            ("run-a", "rest-1", TRANSPORT_REST, rest_identity()),
            ("run-b", "mcp-1", TRANSPORT_MCP, mcp_identity(mcp_params())),
        ], runs=("run-a", "run-b"))
        self.assertEqual(1, sum(1 for status, _ in results if status == "OK"),
                         results)
        refused = [code for status, code in results if status == "REFUSED"]
        self.assertEqual(["DUPLICATE_OPERATION_SIGNATURE"], refused, results)
        self.assertFalse([r for r in results if r[0] == "ERROR"], results)

    def test_eight_processes_in_one_run_yield_one_reservation(self):
        contenders = []
        for index in range(8):
            transport = TRANSPORT_REST if index % 2 == 0 else TRANSPORT_MCP
            args = (rest_identity() if transport == TRANSPORT_REST
                    else mcp_identity(mcp_params()))
            contenders.append(("run-race", f"op-{index}", transport, args))
        results = self._run_race(contenders, runs=("run-race",))
        self.assertEqual(1, sum(1 for status, _ in results if status == "OK"),
                         results)
        refused = [code for status, code in results if status == "REFUSED"]
        self.assertEqual(["DUPLICATE_OPERATION_SIGNATURE"] * 7, refused, results)
        self.assertFalse([r for r in results if r[0] == "ERROR"], results)


# ---------------------------------------------------------------------------
# 4. The REST key stays on the wire and out of every chain and receipt
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, body: bytes):
        self.status = 200
        self._body = body

    def read(self, size: int) -> bytes:
        return self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class RestKeyContainmentTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"SERPAPI_API_KEY": CANARY})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _request(self, opener):
        return dsm._request("serpapi", "web", "coffee", "US", "en", 10, 1,
                            opener=opener)

    def test_the_key_is_on_the_wire_because_the_vendor_requires_it(self):
        """SerpApi's REST contract takes ``api_key`` as a query parameter.

        We cannot move it to a header without changing what the vendor accepts,
        so the fix is containment: it must never leave the request object.
        """
        captured = {}

        def opener(req, timeout=20):
            captured["url"] = req.full_url
            return _Response(json.dumps({"organic_results": []}).encode())

        payload = self._request(opener)
        self.assertIn(f"api_key={CANARY}", captured["url"])
        self.assertNotIn(CANARY, json.dumps(payload))
        self.assertEqual({"organic_results": []}, payload)

    def test_an_http_error_does_not_carry_the_url_into_the_chain(self):
        def opener(req, timeout=20):
            raise urllib.error.HTTPError(req.full_url, 500,
                                         "boom " + req.full_url, {}, None)

        with self.assertRaises(ValueError) as caught:
            self._request(opener)
        self.assertEqual("PROVIDER_HTTP_ERROR_500", str(caught.exception))
        self.assertFalse(_leaks(caught.exception))

    def test_a_transport_value_error_is_collapsed_and_redacted(self):
        def opener(req, timeout=20):
            raise ValueError("bad target " + req.full_url)

        with self.assertRaises(ValueError) as caught:
            self._request(opener)
        self.assertEqual("PROVIDER_TRANSPORT_ERROR", str(caught.exception))
        self.assertFalse(_leaks(caught.exception))

    def test_a_body_that_echoes_the_key_does_not_enter_the_chain(self):
        def opener(req, timeout=20):
            return _Response(("not json " + CANARY).encode())

        with self.assertRaises(ValueError) as caught:
            self._request(opener)
        self.assertEqual("PROVIDER_INVALID_JSON", str(caught.exception))
        self.assertFalse(_leaks(caught.exception))

    def test_a_redirect_is_refused_with_a_closed_code(self):
        def opener(req, timeout=20):
            raise ValueError("PROVIDER_REDIRECT_BLOCKED")

        with self.assertRaises(ValueError) as caught:
            self._request(opener)
        self.assertEqual("PROVIDER_REDIRECT_BLOCKED", str(caught.exception))
        self.assertFalse(_leaks(caught.exception))

    def test_no_log_record_contains_the_key(self):
        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            def failing(req, timeout=20):
                raise urllib.error.HTTPError(req.full_url, 502,
                                             "bad " + req.full_url, {}, None)
            with self.assertRaises(ValueError):
                self._request(failing)
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)
        self.assertFalse([r for r in records if CANARY in r.getMessage()])

    def test_the_public_boundary_does_not_attach_the_transport_exception(self):
        """``search`` must not wrap a URL-bearing exception as ``__context__``."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                    "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
                    "MARKETOPS_SEARCH_MAX_CALLS": "5",
                    "MARKETOPS_SERPAPI_REST_ENABLE": "1"}), \
                 patch.object(dsm, "_reserve"), \
                 patch.object(dsm, "data_root", return_value=Path(tmp)), \
                 patch.object(dsm, "append_event"), \
                 patch.object(dsm, "_request",
                              side_effect=RuntimeError("boom " + CANARY)):
                with self.assertRaises(ValueError) as caught:
                    dsm.search("serpapi", "coffee", "US", "en",
                               "run-1", "op-1")
            self.assertEqual("PROVIDER_OR_RECEIPT_ERROR", str(caught.exception))
            self.assertFalse(_leaks(caught.exception))

    def test_a_receipt_never_stores_the_key_or_the_request_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {
                    "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
                    "MARKETOPS_SEARCH_MAX_CALLS": "5",
                    "MARKETOPS_SERPAPI_REST_ENABLE": "1"}), \
                 patch.object(dsm, "_reserve"), \
                 patch.object(dsm, "data_root", return_value=root), \
                 patch.object(dsm, "append_event"), \
                 patch.object(dsm, "_request",
                              return_value={"organic_results": [
                                  {"title": "A cafe", "link": "https://example.org"}]}):
                result = dsm.search("serpapi", "coffee", "US", "en",
                                    "run-1", "op-1")
            receipts = list((root / "search-receipts" / "run-1").glob("*.json"))
            self.assertEqual(1, len(receipts))
            text = receipts[0].read_text(encoding="utf-8")
            self.assertNotIn(CANARY, text)
            self.assertNotIn("api_key", text)
            self.assertNotIn(CANARY, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
