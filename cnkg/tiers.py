"""知识点分层：把 477 个点分成「核心点」和「工具点」，并归到教学簇。

## 为什么必须分层

项目初衷是「每个知识点讲清楚，配够题，让学生在做的过程中学会用」。
但 477 个点不是同一种东西：

- 一年级「1～5 的认识」「分与合」「9 加几」这类点，学会的标准是**能自动执行**，
  一句话说清、两三道题验证就够。给它们写四步讲解、配七道题，
  学生不会因此更懂，只会把时间从「用」挪到「练手势」上。
- 五年级「组合图形的面积」、六年级「百分数的应用」、八年级「实际问题与一次函数」
  这类点，学会的标准是**能选策略、能建模、能多步推进**，必须讲透、必须配变式。

同一个「讲解卡 ≥4 题」的标准套在两类点上，结果是：基础点过度教学，
核心点题量反而不够。所以先分层，再按层定验收标准。

## 两条判据

1. **学段**：小学 1–2 年级整册都是工具点（20 以内加减、乘法口诀、除法意义）。
   这不是贬低它们，而是它们的教学目标是「熟练」，不是「理解应用」。
2. **点本身的性质**：应用题 / 建模 / 推理，或名称落在「运算律、混合运算、面积、
   体积、分数、小数、百分数、比例、方程、函数、统计、概率」这些**可迁移技能**上，
   算核心点；名称落在「认识、读写、数数、比大小、口诀、口算、笔算、单位」上的，
   算工具点。

初中高中反过来：默认是核心点，只有纯识记（正数和负数、科学记数法、普查抽样）
才降为工具点。

## 簇（cluster）

工具点不单独写卡，按簇合并成一张「速查卡」；核心点每个一张完整卡。
簇 id 同时是挑战题挂靠的单位——竞赛题不硬映射到课时级知识点，
只挂到簇，映射置信度标 `cluster`，见 tools/tal_link.py。
"""
from __future__ import annotations

import json
import pathlib
import re

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

STAGE_TARGET = {"primary": 4, "junior": 6, "senior": 8}
TOOL_TARGET = {"primary": 1, "junior": 2, "senior": 2}

# ------------------------------------------------------------------ 判据

# 「可迁移技能」关键词：命中即核心（除非同时命中工具词，见下）
CORE_NAME = re.compile(
    "解决问题|实际问题|问题解决|应用|模型|运算定律|运算性质|运算律|混合运算|简算|"
    "估算|估测|规律|面积|体积|表面积|容积|周长|统计|概率|可能性|方程|函数|不等式|"
    "比例|百分数|百分|分数|小数|圆|扇形|三角形|四边形|平行四边形|梯形|圆柱|圆锥|"
    "角平分线|内角|外角|相似|全等|勾股|导数|数列|向量|复数|计数原理|分布|回归"
)

# 「单一执行技能」关键词：命中即工具（优先级高于 CORE_NAME，用于化解
# 「分数的简单计算」这种既含核心词又含工具词的命名）
TOOL_NAME = re.compile(
    "认识|再认识|的组成|组成|读法|写法|数数|比大小|大小比较|第几|口诀|口算|笔算|"
    "竖式|分与合|初步|简单的|分类|整理|数位|计数单位|算盘|量一量|厘米|毫米|分米|"
    "千米|克|千克|吨|人民币|单位换算|进率|面积单位|展开|三视图|普查|抽样|科学记数法|"
    "正数和负数|绝对值|近似数|四舍五入"
)

# ------------------------------------------------------------------ 簇规则
# 顺序即优先级：先匹配到的赢。
# (正则, 簇 id)
PRIMARY_RULES = [
    # 「观察物体 / 积木 / 展开图」必须排在体积之前：
    # 「观察简单的立体积木」里有「体积」两个字，但它是空间观察，不是体积计算。
    ("观察|积木|展开|三视图", "p-shape-recog"),
    ("解决问题|实际问题|问题解决|应用|估算|估测|单价|速度|路程|模型", "p-word"),
    ("混合运算|运算定律|运算律|运算性质|两级运算|同级运算|小括号|简算|规律|乘加|乘减|用字母表示", "p-ops"),
    ("百分数|百分|比和比例|比例|按比例|折扣|利率|税率", "p-pct-ratio"),
    ("体积|容积|表面积|圆柱|圆锥|长方体|正方体", "p-volume"),
    ("面积|周长", "p-perim-area"),
    ("分数|小数|几分之|分之一|除以整数|倒数", "p-frac-dec"),
    ("厘米|毫米|分米|千米|米|克|千克|吨|人民币|元、角、分|单位|进率|时间|年月日|时分秒", "p-measure"),
    # 「扇形」只用来指扇形统计图，所以统计规则里留「统计」，形状规则里留「扇形/圆」
    ("统计|条形|折线|数据|平均数|众数|中位数|可能性|达标线|分类|整理", "p-stat"),
    ("位置|方向|数对", "p-position"),
    ("图形|三角形|四边形|多边形|线段|射线|直线|角|平行|垂直|圆|扇形|立体|对称|平移|旋转|"
     "运动现象|展开|图案|形状|拼", "p-shape-recog"),
    ("乘法|除法|口诀|倍|有余数|平均分", "p-mul-div"),
    ("加法|减法|加减|加、减|凑十|破十|分与合|连加|连减|加几|减几|十几减", "p-add-sub"),
    ("认识|数数|组成|读法|写法|读写|比大小|第几|数位|计数单位|算盘|近似数|负数|数的顺序|大得多|大一些", "p-num-recog"),
    ("广角|推理|搭配|集合|优化|鸡兔同笼|植树|找次品|数与形", "p-reasoning"),
]

