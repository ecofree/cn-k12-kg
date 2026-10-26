"""Small authored mathematical representations; actions are not test scores."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RENDERER_BYTES = (_ROOT / "web/static/manipulatives.js").read_bytes()
_VALIDATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_RENDERER_SHA256 = hashlib.sha256(_RENDERER_BYTES).hexdigest()

CONFIG = {
    "ten_frame_place_value": {"kind": "ten_frame", "title": "移动计数片，先凑成十", "state": {"a": 8, "b": 5},
        "objective_ids": ["ten_frame_place_value:compose_ten", "ten_frame_place_value:make_ten"],
        "prompt": "先预测8+5怎样凑十，再改变两部分的个数。说清从第二部分拿了几个，还剩几个。颜色帮助追踪来源；移动不增加总数。"},
    "fraction_equivalence": {"kind": "fraction_strip", "title": "同一个整体，再细分一次", "state": {"numerator": 1, "denominator": 2, "factor": 2},
        "objective_ids": ["fraction_equivalence:represent", "fraction_equivalence:scale"],
        "prompt": "两条纸带总长始终相同。先预测细分后涂色份数和总份数，再改变细分倍数；解释涂色面积为何没有改变。这里不能推断不同大小整体的实际量相等。"},
    "data_reading_and_mean": {"kind": "data_chart", "title": "改变数据与纵轴，检查结论", "state": {"values": [6, 8, 10, 12], "baseline": 0},
        "objective_ids": ["data_reading_and_mean:read_scale", "data_reading_and_mean:mean"],
        "prompt": "四组记录单位都是本。先预测一本书从一组移到另一组后平均数怎样变，再调整数据验证。切换纵轴起点时，说明哪些数量没变、哪些画面长度变了。"},
    "linear_function_representations": {"kind": "linear_graph", "title": "让表、点和式子对应", "state": {"slope": 2, "intercept": 1, "x": 1},
        "objective_ids": ["linear_function_representations:formula", "linear_function_representations:representations"],
        "prompt": "先预测改变k或b会让哪些点移动，再操作核对。横纵轴每格都表示1；这里的直线是数学模型，真实情境仍须另行说明自变量范围。k=0时是常函数，不属于本课程k≠0的一次函数。"},
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
