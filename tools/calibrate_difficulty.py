"""按客观证据校准题目难度（basic / standard / advanced）。

为什么需要：
    现在全库难度几乎为零——小学 4805 道题 100% 标 basic，高中 9568 道也
    100% 标 basic，外部真题 14344 道同样全标 basic。
    这让 difficulty 字段失去意义：诊断引擎的「第 1 问答对后升档确认」
    逻辑（STEP_UP_ON_CORRECT）因此被永久关闭，
    「这道题是基础题还是压轴题」也无从区分。

    难度必须**基于证据**，不能凭空标。这里的证据按可靠性排序：

    1. **卷内题号**（高考/中考真题）—— 最强证据。
       一份卷子内部，题号越靠后难度越高，这是命题的客观事实。
       题号来自原卷的 problem 块顺序，不是推测。

    2. **题型区块**（高考/中考真题）—— 强证据。
       单选题整体比填空题易，填空题比解答题易。

    3. **分值**（上海中考）—— 强证据。
       菁优网标注的题目分值，4 分的选择题与 2 分的填空题难度不同。

    4. **小问数**（高考解答题）—— 中等证据。
       多小问的解答题通常更综合。

    5. **题干长度**（CMM，无卷面结构）—— 弱证据，仅作同知识点内的相对排序。
       注意：长度不是难度本身，长题可能只是叙述长。这里只把它当作
       「在缺少更好信号时的最后 resort」，且必须记录依据。

对**没有卷面信息**的题（CMM 全库、自有生成题），不能假装有难度信息——
它们的 difficulty 保持 basic，并标注 difficulty_basis 说明原因。

用法：
    python tools/calibrate_difficulty.py --dry-run
    python tools/calibrate_difficulty.py --apply
"""
from __future__ import annotations

# Historical CLI is fenced before imports or authoring side effects.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))


import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 小问标记
SUBPART = re.compile(r"(（1）|\(1\)|（Ⅰ）|\(I\)|①|（i）|\\item|（2）|\(2\)|②)")

# 各区块内的**相对位置**分档（0 = 区块第一题，1 = 区块最后一题）。
#
# 为什么用相对位置而不是绝对题号：
#     卷与卷之间题量差异很大——实测高考 774 份卷里，单选题有 4 题的、
#     8 题的、10 题的、13 题的（还有 34 份卷单选超过 12 题）。
#     用绝对题号（比如「第 9 题起算 advanced」）会在 8 题卷上把最后一道
#     简单题判成压轴，在 20 题卷上又全判成基础题。
#
#     命题的客观事实是：**同一份卷子内部，越靠后越难**。
#     所以正确口径是「在这份卷的这个区块里位于百分之几的位置」。
#
# 阈值依据（相对位置 → 难度）：
#     单选：前 40% 基础，中间 35% 标准，后 25% 难
#     填空：前 50% 基础，后 50% 标准（填空题整体比单选难，不设 advanced）
#     解答：前 30% 标准，中间 40% 标准偏难，后 30% 难
RELATIVE_BANDS = {
    "单选": [(0.00, 0.40, "basic"), (0.40, 0.75, "standard"), (0.75, 1.01, "advanced")],
    "填空": [(0.00, 0.50, "basic"), (0.50, 1.01, "standard")],
    "解答": [(0.00, 0.30, "standard"), (0.30, 0.70, "standard"), (0.70, 1.01, "advanced")],
}
# 区块关键词 → 键
SECTION_KEY = (("单选", "单选"), ("填空", "填空"), ("解答", "解答"))


def relative_level(section: str, position: float) -> str:
    """按区块内的相对位置查难度。position ∈ [0,1]。"""
    key = None
    for kw, k in SECTION_KEY:
        if kw in section:
            key = k
            break
    if key is None:
        return "basic"
    for lo, hi, level in RELATIVE_BANDS[key]:
        if lo <= position < hi:
            return level
    return "basic"


# 上海中考：分值越高一般越难（4 分选择 / 3 分 / 2 分填空）
SMSE_POINTS = {
    4: "basic",
    3: "standard",
    2: "standard",
}