JUNIOR_RULES = [
    ("函数|一次函数|二次函数|反比例", "j-function"),
    ("方程|不等式|方程组", "j-equation"),
    ("三角形|全等|勾股|等腰|角平分|内角|外角|相似|锐角三角函数", "j-triangle"),
    ("四边形|平行四边形|矩形|菱形|正方形|梯形", "j-quad"),
    ("圆|扇形|弧长|切线|正多边形", "j-circle"),
    ("坐标|位置|平移|旋转|轴对称|中心对称|投影|视图", "j-transform"),
    ("统计|概率|频率|抽样|数据|方差|平均数|中位数|众数", "j-stat"),
    ("式|整式|分式|因式|根式|幂|代数式|乘法公式", "j-algebra"),
    ("数|有理数|实数|科学记数法|绝对值|平方根|立方根", "j-number"),
]

SENIOR_RULES = [
    ("导数|函数|零点|单调性|极值|最值|幂函数|指数|对数", "s-function"),
    ("三角|正弦|余弦|正切|解三角形", "s-trig"),
    ("数列|归纳法|等差|等比", "s-sequence"),
    ("不等式|基本不等式|线性规划", "s-ineq"),
    ("立体几何|空间向量|棱柱|棱锥|棱台|球|平面|直线与平面", "s-solid"),
    ("解析几何|椭圆|双曲线|抛物线|直线与圆|圆的方程|曲线", "s-analytic"),
    ("概率|统计|抽样|分布|回归|独立性|列联表|二项式分布|超几何", "s-prob"),
    ("向量|复数|解三角形", "s-vector"),
    ("计数|排列|组合|二项式定理", "s-count"),
    ("集合|逻辑|量词|充要条件", "s-logic"),
]

CLUSTER_NAMES = {
    "p-num-recog": "数的认识（读数、写数、比大小、数位）",
    "p-add-sub": "整数加减法（含凑十、破十、连加连减）",
    "p-mul-div": "整数乘除法（含口诀、倍数、有余数）",
    "p-ops": "混合运算与运算律（含简算、估算策略）",
    "p-frac-dec": "分数与小数的意义及运算",
    "p-pct-ratio": "百分数、比与比例（含折扣利率）",
    "p-measure": "常见的量（长度、质量、人民币、时间及换算）",
    "p-shape-recog": "图形的认识与测量（含观察物体、对称平移）",
    "p-perim-area": "周长与面积（含组合图形）",
    "p-volume": "体积与表面积（含圆柱圆锥）",
    "p-stat": "统计与可能性",
    "p-position": "位置与方向",
    "p-reasoning": "数学广角与推理（搭配、优化、鸡兔同笼）",
    "p-word": "应用题与建模（一步到多步、单价速度模型）",
    "j-number": "有理数与实数",
    "j-algebra": "代数式与整式分式",
    "j-equation": "方程与不等式",
    "j-function": "函数（一次、二次、反比例）",
    "j-triangle": "三角形与全等相似",
    "j-quad": "四边形",
    "j-circle": "圆",
    "j-transform": "图形变换与坐标",
    "j-stat": "统计与概率",
    "s-logic": "集合与常用逻辑",
    "s-function": "函数与导数",
    "s-trig": "三角函数与解三角形",
    "s-sequence": "数列",
    "s-ineq": "不等式",
    "s-solid": "立体几何与空间向量",
    "s-analytic": "解析几何",
    "s-prob": "概率与统计",
    "s-vector": "平面向量与复数",
    "s-count": "计数原理",
}


def stage_of(topic: dict) -> str:
    g = topic.get("grade")
    if g is None:
        return "?"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


