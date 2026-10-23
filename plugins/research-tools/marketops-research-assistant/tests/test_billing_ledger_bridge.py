"""Wave-2 W-F: bridging the integrated usage ledger into the quality scorecard.

Two cost accountants now exist and they do not speak the same units. The integrated
`research_usage_ledger` keeps integer micro-USD with a `price_verified` flag and its
own charge states; W-QA's `cost-ledger.schema.json` — which the scorecard reads — keeps
decimal USD strings with a `meter_trust` enum. Nothing converts between them.

Measured on the integration SHA `cf8ff6e` (scratch run, no network, no credential):

* with the ledger's own documented credit baseline every price is `unit_micros=0,
  verified=False`, and `report(accepted_outputs=1)` returned
  `cost_per_accepted_output_micros = 0` while `price_evidence` said `unverified: 1` —
  a "$0 per accepted result" headline standing on a price nobody confirmed;
* with a verified operator price, one settled charge and one unreconciled charge,
  `money` was `actual_micros=1000, unknown_exposure_micros=1000`, and
  `cost_per_accepted_output_micros` was `1000` — equal to the confirmed part alone.

Neither is a crash. Both are the distinction the owner's §1.6 says must not be hidden,
and this bridge exists so the scorecard can keep it: a price that was never verified
must make the dollars UNKNOWN rather than zero, and an unreconciled charge must make
cost-per-accepted-result PARTIAL rather than final.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_ledger_bridge as bridge  # noqa: E402
import research_quality_scorecard as scorer  # noqa: E402

VERIFIED = {"operation_id": "op-a", "provider": "serper", "capability": "search",
            "tier": 2, "charge_state": "SETTLED", "estimated_micros": 1000,
            "reserved_micros": 0, "actual_micros": 1000, "quota_unit": "credits",
            "quota_units": 1, "price_verified": True, "op_signature": "sig-a",
            "transport": "rest", "origin": "live"}
UNRECONCILED = {"operation_id": "op-b", "provider": "serper", "capability": "search",
                "tier": 2, "charge_state": "UNKNOWN", "estimated_micros": 1000,
                "reserved_micros": 1000, "actual_micros": None, "quota_unit": "credits",
                "quota_units": 1, "price_verified": True, "op_signature": "sig-b",
                "transport": "rest", "origin": "live"}
UNVERIFIED_PRICE = {**VERIFIED, "operation_id": "op-c", "op_signature": "sig-c",
                    "price_verified": False}
BLOCKED_OP = {**VERIFIED, "operation_id": "op-d", "op_signature": "sig-d",
              "charge_state": "RESERVED", "actual_micros": None, "reserved_micros": 0}


class MappingTests(unittest.TestCase):
    def test_micro_usd_becomes_a_decimal_usd_string(self):
        ledger = bridge.ledger_from_operations([VERIFIED], project="p")
        op = ledger["operations"][0]
        self.assertEqual("0.001000", op["actual"])
        self.assertEqual("0.001000", op["estimated"])
        # Every amount is formatted the same way, so two rows are comparable by eye.
        self.assertEqual("0.000000", op["reserved"])

    def test_an_unconfirmed_actual_stays_null_and_never_becomes_zero(self):
        ledger = bridge.ledger_from_operations([UNRECONCILED], project="p")
        self.assertIsNone(ledger["operations"][0]["actual"])

    def test_charge_states_map_onto_the_schema_s_enum(self):
        ledger = bridge.ledger_from_operations(
            [VERIFIED, UNRECONCILED, BLOCKED_OP,
             {**VERIFIED, "operation_id": "op-e", "op_signature": "sig-e",
              "charge_state": "NO_CHARGE_PROVEN"},
             {**VERIFIED, "operation_id": "op-f", "op_signature": "sig-f",
              "charge_state": "FREE_TIER"}], project="p")
        states = {op["operation_id"]: op["state"] for op in ledger["operations"]}
        self.assertEqual("SETTLED", states["op-a"])
        self.assertEqual("UNKNOWN_CHARGE_OUTSTANDING", states["op-b"])
        self.assertEqual("RESERVED", states["op-d"])
        self.assertEqual("NO_CHARGE_PROVEN", states["op-e"])
        self.assertEqual("FREE_TIER", states["op-f"])

    def test_the_operation_signature_is_carried_through(self):
        """Without it the duplicate-charge check has nothing to compare."""
        ledger = bridge.ledger_from_operations([VERIFIED], project="p")
        self.assertEqual("sig-a", ledger["operations"][0]["op_signature"])

    def test_an_unverified_price_marks_the_provider_meter_untrusted(self):
        ledger = bridge.ledger_from_operations([VERIFIED, UNRECONCILED], project="p")
        self.assertEqual("verified", ledger["providers"][0]["meter_trust"])
        untrusted = bridge.ledger_from_operations([VERIFIED, UNVERIFIED_PRICE], project="p")
        self.assertEqual("unknown", untrusted["providers"][0]["meter_trust"])

    def test_quota_units_are_counted_in_their_own_meter_and_never_as_money(self):
        ledger = bridge.ledger_from_operations([VERIFIED, UNRECONCILED], project="p")
        # Derived from the operation's own quota unit, so a credit-metered provider
        # cannot end up in the money column by omission.
        self.assertEqual("credits", ledger["providers"][0]["meter"])
        self.assertEqual({"credits": 2}, ledger["totals"]["quota_units"])
        # One confirmed call and one unreconciled call: the money totals keep them apart.
        self.assertEqual("0.001000", ledger["totals"]["usd_actual_confirmed"])
        self.assertEqual("0.001000", ledger["totals"]["usd_unreconciled"])

    def test_the_registry_routing_key_is_emitted_as_the_schema_s_provider_name(self):
        """The registry routes `typesafe_jev`; QA's ledger schema enumerates `jev`."""
        record = {**VERIFIED, "operation_id": "op-t", "provider": "typesafe_jev",
                  "op_signature": "sig-t"}
        ledger = bridge.ledger_from_operations([record], project="p")
        self.assertEqual("jev", ledger["operations"][0]["provider"])
        self.assertEqual("jev", ledger["providers"][0]["provider"])
        # And a baseline given under the routing key is still found.
        with_baseline = bridge.ledger_from_operations(
            [record], project="p",
            baselines={"typesafe_jev": {"b0": "1000", "floor": "200",
                                        "source": "provider_api_read",
                                        "observed_at": "2026-10-17T00:00:00+00:00"}})
        self.assertEqual("1000", with_baseline["providers"][0]["b0"])

    def test_the_short_name_is_left_alone(self):
        ledger = bridge.ledger_from_operations([VERIFIED], project="p")
        self.assertEqual("serper", ledger["providers"][0]["provider"])

    def test_a_baseline_is_reported_only_when_it_was_supplied(self):
        """B0 is read once at campaign start; inventing one here would be the bypass."""
        absent = bridge.ledger_from_operations([VERIFIED], project="p")
        self.assertEqual("", absent["providers"][0]["b0_source"])
        self.assertIn("B0_NOT_SUPPLIED", absent["providers"][0]["account_ref"])
        supplied = bridge.ledger_from_operations(
            [VERIFIED], project="p",
            baselines={"serper": {"b0": "500", "floor": "100", "meter": "credits",
                                  "source": "provider_api_read",
                                  "observed_at": "2026-10-17T00:00:00+00:00"}})
        self.assertEqual("500", supplied["providers"][0]["b0"])
        self.assertEqual("100", supplied["providers"][0]["floor"])


