"""记录外部题源检索观察；检索结果不能决定考试范围。

exam_scope 只接受有明确文件依据的 included/excluded，否则保持 unknown。
source_observation 独立记录 found/not_found_in_scanned_sources/source_unavailable。

python tools/set_exam_scope.py                         # 只报告当前可检索来源
python tools/set_exam_scope.py --migrate-legacy         # 只迁移历史推断，不重新检索
python tools/set_exam_scope.py --migrate-legacy --apply # 同步登记作者源与 topics
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import pathlib
import re
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from tools.rebuild_data import load_registry, write_json  # noqa: E402

# 学段 → 真题池。池的大小一起记下来，让「0 命中」有意义（在 10 道题里 0 命中
# 和在 16000 道题里 0 命中，证据强度完全不同）。
POOLS = {
    "primary": [("CMM-Math 小学", "inbox/candidates/cmm_primary.jsonl")],
    "junior": [("CMM-Math 初中", "inbox/candidates/cmm_junior.jsonl"),
               ("上海中考", "inbox/candidates/smse_junior.jsonl")],
    "senior": [("高考真题全量", "inbox/candidates/gaokao_all.jsonl"),
               ("CMM-Math 高中", "inbox/candidates/cmm_senior.jsonl")],
}

# 精确复核模式：对「术语命中 > 0 但没映射入库」的点，用**题型特征**再查一次。
#
# 为什么需要：术语命中会骗人。例如「有理数的乘法」用「乘法」一词在初中池命中
# 75 道，但精确模式（有理数 + 乘号/积）只有 6 道且全是「差倒数」「相反数」这类
# 假阳性——CMM 初中池实际上没有有理数乘法题。宽泛词命中的是别的内容。
#
# 只有精确模式也命中，才算「真题考到但题源没供上」（source_gap）；
# 精确模式 0 命中只记录当前检索未发现，不能推断考试是否考查。
PRECISE = {
    "m7a.2.2.1": (["有理数", r"\(-\d", r"负数.{0,6}正数"], [r"×", r"\\times", r"积的"]),
    "m8a.14.2": ([r"全等|≌|cong"], [r"SSS|SAS|ASA|AAS|HL|判定.{0,6}全等"]),
    "m9b.31.2.2": ([r"相似|∽|sim"], [r"判定.{0,8}相似|两角.{0,10}对应相等|两边.{0,10}成比例"]),
    "m9a.27.3": ([r"反比例"], [r"实际|应用|压强|电流|路程"]),
    # 向量减法：必须出现「减法」这个**主题词**。
    # 曾用 `\vec{..}-\vec{..}` 做模式，结果命中 8 道全是假阳性——
    # 「2a-b」「(a+c)∥b」是数乘/加法/平行的混合运算，不是减法知识点。
    "mh2.6.2.2": ([r"vec|overrightarrow|向量"],
                  [r"向量的减法", r"向量.{0,4}减法", r"减去.{0,8}向量",
                   r"相反向量"]),
}


def stage_of(topic: dict) -> str:
    g = topic.get("grade", 0)
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def longest_terms(name: str) -> list[str]:
    """取知识点名里最长的名词短语作为检索词。

    不能拆词：「平面与平面平行」拆成「平面」「平行」后，「平面」会命中
    1546 道完全无关的题，产生检索假阳性。
    """
    parts = [p for p in re.split(r"[、（）()，,的与及\s]+", name) if len(p) >= 2]
    ranked = sorted(parts, key=len, reverse=True)
    return list(dict.fromkeys(ranked[:1] + [name]))


def load_pool_texts(rel: str) -> list[str]:
    path = ROOT / rel
    if not path.exists():
        return []
    texts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        blob = row.get("prompt", "") + " " + " ".join(
            c.get("text", "") for c in (row.get("choices") or []))
        texts.append(re.sub(r"\s+", " ", blob))
    return texts


TOPIC_FIELDS = ("exam_scope", "exam_scope_evidence", "source_observation", "source_observation_evidence")
LEGACY_SCOPES = {"examined", "source_gap", "textbook_only"}


def unknown_scope() -> dict:
    return {"method": "not_established", "reason":
            "题源检索与题目关联不构成正式考试范围依据；没有可定位的范围文件时保持未知。"}


def migrate_legacy_topic(topic: dict) -> dict:
    """保留旧标签及完整旧记录，将事实观察与范围结论分开；幂等。"""
    result = copy.deepcopy(topic)
    old = result.get("exam_scope")
    if old not in LEGACY_SCOPES:
        return result
    evidence = copy.deepcopy(result.get("exam_scope_evidence") or {})
    if old in ("examined", "source_gap"):
        status = "found"
    elif any((pool.get("size") or 0) > 0 for pool in evidence.get("pools", {}).values()):
        status = "not_found_in_scanned_sources"
    else:
        status = "source_unavailable"
    observation = {key: copy.deepcopy(evidence[key]) for key in
                   ("sources", "terms", "pools", "total_hits", "precise_hits", "checked_at")
                   if key in evidence}
    observation.update(
        method="legacy_mapped_questions" if old == "examined" else "legacy_full_term_search",
        reason="保留历史题源关联或检索结果；命中只表示发现候选，未检出也不证明不考。",
        legacy_exam_scope=old, legacy_exam_scope_evidence=evidence,
    )
    result.update(exam_scope="unknown", exam_scope_evidence=unknown_scope(),
                  source_observation=status, source_observation_evidence=observation)
    return result


def observe_topic(topic: dict, sources: set[str], pools: list[tuple[str, list[str]]],
                  checked_at: str | None = None) -> dict:
    """记录一次题源检索，不自动赋予 included/excluded 考试范围。"""
    result = migrate_legacy_topic(topic)
    old_scope_evidence = result.get("exam_scope_evidence") or {}
    explicit_scope = (result.get("exam_scope") in ("included", "excluded")
                      and old_scope_evidence.get("method") == "explicit_scope"
                      and old_scope_evidence.get("source_url")
                      and old_scope_evidence.get("scope_reference"))
    if not explicit_scope:
        result.update(exam_scope="unknown", exam_scope_evidence=unknown_scope())
    observation = {k: copy.deepcopy(v) for k, v in result.get("source_observation_evidence", {}).items()
                   if k in ("legacy_exam_scope", "legacy_exam_scope_evidence")}
    observation["checked_at"] = checked_at or date.today().isoformat()
    if sources:
        status = "found"
        observation.update(method="mapped_questions", sources=sorted(sources),
                           reason="已有外部题条目关联此主题；映射准确性与正式考试范围须分别核对。")
    else:
        terms = longest_terms(topic["name"])
        per_pool, total_hits = {}, 0
        for pool_name, texts in pools:
            hits = sum(1 for text in texts if any(term in text for term in terms)) if texts else None
            per_pool[pool_name] = {"size": len(texts), "hits": hits}
            total_hits += hits or 0
        observation.update(method="full_term_search", terms=terms, pools=per_pool,
                           total_hits=total_hits)
        selected_hits = total_hits
        if topic["id"] in PRECISE and total_hits:
            must, any_patterns = PRECISE[topic["id"]]
            selected_hits = sum(1 for _, texts in pools for text in texts
                                if all(re.search(pattern, text) for pattern in must)
                                and any(re.search(pattern, text) for pattern in any_patterns))
            observation["precise_hits"] = selected_hits
            observation["method"] = "term_and_pattern_search"
        if not any(texts for _, texts in pools):
            status = "source_unavailable"
            observation["reason"] = "当前未提供可检索题源内容（缺失或为空）；不能作未命中或考试范围判断。"
        elif selected_hits:
            status = "found"
            observation["reason"] = "当前题源检索发现候选；这不是目标精确映射或考试范围证明。"
        else:
            status = "not_found_in_scanned_sources"
            observation["reason"] = "在已登记题源及本次检索方式下未发现候选；不能推断该知识不考。"
    result.update(source_observation=status, source_observation_evidence=observation)
    return result


def write_topic_fields(topics: list[dict], data_dir: pathlib.Path = DATA) -> None:
    """只写本工具拥有的四个字段，并同步显式作者源；写前先验证完整归属。"""
    index = {topic["id"]: topic for topic in topics}
    if len(index) != len(topics):
        raise ValueError("重复主题 ID，不能迁移")
    sources, seen = [], set()
    for entry in load_registry(data_dir)["content_sources"]:
        path = data_dir / entry["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        for tid, source_topic in payload["topics"].items():
            if tid not in index or tid in seen:
                raise ValueError(f"主题归属无效：{tid}")
            seen.add(tid)
            for key in TOPIC_FIELDS:
                if key in index[tid]:
                    source_topic[key] = copy.deepcopy(index[tid][key])
        sources.append((path, payload))
    if seen != set(index):
        raise ValueError("登记源与主题集合不同，不能只迁移部分主题")
    for path, payload in sources:
        write_json(path, payload)
    write_json(data_dir / "topics.json", topics)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写回登记作者源与 topics.json")
    parser.add_argument("--migrate-legacy", action="store_true", help="保留历史检索结果，只迁移旧范围推断")
    parser.add_argument("--show", type=int, default=0, help="打印前 N 条记录")
    args = parser.parse_args()
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    if args.migrate_legacy:
        updated = [migrate_legacy_topic(topic) for topic in topics]
    else:
        ext_path = DATA / "questions_ext.json"
        external = json.loads(ext_path.read_text(encoding="utf-8")) if ext_path.exists() else []
        has_external: dict[str, set[str]] = collections.defaultdict(set)
        for question in external:
            for tid in question.get("topicIds") or [question.get("topicId")]:
                if tid:
                    has_external[tid].add(question.get("source") or "未登记来源")
        pool_cache = {stage: [(name, load_pool_texts(rel)) for name, rel in pools]
                      for stage, pools in POOLS.items()}
        updated = [observe_topic(topic, has_external.get(topic["id"], set()),
                                 pool_cache[stage_of(topic)]) for topic in topics]
    print("考试范围：", dict(collections.Counter(topic.get("exam_scope", "unknown") for topic in updated)))
    print("题源观察：", dict(collections.Counter(topic.get("source_observation", "not_checked") for topic in updated)))
    print("变更主题数：", sum(a != b for a, b in zip(topics, updated)))
    for topic in updated[:max(0, args.show)]:
        print(topic["id"], topic["name"], topic.get("source_observation"),
              (topic.get("source_observation_evidence") or {}).get("reason"))
    if args.apply:
        write_topic_fields(updated)
        print("已同步登记作者源与 topics.json。")
    else:
        print("只报告；--apply 同步登记作者源。")


if __name__ == "__main__":
    main()
