"""从知识点的 description 生成「记忆检验题」。

**为什么需要这个**：外部真题考的是「用公式解题」，不是「记不记得公式」。
用户说的痛点恰恰是后者——「很多学生考试根本不记得」。

外部题库里几乎没有这种题：
- 高考卷 5335 道里，「下列说法正确的是」这类反向判断题极少
- 中考卷 960 道同理

所以记忆型知识点的题必须**自己生成**。

**生成原理**：description 字段已经把知识点内容写清楚了，例如
- 「平行线的判定」→ 「同位角相等、内错角相等、同旁内角互补，都可判定两条直线平行」
- 「不等式的性质」→ 「两边乘同一个负数方向必须改变」

据此生成四种问法：
1. **正向填空**——「平行线的判定方法有哪些？」
2. **反向判断**——给出若干条说法，正确的是哪条？（这是检验记忆的主力题型）
3. **易错辨析**——针对 description 里明确提到的易错点（如「SSA 不能判定」）
4. **公式补全**——给出公式的一部分，问另一部分

**关键约束**：
- 反向判断题的干扰项必须**明确为错**，不能靠模糊。
  所以干扰项用「张冠李戴」的方式生成：把别的知识点的内容安到这个知识点上。
- 答案唯一，且每个选项都要能说清为什么对/错。
- 生成后必须过 `verify_questions.py` 与公式闸门。
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOPICS = ROOT / "data" / "topics.json"

# 记忆型知识点的判定：名称里含这些词，且 description 非空
MEMORY_KEY = ("公式", "定理", "性质", "法则", "概念", "定义", "原理",
              "规律", "关系", "判定", "证明", "公理")

# 从 description 里切出「可作为陈述的条款」
SPLIT_RE = re.compile(r"[；;]")
# 条款里的否定词——反向判断题要利用它
NEG = ("不能", "不", "非", "无", "未")


def clauses(desc: str) -> list[str]:
    """把 description 切成可独立成陈述的条款。"""
    if not desc:
        return []
    parts = [p.strip(" 　。") for p in SPLIT_RE.split(desc)]
    return [p for p in parts if len(p) >= 6]


def is_memory_topic(t: dict) -> bool:
    """记忆型：名称含公式/定理/性质等。

    这类点最需要「记住公式」的检验题，是生成题的首要目标。
    """
    return (any(k in t["name"] for k in MEMORY_KEY)
            and len(clauses(t.get("description") or "")) >= 1)


def has_clauses(t: dict) -> bool:
    """任何有可用条款的知识点都能生成反向判断题。

    理解型知识点同样受益——「十位读几十个位读几」这种规则，
    考试时也常以「下列说法正确的是」的形式考。
    """
    return len(clauses(t.get("description") or "")) >= 1


def build_reverse_question(topic: dict, others: list[dict]) -> dict | None:
    """生成反向判断题。

    **干扰项必须是「安在本知识点上会错」的表述**，
    不能直接拿本知识点的其它正确条款当干扰项——
    那道题答的其实是另一个正确的定理，单选就不唯一了。

    两种干扰项来源：
    1. **跨知识点张冠李戴**（主力）：别的知识点的真实内容，
       放到本题语境下就是错的。解析能说清「它属于哪个知识点」。
    2. **同知识点内的条件错置**：本知识点其它条款的**条件被改掉**，
       例如「垂径定理」写成「垂直于弦的直径平分弦所对的圆周角」——
       这是最贴近真实错误的干扰项，能暴露「记住了但没理解条件」。

    答案位置打散，避免「正确项总在某个位置」的偏斜。
    """
    cs = clauses(topic.get("description") or "")
    if not cs:
        return None
    right = cs[0]

    distractors: list[tuple[str, str]] = []

    # 1) 同知识点内：条件错置（把另一条定理的主语/宾语换掉）
    #    **优先**——这类干扰项最贴近真实错误（记住了但没理解条件），
    #    比如「垂径定理」写成「平分弦所对的圆周角」。
    for c in cs[1:]:
        wrong = distort(c)
        if wrong and wrong != right and not self_contradictory(wrong):
            distractors.append((topic["name"], wrong))
        if len(distractors) >= 2:
            break

    # 2) 跨知识点：别的知识点的真实内容（张冠李戴）
    #    **按题目 id 做种子打散候选池**——否则所有题的干扰项都来自
    #    列表最前面的那几个知识点，学生认得干扰项就能排除。
    import random as _rnd_mod
    seed = _rnd_mod.Random(hash(topic["id"]) & 0xFFFF)
    pool_others = [o for o in others if o["id"] != topic["id"] and clauses(o.get("description") or "")]
    seed.shuffle(pool_others)
    for o in pool_others:
        oc = clauses(o.get("description") or "")
        if oc:
            distractors.append((o["name"], oc[0]))
        if len(distractors) >= 3:
            break

    if len(distractors) < 3:
        return None
    picked = distractors[:3]

    choices = [{"id": "o1", "text": right}]
    for i, (_src, text) in enumerate(picked, start=2):
        choices.append({"id": f"o{i}", "text": text})

    import random
    rnd = random.Random(hash(topic["id"]) & 0xFFFF)
    pos = rnd.randrange(1, 4)
    shuffled = choices[pos:] + choices[:pos]
    for i, c in enumerate(shuffled):
        c["id"] = f"o{i + 1}"
    ans = next(c["id"] for c in shuffled if c["text"] == right)

    others_desc = "、".join(f"{src}：{text}" for src, text in picked)
    return {
        "type": "choice",
        "prompt": f"关于{topic['name']}，下列说法正确的是（　　）",
        "choices": shuffled,
        "answer": ans,
        "explanation": (
            f"{topic['name']}的正确表述是：{right}。"
            f"其余三项分别出自 {others_desc}，不是本题知识点的正确表述。"
        ),
    }


# 条件错置：把条款里的「A 满足 B」改成「A 满足 C」这类易错形态
# 替换必须精确到分句，不能整句替换——
# 早先「k<0 时 y 随 x 增大而减小」→「…增大」那条，
# 作用在一句「k>0 时…增大，k<0 时…减小」上时，
# 结果变成「k>0 时…增大，k<0 时…增大」，前后自相矛盾。
_DISTORT = [
    (r"平分弦所对的两条弧", "平分弦所对的圆周角"),
    (r"等于它所对圆心角的一半", "等于它所对圆心角的两倍"),
    (r"k<0 时 y 随 x 增大而减小", "k<0 时 y 随 x 增大而增大"),
    (r"k>0 时 y 随 x 增大而增大", "k>0 时 y 随 x 增大而减小"),
    (r"则 A⊆B", "则 B⊆A"),
    (r"空集是任何集合的子集", "空集是任何集合的真子集"),
    (r"两个直角边的平方和等于斜边的平方", "两个直角边的和等于斜边的平方"),
    (r"中间项符号随括号内符号而定", "中间项符号恒为正"),
    (r"即 a²\+b²=c²", "即 a²-b²=c²"),
    (r"垂直于弦的直径平分这条弦", "垂直于弦的直径平分这条弧"),
    (r"两个底角相等", "两个底角互补"),
    (r"三边相等、三角都是 60°", "三边相等、三角都是 45°"),
    (r"分式的分子与分母同乘", "分式的分子与分母同加"),
    (r"分子分母没有公因式时叫最简分式", "分子分母有公因式时叫最简分式"),
    (r"同位角相等", "同位角互补"),
    (r"内错角相等", "内错角互补"),
    (r"同旁内角互补", "同旁内角相等"),
    (r"两边乘（除）同一个负数方向必须改变", "两边乘（除）同一个负数方向不变"),
    (r"两边乘（除）同一个正数方向不变", "两边乘（除）同一个正数方向必须改变"),
]

# 改写后如果选项内部自相矛盾，这个干扰项作废。
# 例：「k>0 时…增大，k<0 时…减小」把后半句改掉，
# 若前半句也在同一句里被改成一样的表述，选项就自相矛盾了。
def self_contradictory(text: str) -> bool:
    for frag in ("随 x 增大而增大", "随 x 增大而减小"):
        if text.count(frag) > 1:
            return True
    return False


def distort(clause: str) -> str:
    """把一条正确表述改成错表述。只用预设的、语义明确的替换。"""
    for pat, rep in _DISTORT:
        if re.search(pat, clause):
            return re.sub(pat, rep, clause)
    return ""


def build_fill_question(topic: dict) -> dict | None:
    """生成正向填空题：问这个知识点的内容是什么。"""
    cs = clauses(topic.get("description") or "")
    if not cs:
        return None
    body = cs[0]
    # 把条款变成一个问句：把主语去掉，保留内容
    # 「平行线的判定：同位角相等…」→「同位角相等、内错角相等、同旁内角互补，都可判定两条直线平行」
    content = body
    # 去掉开头的「XXX的XXX：」形式
    content = re.sub(r"^[^：:]{2,12}[：:]\s*", "", content)
    if len(content) < 8:
        return None
    return {
        "type": "short",
        "prompt": f"{topic['name']}的内容是什么？请写出要点。",
        "choices": [],
        "answer": content,
        "explanation": f"{topic['name']}：{topic.get('description', '')}",
    }


def main() -> None:
    topics = json.loads(TOPICS.read_text(encoding="utf-8"))
    mem = [t for t in topics if is_memory_topic(t)]
    print(f"记忆型知识点 {len(mem)} 个（可生成题）")
    out = []
    for t in mem:
        q = build_reverse_question(t, mem)
        if q:
            out.append({"topicId": t["id"], "kind": "reverse", **q})
        q2 = build_fill_question(t)
        if q2:
            out.append({"topicId": t["id"], "kind": "fill", **q2})
    print(f"生成 {len(out)} 道（反向判断 {sum(1 for x in out if x['kind']=='reverse')} / "
          f"正向填空 {sum(1 for x in out if x['kind']=='fill')}）")
    # 抽样
    for x in out[:2]:
        print(f"\n[{x['topicId']}] {x['kind']}")
        print(f"  {x['prompt']}")
        for c in x["choices"]:
            print(f"    {c['id']}. {c['text'][:60]}")
        print(f"  答案 {x['answer']}")
        print(f"  解析 {x['explanation'][:140]}")


if __name__ == "__main__":
    main()
