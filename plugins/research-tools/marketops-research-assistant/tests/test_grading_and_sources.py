"""A/B/C/D/X by code from light scene rules, and the per-source A+B gate.

The grading cases are the misgrades found when 30 past A/B routes were checked
against their evidence; every value here is synthetic (example.org / example.fr).
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_grading as grading  # noqa: E402
import research_scene as scene  # noqa: E402

PARIS = {"record_type": "research_brief", "scene_rules": {
    "decision": ["directeur des sports", "directrice des sports", "chef du service des sports",
                 "responsable des equipements sportifs"],
    "operational": ["responsable de gymnase", "regisseur", "responsable technique"],
    "entry": ["service des sports", "reservations"],
    "excluded": ["communication", "ressources humaines", "comptabilite"]}}


def rec(kind, key, data, artifact="art-1"):
    return {"record_id": f"{kind}-{key}", "kind": kind, "entity_key": key,
            "artifact_ids": [artifact], "data": data}


def contact(key, email, quote, *, artifact="art-1", **facts):
    return rec("contact_point", key, {"organization_id": "org-1", "type": "email", "value": email,
                                      "claims": [{"field": "email", "artifact_id": artifact,
                                                  "quote": quote}], **facts}, artifact)


def person(key, name, title, quote, artifact="art-1"):
    return rec("person", key, {"name": name, "role_title": title, "organization_id": "org-1",
                               "claims": [{"field": "role_title", "artifact_id": artifact,
                                           "quote": quote}]}, artifact)


class SceneRules(unittest.TestCase):
    def test_accents_and_case_do_not_matter(self):
        rules = scene.load(PARIS)
        self.assertEqual("decision", scene.role_class("Directrice des Sports", rules))
        self.assertEqual("decision", scene.role_class("Responsable des équipements sportifs", rules))
        self.assertEqual("excluded", scene.role_class("Directeur de la communication", rules))
        self.assertEqual("", scene.role_class("Maire adjoint", rules))

    def test_brief_rules_win_over_a_shipped_pack_and_fill_its_gaps(self):
        rules = scene.load({"scene": "uk-natural-grass",
                            "scene_rules": {"decision": ["head of pitches"]}})
        self.assertEqual(["head of pitches"], rules["roles"]["decision"])
        self.assertIn("groundsman", rules["roles"]["operational"])
        self.assertEqual("shipped:uk-natural-grass", scene.load({"scene": "uk-natural-grass"})["source"])

    def test_a_deputy_is_operational(self):
        rules = scene.load(PARIS)
        self.assertEqual("operational", scene.role_class("Directeur adjoint des sports", rules))
        self.assertEqual("operational", scene.role_class("Vice-directrice des sports", rules))
        self.assertEqual("operational", scene.nearest_title(
            "Florence MERX Vice-directrice des sports f.merx@example.fr", "f.merx@example.fr", rules)[0])
        self.assertEqual("decision", scene.role_class("Directrice des sports", rules))

    def test_roles_wanted_alone_still_grades(self):
        rules = scene.load({"roles_wanted": ["Head Groundsman", "Grounds Manager"]})
        self.assertTrue(rules["source"].startswith("brief.roles_wanted"))
        self.assertEqual("decision", scene.role_class("head groundsman", rules))
        # Without a pack, the usual service entries and unrelated functions still sort.
        self.assertEqual("entry", scene.role_class("bookings", rules))
        self.assertEqual("excluded", scene.role_class("finance", rules))
        self.assertEqual({}, scene.load({})["roles"])

    def test_bad_rules_are_named(self):
        self.assertTrue(scene.validate({"boss": ["x"]}))
        self.assertTrue(scene.validate({"entry": ["bookings"]}))
        self.assertFalse(scene.validate(PARIS["scene_rules"]))

    def test_generic_inboxes_in_several_languages(self):
        for value in ("info@example.fr", "accueil@example.fr", "segreteria@example.it",
                      "contact2@example.org", "mairie@example.fr"):
            self.assertTrue(scene.is_generic_inbox(value), value)
        self.assertFalse(scene.is_generic_inbox("m.durand@example.fr"))


class Grades(unittest.TestCase):
    def grade(self, records, brief=PARIS):
        grades, _ = grading.grade_routes(records, brief)
        return {k.split("-", 1)[1]: v for k, v in grades.items()}

    def test_name_role_and_email_in_one_block_is_a(self):
        g = self.grade([
            person("p1", "Marie Durand", "Directrice des sports",
                   "Marie Durand, directrice des sports"),
            contact("c1", "m.durand@example.fr",
                    "Marie Durand, directrice des sports : m.durand@example.fr", person_id="p1")])
        self.assertEqual("A", g["c1"]["grade"])

    def test_a_department_inbox_next_to_a_named_director_is_c(self):
        g = self.grade([
            person("p1", "Jean Martin", "Directeur des sports", "Directeur des sports : Jean Martin"),
            contact("c1", "sports@example.fr",
                    "Directeur des sports : Jean Martin - sports@example.fr", person_id="p1"),
            person("p2", "Lea Moreau", "Directrice des sports", "Directrice des sports : Lea Moreau"),
            contact("c2", "lmoreau@example.fr",
                    "Directrice des sports : Lea Moreau - lmoreau@example.fr", person_id="p2")])
        self.assertEqual("C", g["c1"]["grade"])
        self.assertIn("DEPARTMENT_INBOX_NEXT_TO_NAME", g["c1"]["reasons"])
        self.assertEqual("A", g["c2"]["grade"])

    def test_a_role_mailbox_next_to_a_named_person_is_b(self):
        g = self.grade([
            person("p1", "Jean Dupont", "Directeur des sports", "Directeur des sports : Jean Dupont"),
            contact("c1", "directeur.sports@example.fr",
                    "Directeur des sports : Jean Dupont - directeur.sports@example.fr", person_id="p1")])
        self.assertEqual("B", g["c1"]["grade"])
        self.assertIn("ROLE_MAILBOX_OF_NAMED_PERSON", g["c1"]["reasons"])

    def test_a_name_printed_with_its_own_address_needs_no_person_record(self):
        g = self.grade([
            contact("c1", "jean.dupont@example.fr",
                    "Jean Dupont, directeur des sports : jean.dupont@example.fr"),
            contact("c2", "jdupont@example.fr",
                    "Jean Dupont, directeur des sports : jdupont@example.fr"),
            contact("c3", "jean.dupont@example.fr",
                    "Contactez le directeur des sports : jean.dupont@example.fr", artifact="art-2")])
        self.assertEqual("A", g["c1"]["grade"])
        self.assertIn("NAME_FROM_EMAIL_QUOTE", g["c1"]["reasons"])
        self.assertNotEqual("A", g["c2"]["grade"])  # 'jdupont' spells no full name part
        self.assertEqual("B", g["c3"]["grade"])      # no name printed: role-bound only

    def test_cases_from_the_live_paris_audit(self):
        rules = {"record_type": "research_brief", "scene_rules": {
            **PARIS["scene_rules"],
            "decision": PARIS["scene_rules"]["decision"] + ["president", "presidente"],
            "operational": PARIS["scene_rules"]["operational"] + ["vice presidente"],
            "entry": PARIS["scene_rules"]["entry"] + ["tresoriere"]}}
        g = self.grade([
            contact("c1", "saint-denis@example.fr",
                    "361 avenue du Président Wilson, 93200 Saint-Denis 01 84 21 12 04 saint-denis@example.fr"),
            contact("c2", "florence.merx@example.fr",
                    "Sylvie Sauvage Présidente tcpj@example.fr Florence MERX Vice-présidente "
                    "florence.merx@example.fr Céline Muller Lardeau Trésorière celine.mullerlardeau@example.fr"),
            contact("c3", "celine.mullerlardeau@example.fr",
                    "Florence MERX Vice-présidente florence.merx@example.fr Céline Muller Lardeau "
                    "Trésorière celine.mullerlardeau@example.fr"),
            contact("c4", "club.badminton@example.fr",
                    "Le Bureau Ludovic C. club.badminton@example.fr Président Arnaud P. "
                    "club.badminton@example.fr Vice-Président"),
            contact("c5", "marie-florence.deprince@example.fr",
                    "Contact Marie-Florence Deprince, présidente Tél. : 06 71 96 97 28 "
                    "E-mail : marie-florence.deprince@example.fr"),
            contact("c6", "cirque.arts@example.fr",
                    "Contact Mahi Franck Gbale (Président) et/ou Nicolas Heniet (intervenant) "
                    "Tél. : 07 81 97 70 08 E-mail : cirque.arts@example.fr"),
            contact("c7", "president@example.fr",
                    "Contact présidence : @Mail -> president@example.fr Contact administratif")],
            brief=rules)
        self.assertEqual("C", g["c1"]["grade"])           # a street, not a role
        self.assertEqual("B", g["c2"]["grade"])           # vice-présidente, not présidente
        self.assertEqual("C", g["c3"]["grade"])           # her own role: trésorière
        self.assertEqual("C", g["c4"]["grade"])           # one club box for two roles
        self.assertEqual("A", g["c5"]["grade"])           # hyphenated name, présidente
        self.assertEqual("C", g["c6"]["grade"])           # the club's inbox, role far away
        self.assertEqual("B", g["c7"]["grade"])           # a role mailbox

    def test_a_directory_page_does_not_credit_the_site_owner(self):
        town = rec("organization", "org-1", {"name": "Ville X", "website": "https://www.ville-x.fr"})
        listed = [contact(f"c{i}", f"president{i}@club{i}.fr",
                          f"Club {i} : présidente president{i}@club{i}.fr", artifact="art-annuaire")
                  for i in range(3)]
        own = contact("c9", "j.martin@ville-x.fr", "Jean Martin, directeur des sports j.martin@ville-x.fr",
                      artifact="art-annuaire")
        g = self.grade([town, *listed, own])
        self.assertIn("LISTED_ON_ANOTHER_ORGANISATIONS_PAGE", g["c0"]["reasons"])
        self.assertEqual("", g["c0"]["organization_id"])
        self.assertNotIn("LISTED_ON_ANOTHER_ORGANISATIONS_PAGE", g["c9"]["reasons"])
        self.assertEqual("org-1", g["c9"]["organization_id"])

    def test_role_from_another_page_is_only_c(self):
        g = self.grade([
            person("p1", "Marie Durand", "Directrice des sports",
                   "Marie Durand rejoint la ville comme directrice des sports", artifact="art-jobs"),
            contact("c1", "m.durand@example.fr", "Marie Durand - m.durand@example.fr",
                    artifact="art-staff", person_id="p1")])
        self.assertEqual("C", g["c1"]["grade"])
        self.assertIn("NAME_ROLE_EMAIL_NOT_ONE_BLOCK", g["c1"]["reasons"])

    def test_a_generic_inbox_next_to_a_name_is_d(self):
        g = self.grade([
            person("p1", "Paul Martin", "Directeur des sports", "Paul Martin, directeur des sports"),
            contact("c1", "info@example.fr", "Paul Martin, directeur des sports, info@example.fr",
                    person_id="p1")])
        self.assertEqual("D", g["c1"]["grade"])

    def test_one_address_bound_to_several_people_is_d(self):
        g = self.grade([
            person("p1", "Anne Petit", "Directrice des sports", "Anne Petit"),
            person("p2", "Luc Roux", "Responsable de gymnase", "Luc Roux"),
            contact("c1", "sports.ville@example.fr", "Anne Petit sports.ville@example.fr", person_id="p1"),
            contact("c2", "sports.ville@example.fr", "Luc Roux sports.ville@example.fr", person_id="p2")])
        self.assertEqual({"D"}, {g["c1"]["grade"], g["c2"]["grade"]})
        self.assertIn("SHARED_INBOX_SEVERAL_PEOPLE", g["c1"]["reasons"])

    def test_excluded_and_out_of_scene_roles(self):
        g = self.grade([
            contact("c1", "presse@example.fr", "presse@example.fr", role_title="Communication"),
            contact("c2", "j.blanc@example.fr", "Jean Blanc, maire adjoint, j.blanc@example.fr",
                    role_title="Maire adjoint", person_name="Jean Blanc")])
        self.assertEqual("X", g["c1"]["grade"])
        self.assertEqual("C", g["c2"]["grade"])
        self.assertIn("ROLE_NOT_IN_SCENE", g["c2"]["reasons"])

    def test_a_role_printed_next_to_the_address_without_a_name_is_at_most_b(self):
        g = self.grade([contact("c1", "dirsports@example.fr",
                                "Direction : directeur des sports - dirsports@example.fr")])
        self.assertEqual("B", g["c1"]["grade"])
        self.assertIn("NO_NAME_MAX_B", g["c1"]["reasons"])
        self.assertIn("ROLE_FROM_EMAIL_QUOTE", g["c1"]["reasons"])

    def test_without_any_rules_routes_stay_unknown(self):
        g = self.grade([contact("c1", "a@example.org", "a@example.org")], brief={})
        self.assertEqual("RULES_UNKNOWN", g["c1"]["grade"])

    def test_a_named_person_without_an_email_is_a_lead(self):
        records = [person("p1", "Anne Petit", "Directrice des sports", "Anne Petit, directrice des sports"),
                   person("p2", "Luc Roux", "Chargé de communication", "Luc Roux, communication")]
        grades, rules = grading.grade_routes(records, PARIS)
        leads = grading.named_leads(records, grades, rules)
        self.assertEqual(["Anne Petit"], [lead["name"] for lead in leads])

    def test_the_kpi_counts_people_not_rows(self):
        grades, _ = grading.grade_routes([
            person("p1", "Marie Durand", "Directrice des sports", "Marie Durand, directrice des sports"),
            contact("c1", "m.durand@example.fr",
                    "Marie Durand, directrice des sports : m.durand@example.fr", person_id="p1"),
            contact("c2", "marie.durand@example.fr",
                    "Marie Durand, directrice des sports : marie.durand@example.fr", person_id="p1",
                    artifact="art-1")], PARIS)
        kpi = grading.summary(grades, targets_finished=40)
        self.assertEqual(2, kpi["grades"]["A"])
        self.assertEqual(1, kpi["ab_people"])
        self.assertEqual(2.5, kpi["ab_per_100"])


class SourceGate(unittest.TestCase):
    BRIEF = PARIS

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        import research_lite_store as store
        import research_pool as pool
        self.store, self.pool = store, pool
        store.start_run(self.root, "run-g", "FR-Paris", "Paris sports venues", "v1")
        store.put_candidate(self.root, "run-g", "brief-1", "qualification", "observation",
                            "research-brief", self.BRIEF, [])

    def source(self, source_id, total, with_ab, finished):
        targets = [{"name": f"{source_id} venue {i}", "address": f"7500{i % 10} Paris {i}"}
                   for i in range(total)]
        self.pool.import_targets(self.root, pool_id="pool-g", source_id=source_id,
                                 source_url="https://example.org/" + source_id, provider="fixture",
                                 listed_count=total, targets=targets)
        with sqlite3.connect(self.root / "state" / "research-pool.sqlite3") as db:
            keys = [k for (k,) in db.execute(
                "SELECT target_key FROM pool_targets WHERE source_id=? ORDER BY rowid", (source_id,))]
            for i, key in enumerate(keys[:finished]):
                org = f"org-{source_id}-{i}"
                db.execute("UPDATE pool_targets SET status='done', run_id='run-g', organization_key=? "
                           "WHERE pool_id='pool-g' AND target_key=?", (org, key))
                if i < with_ab:
                    self.store.put_candidate(
                        self.root, "run-g", f"c-{org}", "routes", "contact_point", f"c-{org}",
                        {"organization_id": org, "type": "email", "value": f"dir{i}@{source_id}.example.fr",
                         "claims": [{"field": "email", "artifact_id": "x",
                                     "quote": f"directeur des sports dir{i}@{source_id}.example.fr"}]}, [])

    def test_a_poor_source_is_parked_and_a_good_one_goes_on(self):
        import research_sources as sources
        self.source("good", 40, with_ab=9, finished=30)
        self.source("poor", 40, with_ab=1, finished=30)
        report = {r["source_id"]: r for r in sources.yields(self.root, "run-g")["sources"]}
        self.assertEqual("PROMOTE", report["good"]["verdict"])
        self.assertEqual("STOP", report["poor"]["verdict"])
        # Advice by default: the Agent decides whether to leave a source.
        advice = sources.park_stopped(self.root, "run-g")
        self.assertEqual(0, advice["parked"])
        self.assertEqual(["poor"], [r["source_id"] for r in advice["advice"]])
        from unittest import mock
        with mock.patch.object(sources, "_park_requested", return_value=True):
            parked = sources.park_stopped(self.root, "run-g")
        self.assertEqual(10, parked["parked"])
        after = {r["source_id"]: r for r in sources.yields(self.root, "run-g")["sources"]}
        self.assertEqual(10, after["good"]["pending"])
        self.assertEqual((0, 10), (after["poor"]["pending"], after["poor"]["parked"]))
        self.assertEqual(30, after["poor"]["finished"])  # parking is not work done
        with sqlite3.connect(self.root / "state" / "research-events.sqlite3") as db:
            notes = db.execute("SELECT category, problem FROM research_notes").fetchall()
        self.assertIn(("source_low_yield", "Source poor gave 1 of 30 finished targets with an A or B route"),
                      notes)
        reopened = self.pool.reopen(self.root, pool_id="pool-g", reason=sources.PARK_REASON)
        self.assertEqual(10, reopened["reopened"])

    def test_a_pilot_is_not_judged_early_and_the_gate_can_be_off(self):
        import research_sources as sources
        self.source("new", 40, with_ab=0, finished=12)
        row = sources.yields(self.root, "run-g")["sources"][0]
        self.assertEqual(("PILOT", 18), (row["verdict"], row["needs"]))
        self.assertIsNone(sources.settings({"source_gate": False}))
        self.assertEqual(20, sources.settings({"source_gate": {"pilot": 20}})["pilot"])

    def test_parked_targets_do_not_count_as_method_effort(self):
        import research_methods as methods
        import research_sources as sources
        self.source("poor", 60, with_ab=0, finished=30)
        sources.park_stopped(self.root, "run-g")
        self.assertEqual(30, methods.effort(self.root, "run-g", "contacts", "OWN_SITE")["targets_finished"])


class BriefValidation(unittest.TestCase):
    def test_scene_fields_are_checked_at_start(self):
        import research_mcp as mcp
        base = {"record_type": "research_brief", "target_kind": "organization", "goal": "contacts",
                "require_named_person": False}
        with self.assertRaisesRegex(ValueError, "not shipped"):
            mcp._valid_brief("r", {**base, "scene": "paris-indoor"})
        with self.assertRaisesRegex(ValueError, "scene_rules"):
            mcp._valid_brief("r", {**base, "scene_rules": {"entry": ["accueil"]}})
        mcp._valid_brief("r", {**base, "scene_rules": PARIS["scene_rules"]})
        mcp._valid_brief("r", {**base, "scene": "uk-natural-grass"})
        # A revision through propose gets the same check.
        with self.assertRaisesRegex(ValueError, "scene_rules"):
            mcp._check_scene_fields({**base, "scene_rules": {"boss": ["x"]}})


if __name__ == "__main__":
    unittest.main()


class DeliveryGrades(unittest.TestCase):
    def test_the_workbook_shows_code_grades_and_the_kpi(self):
        import csv
        import json
        import test_delivery_research_delivery as fixture
        import research_delivery as delivery
        case = fixture.CandidateDeliveryE2E("test_delivers_real_xlsx_with_9_sheets_and_evidence")
        case.setUp()
        self.addCleanup(case.doCleanups)
        import research_lite_store as store
        store.put_candidate(case.root, "run-test", "brief-g", "qualification", "observation",
                            "research-brief", {"record_type": "research_brief",
                                               "scene_rules": {"decision": ["head of grounds"],
                                                               "entry": ["grounds enquiries"]}}, [])
        # A brief revised later: the delivery grades with the current version.
        store.put_candidate(case.root, "run-test", "brief-g2", "qualification", "observation",
                            "research-brief", {"record_type": "research_brief", "supersedes": "brief-g",
                                               "scene_rules": {"decision": ["head of grounds"],
                                                               "entry": ["grounds enquiries"]}}, [])
        result = delivery.write_delivery(case.root, "run-test", "delivery-g")
        folder = Path(result["xlsx_path"]).parent
        with (folder / "联系人路线.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual("C", row["研究评级"])
        manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual("brief.scene_rules", manifest["grading"]["rules_source"])
        self.assertEqual(1, manifest["grading"]["grades"]["C"])
        self.assertEqual(0, manifest["grading"]["ab_people"])
        with (folder / "本轮说明.csv").open(encoding="utf-8-sig", newline="") as handle:
            notes = {r["字段"]: r["值"] for r in csv.DictReader(handle)}
        self.assertIn("A+B 去重人数", notes)
        with (folder / "contact-pack" / "C_D_business_routes.csv").open(encoding="utf-8-sig") as handle:
            pack = list(csv.DictReader(handle))
        self.assertEqual(("C", "grounds@example.org"), (pack[0]["grade"], pack[0]["email"]))
        self.assertTrue(pack[0]["quote"] and pack[0]["source_sha256"])
        self.assertIn("contact-pack/A_B_named_role_emails.csv", manifest["files"])
        self.assertIn("Research_Result.xlsx", manifest["files"])


class TargetFamilies(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def rows(self, **finished):
        return [{"pool_id": "p", "source_id": s, "finished": n, "pending": 5} for s, n in finished.items()]

    def test_a_contacts_goal_pilots_every_family(self):
        import research_sources as sources
        import research_methods as methods
        brief = {"goal": "contacts"}
        sources.set_family(self.root, "p", "towns", "OPERATORS")
        sources.set_family(self.root, "p", "clubs", "CLUBS_ASSOCIATIONS")
        state = sources.families(self.root, "r", brief=brief, sources=self.rows(towns=33, clubs=4))
        self.assertEqual(["OPERATORS"], state["piloted"])
        self.assertIn("CLUBS_ASSOCIATIONS", state["missing"])
        self.assertEqual("SCHOOLS_UNIVERSITIES", state["next_family"])  # not even started yet
        methods.record(self.root, "r", "SCHOOLS_UNIVERSITIES", "not_applicable", "no school facilities")
        methods.record(self.root, "r", "FEDERATION_LISTS", "not_applicable", "no league lists online")
        state = sources.families(self.root, "r", brief=brief, sources=self.rows(towns=33, clubs=30))
        self.assertEqual([], state["missing"])
        self.assertEqual([], sources.families(self.root, "r", brief={"goal": "pool"}, sources=[])["wanted"])
        with self.assertRaisesRegex(ValueError, "TARGET_FAMILY_INVALID"):
            sources.set_family(self.root, "p", "x", "SHOPS")


class FamiliesKeepTheRunOpen(unittest.TestCase):
    def test_methods_exhausted_but_clubs_never_tried_is_not_the_end(self):
        import research_execution
        import research_lite_store as store
        import research_methods as methods
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "scope", "objective", "v1")
            store.put_candidate(root, "r", "brief-1", "qualification", "observation", "research-brief",
                                {"record_type": "research_brief", "goal": "contacts",
                                 "target_families": ["OPERATORS", "CLUBS_ASSOCIATIONS"]}, [])
            with patch.object(methods, "effort", return_value={"enough": True}):
                for name, _ in methods.LADDERS["contacts"]:
                    methods.record(root, "r", name, "exhausted")
            state = research_execution.assess(root, "r", goal={"goal_satisfied": False})
            self.assertEqual("IMPORT_NEXT_TARGET_FAMILY", state["next_action"])
            self.assertTrue(state["continuation_required"])
            self.assertEqual("OPERATORS", state["target_families"]["next_family"])
            for family in ("OPERATORS", "CLUBS_ASSOCIATIONS"):
                methods.record(root, "r", family, "not_applicable", "none in this area")
            state = research_execution.assess(root, "r", goal={"goal_satisfied": False})
            self.assertEqual("FINAL_METHODS_EXHAUSTED", state["state"])


class ProductiveSourcesAreWidened(SourceGate):
    """The first live Claude Code run: committee pages gave the A/B, it said so, and
    stopped at 12 people. A paying source is widened before the Run can close."""

    BRIEF = {**PARIS, "goal": "contacts"}

    def test_a_short_list_that_is_done_is_judged_not_left_in_pilot(self):
        import research_sources as sources
        self.source("committees", 8, with_ab=4, finished=8)
        self.source("towns", 6, with_ab=0, finished=6)
        report = {r["source_id"]: r for r in sources.yields(self.root, "run-g")["sources"]}
        self.assertEqual("PROMOTE", report["committees"]["verdict"])
        self.assertEqual("SMALL_DONE", report["towns"]["verdict"])

    def test_next_says_expand_until_the_goal_or_new_lists_stop_paying(self):
        import research_execution
        import research_methods as methods
        self.source("committees", 8, with_ab=4, finished=8)
        with patch.object(methods, "effort", return_value={"enough": True}):
            for name, _ in methods.LADDERS["contacts"]:
                if name != "EXPAND_SOURCE":
                    methods.record(self.root, "run-g", name, "exhausted")
            for name, _ in methods.LADDERS["families"]:
                methods.record(self.root, "run-g", name, "not_applicable")
            state = research_execution.assess(self.root, "run-g", goal={"goal_satisfied": False})
            self.assertEqual("EXPAND_PRODUCTIVE_SOURCE", state["next_action"])
            self.assertTrue(state["continuation_required"])
            self.assertEqual(50, state["ab_goal"])
            self.assertIn("committees (4/8", state["source_advice"][0])
            methods.record(self.root, "run-g", "EXPAND_SOURCE", "exhausted", "10 new lists, none paid")
            state = research_execution.assess(self.root, "run-g", goal={"goal_satisfied": False})
            self.assertEqual("FINAL_METHODS_EXHAUSTED", state["state"])
