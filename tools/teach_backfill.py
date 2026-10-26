"""给第一批 8 张讲解卡补 `why` 与 `when_to_use`。

## 为什么补

第一批卡只有"定义 + 步骤 + 例题"。学生会照着步骤算完这一道，
换一道题仍然不知道"该不该用这招"——因为卡里没有写**识别信号**，
也没有写**为什么这样做**。用户要的"讲清楚、会运用"缺的就是这两块。

补的内容全部落在工具点（一年级加减与 20 以内退位减法）上，
所以 `why` 写的是"这个动作在十进制里的位置"，`when_to_use` 写的是
"题面出现哪些词、算式长什么样时该想起它"。

脚本是幂等的：已有 `why` 的卡不覆盖，只在缺失时写入。
"""
from __future__ import annotations

# Historical CLI is fenced before imports or authoring side effects.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))


import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEACH = ROOT / "data" / "teach"

BACKFILL: dict[str, dict] = {
    "m1a.1.4": {
        "why": "加减法的本质就是「合」与「分」：5 能分成 2 和 3，"
               "所以 2+3=5、5-2=3 是同一件事的三种说法。"
               "把一个数的所有分法练到不用想，后面算加减就不必再数手指。",
        "when_to_use": [
            "题目问「几和几合成几」或「几可以分成几和几」",
            "算 5 以内的加减还要掰手指时，回到分与合重练",
        ],
    },
    "m1a.1.5": {
        "why": "加法不是把两堆从头数一遍，而是「从一个数接着数几格」："
               "3+2 就是从 3 接着数 2 格到 5。接着数比重新数快，也不容易漏。",
        "when_to_use": [
            "题面出现「一共」「合起来」「又来了」",
            "两个部分要合并成一个总数",
        ],
    },
    "m1a.1.6": {
        "why": "减法是加法的反向动作：因为 2+3=5，所以 5-3=2。"
               "用「想加法算减法」比重新数一遍快，而且能自己检查对不对。",
        "when_to_use": [
            "题面出现「还剩」「去掉了」「少了」「吃了」",
            "已知总数和其中一部分，要求另一部分",
        ],
    },
    "m1a.1.7": {
        "why": "0 是一个数量，表示「一个也没有」，不是「没有这个数」。"
               "加 0 等于没添也没减，减 0 等于什么也没拿走，所以结果不变；"
               "两个相同的数相减，正好全部拿走，得 0。",
        "when_to_use": [
            "算式里出现 0（加 0、减 0、0 减 0）",
            "题目问「一个也没有」该用哪个数表示",
        ],
    },
    "m1a.2.10": {
        "why": "10 是十进制里第一个「整十」，凑十法、破十法、进位退位都以它为中转站。"
               "10 的五组组成背到不用想，后面 20 以内的加减才快得起来。",
        "when_to_use": [
            "算式里要凑成 10（9+1、8+2、7+3）",
            "算 10 减几，或学凑十法、破十法之前的热身",
        ],
    },
    "m1a.2.4": {
        "why": "6 和 7 已经超过一只手，数手指容易乱。用数的组成直接「想」出结果，"
               "是把计算从「一个一个数」变成「记住关系」的关键一步。",
        "when_to_use": [
            "算式中出现 6 或 7 的加法、减法",
            "题目问「6 可以分成几和几」「7 可以分成几和几」",
        ],
    },
    "m1a.5.1": {
        "why": "9 离 10 只差 1，所以从另一个数里借 1 给 9，就把「9 加几」"
               "换成了「10 加几」——10 加几不用算，直接看个位就知道。",
        "when_to_use": [
            "看到 9 加一位数，个位一时算不出来",
            "学 8 加几、7 加几时，同样可以用「借到 10」的思路（分别借 2、借 3）",
        ],
    },
    "m1b.2.1": {
        "why": "个位不够减 9，就先从 10 里减 9 得 1，再把个位上的几加回来："
               "等于「先借一整十，再还回多减的那部分」。",
        "when_to_use": [
            "十几减 9、十几减 8 这类个位不够减的退位减法",
            "心算卡住、想不起得数时，用破十法一步步推",
        ],
    },
}


def main() -> int:
    changed, kept = [], []
    for tid, extra in BACKFILL.items():
        path = TEACH / f"{tid}.json"
        if not path.is_file():
            print(f"跳过（没有卡）：{tid}")
            continue
        card = json.loads(path.read_text(encoding="utf-8"))
        if card.get("why") and card.get("when_to_use"):
            kept.append(tid)
            continue
        card.update({k: v for k, v in extra.items() if not card.get(k)})
        path.write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        changed.append(tid)
    print(f"补写 {len(changed)} 张：{'、'.join(changed) or '无'}")
    print(f"已有内容跳过 {len(kept)} 张：{'、'.join(kept) or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
