"""按版本、模式和曝光背景统计匿名作答；不据浏览器记录推断学生人数或因果效果。"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


def report(exports):
    sessions = {}
    for document in exports:
        if document.get("schema") != 1 or not isinstance(document.get("sessions"), list):
            raise ValueError("输入须为 /api/export 的 schema=1 学习记录")
        for session in document["sessions"]:
            sid = session.get("id")
            if not isinstance(sid, str) or not sid:
                raise ValueError("会话缺少稳定 ID")
            previous = sessions.get(sid)
            if previous and previous.get("content_sha256") != session.get("content_sha256"):
                raise ValueError("同一会话出现不同内容版本，需先核对原始记录")
            # 重复导出只保留作答更完整的快照，不能当新增观察数。
            if not previous or len(session.get("answers", [])) >= len(previous.get("answers", [])):
                sessions[sid] = session
    groups = {}
    for session in sessions.values():
        context = session.get("context", {})
        exposure = ("material_requested" if context.get("material_requested") else
                    "reported_only" if context.get("reported_instruction_seen") else "unobserved")
        interval = context.get("actual_delay_seconds")
        delay = ("unknown" if interval is None else "at_least_24h" if interval >= 86400 else "less_than_24h")
        scope = session.get("unit_id") or ",".join(sorted(session.get("targets", [])))
        key = (session.get("kind"), scope, session.get("mode"),
               session.get("content_sha256"), session.get("grader_sha256"),
               exposure, delay, bool(context.get("repeated_question_ids")))
        if key not in groups:
            groups[key] = {
                "kind": key[0], "scope": scope, "mode": key[2], "content_sha256": key[3],
                "grader_sha256": key[4], "exposure": exposure, "delay_window": delay,
                "contains_repeated_items": key[7], "sessions": 0, "completed_sessions": 0,
                "answers_by_status": Counter(), "items": {},
            }
        group = groups[key]
        group["sessions"] += 1
        group["completed_sessions"] += bool(session.get("completed_at"))
        seen = set()
        for answer in session.get("answers", []):
            qid = answer.get("question_id")
            if qid in seen:
                raise ValueError("同一会话含重复题目提交，不能直接统计")
            seen.add(qid)
            status = answer.get("status", "feedback_withheld")
            group["answers_by_status"][status] += 1
            item_key = (qid, answer.get("content_sha256"))
            item = group["items"].setdefault(item_key, {
                "question_id": qid, "content_sha256": item_key[1], "correct": 0, "wrong": 0,
                "needs_review": 0, "not_attempted": 0, "feedback_withheld": 0,
            })
            if status in item:
                item[status] += 1
    output_groups = []
    for group in groups.values():
        group["answers_by_status"] = dict(group["answers_by_status"])
        items = []
        for item in group["items"].values():
            denominator = item["correct"] + item["wrong"]
            item["scored_attempts"] = denominator
            item["observed_correct_rate"] = item["correct"] / denominator if denominator else None
            items.append(item)
        group["items"] = sorted(items, key=lambda item: (str(item["question_id"]), str(item["content_sha256"])))
        output_groups.append(group)
    return {
        "schema": 1, "status": "descriptive_logs_only" if sessions else "pending_data",
        "unique_sessions": len(sessions), "verified_students": None,
        "student_identity_and_study_provenance": "not_verified",
        "educational_effect": "pending", "difficulty_calibration": "pending_independent_study",
        "interpretation": [
            "匿名浏览器或会话数不等于学生人数；测试和演示记录可能包含在导出中。",
            "正确比例的分母只含可自动判分的有效作答；跳过、未反馈与过程题分别列出。",
            "不同内容/判分版本、任务模式、材料曝光、复测间隔和重复题分组统计。",
            "题号不同、间隔超过一天或请求过讲解，均不能证明独立作答、读完或教学因果效果。",
            "真实试点需另行登记样本来源、实施条件、教师量规评分和缺失情况，再解释结果。",
        ],
        "groups": output_groups,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("build/pilot-report.json"))
    args = parser.parse_args()
    try:
        data = report([json.loads(path.read_text(encoding="utf-8")) for path in args.input])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"无法生成报告：{exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{data['status']}: {data['unique_sessions']} 个去重会话；真实学生人数与教学效果待核实。")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