# 规则覆盖：规则只看名字和类型，必然有误判。这里逐条人工改，并写明理由。
# 原则仍是「这个点学会的标志是熟练执行，还是能选策略、能迁移」。
OVERRIDES = {
    # 名字里带「三角形/平行四边形」但实际是「认识图形」，不是计算
    ("三角形", 4): ("tool", "认识三角形，不是解三角形"),
    ("平行四边形和梯形", 4): ("tool", "图形认识，面积在五年级单独成点"),
    # 观察物体系列：空间想象的第一步，做两题建立经验即可
    ("根据直观图猜测积木形状", 3): ("tool", "观察物体的单点技能"),
    ("长方体纸盒的展开", 3): ("tool", "展开图识别，属经验积累"),
    ("观察简单组合体", 5): ("tool", "三视图前的观察经验"),
    ("用圆规比较线段的长短", 3): ("tool", "圆规使用技能"),
    ("设计图案", 5): ("tool", "操作性任务，不承载新概念"),
    ("利用圆设计图案", 6): ("tool", "操作性任务，不承载新概念"),
    # 反例：名字里有「简单的」，但三角恒等变换是高中核心工具链
    ("简单的三角恒等变换", 10): ("core", "恒等变换是三角求值/证明的主干"),
    # 大小比较是单一技能，讲一次就够
    ("简单的分数大小比较", 3): ("tool", "同分母/同分子比较，规则单一"),
    ("小数的大小比较", 3): ("tool", "对齐数位比较，规则单一"),
}


def override_of(topic: dict):
    return OVERRIDES.get((topic.get("name"), topic.get("grade")))


def classify(topic: dict) -> str:
    """返回 'core' 或 'tool'。规则见模块 docstring；人工覆盖优先。"""
    ov = override_of(topic)
    if ov:
        return ov[0]
    g = topic.get("grade") or 0
    name = topic.get("name") or ""
    typ = topic.get("type") or ""

    if g <= 2:
        return "tool"
    if typ in ("application", "modeling"):
        return "core"
    if g >= 7:
        # 初中高中默认核心；只有纯识记且不含可迁移词才降为工具
        if TOOL_NAME.search(name) and typ in ("conceptual", "procedural") \
                and not CORE_NAME.search(name):
            return "tool"
        return "core"
    # 小学 3–6
    if typ == "reasoning":
        return "core"
    if CORE_NAME.search(name) and not TOOL_NAME.search(name):
        return "core"
    return "tool"


def cluster_of(topic: dict) -> str:
    st = stage_of(topic)
    rules = {"primary": PRIMARY_RULES, "junior": JUNIOR_RULES,
             "senior": SENIOR_RULES}.get(st, PRIMARY_RULES)
    name = topic.get("name") or ""
    for pat, cid in rules:
        if re.search(pat, name):
            return cid
    # 兜底：按领域给一个保底簇，宁可粗也不能没有
    return {"primary": "p-word", "junior": "j-algebra",
            "senior": "s-function"}.get(st, "p-word")


def practice_target(topic: dict) -> int:
    st = stage_of(topic)
    if classify(topic) == "core":
        return STAGE_TARGET[st]
    return TOOL_TARGET[st]


def build(topics: list[dict]) -> dict:
    """生成分层数据（含簇汇总）。"""
    out = {}
    clusters: dict[str, dict] = {}
    for t in topics:
        tier = classify(t)
        cid = cluster_of(t)
        info = {
            "tier": tier,
            "cluster": cid,
            "teach_mode": "full" if tier == "core" else "brief",
            "practice_target": practice_target(t),
            "why": _why(t, tier),
        }
        if tier == "tool":
            # 速查卡的粒度：同一簇内再按册次切，否则「一上的加减法」和
            # 「二下的有余数除法」会被塞进同一张卡，谁也不适用。
            info["brief_group"] = f"{cid}@{t.get('volume') or t.get('grade')}"
        out[t["id"]] = info
        c = clusters.setdefault(cid, {
            "id": cid,
            "name": CLUSTER_NAMES.get(cid, cid),
            "stage": stage_of(t),
            "core": 0,
            "tool": 0,
            "brief_groups": [],
            "topics": [],
        })
        c[tier] += 1
        c["topics"].append(t["id"])
        if tier == "tool":
            bg = info["brief_group"]
            if bg not in c["brief_groups"]:
                c["brief_groups"].append(bg)
    for c in clusters.values():
        # 每个核心点一张完整卡；工具点按 (簇 × 册次) 合并成一张速查卡
        c["full_cards_needed"] = c["core"]
        c["brief_cards_needed"] = len(c["brief_groups"])
    return {"schema": 1, "topics": out, "clusters": clusters}


def _why(t: dict, tier: str) -> str:
    ov = override_of(t)
    if ov:
        return f"人工覆盖：{ov[1]}"
    if tier == "tool":
        g = t.get("grade")
        if g <= 2:
            return "低年级基础技能：目标是熟练，不是迁移"
        if TOOL_NAME.search(t.get("name") or ""):
            return "单一执行技能（识记/换算/口算），并入簇速查卡"
        return "规则判定为工具点"
    if t.get("type") in ("application", "modeling"):
        return "应用题/建模：必须讲透并配变式"
    return "可迁移技能：需理解策略与多步推进"


def load(path: pathlib.Path | None = None) -> dict:
    p = path or DATA_DIR / "topic_tiers.json"
    if not p.exists():
        return {"schema": 1, "topics": {}, "clusters": {}}
    return json.loads(p.read_text(encoding="utf-8"))
