"""按教学目标标注理解与熟练两个独立维度，再给出资源编写优先级。

年级不能决定一个目标是否需要理解。概念的建立、应用、推理与建模需要
讲清依据；同一点也可能需要计算、表征、操作的熟练练习。core/tool 只作为
现有资源计划的组织标签：core 按独立单元规划，tool 可在同簇同册组织。
understanding=True 不自动要求独立卡；簇教学同样需要解释原理。速查卡
的存在不能证明全部目标已讲清，tool 也不表示学习无需理解。

判定优先看类型和 mastery_criteria，名称规则只作兜底。所有自动分层都是
draft，仍须逐点学科校订；不再以“教学单元必须少于某比例”约束结果。
旧速查卡的参考范围由 brief_groups 显式登记，可继续辅助 core 点。
"""
from __future__ import annotations

import json
import pathlib
import re

from cnkg.brief_groups import BRIEF_REFERENCE_GROUPS

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

# 依据达标目标而非年级识别理解/迁移要求。两个轴可以同时为 True。
UNDERSTANDING_CRITERIA = re.compile(
    "理解|解释|说明|说清|道理|原因|意义|含义|关系|为何|为什么|推导|推理|推断|推出|"
    "证明|探索|探究|发现.*规律|选择|区分|辨析|建模|提出|解决|合理|验证|检验|检查|"
    "每一步.*意思|相同加数.*乘法|对应|平均分|等分|标准|具体情境|表示什么|"
    "把.*写成.*算式|转化|交换.*简化"
)
FLUENCY_CRITERIA = re.compile(
    "计算|口算|笔算|列式|求出|求值|求商|求积|读|写|数出|数数|熟|认|辨|换算|"
    "画|背诵|量|比较|表示|找出|识别|代入|解方程|解不等式|化简|作图"
)
REFERENCE_GROUP_BY_TOPIC = {
    tid: group for group, members in BRIEF_REFERENCE_GROUPS.items() for tid in members
}

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


# 逐点目标校订（draft），理由引用现有目标，不冒充教师审核。
OVERRIDES = {
    ("三角形", 4): ("core", "目标含分类、内角和与三边关系的运用，需要概念与推理依据"),
    ("长方体和正方体", 5): ("core", "目标含空间结构、表面积和体积，不是仅识记图形名称"),
    ("平行四边形和梯形", 4): ("core", "区分类别与画高需要理解平行关系及高的含义"),
    ("根据直观图猜测积木形状", 3): ("core", "目标要求由多个方向的视图推断立体图形"),
    ("长方体纸盒的展开", 3): ("core", "目标要求空间展开与相对面的对应关系"),
    ("观察简单组合体", 5): ("core", "目标要求多方向表征并理解单个视图的不唯一性"),
    ("用圆规比较线段的长短", 3): ("tool", "测量操作与圆规两脚距离的含义可在同组展开，不省略理解要求"),
    ("设计图案", 5): ("core", "目标要求运用图形变换并说明变换依据"),
    ("利用圆设计图案", 6): ("core", "目标要求说明多个圆的位置关系"),
    ("简单的三角恒等变换", 10): ("core", "恒等变换是三角求值/证明的主干"),
    ("简单的分数大小比较", 3): ("core", "比较规则须联系相同整体、份数和每份大小解释"),
    ("小数的大小比较", 3): ("core", "目标要求说清比较顺序，需要数位意义支撑"),
}


def override_of(topic: dict):
    return OVERRIDES.get((topic.get("name"), topic.get("grade")))


def learning_axes(topic: dict) -> dict:
    """Independent needs, with traceable objective signals (not student mastery)."""
    g = topic.get("grade") or 0
    name = topic.get("name") or ""
    typ = topic.get("type") or ""
    criteria = topic.get("mastery_criteria") or []
    understanding_basis = [c for c in criteria if UNDERSTANDING_CRITERIA.search(c)]
    fluency_basis = [c for c in criteria if FLUENCY_CRITERIA.search(c)]
    ov = override_of(topic)
    if typ in ("application", "reasoning", "modeling"):
        understanding_basis.insert(0, f"目标类型 {typ}：需要策略、关系或依据")
    elif typ == "conceptual":
        understanding_basis.insert(0, "概念目标：需要建立概念、区分边界或联系表征")
    if ov and ov[0] == "core":
        understanding_basis.insert(0, ov[1])
    if not understanding_basis and CORE_NAME.search(name) and not TOOL_NAME.search(name):
        understanding_basis.append("名称规则提示可迁移技能，待逐目标学科校订")
    if not understanding_basis and g >= 7 and not TOOL_NAME.search(name):
        understanding_basis.append("初高中主干目标暂按完整讲解规划，待逐目标学科校订")
    if typ == "procedural" and not fluency_basis:
        fluency_basis.append("程序性目标需要操作或方法执行的熟练练习")
    return {"fluency": bool(fluency_basis), "understanding": bool(understanding_basis),
            "axis_basis": {"fluency": fluency_basis, "understanding": understanding_basis}}


def classify(topic: dict) -> str:
    """教学组织粒度，不从理解需求轴机械推导独立卡数量。"""
    typ = topic.get("type") or ""
    if typ in ("application", "reasoning", "modeling"):
        return "core"
    ov = override_of(topic)
    if ov:
        return ov[0]
    name = topic.get("name") or ""
    grade = topic.get("grade") or 0
    if grade >= 7:
        if TOOL_NAME.search(name) and typ in ("conceptual", "procedural") and not CORE_NAME.search(name):
            return "tool"
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
            "review_status": "draft",
            **learning_axes(t),
        }
        reference = REFERENCE_GROUP_BY_TOPIC.get(t["id"])
        if reference:
            info["reference_group"] = reference
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
    return {"schema": 2, "review_status": "draft",
            "axis_scope": "fluency/understanding 是可同时成立的教学需求，不是学生掌握状态；False 不等于不需理解",
            "topics": out, "clusters": clusters}


def _why(t: dict, tier: str) -> str:
    ov = override_of(t)
    if ov:
        return f"目标校订（待学科审核）：{ov[1]}"
    if tier == "tool":
        return "可在同簇同册组织教学；原理与熟练要求见独立需求轴，现有速查不等于全部目标已覆盖"
    if t.get("type") in ("application", "reasoning", "modeling"):
        return "目标要求应用、推理或建模，按独立单元规划；依据：" + "；".join(t.get("mastery_criteria") or [])
    return "沿用主干主题的独立教学单元规划；理解与熟练需求另行标注，待逐目标学科校订"


def load(path: pathlib.Path | None = None) -> dict:
    p = path or DATA_DIR / "topic_tiers.json"
    if not p.exists():
        return {"schema": 1, "topics": {}, "clusters": {}}
    return json.loads(p.read_text(encoding="utf-8"))
