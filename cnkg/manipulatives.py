"""Small authored mathematical representations; actions are not test scores."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RENDERER_BYTES = (_ROOT / "web/static/manipulatives.js").read_bytes()
_VALIDATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_RENDERER_SHA256 = hashlib.sha256(_RENDERER_BYTES).hexdigest()

CONFIG = {
    "ten_frame_place_value": {"kind": "ten_frame", "title": "逐一点数，看看几个十和几个一", "state": {"a": 8, "b": 5},
        "objective_ids": ["ten_frame_place_value:compose_ten", "ten_frame_place_value:make_ten"],
        "reference_family_ids": ["addition.make-ten", "placevalue.ten-and-ones", "ten.complete-frame"],
        "prompt": "选好两部分，先猜一共有几个。再点一点：每个小圆片只数一次。两种颜色告诉你它们从哪里来。数完后，看看有几个十，还剩几个一。"},
    "fraction_equivalence": {"kind": "fraction_strip", "title": "同一个整体，再细分一次", "state": {"numerator": 1, "denominator": 2, "factor": 2},
        "objective_ids": ["fraction_equivalence:represent", "fraction_equivalence:scale"],
        "reference_family_ids": ["fraction.partition-refinement"],
        "prompt": "两条纸带总长始终相同。先选涂色份数、等份数和细分倍数，预测细分后涂色的实际长度怎样变化，再用图检验。可以说说为什么；这里不能推断不同大小整体的实际量相等。"},
    "data_reading_and_mean": {"kind": "data_chart", "title": "改变数据与纵轴，检查结论", "state": {"values": [6, 8, 10, 12], "baseline": 0},
        "objective_ids": ["data_reading_and_mean:read_scale", "data_reading_and_mean:mean"],
        "reference_family_ids": ["statistics.bar-axis-reading", "statistics.mean-from-values"],
        "prompt": "四组记录单位都是本。先选四组的数量，预测这四个数的平均数，再打开图表检验。纵轴可以从0或5开始：改起点不改实际数量，可以比较画面长度与实际数量的区别。这里只核对当前数据的一次预测。"},
    "linear_function_representations": {"kind": "linear_graph", "title": "让表、点和式子对应", "state": {"slope": 2, "intercept": 1, "x": 1},
        "objective_ids": ["linear_function_representations:formula", "linear_function_representations:representations"],
        "reference_family_ids": ["linear.affine-classification", "linear.formula-evaluation", "linear.graph-point-read"],
        "prompt": "先选k、b和输入x，预测这个x对应的y，再对照表、点和式子检验。本次只核对选定输入的输出；横纵轴每格都表示1。真实情境须另行说明自变量范围；k=0时是常函数，不属于本课程k≠0的一次函数。"},
}

_VERSIONS = {uid: hashlib.sha256(json.dumps({"configuration": config,
    "validation_sha256": _VALIDATION_SHA256, "renderer_sha256": _RENDERER_SHA256},
    ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for uid, config in CONFIG.items()}


def representation_version(unit_id):
    """Bind saved actions to the configuration and renderer loaded by this process."""
    return _VERSIONS[unit_id]


def snapshot(unit_id):
    return {"configuration": copy.deepcopy(CONFIG[unit_id]),
            "representation_version": representation_version(unit_id),
            "renderer_sha256": _RENDERER_SHA256, "validation_sha256": _VALIDATION_SHA256}


def public_config(unit_id):
    return {**copy.deepcopy(CONFIG[unit_id]), "representation_version": representation_version(unit_id)}


def renderer_bytes():
    return _RENDERER_BYTES


def validate_state(unit_id, state):
    if unit_id not in CONFIG or not isinstance(state, dict):
        raise ValueError("操作表征不存在或记录格式不正确")
    expected = CONFIG[unit_id]["state"]
    if set(state) != set(expected):
        raise ValueError("操作记录字段不匹配")
    limits = {"a": (0, 10), "b": (0, 10), "numerator": (0, 12), "denominator": (1, 12),
              "factor": (2, 3), "slope": (-3, 3), "intercept": (-4, 4), "x": (-2, 2), "baseline": (0, 5)}
    for key, value in state.items():
        if key == "values":
            if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int or not 5 <= v <= 15 for v in value):
                raise ValueError("图表需要四个5至15之间的整数")
        elif type(value) is not int or not limits[key][0] <= value <= limits[key][1]:
            raise ValueError("操作数值超出范围")
    if "denominator" in state and (state["denominator"] not in (2, 3, 4, 6, 8, 12) or state["numerator"] > state["denominator"]):
        raise ValueError("分数条需要2、3、4、6、8或12个等份，分子不能超过分母")
    if "baseline" in state and state["baseline"] not in (0, 5):
        raise ValueError("纵轴起点只能选0或5")
    return state


def evaluate_prediction(unit_id, state, value):
    """Compare one prediction in a declared model, never infer mastery."""
    validate_state(unit_id, state)
    if not isinstance(value, str) or not value.strip() or len(value) > 32:
        raise ValueError("请先留下预测；数字、分数或指定选项最多32个字符")
    raw = value.strip()
    kind = CONFIG[unit_id]["kind"]
    if kind == "fraction_strip":
        if raw not in {"same", "longer", "shorter"}:
            raise ValueError("请选择涂色长度不变、变长或变短")
        expected = "same"
        feedback = "同一条纸带的每份再等分，只改变份数和每份大小，涂色的实际长度不变。不同大小整体仍须另行比较。"
        matched = raw == expected
    else:
        if not re.fullmatch(r"[+-]?(?:\d{1,4}(?:\.\d{1,4})?|\d{1,4}/[1-9]\d{0,3})", raw):
            raise ValueError("预测请填有限范围的整数、小数或分数；本活动不解析其他表达式")
        number = Fraction(raw)
        if kind == "ten_frame":
            expected = Fraction(state["a"] + state["b"])
            if number.denominator != 1 or not 0 <= number <= 20:
                raise ValueError("计数片的预测须为0至20之间的整数")
            feedback = "每片只数一次。移到十格框中不增加也不减少计数片；用两种颜色追踪各部分的来源。"
        elif kind == "data_chart":
            expected = Fraction(sum(state["values"]), 4)
            feedback = "平均数等于四个实际数量之和除以4；改变纵轴起点只改变条形画出的长度，不改变这些实际数量。"
        else:
            expected = Fraction(state["slope"] * state["x"] + state["intercept"])
            feedback = "把选定的x代入同一个表达式，再把所得(x,y)同时放到表和图中；图示窗口外的点仍有对应数值。"
        matched, expected = number == expected, str(expected)
    return {"value": raw, "matches_model": matched, "expected": expected, "feedback": feedback,
            "independent_evidence": False, "scope": "authored_model_prediction_not_mastery"}


def validate_interaction(unit_id, state, interaction):
    interaction = {} if interaction is None else interaction
    if not isinstance(interaction, dict) or set(interaction) - {"tap_sequence"}:
        raise ValueError("操作过程字段不正确")
    sequence = interaction.get("tap_sequence", [])
    if not isinstance(sequence, list) or len(sequence) > 200:
        raise ValueError("本次点数最多保留200次操作，请保存后开始新一轮")
    if CONFIG[unit_id]["kind"] != "ten_frame" and sequence:
        raise ValueError("这项表征不接受点数操作")
    total = state.get("a", 0) + state.get("b", 0)
    if any(type(index) is not int or not 0 <= index < total for index in sequence):
        raise ValueError("点数记录引用了不存在的计数片")
    return {"tap_sequence": list(sequence), "unique_count": len(set(sequence)),
            "repeated_taps": len(sequence) - len(set(sequence)),
            "counted_all": CONFIG[unit_id]["kind"] == "ten_frame" and len(set(sequence)) == total,
            "evidence": "browser_reported_actions_not_independent_assessment"}
