"""题库口径对照：同一个"够不够用"，六种算法六个答案。

这个工具存在的理由：项目里已经出现过一次「0 缺口」其实是口径造成的
（全库题点对 / 诊断池 / 主知识点三套账）。加进证据分级之后，口径又多了一层：
**能不能判薄弱**取决于这道题有没有独立证据，而不只是它进没进诊断池。

所以这里一次性把六种池子并排算出来，谁也别再拿一个数字回答所有问题：

    1 自有              只有 questions.json
    2 全库              自有 + 全部外部题（旧口径，最宽松）
    3 准入              自有 + usable_for_diagnosis（上一版诊断池）
    4 证据              自有 + 准入 ∧ evidence_level ∈ 可诊断（本版）
    5 独立              自有 + 准入 ∧ evidence ∈ {verified, corroborated}
    6 证据-去smse       4 去掉 smse（许可未声明，准备隔离）

指标：
    below      低于该学段题量下限的知识点数
    single     只有单一难度档的点数
    std        有 standard 题的点数
    indep      有**独立证据**题的点击数 —— 只有这些点判"薄弱"才站得住

用法：
    python tools/pool_policy.py
    python tools/pool_policy.py --json build/pool_policy.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cnkg.evidence import is_own  # noqa: E402

DATA = ROOT / "data"

STAGES = ("primary", "junior", "senior")
TARGET = {"primary": 4, "junior": 6, "senior": 8}
INDEP = {"verified", "corroborated"}


def stage_of(topic: dict) -> str:
    g = topic.get("grade")
    if g is None:
        return "?"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def idx(q: dict) -> list[str]:
    return q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])


def assess(topics: list[dict], pool: list[dict]) -> dict:
    per = collections.Counter()
    diff = collections.defaultdict(set)
    judge = collections.Counter()      # 答错能可靠判薄弱的题
    ext_indep = collections.Counter()  # 有外部独立证据的题
    for q in pool:
        lv = q.get("evidence_level")
        own = is_own(q)
        can_judge = bool(q.get("verify")) if own else lv in INDEP
        for t in idx(q):
            per[t] += 1
            diff[t].add(q.get("difficulty"))
            if can_judge:
                judge[t] += 1
            if not own and lv in INDEP:
                ext_indep[t] += 1
    out = {}
    for st in STAGES:
        ts = [t for t in topics if stage_of(t) == st]
        out[st] = {
            "topics": len(ts),
            "pairs": sum(per[t["id"]] for t in ts),
            "below": sum(1 for t in ts if per[t["id"]] < TARGET[st]),
            "single": sum(1 for t in ts if len(diff[t["id"]]) < 2),
            "std": sum(1 for t in ts if "standard" in diff[t["id"]]),
            "judge": sum(1 for t in ts if judge[t["id"]] > 0),
            "ext_indep": sum(1 for t in ts if ext_indep[t["id"]] > 0),
            "zero": sum(1 for t in ts if per[t["id"]] == 0),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="build/pool_policy.json")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))
    adm = [q for q in ext if q.get("usable_for_diagnosis")]
    ev = [q for q in adm if q.get("evidence_level") in
          ("verified", "corroborated", "explained")]
    ev_nosmse = [q for q in ev if q.get("source") != "smse-shanghai-zhongkao"]

    pools = {
        "1_自有": own,
        "2_全库": own + ext,
        "3_准入": own + adm,
        "4_证据": own + ev,
        "5_独立": own + [q for q in ev if q.get("evidence_level") in INDEP],
        "6_证据去smse": own + ev_nosmse,
    }
    result = {}
    print(f"{'池子':<14}{'题量':>7}{'学段':<8}{'题点对':>7}{'不足':>5}"
          f"{'单难':>5}{'std':>5}{'可判':>5}{'外证':>5}{'零题':>5}")
    for name, pool in pools.items():
        a = assess(topics, pool)
        result[name] = {"size": len(pool), **a}
        for st in STAGES:
            x = a[st]
            print(f"{name:<14}{len(pool):>7}{st:<8}{x['pairs']:>7}{x['below']:>5}"
                  f"{x['single']:>5}{x['std']:>5}{x['judge']:>5}{x['ext_indep']:>5}{x['zero']:>5}")
        print()

    path = ROOT / args.json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
