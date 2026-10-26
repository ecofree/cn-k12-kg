"""把 TAL-SCQ5K-CN 竞赛题导入为「挑战层」。

## 为什么导入，以及为什么不进诊断

用户的原话是「让一般学生见识一下竞赛题的思考维度」。这句话决定了三件事：

1. **要进系统**。放在 build/dl 里当离线对照集，学生看不到，等于没有。
2. **不能进诊断**。竞赛题默认超纲，答错不代表课内知识点没掌握。
   拿它判薄弱会把「没学过奥数」误判成「基础不牢」。
3. **不能硬映射到课时级知识点**。TAL 的知识点是竞赛体系（1503 个叶子），
   和义务教育课标不是一套分类。之前词典抽样 5 例只对 1–2 例，
   硬映射会把错题挂到对的点上，比不映射更糟。

所以这里只做到**簇级映射**（见 cnkg/tiers.py 的 cluster），
置信度如实写 `cluster`，并在每题上标 `for_diagnosis: false`。

## 数据清洗

- 去掉题号前缀（`1. `、`5、`）和来源标签（`【2020五年级卷第33题】`）
- 选项从 `[[{aoVal, content}]]` 摊平成 `[{id, text}]`
- 解析从 list 拼成一段，再按句号切成 `steps`
- 反引号引号 `` `` `` → 中文引号
- 选项少于 2 个的丢弃（6 道）

用法：
    python tools/import_tal.py
    python tools/import_tal.py --json build/tal_import.json
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

DL = ROOT / "build" / "dl"
OUT = ROOT / "data" / "challenge" / "tal.json"

DATASETS = {
    "prime_math_competition_ch_single_choice": "primary",
    "mid_math_competition_ch_single_choice": "junior",
    "high_math_competition_ch_single_choice": "senior",
}

GRADE_RE = re.compile(r"(小学)?([一二三四五六七八九])年级|(初|高)([一二三])")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9}

# 小学的模块标签比路由可靠：路由里混着「能力/思想」，那是考查维度不是知识点。
# 模块只有七八个，正好当挑战层的「板块」。
PRIMARY_MODULE = {
    "计算模块": "calc",
    "应用题模块": "word",
    "行程模块": "word",
    "数论模块": "number",
    "计数模块": "count",
    "组合模块": "count",
    "几何模块": "geo",
}

# 模块缺失时（小学 1541 道、初高中几乎全部），只能从路由和题干里认板块。
# 顺序即优先级：先认具体板块，最后才落 logic（竞赛题里「思想/能力」类最多，
# 它们确实是纯逻辑推理，落 logic 不算兜底失败）。
STRAND_RULES = [
    (r"数论|整除|余数|质数|合数|约数|倍数|位值|进制|同余", "number"),
    (r"计数|组合|排列|容斥|抽屉|染色|图论|握手", "count"),
    (r"行程|工程|浓度|经济|利润|分数应用|分百|比例应用|周期", "word"),
    (r"几何|面积|体积|周长|图形|三角形|圆形|立体|角度", "geo"),
    (r"计算|巧算|速算|估算|数列求和|裂项|换元", "calc"),
    (r"函数|导数|不等式|方程|数列|三角|解析几何|立体几何|向量|复数|概率|统计",
     "formal"),
    (r"逻辑|推理|对应|枚举|调整|构造|最值|思想|能力|综合", "logic"),
]

STRAND_NAMES = {
    "calc": "计算与巧算",
    "word": "应用题与建模",
    "number": "数论与数感",
    "count": "计数与组合",
    "geo": "几何与图形",
    "logic": "逻辑与推理",
    "formal": "代数与函数",
}

NOISE_PREFIX = re.compile(r"^\s*(【[^】]{0,40}】|\d{1,3}\s*[.、．]\s*)")


def stage_of(dataset_name: str) -> str | None:
    for key, st in DATASETS.items():
        if dataset_name.startswith(key):
            return st
    return None


def grade_hint(text: str, stage: str) -> int | None:
    m = GRADE_RE.search(text or "")
    if not m:
        return None
    if m.group(2):
        return CN_NUM.get(m.group(2))
    if m.group(4):
        base = 6 if m.group(3) == "初" else 9
        return base + CN_NUM.get(m.group(4), 0)
    return None


def clean_text(s: str) -> str:
    s = (s or "").replace("``", "“").replace("''", "”")
    s = s.replace("\u3000", " ").strip()
    return s


def strand_of(stage: str, routes: list[str], problem: str) -> str:
    """判板块。竞赛题只分板块，不硬套课时级知识点。"""
    text = " ".join(routes or [])
    if stage == "primary":
        for mod, sid in PRIMARY_MODULE.items():
            if mod in text:
                return sid
    for pat, sid in STRAND_RULES:
        if re.search(pat, text):
            return sid
    for pat, sid in STRAND_RULES:
        if re.search(pat, problem):
            return sid
    return "logic" if stage == "primary" else "formal"


def level_of(diff) -> int:
    try:
        d = int(diff)
    except (TypeError, ValueError):
        return 1
    return 1 if d <= 1 else (2 if d == 2 else 3)


def split_steps(solution: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[。；])", solution) if p.strip()]
    if len(parts) < 2:
        parts = [p.strip() for p in re.split(r"(?<=[，,])", solution) if p.strip()]
    return parts if len(parts) >= 2 else [solution]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="build/tal_import.json")
    args = ap.parse_args()

    items, dropped, per = [], collections.Counter(), collections.Counter()
    for fname in ("TAL_CN_train.jsonl", "TAL_CN_test.jsonl"):
        path = DL / fname
        if not path.exists():
            print(f"缺文件：{path}")
            return 1
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            stage = stage_of(r.get("dataset_name", ""))
            if not stage:
                dropped["未知数据集"] += 1
                continue
            opts = [o for grp in (r.get("answer_option_list") or []) for o in grp]
            opts = [o for o in opts if (o.get("content") or "").strip()]
            if len(opts) < 2:
                dropped["选项不足"] += 1
                continue
            ans = r.get("answer_value")
            if ans not in {o.get("aoVal") for o in opts}:
                dropped["答案不在选项"] += 1
                continue
            problem = NOISE_PREFIX.sub("", clean_text(r.get("problem")))
            solution = clean_text(" ".join(r.get("answer_analysis") or []))
            if len(solution) < 10:
                dropped["无解析"] += 1
                continue
            routes = r.get("knowledge_point_routes") or []
            src = r.get("competition_source_list") or []
            qid = str(r.get("qid"))
            items.append({
                "id": f"tal-{stage[0]}-{qid}",
                "challenge": True,
                "for_diagnosis": False,
                "stage": stage,
                "grade_hint": grade_hint(" ".join(src) + " " + problem, stage),
                "strand": strand_of(stage, routes, problem),
                "level": level_of(r.get("difficulty")),
                "prompt": problem,
                "choices": [{"id": o.get("aoVal"), "text": clean_text(o.get("content"))}
                            for o in opts],
                "answer": ans,
                "solution": solution,
                "steps": split_steps(solution),
                "source_competition": src,
                "route": routes,
                "topic_hint": (routes[0].split("->")[-1] if routes else ""),
                "license": "MIT",
                "source": "TAL-SCQ5K-CN",
            })
            per[stage] += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "name": "TAL-SCQ5K-CN 挑战层",
        "license": "MIT",
        "license_note": "好未来 TAL-SCQ5K-CN，MIT；竞赛题，仅作拓展挑战，不参与诊断判弱",
        "count": len(items),
        "items": items,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"导入 {len(items)} 道挑战题，丢弃 {sum(dropped.values())} 道")
    for k, v in dropped.most_common():
        print(f"  丢弃 {k}: {v}")
    print("按学段：", dict(per))
    print("按簇：")
    byc = collections.Counter((x["stage"], x["strand"]) for x in items)
    for (st, cid), n in sorted(byc.items()):
        print(f"  {st:<8}{cid:<10}{n:4}  {STRAND_NAMES.get(cid, '')}")
    print("解析长度中位数：",
          sorted(len(x["solution"]) for x in items)[len(items) // 2])

    report = {
        "count": len(items),
        "dropped": dict(dropped),
        "by_stage": dict(per),
        "by_strand": {f"{st}/{cid}": n for (st, cid), n in byc.items()},
        "grade_hint_missing": sum(1 for x in items if x["grade_hint"] is None),
    }
    p = ROOT / args.json
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"写入 {OUT}\n写入 {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
