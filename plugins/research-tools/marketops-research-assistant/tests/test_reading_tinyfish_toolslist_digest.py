"""A full `tools/list` descriptor digest, and an honest capture label.

The defect this pins: `probe_schema` used to hash **tool names only**, so a
server could change every `inputSchema`, keep the names, and produce the same
hash. A digest that cannot see the schema is not a schema digest.

What is required instead:

* the **full** canonical descriptor per tool -- name, description, inputSchema,
  annotations when advertised -- under a stable SHA-256 over canonicalised
  allowed JSON;
* the source endpoint and transport, and the raw tool count;
* a **capture label** that separates a locally pinned fake from a trusted,
  authorized real vendor capture. There is no real capture, so the vendor hash
  stays `None` and the promotion state stays `NOT_CAPTURED`;
* name-only input stays supported, but is labelled reduced rather than passed off
  as a schema digest.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_adapter_tinyfish as tinyfish

PINNED = sorted(tinyfish.capability_catalog()["tools"])


def descriptor(name, *, schema=None, description="Pinned TinyFish tool.",
               annotations=None):
    record = {"name": name,
              "description": description,
              "input_schema": schema or {"type": "object", "properties": {}}}
    if annotations is not None:
        record["annotations"] = annotations
    return record


def catalog_descriptors(**overrides):
    out = []
    for name in PINNED:
        override = overrides.get(name, {})
        out.append(descriptor(name, **override))
    return out


def canonical(record):
    allowed = {"name", "description", "input_schema", "annotations"}
    return {key: record[key] for key in sorted(allowed & set(record))}


def expected_digest(records):
    payload = [canonical(r) for r in sorted(records, key=lambda r: r["name"])]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class FullDescriptorDigest(unittest.TestCase):
    def discover(self, descriptors):
        return tinyfish.discover_schema(descriptors=descriptors)

    def test_the_result_carries_the_full_descriptor_not_just_names(self):
        result = self.discover(catalog_descriptors())
        self.assertEqual(28, result["raw_tool_count"])
        self.assertEqual(PINNED, [t["name"] for t in result["tools"]])
        for tool in result["tools"]:
            self.assertEqual({"name", "description", "input_schema"}, set(tool))
        self.assertEqual("VENDOR_TOOLS_LIST_DESCRIPTORS", result["digest_subject"])

    def test_the_digest_is_recomputable_from_the_returned_descriptors(self):
        result = self.discover(catalog_descriptors())
        self.assertEqual(expected_digest(result["tools"]), result["digest_sha256"])
        self.assertRegex(result["digest_sha256"], r"^[0-9a-f]{64}$")

    def test_mutating_input_schema_under_unchanged_names_changes_the_digest(self):
        base = self.discover(catalog_descriptors())
        mutated = self.discover(catalog_descriptors(search={
            "schema": {"type": "object", "properties": {
                "query": {"type": "string"}, "evil": {"type": "string"}}}}))
        self.assertEqual([t["name"] for t in base["tools"]],
                         [t["name"] for t in mutated["tools"]])
        self.assertNotEqual(base["digest_sha256"], mutated["digest_sha256"])

    def test_mutating_only_a_description_also_changes_the_digest(self):
        base = self.discover(catalog_descriptors())
        mutated = self.discover(catalog_descriptors(fetch_content={
            "description": "now with a different sentence"}))
        self.assertNotEqual(base["digest_sha256"], mutated["digest_sha256"])

    def test_annotations_are_included_when_advertised(self):
        with_notes = self.discover(catalog_descriptors(search={
            "annotations": {"readOnlyHint": True}}))
        without = self.discover(catalog_descriptors())
        self.assertNotEqual(with_notes["digest_sha256"], without["digest_sha256"])
        self.assertIn("annotations", with_notes["tools"][PINNED.index("search")])

    def test_unknown_fields_are_not_carried_into_the_digest(self):
        noisy = [dict(d, surprise="ignored") for d in catalog_descriptors()]
        self.assertEqual(self.discover(catalog_descriptors())["digest_sha256"],
                         self.discover(noisy)["digest_sha256"])


class CaptureLabelIsHonest(unittest.TestCase):
    def test_a_local_synthetic_catalog_is_not_promoted(self):
        result = tinyfish.discover_schema(descriptors=catalog_descriptors())
        self.assertEqual("OFFLINE_SYNTHETIC", result["capture"]["mode"])
        self.assertFalse(result["capture"]["trusted_as_vendor"])
        self.assertFalse(result["capture"]["live_handshake_performed"])
        self.assertIsNone(result["vendor_schema_sha256"])
        self.assertEqual("NOT_CAPTURED", result["promotion"])

    def test_the_digest_exists_without_being_a_vendor_hash(self):
        result = tinyfish.discover_schema(descriptors=catalog_descriptors())
        self.assertIsNotNone(result["digest_sha256"])
        self.assertNotEqual(result["digest_sha256"], result["vendor_schema_sha256"])

    def test_name_only_input_is_labelled_reduced_not_a_schema_digest(self):
        reduced = tinyfish.discover_schema(names=PINNED)
        self.assertEqual("TOOL_NAMES_ONLY", reduced["digest_subject"])
        self.assertTrue(reduced["reduced"])
        self.assertEqual("OFFLINE_SYNTHETIC", reduced["capture"]["mode"])
        self.assertIsNone(reduced["vendor_schema_sha256"])
        full = tinyfish.discover_schema(descriptors=catalog_descriptors())
        self.assertNotEqual(full["digest_sha256"], reduced["digest_sha256"])

    def test_the_result_records_endpoint_transport_and_version(self):
        result = tinyfish.discover_schema(descriptors=catalog_descriptors())
        self.assertEqual(tinyfish.ENDPOINT, result["endpoint"])
        self.assertEqual(tinyfish.TRANSPORT, result["transport"])
        self.assertEqual("marketops.tinyfish-tools-list.v1", result["schema_version"])


class DriftReasons(unittest.TestCase):
    def test_an_unreviewed_tool_is_reported_as_added_and_stays_uncallable(self):
        result = tinyfish.discover_schema(
            descriptors=catalog_descriptors() + [descriptor("wait_for_run")])
        self.assertIn("wait_for_run", result["drift"]["tool_added"])
        self.assertNotIn("tinyfish.wait_for_run", tinyfish.capabilities())
        with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG"):
            tinyfish.validate("wait_for_run", {"run_id": "run-1"})

    def test_get_run_is_the_official_run_status_tool(self):
        result = tinyfish.discover_schema(descriptors=catalog_descriptors())
        self.assertIn("get_run", [t["name"] for t in result["tools"]])
        validated = tinyfish.validate("get_run", {"id": "run-abc_1"})
        self.assertEqual({"id": "run-abc_1"}, validated)

    def test_a_missing_tool_is_reported_as_removed(self):
        trimmed = [d for d in catalog_descriptors() if d["name"] != "create_monitor"]
        result = tinyfish.discover_schema(descriptors=trimmed)
        self.assertIn("create_monitor", result["drift"]["tool_removed"])

    def test_a_schema_change_is_reported_against_a_supplied_baseline(self):
        base = tinyfish.discover_schema(descriptors=catalog_descriptors())
        mutated = tinyfish.discover_schema(
            descriptors=catalog_descriptors(search={"schema": {
                "type": "object", "properties": {"q": {"type": "string"}}}}),
            baseline=base)
        self.assertIn("search", mutated["drift"]["schema_changed"])
        self.assertNotIn("fetch_content", mutated["drift"]["schema_changed"])
        self.assertEqual("BASELINE_TOOL_DIGESTS", mutated["drift"]["basis"])

    def test_without_a_baseline_no_schema_change_is_invented(self):
        result = tinyfish.discover_schema(descriptors=catalog_descriptors())
        self.assertEqual([], result["drift"]["schema_changed"])
        self.assertEqual("NO_BASELINE", result["drift"]["basis"])


class ArgumentContractStillGoverns(unittest.TestCase):
    def test_the_vendor_agent_descriptor_does_not_create_agent_config(self):
        # A vendor descriptor is data. Even one that advertised the parameter
        # would not widen our argument contract.
        result = tinyfish.discover_schema(descriptors=catalog_descriptors(
            run_web_automation={"schema": {"type": "object", "properties": {
                "agent_config": {"type": "object"}}}}))
        self.assertIn("run_web_automation", [t["name"] for t in result["tools"]])
        with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_UNPUBLISHED_PARAMETER"):
            tinyfish.validate("run_web_automation", {
                "url": "https://public.example.org/x",
                "goal": "Read the publicly listed venue names",
                "agent_config": {"max_steps": 4}})

    def test_a_caller_supplied_session_id_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_SESSION_ID_MUST_BE_GENERATED"):
            tinyfish.validate("run_web_automation", {
                "url": "https://public.example.org/x",
                "goal": "Read the publicly listed venue names",
                "session_id": "8f1c2d34-0000-4000-8000-000000000000"})


if __name__ == "__main__":
    unittest.main()