# 各学段的分位阈值。**分位只在同学段内比较**——
# 小学题的「长」只是相对小学题长，跨学段用同一阈值没有意义，
# 而且会让小学 advanced 占比冲到 20%（目标 10%）。
PERCENTILE_BANDS = {
    "primary": [(0.00, 0.60, "basic"), (0.60, 0.92, "standard"), (0.92, 1.01, "advanced")],
    "junior": [(0.00, 0.42, "basic"), (0.42, 0.82, "standard"), (0.82, 1.01, "advanced")],
    "senior": [(0.00, 0.34, "basic"), (0.34, 0.78, "standard"), (0.78, 1.01, "advanced")],
}

# 知识点 id → 年级的映射，启动时加载一次
_TOPIC_GRADE: dict[str, int] = {}


def load_topic_grades() -> None:
    global _TOPIC_GRADE
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    _TOPIC_GRADE = {t["id"]: t.get("grade") or 0 for t in topics}


def topic_stage(tid: str) -> str:
    """知识点所属学段。"""
    g = _TOPIC_GRADE.get(tid, 0)
    if g <= 6:
        return "primary"
    if g <= 9:
        return "junior"
    return "senior"


def level_from_percentile(p: float, stage: str) -> str:
    """按学段内的分位查难度。"""
    for lo, hi, level in PERCENTILE_BANDS.get(stage, PERCENTILE_BANDS["senior"]):
        if lo <= p < hi:
            return level
    return "basic"


def count_subparts(prompt: str) -> int:
    """数小问个数。"""
    return len(set(SUBPART.findall(prompt or "")))


def compute_positions(questions: list[dict]) -> dict:
    """算每道高考题在「本卷本区块」里的相对位置。

    返回 {题 id: position}，position ∈ [0,1]，0 是区块首题，1 是区块末题。

    必须按区块分组算，不能按整卷算——卷内题量差异大，
    而且不同区块（单选/填空/解答）难度基准不同。
    """
    groups = collections.defaultdict(list)
    for q in questions:
        meta = q.get("meta") or {}
        pid = meta.get("paper_id") or ""
        sec = str(meta.get("section") or "")
        idx = int(meta.get("index") or 0)
        groups[(pid, sec)].append((idx, q["id"]))

    pos = {}
    for (pid, sec), items in groups.items():
        items.sort(key=lambda x: x[0])
        n = len(items)
        if n == 1:
            pos[items[0][1]] = 0.0
            continue
        for i, (_idx, qid) in enumerate(items):
            pos[qid] = i / (n - 1)
    return pos


def calibrate_gaokao(q: dict, positions: dict) -> tuple[str, str]:
    """高考真题：卷内相对位置 + 题型区块 + 小问数。"""
    meta = q.get("meta") or {}
    section = str(meta.get("section") or "")
    parts = count_subparts(q.get("prompt", ""))

    pos = positions.get(q["id"])
    if pos is None:
        level = "basic"
        basis = "高考真题但卷内位置未知，按基础题处理"
    else:
        level = relative_level(section, pos)
        kind = ("单选" if "单选" in section else
                "填空" if "填空" in section else
                "解答" if "解答" in section else "题目")
        basis = f"高考卷内{kind}题，相对位置 {pos*100:.0f}%"

    # 小问数可以把标准解答题往上提一档
    if parts >= 3 and level == "standard":
        level = "advanced"
        basis += f"，含 {parts} 个小问"
    elif parts >= 2:
        basis += f"，含 {parts} 个小问"

    return level, basis


def calibrate_smse(q: dict) -> tuple[str, str]:
    """上海中考：分值是主要证据。"""
    meta = q.get("meta") or {}
    points = meta.get("points")
    if isinstance(points, (int, float)):
        level = SMSE_POINTS.get(int(points), "basic")
        return level, f"中考卷内分值 {points} 分"
    return "basic", "中考真题但无分值信息，按基础题处理"


