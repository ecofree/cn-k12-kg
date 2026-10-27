"""Registry stability, honest alignment and real material exposure regressions."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cnkg.catalog import Catalog, CatalogError, compile_catalog, load_catalog, public_task
from cnkg.contracts import validate_resource
from tools.register_catalog import register_new_sources

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class CatalogFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.topic_ids = {"m6a.5.3", "m5b.4.1"}
        topics = [t for t in read(DATA / "topics.json") if t["id"] in self.topic_ids]
        questions = [q for q in read(DATA / "questions.json") if q["topicId"] in self.topic_ids]
        unit = read(DATA / "learning_units/fraction_equivalence.json")
        unit["prerequisite_unit_ids"] = []
        unit["prerequisite_objective_ids"] = []
        # This fixture exercises one complete, real learning unit. Its textbook
        # placements are omitted intentionally, since unit skill links use IDs.
        unit["topic_ids"] = []
        registry = read(DATA / "skills/registry.json")
        registry["placements"] = {tid: p for tid, p in registry["placements"].items() if tid in self.topic_ids}
        objective_ids = {o["id"] for o in unit["objectives"]}
        # Cross-unit prerequisites belong to the production catalog. This
        # isolated fixture keeps only relations whose source is included.
        for objective in unit["objectives"]:
            objective["prerequisite_objective_ids"] = [oid for oid in objective.get("prerequisite_objective_ids", [])
                                                        if oid in objective_ids]
        registry["objective_skill_ids"] = {oid: sid for oid, sid in registry["objective_skill_ids"].items() if oid in objective_ids}
        skill_ids = {sid for p in registry["placements"].values() for sid in p["criterion_skill_ids"]}
        skill_ids.update(registry["objective_skill_ids"].values())
        registry["skills"] = {sid: s for sid, s in registry["skills"].items() if sid in skill_ids}
        for skill in registry["skills"].values():
            skill["prerequisite_skill_ids"] = [sid for sid in skill["prerequisite_skill_ids"] if sid in skill_ids]
        bindings = read(DATA / "skills/catalog-bindings.json")
        qids = {q["id"] for q in questions}
        bindings["tasks"] = {key: row for key, row in bindings["tasks"].items()
                              if (key.startswith("bank:") and row["id"] in qids)
                              or key.startswith("unit:fraction_equivalence:")
                              or any(key.startswith("self_check:" + tid + ":") for tid in self.topic_ids)}
        bindings["examples"] = {key: row for key, row in bindings["examples"].items()
                                 if key.startswith("example:unit:fraction_equivalence:")
                                 or key in {"example:topic:" + tid for tid in self.topic_ids}}
        for name, value in (("topics.json", topics), ("questions.json", questions),
                            ("skills/registry.json", registry), ("skills/catalog-bindings.json", bindings),
                            ("learning_units/fraction_equivalence.json", unit)):
            write(self.data / name, value)
        for tid in self.topic_ids:
            write(self.data / f"teach/{tid}.json", read(DATA / f"teach/{tid}.json"))
        shutil.copytree(ROOT / "web/static/diagrams", self.root / "web/static/diagrams")

    def compile(self):
        return compile_catalog(self.data)

    def test_public_constructor_and_accessors_keep_nested_payloads_independent(self):
        payload = self.compile()
        catalog = Catalog(payload)
        qid = "m5b.4.1-q1"
        original = catalog.task(qid)
        payload["tasks"][qid]["prompt"] = "caller changed the input"
        payload["tasks"][qid]["skill_ids"].append("caller-only-skill")
        self.assertEqual(catalog.task(qid), original)
        shown = catalog.task(qid)
        shown["skill_ids"].append("accessor-only-skill")
        self.assertEqual(catalog.task(qid), original)

    def test_loaded_catalogs_are_isolated_and_every_load_revalidates_bytes(self):
        payload = self.compile()
        path = self.data / "catalog.json"
        write(path, payload)
        first = load_catalog(self.data)
        qid = "m5b.4.1-q1"
        expected = first.task(qid)
        first.tasks[qid]["skill_ids"].append("first-instance-only")
        second = load_catalog(self.data)
        self.assertEqual(second.task(qid), expected)
        returned = second.task(qid)
        returned["skill_ids"].append("returned-copy-only")
        self.assertEqual(second.task(qid), expected)

        # The private ownership path must not bypass either integrity check.
        payload["tasks"][qid]["prompt"] += " changed without rebuilding"
        write(path, payload)
        with self.assertRaisesRegex(CatalogError, "内容指纹不匹配"):
            load_catalog(self.data)
        write(path, second.data)
        questions = read(self.data / "questions.json")
        questions[0]["explanation"] += " source changed after a successful load"
        write(self.data / "questions.json", questions)
        with self.assertRaisesRegex(CatalogError, "目录源已改变"):
            load_catalog(self.data)

    def test_known_material_answer_duplicates_share_families(self):
        c = Catalog(self.compile())
        circle = c.task("m6a.5.3-q2")
        self.assertIn(circle["family_id"], c.material("topic:m6a.5.3")["family_ids"])
        cube = c.task("m5b.4.1-q2")
        self.assertEqual(cube["family_id"], c.self_check("m5b.4.1", 1)["family_id"])
        self.assertIn(cube["family_id"], c.material("topic:m5b.4.1")["family_ids"])

    def test_authored_check_metadata_and_its_own_diagram_survive_compilation(self):
        card = read(self.data / "teach/m5b.4.1.json")
        check = card["objective_coverage"][1]["self_check"]
        check.update(family_id="geometry.cube.surface-volume-from-edge",
                     variant_id="cube-check-with-own-diagram", representation="geometry",
                     cognitive_demand="explain",
                     diagram={"src": "/static/diagrams/polyhedra-heights-v5.svg", "alt": "高和侧棱分开的示意图"})
        write(self.data / "teach/m5b.4.1.json", card)
        c = Catalog(self.compile())
        task = c.self_check("m5b.4.1", 1)
        for field in ("family_id", "variant_id", "representation", "cognitive_demand", "diagram"):
            self.assertEqual(task[field], check[field])
        self.assertEqual(task["family_status"], "declared")
        self.assertEqual(task["skill_ids"], c.placement("m5b.4.1")["criterion_skill_ids"][1:2])

    def test_each_reference_tracks_all_of_its_structures_without_subtracting_shared_ones(self):
        card = read(self.data / "teach/m5b.4.1.json")
        shared = "geometry.cube.surface-volume-from-edge"
        area, volume = "geometry.cube.six-face-area", "geometry.cube.three-edge-volume"
        card["worked_example"].update(family_id=shared, reference_family_ids=[area, volume])
        card["objective_coverage"][1]["self_check"].update(family_id=shared, reference_family_ids=[area])
        write(self.data / "teach/m5b.4.1.json", card)
        c = Catalog(self.compile())
        material = c.material("topic:m5b.4.1")
        self.assertEqual(set(material["reference_families_by_key"]["example:0"]), {shared, area, volume})
        self.assertEqual(set(material["reference_families_by_key"]["self_check:1"]), {shared, area})
        self.assertTrue({shared, area, volume} <= set(material["family_ids"]))
        self.assertNotIn(volume, material["reference_families_by_key"]["self_check:1"])

    def test_material_exposure_does_not_include_every_assessment_family(self):
        c = Catalog(self.compile())
        material = c.material("topic:m5b.4.1")
        unrelated = c.task("m5b.4.1-q1")["family_id"]
        self.assertIn(unrelated, material["assessment_family_ids"])
        self.assertNotIn(unrelated, material["family_ids"])
        unit = c.unit("fraction_equivalence")
        unit_material = c.material(unit["resource_id"])
        self.assertTrue(set(unit_material["assessment_family_ids"]) - set(unit_material["family_ids"]))

    def test_quarantined_memory_items_keep_ids_but_have_no_practice_or_check(self):
        c = Catalog(self.compile())
        q = c.task("m6a.5.3-q9")
        self.assertEqual(q["availability"], "quarantined")
        self.assertEqual(q["purpose"], [])
        self.assertFalse(q["policy"]["practice_eligible"])
        self.assertFalse(q["policy"]["diagnosis_eligible"])
        self.assertEqual(q["source_ref"]["legacy_id"], "m6a.5.3-q9")

    def test_topic_only_mapping_does_not_claim_every_criterion(self):
        c = Catalog(self.compile())
        old = c.task("m5b.4.1-q1")
        self.assertEqual(old["skill_ids"], [])
        self.assertEqual(old["alignment"], "topic_only")
        mapped = c.task("m5b.4.1-q101")
        self.assertEqual(mapped["skill_ids"], c.placement("m5b.4.1")["criterion_skill_ids"][1:2])
        self.assertEqual(mapped["alignment"], "explicit_component")
        self.assertEqual(c.skill(mapped["skill_ids"][0])["granularity"], "legacy_composite")

    def test_unknown_family_identity_never_establishes_independence(self):
        c = Catalog(self.compile())
        q = c.task("m5b.4.1-q1")
        self.assertEqual(q["family_status"], "unclassified")
        self.assertFalse(c.families[q["family_id"]]["independence_established"])
        self.assertFalse(q["policy"]["independent_evidence"])

    def test_title_or_source_order_changes_do_not_reallocate_stable_skills(self):
        before = self.compile()
        topics = read(self.data / "topics.json")
        topics.reverse()
        topics[0]["name"] += "（新版标题）"
        write(self.data / "topics.json", topics)
        after = self.compile()
        self.assertEqual(set(before["skills"]), set(after["skills"]))
        self.assertEqual(before["source_index"], after["source_index"])
        self.assertEqual(before["units"]["fraction_equivalence"]["resource_version"],
                         after["units"]["fraction_equivalence"]["resource_version"])

    def test_criterion_reordering_requires_explicit_mapping_reconciliation(self):
        topics = read(self.data / "topics.json")
        topics[0]["mastery_criteria"].reverse()
        write(self.data / "topics.json", topics)
        with self.assertRaisesRegex(CatalogError, "重排或改写"):
            self.compile()

    def test_missing_registration_is_not_guessed(self):
        b = read(self.data / "skills/catalog-bindings.json")
        del b["tasks"]["bank:m5b.4.1-q1"]
        write(self.data / "skills/catalog-bindings.json", b)
        with self.assertRaisesRegex(CatalogError, "尚未注册"):
            self.compile()

    def test_registration_preserves_existing_ids_and_is_idempotent(self):
        old_registry = read(self.data / "skills/registry.json")
        old_bindings = read(self.data / "skills/catalog-bindings.json")
        path = self.data / "learning_units/fraction_equivalence.json"
        unit = read(path)
        oid = "fraction_equivalence:new_observable"
        unit["objectives"].append({"id": oid, "statement": "能指出等值分数中不变的整体。", "granularity": "observable"})
        write(path, unit)
        registry, bindings, changes = register_new_sources(self.data)
        self.assertEqual(len(changes["skills"]), 1)
        self.assertEqual(bindings, old_bindings)
        for sid in old_registry["skills"]:
            self.assertEqual(registry["skills"][sid], old_registry["skills"][sid])
        new_id = registry["objective_skill_ids"][oid]
        write(self.data / "skills/registry.json", registry)
        write(self.data / "skills/catalog-bindings.json", bindings)
        again, _, changes = register_new_sources(self.data)
        self.assertEqual(changes, {"skills": [], "tasks": [], "examples": []})
        self.assertEqual(again["objective_skill_ids"][oid], new_id)

    def test_registration_requires_authored_contract_for_a_new_question(self):
        path = self.data / "questions.json"
        questions = read(path)
        new = {**questions[0], "id": "m5b.4.1-q999", "topicId": "m5b.4.1"}
        questions.append(new)
        write(path, questions)
        with self.assertRaisesRegex(CatalogError, "不从题干猜测"):
            register_new_sources(self.data)

    def test_project_step_reordering_cannot_silently_reuse_task_ids(self):
        project = read(DATA / "projects/one_hundred_million.json")
        registry = read(self.data / "skills/registry.json")
        original_registry = read(DATA / "skills/registry.json")
        for objective in project["objectives"]:
            oid = objective["id"]
            sid = original_registry["objective_skill_ids"][oid]
            registry["objective_skill_ids"][oid] = sid
            registry["skills"][sid] = original_registry["skills"][sid]
        bindings = read(self.data / "skills/catalog-bindings.json")
        original_bindings = read(DATA / "skills/catalog-bindings.json")
        for key, binding in original_bindings["tasks"].items():
            if key.startswith("project_step:one_hundred_million:"):
                bindings["tasks"][key] = binding
        write(self.data / "skills/registry.json", registry)
        write(self.data / "skills/catalog-bindings.json", bindings)
        path = self.data / "projects/one_hundred_million.json"
        write(path, project)
        catalog = Catalog(self.compile())
        self.assertEqual(len(catalog.project_steps(project["id"])), len(project["steps"]))
        project["steps"].reverse()
        write(path, project)
        with self.assertRaisesRegex(CatalogError, "项目步骤已改名或重排"):
            self.compile()

    def test_bad_contract_blocks_compilation(self):
        b = read(self.data / "skills/catalog-bindings.json")
        b["tasks"]["bank:m5b.4.1-q1"]["answer_spec"] = {"kind": "number", "comparison": "tolerance"}
        write(self.data / "skills/catalog-bindings.json", b)
        with self.assertRaises(CatalogError):
            self.compile()

    def test_local_task_or_asset_revision_changes_unit_version(self):
        before = self.compile()
        unit_path = self.data / "learning_units/fraction_equivalence.json"
        unit = read(unit_path)
        unit["tasks"][0]["prompt"] += " 请认真核对。"
        write(unit_path, unit)
        after = self.compile()
        uid = unit["id"]
        self.assertNotEqual(before["units"][uid]["resource_version"], after["units"][uid]["resource_version"])
        self.assertEqual(before["materials"]["topic:m5b.4.1"]["resource_version"],
                         after["materials"]["topic:m5b.4.1"]["resource_version"])
        diagram = unit["diagram"]["src"]
        path = self.root / "web" / diagram.lstrip("/")
        path.write_text(path.read_text() + "\n<!-- revision -->\n")
        assets_changed = self.compile()
        self.assertNotEqual(after["units"][uid]["resource_version"], assets_changed["units"][uid]["resource_version"])

    def test_prerequisite_cycle_and_unknown_objective_are_rejected(self):
        path = self.data / "learning_units/fraction_equivalence.json"
        unit = read(path)
        unit["prerequisite_unit_ids"] = [unit["id"]]
        unit["prerequisite_objective_ids"] = ["missing:objective"]
        write(path, unit)
        with self.assertRaises(CatalogError) as context:
            self.compile()
        self.assertIn("自指", str(context.exception))
        self.assertIn("无效先修目标", str(context.exception))

    def test_public_task_does_not_leak_reference_or_rubric_examples(self):
        c = Catalog(self.compile())
        source = c.self_check("m5b.4.1", 1)
        shown = public_task(source)
        for field in ("answer", "answer_spec", "reference_answer", "verify", "explanation"):
            self.assertNotIn(field, shown)
        self.assertTrue(shown["rubric"])
        self.assertNotIn("example", shown["rubric"][0])
        shown["rubric"][0]["criterion"] = "changed"
        self.assertNotEqual(c.task(source["id"])["rubric"][0]["criterion"], "changed")

    def test_real_compiler_entrypoint_check_detects_stale_output(self):
        base = [sys.executable, str(ROOT / "tools/compile_catalog.py"), "--data-dir", str(self.data),
                "--report", str(self.root / "report.json")]
        first = subprocess.run([*base, "--write"], capture_output=True, text=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run([*base, "--check"], capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIsInstance(load_catalog(self.data), Catalog)
        q = read(self.data / "questions.json")
        q[0]["explanation"] += " 已更新说明。"
        write(self.data / "questions.json", q)
        with self.assertRaisesRegex(CatalogError, "目录源已改变"):
            load_catalog(self.data)
        changed = subprocess.run([*base, "--check"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("已过期", changed.stderr)


class FullCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = load_catalog()

    def test_all_real_resource_classes_are_registered_and_contracts_explicit(self):
        counts = self.c.coverage["task_source_counts"]
        self.assertEqual(counts["bank"], 3572)
        self.assertEqual(counts["self_check"], 964)
        self.assertEqual(counts["project_step"], 84)
        self.assertEqual(counts["unit"], 231)
        self.assertEqual(self.c.coverage["quarantined_tasks"], 489)
        self.assertTrue(all("answer_spec" in t for t in self.c.tasks.values()))
        self.assertEqual(validate_resource("catalog", self.c.data), [])

    def test_policy_is_unchanged_by_current_explicit_contract_migration(self):
        self.assertEqual(self.c.coverage["policy_migration_changes"], [])
        policies = self.c.coverage["bank_policy_counts"]
        self.assertEqual(policies["original"], policies["compiled"])
        self.assertEqual(policies["compiled"]["practice_eligible"], 3083)

    def test_reviewed_component_observations_cannot_confirm_or_reject_the_whole_topic(self):
        focus = set(read(DATA / "alignment_targets.json")["topic_ids"])
        tasks = [task for task in self.c.tasks.values() if task["source_kind"] == "bank"
                 and task["availability"] == "active" and focus.intersection(task["topic_ids"])]
        self.assertEqual(len(tasks), 251)
        for task in tasks:
            self.assertFalse(task["policy"]["may_confirm_mastery"], task["id"])
            self.assertFalse(task["policy"]["may_judge_weak"], task["id"])
        # This is still a useful local screening task; its numerical result
        # cannot establish that the learner can collect or represent data.
        self.assertTrue(self.c.task("m3b.5.1-q1")["policy"]["diagnosis_eligible"])

    def test_real_approximation_tasks_have_explicit_rounding_precision(self):
        spec = self.c.task("m7b.8.3-q101")["answer_spec"]
        self.assertEqual(spec["comparison"], "rounded")
        self.assertEqual(spec["decimal_places"], 3)
        self.assertEqual(spec["representation"], "decimal")

    def test_refined_objectives_and_prerequisites_use_stable_skills(self):
        unit = self.c.unit("ratio_models")
        self.assertTrue(unit["prerequisite_skill_ids"])
        self.assertTrue(all(sid.startswith("sk_") for sid in unit["prerequisite_skill_ids"]))
        observed = [o for o in unit["objectives"] if o["granularity"] == "observable"]
        self.assertTrue(observed)
        self.assertTrue(self.c.skill(observed[0]["skill_id"])["refines_skill_ids"])


if __name__ == "__main__":
    unittest.main()
