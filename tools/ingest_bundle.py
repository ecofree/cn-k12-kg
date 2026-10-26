"""接收外部回传包（Astra 式），按"证据分层"落盘。

与 `tools/ingest.py` 的分工：
- `ingest.py` 处理手工填写的 inbox CSV（课标原文、复核结论）；
- 本工具处理完整回传包（含 audit/ 证据、真实 KC 清单、目录提案），
  把每类数据放到它在项目里的**正确层级**，不做越权修改。

分层规则：

| 输入 | 落到哪 | 能不能影响诊断 |
|---|---|---|
| 复核结论 review.csv | data/reviews.json（附 reviewer_type） | 不能，只作审计记录 |
| KC 候选 kc_map.csv | data/external/kc_candidates.json，status=candidate | 不能，只报覆盖率 |
| 课标出处 standards.csv + standards_index.json | data/external/standards_sources.json | 不能，verbatim 保持 false |
| 目录提案 outline_proposals.json | data/external/outline_proposals/ | 不能，需人工确认 |
| 真实作答 question_stats.csv | data/external/response_stats.json | 空表就记空，不伪造 |

**永不自动做**：改题目难度、改先修边 hard/soft、把 verbatim 改成 true、
把空映射填成猜测、把 skip 当 pass。

    python tools/ingest_bundle.py --bundle <解压目录> --label astra_2026-10-24
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EXTERNAL = DATA / "external"


def read_csv(path: pathlib.Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: pathlib.Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, help="回传包解压目录")
    ap.add_argument("--label", default="astra", help="这批证据的标签，用于目录命名")
    args = ap.parse_args()

    bundle = pathlib.Path(args.bundle).resolve()
    if not (bundle / "inbox").is_dir():
        print(f"不像是回传包：{bundle} 缺少 inbox/")
        return 2

    out = EXTERNAL / args.label
    out.mkdir(parents=True, exist_ok=True)
    topics = {t["id"]: t for t in load_json(DATA / "topics.json", [])}
    questions = {q["id"]: q for q in load_json(DATA / "questions.json", [])}

    manifest = {"label": args.label, "source_dir": str(bundle), "files": {}, "counts": {}}

    # 1) 复核结论：只入审计记录，附身份
    review_rows = read_csv(bundle / "inbox" / "review.csv") or []
    meta = load_json(bundle / "audit" / "review_metadata.json", {}) or {}
    by_id = {r["question_id"]: r for r in meta.get("records", [])}
    reviews = {}
    for row in review_rows:
        qid = (row.get("question_id") or "").strip()
        verdict = (row.get("verdict") or "").strip().lower()
        if not qid or verdict not in ("pass", "fail", "skip"):
            continue
        rec = by_id.get(qid, {})
        reviews[qid] = {
            "verdict": verdict,
            "note": (row.get("note") or "").strip(),
            "reviewer_type": "ai",
            "teacher_reviewed": False,
            "date": "2026-10-24",
            "source": args.label,
            "issue_codes": rec.get("issue_codes", []),
            "evidence": rec.get("evidence", ""),
        }
    # 只做合并，不覆盖：空表（对方还没填 verdict）时绝不能把已有复核记录清掉
    reviews_file = DATA / "reviews.json"
    existing = load_json(reviews_file, {}) or {}
    if reviews:
        existing.update(reviews)
        reviews_file.write_text(
            json.dumps(existing, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    else:
        print("  复核表为空，保留已有 reviews.json 不变")
    manifest["counts"]["reviews"] = {
        "total": len(reviews),
        **{v: sum(1 for r in reviews.values() if r["verdict"] == v)
           for v in ("pass", "fail", "skip")},
    }

    # 2) KC：未审定的是 candidate；带回 adjudication 的抽检结果按结论落状态
    kc_path = bundle / "inbox" / "kc_map.csv"
    if not kc_path.exists():
        kc_path = bundle / "inbox" / "kc_review_100.csv"   # 第二轮抽检表
    kc_rows = read_csv(kc_path) or []
    kcs, mapped, missing_topic = [], 0, 0
    adjudicated = {"accept": 0, "reject": 0, "uncertain": 0}
    for row in kc_rows:
        tid = (row.get("topic_id") or "").strip()
        if tid and tid not in topics:
            missing_topic += 1
            tid = ""
        mapped += bool(tid)
        verdict = (row.get("adjudication") or "").strip().lower()
        status = {"accept": "approved", "reject": "rejected",
                  "uncertain": "candidate"}.get(verdict, "candidate")
        if verdict in adjudicated:
            adjudicated[verdict] += 1
        kcs.append({
            "kc_id": row.get("kc_id", "").strip(),
            "kc_name": row.get("kc_name", "").strip(),
            "topic_id": tid,
            "confidence": (row.get("confidence") or "").strip() or None,
            "status": status,
            "reviewed": verdict in adjudicated,
            "relation": (row.get("relation") or "").strip() or None,
            "evidence": (row.get("evidence") or "").strip() or None,
            "note": (row.get("note") or "").strip(),
        })
    covered = {k["topic_id"] for k in kcs if k["topic_id"]}
    (out / "kc_candidates.json").write_text(json.dumps({
        "source": "XES3G5M",
        "status_rule": "全部为 candidate，未经抽检不得用于难度或先修边",
        "total_kc": len(kcs),
        "mapped": mapped,
        "unmapped": len(kcs) - mapped,
        "covered_topics": len(covered),
        "items": kcs,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest["counts"]["kc"] = {"total": len(kcs), "mapped": mapped,
                                "covered_topics": len(covered),
                                "unknown_topic_refs": missing_topic,
                                "adjudicated": adjudicated}
    summary = load_json(bundle / "inbox" / "kc_review_summary.json")
    if summary:
        (out / "kc_review_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 3) 课标出处：只加证据，不动摘要、不设 verbatim
    std_rows = read_csv(bundle / "inbox" / "standards.csv") or []
    index = load_json(bundle / "audit" / "standards_index.json", {}) or {}
    # 键名是 entries，不是 items/records——第一版读错，78 条出处索引全部落空
    idx_items = index.get("entries") or index.get("items") or index.get("records") or []
    idx_by_id = {i.get("id") or i.get("standard_id"): i for i in idx_items if (i.get("id") or i.get("standard_id"))}
    sources = {}
    for row in std_rows:
        sid = (row.get("id") or "").strip()
        if not sid:
            continue
        text = (row.get("verbatim_text") or "").strip() or None
        claimed = (row.get("verbatim") or "").strip().lower() in ("true", "1", "是")
        # 即使对方声称逐字核准，本项目仍先标 False：原文必须自己抽检原页后才能置真
        sources[sid] = {
            "page_or_ref": (row.get("page_or_ref") or "").strip(),
            "verbatim_text": text,
            "verbatim": False,
            "claimed_verbatim": bool(text and claimed),
            "evidence_status": ("verbatim_claimed_pending_spotcheck" if text and claimed
                                else "text_supplied_unverified" if text
                                else "located_not_verbatim"),
            "index": idx_by_id.get(sid, {}),
        }
    claimed_n = sum(1 for v in sources.values() if v["claimed_verbatim"])
    (out / "standards_sources.json").write_text(json.dumps({
        "note": "只记录出处与对方提供的文本，verbatim 一律 false；逐字核准需本项目对原页抽检",
        "total": len(sources),
        "verbatim_true": 0,
        "items": sources,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest["counts"]["standards"] = {"total": len(sources), "verbatim_true": 0,
                                       "verbatim_claimed": claimed_n}

    # 4) 目录提案：原样保存，等人工确认
    props = load_json(bundle / "audit" / "outline_proposals.json", {}) or {}
    (out / "outline_proposals.json").write_text(
        json.dumps(props, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    audit_rows = read_csv(bundle / "audit" / "outline_audit.csv") or []
    manifest["counts"]["outline"] = {
        "audit_rows": len(audit_rows),
        "proposal_keys": list(props)[:8],
    }

    # 5) 真实作答：空就是空
    stats = read_csv(bundle / "inbox" / "question_stats.csv") or []
    (out / "response_stats.json").write_text(json.dumps({
        "rows": stats,
        "status": "empty" if not stats else "provided",
        "note": "没有本项目题目的真实作答事件就不填；外部 KC 正确率不能改贴本地题号",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest["counts"]["response_stats"] = len(stats)

    # 6) 原始证据留档 + 哈希
    keep = ["README.md", "report.html", "audit/review_metadata.json",
            "audit/question_correction_proposals.json", "audit/kc_mapping_evidence.json",
            "audit/xes_kc_catalog.json", "audit/standards_index.json",
            "audit/standards_corrections.json", "audit/outline_audit.csv",
            "audit/required_inputs.json", "audit/integration_tasks.json",
            "audit/validation_result.json"]
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    for rel in keep:
        src = bundle / rel
        if src.is_file():
            dst = raw / rel.replace("/", "__")
            shutil.copy2(src, dst)
            manifest["files"][rel] = sha256(src)

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"已接收 {args.label}")
    print(f"  复核：{manifest['counts']['reviews']}")
    print(f"  KC  ：{manifest['counts']['kc']}")
    print(f"  课标：{manifest['counts']['standards']}")
    print(f"  目录：{manifest['counts']['outline']['audit_rows']} 行审计")
    print(f"  作答：{manifest['counts']['response_stats']} 行")
    print(f"  落到：{out.relative_to(ROOT)}")
    print("未改：题目难度、先修边、verbatim 状态")
    return 0


if __name__ == "__main__":
    sys.exit(main())