def calibrate_cmm(q: dict) -> tuple[str, str]:
    """CMM 无卷面结构时的默认处置：留 basic，注明待定。

    真正的分位 relativize 不在这里做——它在 main() 的 --cmm-relativize
    分支里（按主知识点内题干长度分位），因为那个 pass 需要**全组数据**
    排序后才能给单题定档，单题函数做不到。
    """
    return "basic", "CMM 无卷面结构，难度待定"


def count_steps(verify: str) -> int:
    """数 verify 表达式的运算步数。

    这是自产计算题的真实难度信号——表达式里有几个运算符，
    就要做几次运算。括号内的内容先去掉，避免把 (a-b) 的减号算进去。
    """
    if not verify:
        return 0
    stripped = re.sub(r"\([^()]*\)", "", verify)
    return len(re.findall(r"[+\-*/×÷]", stripped))


def calibrate_self_generated(q: dict) -> tuple[str, str]:
    """自产生成题：按题型 + verify 表达式的运算步数。

    记忆检验题（memory-drill）考的是「记不记得」，难度由选项干扰项决定，
    统一 basic。计算题（calc）的 verify 表达式是机器可验算的，
    运算步数是真实难度信号：一步 basic，两步 standard，三步以上 advanced。
    """
    t = q.get("type")
    src = q.get("source") or ""
    if "memory-drill" in src:
        return "basic", "自产记忆检验题：考识记"
    steps = count_steps(q.get("verify") or "")
    if steps <= 1:
        return "basic", f"自产计算题：{steps} 步运算"
    if steps == 2:
        return "standard", f"自产计算题：{steps} 步运算"
    return "advanced", f"自产计算题：{steps} 步运算"


def calibrate_own_handwritten(q: dict) -> tuple[str, str]:
    """自有手写题：全部 basic——这是**事实**，不是偷懒。

    实测 1077 道自有手写题的性质：
      · 每点 2~4 道（364 个点 2 道、103 个点 3 道、10 个点 4 道）
      · 措辞高度统一（「下列说法正确的是哪一个」「…是什么」）
      · 不含小问，不含多步骤推导
      · 定位是**记忆检验**：每个知识点问「记不记得」

    所以它们就是基础题。之前尝试按题号（q2/q3 更难）或按问法关键词
    推断难度，都站不住：
      · 题号大不等于题难——q3 的说理题内容是「一条线段有几个端点」
      · 关键词法会自相矛盾——「下列说法正确的是哪一个」同时命中
        「直接问」和「需要推理」两组
    与其编一个假的难度阶梯，不如如实标注。难度阶梯由外部真题承担
    （高考题有客观的卷内位置依据）。

    说理题（short）也判 basic：它在诊断里的角色是**入口题**——
    引擎挑最低难度的题作入口，说理题猜测率只有 0.05（四选一是 0.25），
    压猜测率靠它不靠难度档。若标成 standard，51 个点会出现
    「入口题全是四选一」，诊断准确率会掉。
    """
    t = q.get("type")
    qno = q["id"].split("-q")[-1] if "-q" in q.get("id", "") else "1"
    try:
        qno = int(qno)
    except ValueError:
        qno = 1

    if t == "short":
        return "basic", f"手写说理题（第 {qno} 问）：单知识点、无干扰项，猜测率低"
    return "basic", f"手写记忆检验题（第 {qno} 问）：识记或一步运算"


