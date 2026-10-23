"""The vendor-schema contract: two hash subjects, and no invented authority.

S0_07 compares a captured ``tools/list`` payload's sha256 with the Registry's
``vendor_schema_sha256``. That digest has a *different subject* from
``CapabilityEntry.schema_sha256``: the latter covers our reviewed host subset,
the former covers the vendor's complete catalog — every tool's name,
``inputSchema``, description and annotations. Treating one as the other is the
fabrication S0 exists to refuse, so the two live in different fields here.

What this file proves, offline:

* the shipped registry has **no** vendor digest and says ``NOT_CAPTURED`` for
  every provider — a count of 28 tool names does not change that;
* a names-only or host-subset payload cannot be pinned as a vendor catalog;
* a pin needs an endpoint, capture time, provenance, egress decision and audit
  refs, a trusted source, and the complete payload whose digest it declares;
* the comparison is over the whole canonical payload, so the same tool names
  with a changed argument schema, description or annotation are ``DRIFT``;
* a stale, future or untrusted capture is refused, and a drift invalidates the
  provider's capabilities before any adapter can be dispatched;
* a synthetic pair can match mechanically and is still never authoritative, so
  no offline test becomes a LIVE claim;
* the shared ``DEFAULT_REGISTRY`` carries no mutable observation state, so one
  caller's probe cannot contaminate another's.

No network, no credential, no vendor call. The only adapter in this file counts
calls and asserts it was never reached.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_capability_registry as registry_module
import research_contract as contract
import research_policy as policy_module

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "https://mcp.synthetic.invalid/mcp"


def tool(name: str, *, kind: str = "string", description: str = "",
         annotations: dict | None = None) -> dict:
    entry = {
        "name": name,
        "description": description or f"synthetic catalog entry for {name}",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": kind}},
            "required": ["query"],
        },
    }
    if annotations is not None:
        entry["annotations"] = annotations
    return entry


def payload(*tools: dict) -> dict:
    return {"tools": [dict(item) for item in tools]}


def tinyfish_payload() -> dict:
    """Two complete tools whose names are the registry's TinyFish upstream names."""
    return payload(
        tool("search", annotations={"readOnlyHint": True}),
        tool("fetch_content", kind="array"),
    )


def serpapi_payload() -> dict:
    return payload(tool("search"))


def pin(reg, provider: str = "tinyfish", body: dict | None = None, **overrides):
    """Pin a synthetic capture: every provenance field is present, and it is not live."""
    body = tinyfish_payload() if body is None else body
    kwargs = dict(
        endpoint=ENDPOINT, captured_at=NOW - timedelta(minutes=5),
        provenance=("synthetic-fixture",), egress_decision_ref="egress-synthetic-1",
        audit_ref="audit-synthetic-1", capture_mode=registry_module.CaptureMode.SYNTHETIC)
    kwargs.update(overrides)
    return reg.with_vendor_capture(provider, body, **kwargs)


def policy_request(capability_id: str, params: dict) -> contract.CapabilityRequest:
    return contract.CapabilityRequest(
        capability_id=capability_id, params=params, run_id="run-01",
        operation_id="op-01", account_ref="acct", intent_fingerprint="intent-1")