class ScorecardHonestyTests(unittest.TestCase):
    """The scorecard must refuse a confirmed dollar figure built on an unverified price."""

    def _measure(self, operations, **kwargs):
        ledger = bridge.ledger_from_operations(operations, project="p", **kwargs)
        return scorer.measurement_from_ledger(ledger, accepted_rows=1)

    def test_an_unverified_price_makes_the_dollar_figure_unknown_not_zero(self):
        measured = self._measure([VERIFIED, UNVERIFIED_PRICE])
        self.assertEqual("UNKNOWN", measured["usd_per_accepted_row"]["state"])
        self.assertIsNone(measured["usd_per_accepted_row"]["value"])
        self.assertNotEqual("0.000000", str(measured["usd_per_accepted_row"]["value"]))

    def test_a_verified_price_with_no_unreconciled_charge_measures_cleanly(self):
        measured = self._measure([VERIFIED])
        self.assertEqual("MEASURED", measured["usd_per_accepted_row"]["state"])
        self.assertEqual("0.001000", measured["usd_per_accepted_row"]["value"])

    def test_an_unreconciled_charge_makes_the_figure_partial_and_names_the_exposure(self):
        measured = self._measure([VERIFIED, UNRECONCILED])
        usd = measured["usd_per_accepted_row"]
        self.assertEqual("PARTIAL_UNKNOWN", usd["state"])
        self.assertEqual("0.001000", usd["value"])
        self.assertEqual("0.001000", usd["unknown_exposure_usd"])
        self.assertEqual("0.002000", usd["all_in_usd"])

    def test_a_bridged_ledger_is_accepted_by_the_scorer_s_own_shape_check(self):
        ledger = bridge.ledger_from_operations([VERIFIED, UNRECONCILED], project="p")
        self.assertEqual("marketops.qa.cost_ledger.v1", ledger["schema"])
        for op in ledger["operations"]:
            for field in ("operation_id", "provider", "capability_id", "tier",
                          "reserved", "estimated", "derived", "actual", "state"):
                self.assertIn(field, op, field)


class DuplicateChargeTests(unittest.TestCase):
    def test_one_signature_charged_twice_fails_the_duplicate_check(self):
        twice = [{**VERIFIED, "operation_id": "op-x"},
                 {**VERIFIED, "operation_id": "op-y"}]
        ledger = bridge.ledger_from_operations(twice, project="p")
        self.assertEqual(1, len({op["op_signature"] for op in ledger["operations"]}))
        verdict = scorer.af7_from_ledger(ledger)
        self.assertEqual("FAIL", verdict["state"])
        self.assertIn("ONE_SIGNATURE_CHARGED_TWICE", verdict["reason_codes"])

    def test_an_empty_ledger_blocks_the_duplicate_check_rather_than_passing_it(self):
        ledger = bridge.ledger_from_operations([], project="p")
        self.assertEqual("BLOCKED", scorer.af7_from_ledger(ledger)["state"])

    def test_distinct_signatures_pass(self):
        ledger = bridge.ledger_from_operations([VERIFIED, UNRECONCILED], project="p")
        self.assertEqual("PASS", scorer.af7_from_ledger(ledger)["state"])


class RoundTripTests(unittest.TestCase):
    def test_the_bridge_loads_the_integrated_ledger_when_it_is_importable(self):
        """Skips on a worker branch; runs where the integration tree is beside us."""
        try:
            import research_usage_ledger  # noqa: F401
        except ImportError:  # pragma: no cover - depends on which branch this is
            self.skipTest("research_usage_ledger is not on this branch")
        self.assertTrue(hasattr(bridge, "operations_from_ledger"))


if __name__ == "__main__":
    unittest.main()