def calibrate(q: dict, positions: dict | None = None) -> tuple[str, str]:
    """返回 (难度, 依据)。"""
    source = q.get("source", "")
    if source == "gaokao-tex":
        return calibrate_gaokao(q, positions or {})
    if source == "smse-shanghai-zhongkao":
        return calibrate_smse(q)
    if source == "cmm-math":
        return calibrate_cmm(q)
    src = source or ""
    if src.startswith("self-generated"):
        return calibrate_self_generated(q)
    return calibrate_own_handwritten(q)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只报告）")
    ap.add_argument("--cmm-relativize", action="store_true",
                    help="对 CMM 按知识点内题干长度分位做相对分级")
    args = ap.parse_args()

    ext_path = DATA / "questions_ext.json"
    own_path = DATA / "questions.json"
    ext = json.loads(ext_path.read_text(encoding="utf-8"))
    own = json.loads(own_path.read_text(encoding="utf-8"))
    load_topic_grades()

    changed_ext = 0
    changed_own = 0
    dist = collections.defaultdict(collections.Counter)

    # 高考题要按卷内相对位置分级，所以先算位置表
    positions = compute_positions([q for q in ext if q.get("source") == "gaokao-tex"])

    for q in own:
        level, basis = calibrate(q, positions)
        if q.get("difficulty") != level:
            changed_own += 1
        q["difficulty"] = level
        q["difficulty_basis"] = basis

    for q in ext:
        # CMM 的默认处置是「留 basic + 难度待定」，但那是有 relativize 时的
        # 起点值。**不带 --cmm-relativize 时不能覆盖已有标签**——否则一次
        # 普通的 --apply 就会把库里 5848 道 CMM 分位标签静默抹平
        # （2026-10-25 实际发生过：小学 standard 1883→48）。
        if q.get("source") == "cmm-math" and not args.cmm_relativize:
            if q.get("difficulty") not in ("basic", "standard", "advanced"):
                q["difficulty"] = "basic"
                q["difficulty_basis"] = "CMM 无卷面结构，难度待定"
            continue
        level, basis = calibrate(q, positions)
        if q.get("difficulty") != level:
            changed_ext += 1
        q["difficulty"] = level
        q["difficulty_basis"] = basis

    # CMM 相对分级：按知识点内题干长度分位。
    # 必须在统计分布**之前**做，否则统计的是覆盖前的旧值。
    #
    # 复合题只用**主知识点**的分位：一题多知识点时，如果按每个关联知识点
    # 各算一次，同一道题会拿到不同难度（实测 2112 道题会这样）。
    # 难度是题目自身的属性，必须唯一，所以以主知识点为准。
    #
    # **分位只在同学段内比较**：小学题的「长」只是相对小学题长，
    # 拿小学的长题去和初中的长题比没有意义。跨学段用同一阈值会让
    # 小学 advanced 占比冲到 20%（目标 10%）。所以每个知识点组
    # 要按它所属的学段选阈值。
    if args.cmm_relativize:
        by_topic = collections.defaultdict(list)
        for q in ext:
            if q.get("source") == "cmm-math":
                primary = q.get("primaryTopicId") or q.get("topicId")
                if primary:
                    by_topic[primary].append(q)
        touched = set()
        for tid, group in by_topic.items():
            if len(group) < 6:
                continue  # 太少不做分位，避免噪声
            group.sort(key=lambda q: len(q.get("prompt", "")))
            n = len(group)
            stage = topic_stage(tid)
            for i, q in enumerate(group):
                p = i / (n - 1) if n > 1 else 0.0
                level = level_from_percentile(p, stage)
                touched.add(q["id"])
                q["difficulty"] = level
                q["difficulty_basis"] = (
                    f"CMM 主知识点内题干长度分位 {p*100:.0f}%"
                    f"（{stage}学段内相对，无卷面结构的弱信号）")
        changed_ext += len(touched)

    # 统计分布（放在所有改判之后）
    for q in own:
        dist["own"][q["difficulty"]] += 1
    for q in ext:
        dist[q.get("source", "?")][q["difficulty"]] += 1

    total = sum(sum(c.values()) for c in dist.values())
    print(f"自有题改判 {changed_own} 道，外部题改判 {changed_ext} 道")
    print(f"\n难度分布（共 {total} 道）：")
    for src, c in dist.items():
        t = sum(c.values())
        parts = "  ".join(f"{k}={c[k]}({c[k]/t*100:.0f}%)"
                          for k in ("basic", "standard", "advanced") if c[k])
        print(f"  {src:<26}{parts}")

    if not args.apply:
        print("\n（试运行，未写入。加 --apply 生效）")
        return

    own_path.write_text(json.dumps(own, ensure_ascii=False, indent=1), encoding="utf-8")
    ext_path.write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入：{own_path.name} {len(own)} 道，{ext_path.name} {len(ext)} 道")


if __name__ == "__main__":
    main()