class CountingAdapter:
    """An adapter that must never be reached by a refused call."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise AssertionError("an adapter was dispatched by a refused request")


class MetadataTests(unittest.TestCase):
    def test_the_default_registry_has_no_vendor_digest(self):
        reg = registry_module.DEFAULT_REGISTRY
        self.assertIs(reg.pinned_hash_subject,
                      registry_module.HashSubject.HOST_CAPABILITY_SUBSET)
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertEqual(
            set(reg.vendor_schema_statuses()),
            {"tinyfish", "serpapi", "serper", "crawl4ai", "typesafe_jev", "deepseek"})
        for provider in reg.vendor_schema_statuses():
            with self.subTest(provider=provider):
                self.assertIs(reg.authoritative_status(provider),
                              registry_module.VendorSchemaStatus.NOT_CAPTURED)
                self.assertIsNone(reg.authoritative_vendor_schema_sha256(provider))
                self.assertIsNone(reg.vendor_schema_sha256_for(provider))
                entry = reg.vendor_schema_pin(provider)
                self.assertIs(entry.subject,
                              registry_module.HashSubject.HOST_CAPABILITY_SUBSET)
                self.assertIsNone(entry.vendor_schema_sha256)

    def test_the_legacy_host_subset_spelling_reads_but_never_authorizes(self):
        self.assertIs(
            registry_module.HashSubject.coerce("HOST_SUBSET_NOT_VENDOR_TOOLS_LIST"),
            registry_module.HashSubject.HOST_CAPABILITY_SUBSET)
        self.assertIs(
            registry_module.HashSubject.coerce("FULL_VENDOR_TOOLS_LIST"),
            registry_module.HashSubject.FULL_VENDOR_TOOLS_LIST)
        with self.assertRaises(registry_module.RegistryError):
            registry_module.HashSubject.coerce("VENDOR_GUESS")

    def test_the_reviewed_table_is_28_with_no_live_claim(self):
        # 25 before the documented TinyFish account-read review, plus the three
        # reviewed reads (get_monitor / list_monitors / list_browser_sessions), plus
        # DeepSeek Search and Fetch.
        reg = registry_module.DEFAULT_REGISTRY
        self.assertEqual(len(reg), 30)
        statuses = [(entry.readiness, entry.availability.value) for entry in reg]
        self.assertEqual(statuses.count(("OFFLINE_TESTED", "ENABLED")), 25)
        self.assertEqual(statuses.count(("CODED", "ENABLED")), 1)
        self.assertEqual(statuses.count(("DEFERRED", "DISABLED")), 4)
        self.assertNotIn("READY", " ".join(entry.readiness for entry in reg))

    def test_a_host_subset_pin_cannot_carry_a_vendor_digest(self):
        with self.assertRaises(registry_module.RegistryError):
            registry_module.VendorSchemaPin(provider="tinyfish",
                                            vendor_schema_sha256="0" * 64)

    def test_a_full_pin_requires_every_provenance_field(self):
        valid = registry_module.VendorSchemaPin.from_payload(
            "tinyfish", tinyfish_payload(), endpoint=ENDPOINT,
            captured_at=NOW - timedelta(minutes=5), provenance=("synthetic-fixture",),
            egress_decision_ref="egress-1", audit_ref="audit-1",
            capture_mode=registry_module.CaptureMode.SYNTHETIC)
        broken = {
            "endpoint": None, "captured_at": None, "provenance": (),
            "egress_decision_ref": "", "audit_ref": "", "tool_count": 0,
            "source": "adapter_guess", "snapshot_serialized": None,
            "vendor_schema_sha256": None,
        }
        for name, value in broken.items():
            with self.subTest(missing=name):
                with self.assertRaises(registry_module.RegistryError):
                    replace(valid, **{name: value})

    def test_a_names_only_payload_cannot_be_pinned(self):
        names_only = {"tools": [{"name": f"tool_{index}"} for index in range(28)]}
        with self.assertRaises(registry_module.VendorSchemaRefused) as ctx:
            registry_module.DEFAULT_REGISTRY.with_vendor_capture(
                "tinyfish", names_only, endpoint=ENDPOINT,
                captured_at=NOW, provenance=("synthetic",),
                egress_decision_ref="egress-1", audit_ref="audit-1")
        self.assertEqual(ctx.exception.code, "VENDOR_SCHEMA_NOT_FULL_TOOLS_LIST")
        self.assertIsNone(registry_module.DEFAULT_REGISTRY.vendor_schema_sha256)
        self.assertIs(registry_module.DEFAULT_REGISTRY.authoritative_status("tinyfish"),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)

    def test_28_matching_tool_names_still_invent_no_digest(self):
        reviewed_names = [entry.upstream_tool for entry in registry_module.DEFAULT_REGISTRY
                          if entry.provider == "tinyfish"]
        names_only = {"tools": [{"name": name} for name in reviewed_names]}
        with self.assertRaises(registry_module.VendorSchemaRefused):
            registry_module.DEFAULT_REGISTRY.with_vendor_capture(
                "tinyfish", names_only, endpoint=ENDPOINT, captured_at=NOW,
                provenance=("synthetic",), egress_decision_ref="e", audit_ref="a")
        reg = registry_module.DEFAULT_REGISTRY
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertIsNone(reg.vendor_schema_sha256_for("tinyfish"))
        self.assertIs(reg.authoritative_status("tinyfish"),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)

    def test_a_payload_without_input_schema_is_refused(self):
        body = payload({"name": "search", "description": "no schema"})
        with self.assertRaises(registry_module.VendorSchemaRefused):
            pin(registry_module.DEFAULT_REGISTRY, body=body)

    def test_a_payload_without_a_descriptor_is_refused(self):
        item = tool("search")
        del item["description"]
        with self.assertRaises(registry_module.VendorSchemaRefused):
            pin(registry_module.DEFAULT_REGISTRY, body=payload(item))

    def test_annotations_must_be_an_object_when_present(self):
        body = payload(tool("search", annotations=("readOnly",)))
        with self.assertRaises(registry_module.VendorSchemaRefused):
            pin(registry_module.DEFAULT_REGISTRY, body=body)


class SyntheticPairTests(unittest.TestCase):
    def test_a_synthetic_valid_pair_matches_but_is_not_authoritative(self):
        body = tinyfish_payload()
        reg = pin(registry_module.DEFAULT_REGISTRY, body=body)
        check = reg.check_vendor_schema("tinyfish", body, as_of=NOW)
        self.assertIs(check.verdict, registry_module.VendorSchemaVerdict.MATCH)
        self.assertFalse(check.authoritative)
        self.assertEqual(check.expected_sha256, check.observed_sha256)
        self.assertEqual(check.expected_tool_count, 2)
        self.assertIs(reg.authoritative_status("tinyfish", as_of=NOW),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)
        # The S0-facing accessor stays None; only the mechanical one answers.
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertIsNone(reg.authoritative_vendor_schema_sha256("tinyfish", as_of=NOW))
        self.assertEqual(reg.vendor_schema_sha256_for("tinyfish"),
                         registry_module.tools_list_digest(body))
        with self.assertRaises(registry_module.VendorSchemaRefused) as ctx:
            reg.require_authoritative_vendor_schema("tinyfish", as_of=NOW)
        self.assertEqual(ctx.exception.code, "VENDOR_SCHEMA_NOT_CAPTURED")

    def test_a_synthetic_pin_changes_no_capability_availability(self):
        before = {entry.capability_id: entry.availability
                  for entry in registry_module.DEFAULT_REGISTRY}
        reg = pin(registry_module.DEFAULT_REGISTRY)
        after = {entry.capability_id: reg.get(entry.capability_id).availability
                 for entry in registry_module.DEFAULT_REGISTRY}
        self.assertEqual(before, after)
        self.assertEqual(reg.observed_verdicts(), {})
        self.assertNotIn("LIVE", " ".join(
            reg.authoritative_status(provider).value
            for provider in reg.vendor_schema_statuses()))

    def test_the_digest_is_the_s0_canonical_form(self):
        body = tinyfish_payload()
        expected = json.dumps(body, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)
        import hashlib
        self.assertEqual(registry_module.tools_list_digest(body),
                         hashlib.sha256(expected.encode("utf-8")).hexdigest())
        # Key order in the mapping is not content; the canonical form sorts it.
        reordered = {"tools": [dict(reversed(list(item.items()))) for item in body["tools"]]}
        self.assertEqual(registry_module.tools_list_digest(body),
                         registry_module.tools_list_digest(reordered))


class StrictDriftTests(unittest.TestCase):
    def setUp(self):
        self.body = tinyfish_payload()
        self.registry = pin(registry_module.DEFAULT_REGISTRY, body=self.body)

    def check(self, observed):
        return self.registry.check_vendor_schema("tinyfish", observed, as_of=NOW)

    def test_same_names_different_argument_schema_is_drift(self):
        observed = tinyfish_payload()
        observed["tools"][0]["inputSchema"]["properties"]["query"]["type"] = "integer"
        result = self.check(observed)
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        self.assertFalse(result.authoritative)
        self.assertNotEqual(result.expected_sha256, result.observed_sha256)
        self.assertEqual(result.changed_tools, ("search",))
        self.assertEqual(result.added_tools, ())
        self.assertEqual(result.removed_tools, ())

    def test_same_names_different_description_is_drift(self):
        observed = tinyfish_payload()
        observed["tools"][1]["description"] = "a different upstream sentence"
        result = self.check(observed)
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        self.assertEqual(result.changed_tools, ("fetch_content",))

    def test_same_names_different_annotations_is_drift(self):
        observed = tinyfish_payload()
        observed["tools"][0]["annotations"]["readOnlyHint"] = False
        result = self.check(observed)
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        self.assertEqual(result.changed_tools, ("search",))

    def test_an_added_tool_is_drift(self):
        observed = tinyfish_payload()
        observed["tools"].append(tool("brand_new"))
        result = self.check(observed)
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        self.assertEqual(result.added_tools, ("brand_new",))

    def test_a_removed_tool_is_drift(self):
        observed = payload(self.body["tools"][0])
        result = self.check(observed)
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        self.assertEqual(result.removed_tools, ("fetch_content",))

    def test_names_only_observation_is_never_a_match(self):
        observed = {"tools": [{"name": "search"}, {"name": "fetch_content"}]}
        result = self.check(observed)
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        self.assertNotEqual(result.expected_sha256, result.observed_sha256)
        self.assertFalse(result.authoritative)

    def test_an_observation_that_is_not_a_tools_list_is_unverified(self):
        result = self.check({"tools": "search,fetch_content"})
        self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.UNVERIFIED)
        self.assertFalse(result.authoritative)
        self.assertIn("TOOLS_LIST_MISSING_TOOLS", result.problems)


class StaleAndProvenanceTests(unittest.TestCase):
    def _live_shaped_pin(self, captured_at: datetime):
        """A fixture that exercises the freshness predicate; not a capture.

        The assertions around it prove the predicate refuses it when stale. The
        shipped registry never contains a pin like this — see MetadataTests.
        """
        return registry_module.VendorSchemaPin.from_payload(
            "tinyfish", tinyfish_payload(), endpoint=ENDPOINT, captured_at=captured_at,
            provenance=("fixture-only",), egress_decision_ref="egress-1",
            audit_ref="audit-1", capture_mode=registry_module.CaptureMode.LIVE_CAPTURE)

    def test_a_stale_capture_is_never_authoritative(self):
        old = self._live_shaped_pin(NOW - timedelta(days=8))
        self.assertIs(old.status(NOW), registry_module.VendorSchemaStatus.STALE)
        self.assertFalse(old.is_authoritative(NOW))

    def test_a_capture_from_the_future_is_refused(self):
        future = self._live_shaped_pin(NOW + timedelta(hours=1))
        self.assertIs(future.status(NOW), registry_module.VendorSchemaStatus.STALE)
        self.assertFalse(future.is_authoritative(NOW))

    def test_the_live_predicate_accepts_only_a_fresh_trusted_capture(self):
        fresh = self._live_shaped_pin(NOW - timedelta(hours=1))
        self.assertIs(fresh.status(NOW), registry_module.VendorSchemaStatus.VERIFIED)
        self.assertTrue(fresh.is_authoritative(NOW))
        # ... and the shipped registry is still not one of those.
        self.assertIs(registry_module.DEFAULT_REGISTRY.authoritative_status("tinyfish"),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)

    def test_an_untrusted_source_cannot_even_be_constructed(self):
        with self.assertRaises(registry_module.RegistryError):
            registry_module.VendorSchemaPin.from_payload(
                "tinyfish", tinyfish_payload(), endpoint=ENDPOINT, captured_at=NOW,
                provenance=("fixture-only",), egress_decision_ref="e", audit_ref="a",
                source="adapter_guess", capture_mode=registry_module.CaptureMode.SYNTHETIC)

    def test_the_observation_provenance_must_match_the_pin(self):
        reg = pin(registry_module.DEFAULT_REGISTRY)
        for kwargs in (
            {"endpoint": "https://other.invalid/mcp"},
            {"source": "probe_schema@unofficial"},
            {"egress_decision_ref": "egress-other"},
            {"audit_ref": "audit-other"},
        ):
            with self.subTest(field=next(iter(kwargs))):
                result = reg.check_vendor_schema(
                    "tinyfish", tinyfish_payload(), as_of=NOW, **kwargs)
                self.assertIs(result.verdict, registry_module.VendorSchemaVerdict.UNTRUSTED)
                self.assertFalse(result.authoritative)


class InvalidationTests(unittest.TestCase):
    def _drifted(self, provider="serpapi", body=None):
        body = serpapi_payload() if body is None else body
        reg = pin(registry_module.DEFAULT_REGISTRY, provider=provider, body=body)
        observed = json.loads(json.dumps(body))
        observed["tools"][0]["inputSchema"]["properties"]["query"]["type"] = "integer"
        check = reg.check_vendor_schema(provider, observed, as_of=NOW)
        self.assertIs(check.verdict, registry_module.VendorSchemaVerdict.DRIFT)
        return reg, check

    def test_a_drift_invalidates_the_provider_before_any_adapter(self):
        reg, check = self._drifted()
        invalid = reg.with_vendor_check(check)
        self.assertIs(invalid.authoritative_status("serpapi"),
                      registry_module.VendorSchemaStatus.INVALIDATED)
        self.assertIs(invalid.get("serpapi.search").availability,
                      contract.Availability.SCHEMA_DRIFT)
        adapter = CountingAdapter()
        policy = policy_module.Policy(
            registry=invalid, network_enabled=True, allowed_domains=("example.com",),
            clock=lambda: NOW)
        decision = policy.evaluate(policy_request("serpapi.search", {"query": "venue"}))
        self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "CAPABILITY_SCHEMA_DRIFT")
        if decision.verdict is contract.PolicyVerdict.ALLOW:
            adapter.execute(None)
        self.assertEqual(adapter.calls, 0)

    def test_invalidation_keeps_t0_t1_read_only_available(self):
        reg, check = self._drifted(provider="crawl4ai", body=payload(tool("fetch_page")))
        invalid = reg.with_vendor_check(check)
        self.assertIs(invalid.get("crawl4ai.fetch_page").availability,
                      contract.Availability.SCHEMA_DRIFT)
        policy = policy_module.Policy(
            registry=invalid, network_enabled=False, allowed_domains=("example.com",),
            clock=lambda: NOW)
        decision = policy.evaluate(policy_request(
            "crawl4ai.fetch_page", {"url": "https://example.com/page"}))
        self.assertIs(decision.verdict, contract.PolicyVerdict.ALLOW)
        self.assertIs(decision.effective_tier, contract.Tier.T1)

    def test_a_non_authoritative_match_does_not_clear_an_invalidation(self):
        reg, check = self._drifted()
        invalid = reg.with_vendor_check(check)
        # Check against the pre-invalidation pin: the invalidation is about what
        # was observed, and a later synthetic match must not silently undo it.
        match = reg.check_vendor_schema("serpapi", serpapi_payload(), as_of=NOW)
        self.assertIs(match.verdict, registry_module.VendorSchemaVerdict.MATCH)
        self.assertFalse(match.authoritative)
        still = invalid.with_vendor_check(match)
        self.assertIs(still.authoritative_status("serpapi"),
                      registry_module.VendorSchemaStatus.INVALIDATED)

    def test_a_derived_check_does_not_invalidate_the_shared_default(self):
        _, check = self._drifted()
        registry_module.DEFAULT_REGISTRY.with_vendor_check(check)
        self.assertIs(registry_module.DEFAULT_REGISTRY.get("serpapi.search").availability,
                      contract.Availability.ENABLED)
        self.assertIs(registry_module.DEFAULT_REGISTRY.authoritative_status("serpapi"),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)


class SingletonIsolationTests(unittest.TestCase):
    def test_verify_schema_does_not_mutate_the_shared_default(self):
        default = registry_module.DEFAULT_REGISTRY
        before = dict(default.observed_verdicts())
        verdict = default.verify_schema("serper.search", {"tools": []})
        self.assertIs(verdict, contract.SchemaVerdict.ABSENT)
        self.assertEqual(default.observed_verdicts(), before)
        self.assertIs(default.get("serper.search").availability,
                      contract.Availability.ENABLED)

    def test_two_derived_registries_do_not_share_observations(self):
        default = registry_module.DEFAULT_REGISTRY
        first = default.with_schema_verdicts(
            {"serper.search": contract.SchemaVerdict.DRIFT})
        second = default.with_schema_verdicts(
            {"serper.search": contract.SchemaVerdict.UNVERIFIED})
        self.assertIs(first.get("serper.search").availability,
                      contract.Availability.SCHEMA_DRIFT)
        self.assertIs(second.get("serper.search").availability,
                      contract.Availability.ENABLED)
        self.assertEqual(first.observed_verdicts(),
                         {"serper.search": contract.SchemaVerdict.DRIFT})
        self.assertEqual(second.observed_verdicts(),
                         {"serper.search": contract.SchemaVerdict.UNVERIFIED})
        self.assertEqual(default.observed_verdicts(), {})

    def test_concurrent_readers_do_not_contaminate_each_other(self):
        default = registry_module.DEFAULT_REGISTRY
        failures: list[str] = []

        def worker(index: int) -> None:
            try:
                for step in range(50):
                    verdict = (contract.SchemaVerdict.DRIFT if (index + step) % 2
                               else contract.SchemaVerdict.ABSENT)
                    derived = default.with_schema_verdicts({"serper.search": verdict})
                    if derived.observed_verdicts() != {"serper.search": verdict}:
                        failures.append(f"worker {index}: own verdict leaked")
                    if derived.get("serper.search").availability is not \
                            contract.Availability.SCHEMA_DRIFT:
                        failures.append(f"worker {index}: own drift not applied")
                    if default.observed_verdicts():
                        failures.append(f"worker {index}: default was contaminated")
                    if default.get("serper.search").availability is not \
                            contract.Availability.ENABLED:
                        failures.append(f"worker {index}: default was downgraded")
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(f"worker {index}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(default.observed_verdicts(), {})


class S0SeamTests(unittest.TestCase):
    """The handoff shape: what the Registry gives S0, and what it refuses to give."""

    @staticmethod
    def _gate():
        try:
            import research_s0_preflight_gate as gate
        except ModuleNotFoundError:
            return None
        return gate

    def test_no_synthetic_pin_is_ever_offered_to_s0(self):
        reg = pin(registry_module.DEFAULT_REGISTRY)
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertIsNone(reg.authoritative_vendor_schema_sha256("tinyfish", as_of=NOW))
        with self.assertRaises(registry_module.VendorSchemaRefused):
            reg.require_authoritative_vendor_schema("tinyfish", as_of=NOW)

    def test_the_canonical_form_agrees_with_the_s0_gate(self):
        gate = self._gate()
        if gate is None:
            self.skipTest("BLOCKED: research_s0_preflight_gate is not on this branch")
        body = tinyfish_payload()
        self.assertEqual(registry_module.tools_list_digest(body),
                         gate.snapshot_digest(body))

    def test_s0_refuses_a_full_payload_when_the_registry_has_no_digest(self):
        gate = self._gate()
        if gate is None:
            self.skipTest("BLOCKED: research_s0_preflight_gate is not on this branch")
        body = tinyfish_payload()
        with self.assertRaises(gate.BootstrapRefused) as ctx:
            gate.bootstrap_probe_schema(
                probe=lambda: body, provider="tinyfish", endpoint=ENDPOINT,
                allowlist=("mcp.synthetic.invalid",),
                egress_decision=lambda endpoint: "egress-1",
                audit=lambda record: "audit-1",
                registry_schema_sha256=registry_module.DEFAULT_REGISTRY.vendor_schema_sha256,
                captured_at=NOW.isoformat())
        self.assertEqual(ctx.exception.code, "BOOTSTRAP_SCHEMA_MISMATCH")

    def test_a_bootstrap_snapshot_can_be_pinned_without_becoming_live(self):
        gate = self._gate()
        if gate is None:
            self.skipTest("BLOCKED: research_s0_preflight_gate is not on this branch")
        body = tinyfish_payload()
        digest = registry_module.tools_list_digest(body)
        snapshot = gate.bootstrap_probe_schema(
            probe=lambda: body, provider="tinyfish", endpoint=ENDPOINT,
            allowlist=("mcp.synthetic.invalid",),
            egress_decision=lambda endpoint: "egress-1",
            audit=lambda record: "audit-1", registry_schema_sha256=digest,
            captured_at=NOW.isoformat())
        reg = registry_module.DEFAULT_REGISTRY.with_vendor_snapshot(snapshot["tinyfish"])
        self.assertEqual(reg.vendor_schema_sha256_for("tinyfish"), digest)
        # The bootstrap fixture is synthetic by construction; the pin defaults to
        # SYNTHETIC and therefore never becomes the authoritative digest.
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertIs(reg.authoritative_status("tinyfish", as_of=NOW),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)
        check = reg.check_vendor_schema("tinyfish", body, as_of=NOW)
        self.assertIs(check.verdict, registry_module.VendorSchemaVerdict.MATCH)
        self.assertFalse(check.authoritative)


if __name__ == "__main__":
    unittest.main()
