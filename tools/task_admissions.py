"""Check explicit, content-bound bank eligibility. Never infer it from prose length."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.admissions import fingerprint, policies, resolved_question
from cnkg.evidence import _legacy_question_policy
from cnkg.evidence import may_confirm_mastery, may_judge_weak
from cnkg.answers import grade_answer
from cnkg.verify import check_question


def check(data):
    manifest = json.loads((data / "task_admissions.json").read_text())
    questions = json.loads((data / "questions.json").read_text())
    bindings = json.loads((data / "skills/catalog-bindings.json").read_text())["tasks"]
    rows, errors = manifest.get("tasks", {}), []
    if manifest.get("schema") != 1 or not isinstance(rows, dict):
        return ["准入登记版本或结构不正确"]
    if set(rows) != {q["id"] for q in questions}:
        errors.append("准入登记与自有题目身份集合不一致")
    for q in questions:
        try:
            policies(q, resolved_question(q, bindings["bank:" + q["id"]]), rows.get(q["id"]))
        except ValueError as exc:
            errors.append(f"{q['id']}: {exc}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--snapshot-baseline", action="store_true", help="仅允许首次迁移，不覆盖既有准入登记")
    parser.add_argument("--upgrade-fingerprints", action="store_true", help="仅在所有旧指纹仍匹配时绑定目标与题族元数据，不更改用途")
    parser.add_argument("--bind-effective-metadata", action="store_true", help="为尚未绑定的V2登记补充实际采用的目录元数据；原题义与用途必须仍匹配")
    parser.add_argument("--record-id", help="完成具体语义核对后，登记一个题目的新用途")
    parser.add_argument("--purpose", choices=("practice", "screening", "quarantine"))
    parser.add_argument("--evidence-level", choices=("bare", "explained", "verified"))
    parser.add_argument("--reviewer", help="核对者标识；不因此获得独立教师审核状态")
    parser.add_argument("--reason", help="写明实际核对的题义、条件、答案及观察边界")
    args = parser.parse_args()
    data, target = ROOT / "data", ROOT / "data/task_admissions.json"
    if args.bind_effective_metadata:
        manifest = json.loads(target.read_text())
        if manifest.get("fingerprint_version") != 2 or manifest.get("effective_metadata_bound"):
            parser.error("仅允许为尚未绑定实际元数据的V2登记补充迁移")
        questions = json.loads((data / "questions.json").read_text())
        bindings = json.loads((data / "skills/catalog-bindings.json").read_text())["tasks"]
        if set(manifest["tasks"]) != {q["id"] for q in questions}:
            parser.error("题目集合已改变，不能用补充迁移登记新题")
        for q in questions:
            row, binding = manifest["tasks"][q["id"]], bindings["bank:" + q["id"]]
            former = {**q, "answer_spec": q.get("answer_spec") or binding.get("answer_spec")}
            if row["source_sha256"] != fingerprint(q) or row["resolved_sha256"] != fingerprint(former):
                parser.error(f"{q['id']}: 原登记已不匹配，须先逐题复核")
            row["prior_resolved_fingerprint_without_effective_metadata"] = row["resolved_sha256"]
            row["resolved_sha256"] = fingerprint(resolved_question(q, binding))
        manifest["effective_metadata_bound"] = True
        manifest["effective_metadata_migration_note"] = "仅将仍匹配的既有题义登记补充绑定实际采用的评分契约、题族状态、变式、表征和认知要求；保留旧指纹及全部用途，没有新增语义审核或诊断资格。"
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if args.upgrade_fingerprints:
        manifest = json.loads(target.read_text())
        if manifest.get("fingerprint_version", 1) != 1:
            parser.error("准入指纹已升级；后续内容变更须逐题重新核对")
        questions = json.loads((data / "questions.json").read_text())
        bindings = json.loads((data / "skills/catalog-bindings.json").read_text())["tasks"]
        if set(manifest["tasks"]) != {q["id"] for q in questions}:
            parser.error("旧登记与题目集合不一致，不能用迁移登记新题")
        for q in questions:
            row = manifest["tasks"][q["id"]]
            resolved = {**q, "answer_spec": q.get("answer_spec") or bindings["bank:" + q["id"]].get("answer_spec")}
            if row["source_sha256"] != fingerprint(q, 1) or row["resolved_sha256"] != fingerprint(resolved, 1):
                parser.error(f"{q['id']}: 旧内容已改变，不能用指纹迁移代替实际语义审查")
            row["prior_fingerprints_v1"] = {key: row[key] for key in ("source_sha256", "resolved_sha256")}
            row["source_sha256"], row["resolved_sha256"] = fingerprint(q), fingerprint(resolved_question(q, bindings["bank:" + q["id"]]))
        manifest["fingerprint_version"] = 2
        manifest["effective_metadata_bound"] = True
        manifest["fingerprint_migration_note"] = "在既有题义指纹全部匹配时扩展绑定目标映射、题族、表征和认知元数据。所有准入用途及原审查依据原样保留；未知继续未知，不新增学科审核或诊断资格。旧指纹逐题保存。"
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if args.snapshot_baseline:
        if target.exists():
            parser.error("已有显式登记，不能通过重跑迁移绕过语义复核")
        questions = json.loads((data / "questions.json").read_text())
        bindings = json.loads((data / "skills/catalog-bindings.json").read_text())["tasks"]
        rows = {}
        for q in questions:
            spec = q.get("answer_spec") or bindings["bank:" + q["id"]].get("answer_spec")
            resolved = resolved_question(q, bindings["bank:" + q["id"]])
            rows[q["id"]] = {"id": q["id"], "source_sha256": fingerprint(q), "resolved_sha256": fingerprint(resolved),
                "source_policy": _legacy_question_policy(q), "policy": _legacy_question_policy(resolved),
                "basis": {"method": "v3_provisional_policy_migration", "independent_review": False,
                    "limitation": "仅固定原有用途，不提升数学正确性或学科审核等级；解析长度仅作历史来源说明。修改后须核对题义、答案、适用条件和用途。"}}
        target.write_text(json.dumps({"schema": 1, "fingerprint_version": 2, "effective_metadata_bound": True, "tasks": rows}, ensure_ascii=False, indent=2) + "\n")
    if args.record_id:
        if not all((args.purpose, args.evidence_level, args.reviewer, args.reason and args.reason.strip())):
            parser.error("登记须同时提供 --purpose、--evidence-level、--reviewer、--reason")
        questions = json.loads((data / "questions.json").read_text())
        q = next((row for row in questions if row["id"] == args.record_id), None)
        if q is None:
            parser.error("题目尚未进入登记的作者源构建")
        bindings = json.loads((data / "skills/catalog-bindings.json").read_text())["tasks"]
        spec = q.get("answer_spec") or bindings["bank:" + q["id"]].get("answer_spec")
        resolved = resolved_question(q, bindings["bank:" + q["id"]])
        if args.purpose != "quarantine" and grade_answer(resolved, q.get("answer"))["status"] != "correct":
            parser.error("参考答案不满足显式答案契约，不能准入")
        if args.evidence_level == "verified" and (q.get("type") != "numeric" or not q.get("verify") or check_question(q)["status"] != "pass"):
            parser.error("此登记入口的 verified 要求实际数值复算通过；其它说明题须按已核对的解释登记")
        if args.purpose == "screening" and args.evidence_level == "bare":
            parser.error("只有答案的题不能用于诊断筛查")
        if args.evidence_level == "explained" and not q.get("explanation", "").strip():
            parser.error("需要实际解释；不会用文字长度替代语义判断")
        screening = args.purpose == "screening"
        policy = {"diagnosis_eligible": screening, "practice_eligible": args.purpose != "quarantine",
            "may_confirm_mastery": screening and may_confirm_mastery(args.evidence_level),
            "may_judge_weak": screening and may_judge_weak(args.evidence_level),
            "evidence_level": args.evidence_level, "reviewer_pending": True, "confidence": "screening", "reason": "explicit_provisional_use_decision"}
        row = {"id": q["id"], "source_sha256": fingerprint(q), "resolved_sha256": fingerprint(resolved),
            "source_policy": policy, "policy": policy,
            "basis": {"method": "explicit_semantic_use_review", "reviewer": args.reviewer, "independent_review": False,
                "review_note": args.reason, "limitation": "独立教师审核及真实效果仍暂缓；该记录只说明当前版本的有限用途。"}}
        try:
            policies(q, resolved, row)
        except ValueError as exc:
            parser.error(str(exc))
        manifest = json.loads(target.read_text()); old = manifest["tasks"].get(q["id"])
        if old:
            row["previous_decisions"] = [*old.get("previous_decisions", []), {key: value for key, value in old.items() if key != "previous_decisions"}]
        manifest["tasks"][q["id"]] = row
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    problems = check(data)
    for error in problems:
        print(error)
    print(f"显式准入：{'FAIL' if problems else 'PASS'}；解析措辞不会自动扩大诊断用途")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
