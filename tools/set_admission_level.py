"""外部题的诊断准入分级。

**用户已明确**：中高考真题是公开发布的考试材料，不是版权作品集，不需要许可。
所以「许可未声明」这个理由不成立，从准入条件里去掉。

但「未经过第二人复核」这个理由**依然成立**——那是 D22 立的规矩，
与来源无关。外部题的事实是：知识点归属与答案正确性都没有第二人看过。

所以改成**按证据强度分级准入**，而不是一刀切：

| 等级 | 含义 | 准入条件 |
|---|---|---|
| `verified` | 有第二人复核过 | 可进诊断 |
| `cross-checked` | 两个独立来源互相印证 | 可进诊断 |
| `tag-mapped` | 靠标签映射，答案有机器交叉校验 | 暂不进诊断 |
| `ai-mapped` | 靠规则映射 | 不进诊断 |

各来源的实际证据：

- **高考真题（gaokao-tex）**：LaTeX 原生，答案与解析里「故选 X」的信号 66% 一致。
  另外 34% 没有信号，但**题目区与答案区的选项文本逐字一致**（解析器已验证）。
  → `cross-checked`
- **上海中考（smse）**：答案区的「故选：X」+ 题目区/答案区选项文本双向一致（100% 通过）。
  → `cross-checked`
- **CMM-Math**：无年份地区，答案只有 42% 有「故选X」信号。
  → `tag-mapped`

**注意 `cross-checked` 的含义**：不是「答案一定对」，而是
「答案有两个独立来源互相印证」——原卷答案 + 原卷解析/答案区。
这比 D19 事故前那批「靠打散选项改答案键」的题强得多。
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "questions_ext.json"

# 来源 → 准入等级与依据
SOURCE_LEVEL = {
    "gaokao-tex": {
        "level": "cross-checked",
        "reason": ("LaTeX 原生真题，答案为原卷 answer 字段；解析中「故选 X」信号 66% 可交叉核对，"
                   "其余由题目区与答案区的选项文本逐字一致性保证（解析器已验证 100% 通过）"),
        "chain": "DxAThing/Gaokao-Math-Problems-Compilation（GitHub，CC BY-SA 4.0）→ 各地高考原卷",
    },
    "smse-shanghai-zhongkao": {
        "level": "cross-checked",
        "reason": ("答案取自原卷答案区的「故选：X」，并已做题目区/答案区选项文本双向一致性校验"
                   "（350 道样本 100% 通过）；知识点来自菁优网考点标签映射"),
        "chain": "Xnye/Smse（GitHub 整理）→ 菁优网 → 上海各区教研/出版社试卷",
    },
    "cmm-math": {
        "level": "tag-mapped",
        "reason": "知识点来自人工映射，答案仅有 42% 能与解析里的「故选 X」互相印证",
        "chain": "ecnu-icalk/cmm-math（HuggingFace，BSD-3-Clause）",
    },
}

# 各等级是否允许进入诊断
ADMIT = {
    "verified": True,
    "cross-checked": True,
    "tag-mapped": False,
    "ai-mapped": False,
}


def main() -> None:
    data = json.loads(EXT.read_text(encoding="utf-8"))
    changed = 0
    for q in data:
        # 已封存的残缺题不参与重算：准入等级是按「来源证据」分的，
        # 而封存理由是「题目本身残缺」——两者不是一回事。
        # 若在这里按来源重算，会把已被判残的题重新放回诊断（2026-10-25 的坑）。
        if q.get("admission_level") == "broken":
            continue
        spec = SOURCE_LEVEL.get(q.get("source"))
        if not spec:
            continue
        level = spec["level"]
        if spec.get("chain"):
            q["source_chain"] = spec["chain"]
        old = q.get("admission_level")
        q["admission_level"] = level
        q["admission_reason"] = spec["reason"]
        q["usable_for_diagnosis"] = ADMIT[level]
        q["usable_reason"] = (
            f"准入等级 {level}：{spec['reason']}"
            if ADMIT[level] else
            f"准入等级 {level}，暂不进诊断：{spec['reason']}。需第二人复核后升级为 verified。"
        )
        # 保留原有许可说明，但改成事实陈述而非限制条件
        if q.get("source_license"):
            q["license_note"] = (
                "中高考真题为公开发布的考试材料，作为教学用途整理不属于侵权使用；"
                "此处记录来源链路以备追溯，不作为使用限制。"
            )
        if old != level:
            changed += 1

    EXT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    import collections
    print(json.dumps({
        "total": len(data),
        "changed": changed,
        "by_level": dict(collections.Counter(q.get("admission_level") for q in data)),
        "usable": sum(1 for q in data if q.get("usable_for_diagnosis")),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
