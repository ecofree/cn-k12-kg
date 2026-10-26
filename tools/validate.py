"""数据校验：结构、引用、环、目录覆盖、题库可验算性。

用法：
    python tools/validate.py
    python tools/validate.py --strict     # 警告也视为失败
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

from cnkg.verify import check_question  # noqa: E402
from cnkg.contracts import (ContractDependencyError, load_schema, property_schema,
                            validate_repository)  # noqa: E402
# 解析里「选项 C / C 项 / 故答案为: D」这类写法的判定口径与修复工具共用一份，
# 免得两边各写一份正则、慢慢漂移。宽版正则（validate 给自有题用的
# OPTION_REF_RE）在外部题上会把数学变量 a/b/c 误判成选项字母，不能共用。
from tools.fix_explanation_letters import PATTERNS as OPTION_LETTER_PATS  # noqa: E402
from tools.balance_options import (OPTION_REF_RE, correct_position, display_position_counts,
                                  position_label, position_reference_fields)  # noqa: E402

DATA = ROOT / "data"

ID_RE = re.compile(
    r"^m(?:(?P<grade>[1-9])(?P<sem>[ab])|h(?P<hvol>[1-5]))"
    r"\.(?P<ch>\d{1,2})\.(?P<sec>\d{1,2})(?:\.(?P<sub>\d{1,2}))?$")
QLID_RE = re.compile(
    r"^(?P<topic>m(?:[1-9][ab]|h[1-5])\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)-q(?P<n>\d+)$")
STD_RE = re.compile(r"^(cms-2022:[1-4]\.(NA|GE|SP|CP)\.\d{2}|cms-2017:H\.(PR|FU|GA|PS|MM)\.\d{2})$")

# 册次键 -> (册名, 年级, 学期, 学段)
VOLUMES = {
    "1a": ("一上", 1, "上", "primary"), "1b": ("一下", 1, "下", "primary"),
    "2a": ("二上", 2, "上", "primary"), "2b": ("二下", 2, "下", "primary"),
    "3a": ("三上", 3, "上", "primary"), "3b": ("三下", 3, "下", "primary"),
    "4a": ("四上", 4, "上", "primary"), "4b": ("四下", 4, "下", "primary"),
    "5a": ("五上", 5, "上", "primary"), "5b": ("五下", 5, "下", "primary"),
    "6a": ("六上", 6, "上", "primary"), "6b": ("六下", 6, "下", "primary"),
    "7a": ("七上", 7, "上", "junior"), "7b": ("七下", 7, "下", "junior"),
    "8a": ("八上", 8, "上", "junior"), "8b": ("八下", 8, "下", "junior"),
    "9a": ("九上", 9, "上", "junior"), "9b": ("九下", 9, "下", "junior"),
    "h1": ("必修一", 10, "上", "senior"), "h2": ("必修二", 10, "下", "senior"),
    "h3": ("选必一", 11, "上", "senior"), "h4": ("选必二", 11, "下", "senior"),
    "h5": ("选必三", 12, "上", "senior"),
}

CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
          7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二",
          13: "十三", 14: "十四", 15: "十五", 16: "十六", 17: "十七", 18: "十八",
          19: "十九", 20: "二十", 21: "二十一", 22: "二十二", 23: "二十三", 24: "二十四",
          25: "二十五", 26: "二十六", 27: "二十七", 28: "二十八", 29: "二十九",
          30: "三十", 31: "三十一", 32: "三十二", 33: "三十三", 34: "三十四"}

# Enumerations come from the public contracts; no second field model lives here.
# ID_RE/QLID_RE above parse validated IDs for textbook semantic checks.
DOMAINS = set(property_schema("topic", "domain")["enum"])
TYPES = set(property_schema("topic", "type")["enum"])
STRENGTHS = set(property_schema("dependency", "strength")["enum"])
SEMESTERS = set(property_schema("topic", "semester")["enum"])
DIFFICULTIES = set(property_schema("question", "difficulty")["enum"])
QTYPES = set(property_schema("question", "type")["enum"])

# OPTION_REF_RE remains exported for existing explanation regressions.  The
# source transform and validator share explicit position-reference checks.

STRUCT_REQUIRED = load_schema("topic")["required"]
CONTENT_REQUIRED = load_schema("topic")["allOf"][0]["then"]["required"]
FILLED_STATUS = ("draft", "reviewed")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def show(self, limit=40):
        for msg in self.errors[:limit]:
            print(f"  [ERROR] {msg}")
        if len(self.errors) > limit:
            print(f"  ... 另有 {len(self.errors) - limit} 条错误")
        for msg in self.warnings[:limit]:
            print(f"  [WARN ] {msg}")
        if len(self.warnings) > limit:
            print(f"  ... 另有 {len(self.warnings) - limit} 条警告")


def load(name):
    with open(DATA / name, encoding="utf-8") as fh:
        return json.load(fh)


def load_outline(rpt):
    """返回 {volume: {chapter_no: {title, leaves: {no: leaf}}}}"""
    try:
        outline = load("outline.json")
    except FileNotFoundError:
        rpt.warn("没有 outline.json，跳过目录覆盖检查")
        return None
    index = {}
    for vol in outline.get("volumes", []):
        chapters = {}
        for ch in vol["chapters"]:
            leaves = {}
            for leaf in ch.get("leaves", []):
                leaves[leaf["no"]] = leaf
            chapters[ch["no"]] = {"title": ch["title"], "leaves": leaves}
        index[vol["volume"]] = {"chapters": chapters,
                                "stage": vol.get("stage", "junior"),
                                "curriculum_version": vol.get("curriculum_version", "2024")}
    return index


def check_topics(topics, outline, rpt):
    ids = set()
    by_volume = collections.Counter()
    leaf_used = collections.Counter()

    for t in topics:
        tid = t.get("id", "<no-id>")
        for field in STRUCT_REQUIRED:
            if field not in t or t[field] in (None, "", []):
                rpt.err(f"{tid}: 缺少必填字段 {field}")
        filled = t.get("review_status") in FILLED_STATUS
        if filled:
            for field in CONTENT_REQUIRED:
                if field not in t or t[field] in (None, "", []):
                    rpt.err(f"{tid}: 已填内容的知识点缺少字段 {field}")
        if tid in ids:
            rpt.err(f"{tid}: id 重复")
        ids.add(tid)

        m = ID_RE.match(tid)
        if not m:
            rpt.err(f"{tid}: id 格式不合法（应为 m7a.1.2.2 形式）")
            continue
        key = ("h" + m.group("hvol")) if m.group("hvol") else (m.group("grade") + m.group("sem"))
        volume, grade, semester, stage = VOLUMES[key]
        if t.get("volume") != volume:
            rpt.err(f"{tid}: volume 应为 {volume}，实际 {t.get('volume')}")
        if t.get("grade") != grade:
            rpt.err(f"{tid}: grade 应为 {grade}，实际 {t.get('grade')}")
        if t.get("semester") != semester:
            rpt.err(f"{tid}: semester 应为 {semester}，实际 {t.get('semester')}")
        chapter_no = int(m.group("ch"))
        if stage != "primary" and not str(t.get("chapter", "")).startswith(
                f"第{CN_NUM.get(chapter_no, chapter_no)}章"):
            rpt.err(f"{tid}: chapter 与 id 的章号不一致（id 表示第 {chapter_no} 章）")
        if t.get("domain") not in DOMAINS:
            rpt.err(f"{tid}: domain 非法：{t.get('domain')}")
        if t.get("type") not in TYPES:
            rpt.err(f"{tid}: type 非法：{t.get('type')}")
        if t.get("semester") not in SEMESTERS:
            rpt.err(f"{tid}: semester 非法：{t.get('semester')}")
        minimum_goals = property_schema("topic", "mastery_criteria")["minItems"]
        if filled and (not isinstance(t.get("mastery_criteria"), list)
                       or len(t["mastery_criteria"]) < minimum_goals):
            rpt.err(f"{tid}: mastery_criteria 至少要有 {minimum_goals} 条")

        by_volume[volume] += 1

        if outline is not None and volume in outline:
            volume_meta = outline[volume]
            chapters = volume_meta["chapters"]
            if volume_meta["curriculum_version"] != t.get("curriculum_version"):
                rpt.err(f"{tid}: curriculum_version 应为 {volume_meta['curriculum_version']}，"
                        f"实际 {t.get('curriculum_version')}")
            if chapter_no not in chapters:
                rpt.err(f"{tid}: 章号 {chapter_no} 不在 {volume} 目录中")
            else:
                ch = chapters[chapter_no]
                if t.get("chapter") != ch["title"]:
                    rpt.err(f"{tid}: chapter 应为「{ch['title']}」，实际「{t.get('chapter')}」")
                leaf_no = f"{chapter_no}.{m.group('sec')}" + (f".{m.group('sub')}" if m.group("sub") else "")
                leaf = ch["leaves"].get(leaf_no)
                if leaf is None:
                    rpt.err(f"{tid}: 小节 {leaf_no} 不在 {volume} 目录中")
                else:
                    leaf_used[(volume, chapter_no, leaf_no)] += 1
                    if t.get("section") != leaf["section"]:
                        rpt.err(f"{tid}: section 应为「{leaf['section']}」，实际「{t.get('section')}」")
                    if t.get("name") != leaf["title"]:
                        rpt.warn(f"{tid}: 名称与目录不一致（目录「{leaf['title']}」，图谱「{t.get('name')}」）")

    if outline is not None:
        for volume, meta in outline.items():
            for chapter_no, ch in meta["chapters"].items():
                for leaf_no, leaf in ch["leaves"].items():
                    if leaf_used[(volume, chapter_no, leaf_no)] == 0:
                        rpt.err(f"{volume} {ch['title']} {leaf_no}「{leaf['title']}」有目录但无知识点")

    return ids, by_volume


def check_dependencies(deps, ids, rpt):
    pairs = set()
    for edge in deps:
        a, b = edge.get("topicId"), edge.get("prerequisiteId")
        if a not in ids or b not in ids:
            rpt.err(f"边 {a} <- {b}: 端点不存在")
            continue
        if a == b:
            rpt.err(f"边 {a} <- {b}: 自环")
        if (a, b) in pairs:
            rpt.err(f"边 {a} <- {b}: 重复")
        pairs.add((a, b))
        if edge.get("strength") not in STRENGTHS:
            rpt.err(f"边 {a} <- {b}: strength 非法：{edge.get('strength')}")
        if not edge.get("reason"):
            rpt.warn(f"边 {a} <- {b}: 没有给出理由")

    # 环检测（迭代 DFS）
    adj = collections.defaultdict(list)
    for a, b in pairs:
        adj[a].append(b)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = collections.defaultdict(int)
    for start in list(adj):
        if color[start] != WHITE:
            continue
        stack = [(start, iter(adj[start]))]
        color[start] = GRAY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[node] = BLACK
                stack.pop()
                continue
            if color[nxt] == GRAY:
                rpt.err(f"发现环：{node} -> {nxt}")
                color[nxt] = BLACK
                continue
            if color[nxt] == WHITE:
                color[nxt] = GRAY
                stack.append((nxt, iter(adj[nxt])))


VOLUME_SEQ = ["一上", "一下", "二上", "二下", "三上", "三下", "四上", "四下",
              "五上", "五下", "六上", "六下", "七上", "七下", "八上", "八下",
              "九上", "九下", "必修一", "必修二", "选必一", "选必二", "选必三"]


def curriculum_key(topic):
    """把知识点折算成教材先后顺序：册次 → 章 → 节 → 子节。"""
    m = ID_RE.match(topic["id"])
    if not m:
        return (99, 99, 99, 99)
    key = ("h" + m.group("hvol")) if m.group("hvol") else (m.group("grade") + m.group("sem"))
    name = VOLUMES[key][0]
    return (VOLUME_SEQ.index(name), int(m.group("ch")), int(m.group("sec")),
            int(m.group("sub") or 0))


def check_edge_order(topics, deps, rpt):
    """先修边不能指向"以后才学"的内容。

    先修的含义是"前置没学会导致当前学不会"。如果前置在教材里比当前还晚，
    这条边一定是挂反了或挂错了——它会让学生卡在一个还没学过的东西上。
    """
    by_id = {t["id"]: t for t in topics}
    hard_back = soft_back = 0
    for edge in deps:
        a, b = edge["topicId"], edge["prerequisiteId"]
        if a not in by_id or b not in by_id:
            continue
        if curriculum_key(by_id[b]) > curriculum_key(by_id[a]):
            label = f"{a}（{by_id[a]['name']}）<- {b}（{by_id[b]['name']}）"
            if edge.get("strength") == "hard":
                hard_back += 1
                rpt.err(f"先修晚于当前点：{label}")
            else:
                soft_back += 1
                rpt.warn(f"软先修晚于当前点：{label}")
    return hard_back, soft_back


def check_graph_shape(topics, deps, rpt):
    has_prereq = {d["topicId"] for d in deps}
    has_dependent = {d["prerequisiteId"] for d in deps}
    roots = 0
    for t in topics:
        tid = t["id"]
        if t.get("review_status") == "skeleton":
            continue  # 骨架点还没有先修边，不参与根节点判定
        if tid not in has_prereq:
            roots += 1
            if not t.get("root_reason"):
                rpt.err(f"{tid}: 没有先修边，但缺少 root_reason")
            if tid not in has_dependent:
                rpt.err(f"{tid}: 完全孤立（既无先修也无后继）")
    return roots


def check_standards(standards, topics, rpt):
    ids = set()
    for s in standards:
        sid = s.get("id", "")
        if sid in ids:
            rpt.err(f"课标 {sid}: id 重复")
        ids.add(sid)
        if not STD_RE.match(sid):
            rpt.err(f"课标 {sid}: id 格式不合法")
        if s.get("verbatim") is True:
            # verbatim 只对 verbatim_text 这一列生效；必须同时具备原文、出处、核对方，
            # 否则就是"标了个 true 但没有依据"。作者自撰的 summary 另算，不借这个字段洗白。
            missing = [k for k in ("verbatim_text", "page_or_ref", "verbatim_source") if not s.get(k)]
            if missing:
                rpt.err(f"课标 {sid}: verbatim=true 但缺少 {missing}，属于无依据声明")
            if s.get("summary_status") == "needs_author_review":
                rpt.warn(f"课标 {sid}: 自撰摘要与原文有差异，待作者改写（见 summary_note）")
    for t in topics:
        for ref in t.get("standards", []):
            if ref.get("id") not in ids:
                rpt.err(f"{t['id']}: 引用不存在的课标 {ref.get('id')}")
            if ref.get("confidence") not in ("exact", "inferred"):
                rpt.err(f"{t['id']}: standards.confidence 非法：{ref.get('confidence')}")
    return ids


def check_questions(questions, ids, rpt, filled_ids, min_per_topic=2):
    qids = set()
    per_topic = collections.Counter()
    prompts = collections.defaultdict(set)
    signatures = {}
    verifiable = 0

    for q in questions:
        qid = q.get("id", "<no-id>")
        if qid in qids:
            rpt.err(f"{qid}: 题目 id 重复")
        qids.add(qid)
        m = QLID_RE.match(qid)
        if not m:
            rpt.err(f"{qid}: 题目 id 格式不合法")
        elif m.group("topic") != q.get("topicId"):
            rpt.err(f"{qid}: id 前缀与 topicId 不一致")
        tid = q.get("topicId")
        if tid not in ids:
            rpt.err(f"{qid}: topicId 不存在：{tid}")
            continue
        per_topic[tid] += 1
        if q.get("difficulty") not in DIFFICULTIES:
            rpt.err(f"{qid}: difficulty 非法：{q.get('difficulty')}")
        if q.get("type") not in QTYPES:
            rpt.err(f"{qid}: type 非法：{q.get('type')}")
        if not q.get("prompt"):
            rpt.err(f"{qid}: 题干为空")
        if q["prompt"] in prompts[tid]:
            rpt.err(f"{qid}: 同知识点内题干重复")
        prompts[tid].add(q["prompt"])

        # 选择题：答案必须指向存在的 option_id。
        # 字母不再是身份（M3.1 之后），所以这里校验的是 id，不是 A/B/C/D。
        if q.get("type") == "choice":
            choices = q.get("choices") or []
            opt_ids = [c.get("id") for c in choices]
            if len(opt_ids) < 3:
                rpt.err(f"{qid}: 选项少于 3 个")
            elif any(i is None for i in opt_ids):
                rpt.err(f"{qid}: 选项缺少 id（字母已改为渲染时生成，id 才是身份）")
            elif len(set(opt_ids)) != len(opt_ids):
                rpt.err(f"{qid}: 选项 id 重复")
            elif q.get("answer") not in opt_ids:
                rpt.err(f"{qid}: 答案 {q.get('answer')} 不在选项 id 内：{opt_ids}")
            if any("key" in c for c in choices):
                rpt.err(f"{qid}: 选项里还有 key 字段，字母应改为渲染时生成")

        # 题干、选项和解析都不能依赖选项的显示位置。
        # 事故背景见 docs/decisions.md D19——这类引用一旦遇上重排就会变成假解释。
        referenced = position_reference_fields(q)
        if referenced:
            rpt.err(f"{qid}: {', '.join(referenced)} 引用了选项显示位置，请按内容说明")

        # 完全相同的题（题干+答案+选项）出现在两个知识点里：同一道题被写了两遍
        sig = (q.get("prompt"), str(q.get("answer")),
               tuple(sorted(c.get("text", "") for c in (q.get("choices") or []))))
        if sig in signatures:
            rpt.err(f"{qid}: 与 {signatures[sig]} 完全重复（题干、答案、选项都一样）")
        else:
            signatures[sig] = qid

        if q.get("type") == "choice":
            # 身份是 option_id（M3.1），字母不再存在
            keys = [c.get("id") for c in q.get("choices") or []]
            if len(keys) < 2:
                rpt.err(f"{qid}: 选择题至少要有 2 个选项")
            if q.get("answer") not in keys:
                rpt.err(f"{qid}: 答案 {q.get('answer')} 不在选项 {keys} 中")
        elif q.get("type") == "numeric" and not q.get("verify"):
            rpt.err(f"{qid}: 数值题缺少 verify 表达式")
        elif q.get("type") == "short":
            accept = q.get("accept")
            if not isinstance(accept, list):
                rpt.err(f"{qid}: 短答题必须显式给出 accept 列表（可为空），便于复核同义写法")
            elif any(not isinstance(x, str) or not x.strip() for x in accept):
                rpt.err(f"{qid}: accept 里有空项")

        # 题干写出答案：形如「2/7×3=6/7，结果的分子是多少」——学生照抄即可，题就废了。
        # 注意不能只看「=答案」：题干里的等号多数是已知条件（a+b=4、y=2x-4），
        # 宽版规则实测误报 9/9。只有「等式紧跟着问结果」才是真泄露。
        if q.get("type") == "numeric":
            ans = str(q.get("answer", "")).strip().replace("−", "-")
            if ans and re.search(rf"=\s*{re.escape(ans)}(?![\d.])[^=]{{0,8}}结果",
                                 q["prompt"].replace(" ", "")):
                rpt.err(f"{qid}: 题干先写出等式结果再问结果，等于把答案 {ans} 送到眼前")

        result = check_question(q)
        if result["status"] == "pass":
            verifiable += 1
        elif result["status"] in ("mismatch", "error"):
            rpt.err(f"{qid}: {result['detail']}")

    # 选项设计：稳定 option_id 是身份，不能拿来统计 A/B/C/D 显示位置。
    by_len = collections.defaultdict(list)
    for q in questions:
        if q.get("type") == "choice":
            by_len[len(q.get("choices") or [])].append(q)
    for size, rows in sorted(by_len.items()):
        if size < 3:
            rpt.err(f"有 {len(rows)} 道选择题只有 {size} 个选项，猜中率过高，应改为短答")
            continue
        positions = collections.Counter(correct_position(q) for q in rows
                                        if correct_position(q) is not None)
        if not positions:
            continue  # malformed choices were reported above
        total = sum(positions.values())
        top_count = max(positions.values())
        top = top_count / total
        ideal = 1 / size
        # A small group cannot always realize exactly 1/n; allow the integer
        # optimum as well as the usual eight-percentage-point tolerance.
        if top_count > max((total + size - 1) // size, (ideal + 0.08) * total):
            labels = {position_label(i): positions[i] for i in range(size)}
            rpt.err(f"{size} 选项题正确答案显示位置偏斜：{labels}，"
                    f"最高占 {top:.0%}（理想 {ideal:.0%}），跑 tools/balance_options.py")

    for tid in filled_ids:
        if per_topic[tid] < min_per_topic:
            rpt.err(f"{tid}: 只有 {per_topic[tid]} 道题，少于 {min_per_topic} 道")
    for tid in ids - filled_ids:
        if per_topic[tid]:
            rpt.warn(f"{tid}: 还是骨架点，但有 {per_topic[tid]} 道题，请先把知识点补成 draft")

    # 自适应诊断要求每个点至少有 2 道“入口档”题，否则第二题只能升难度
    levels = collections.defaultdict(list)
    for q in questions:
        if q.get("topicId") in filled_ids:
            levels[q["topicId"]].append(q.get("difficulty"))
    for tid, values in levels.items():
        if not values:
            continue
        lowest = min(values, key=lambda d: {"basic": 0, "standard": 1, "advanced": 2}.get(d, 9))
        if values.count(lowest) < 2:
            rpt.warn(f"{tid}: 最低难度（{lowest}）只有 1 道题，诊断第 2 题会被迫升难度")
    return verifiable, per_topic, by_len


def check_external(ext, own_ids, ids, rpt):
    """校验外部题 questions_ext.json——这个文件**曾经完全没进过校验**。

    2026-10-25 事故：外部题里有 4429 个重复 id（10628 道题共用），
    validate.py 从来只加载 questions.json，所以一直没被发现。
    重复 id 的后果是「按 id 取题」会拿错对象——实测 6199 道题在
    id-keyed 视图里不可见，其中 546 道是可诊断题。

    分三层，因为外部题的「质量问题」和「完整性问题」得区别对待：

    1. **完整性（error）**：id 唯一、引用存在、枚举合法、题干非空。
    2. **可诊断题的内容底线（error）**：能进诊断的题必须真能判分——
       答案要落在选项内、不能有空选项、解析不能用字母指代选项（D19）。
       不进诊断的题不做这些要求，它们是按准入规则留作储备的。
    3. **质量缺口（warning，只报总数不刷屏）**：数值题缺 verify、
       短答题缺 accept 之类。这些是设计取舍（外部题靠来源证据准入，
       不是靠机器验算），报出来是为了让人知道，不是为了让校验失败。
    """
    seen: dict[str, int] = {}
    per_source = collections.Counter()
    bad_type, bad_diff, bad_topic, empty_prompt = [], [], [], []
    no_verify = no_accept = diag_total = 0

    for q in ext:
        qid = q.get("id", "<no-id>")
        if qid in seen or qid in own_ids:
            rpt.err(f"[外部题] {qid}: 题目 id 重复"
                    f"（{'与自有题撞车' if qid in own_ids else '外部题内部撞车'}）")
        seen[qid] = seen.get(qid, 0) + 1
        per_source[q.get("source")] += 1

        if q.get("type") not in QTYPES:
            bad_type.append(qid)
        if q.get("difficulty") not in DIFFICULTIES:
            bad_diff.append(qid)
        tid = q.get("topicId")
        if not tid or tid not in ids:
            bad_topic.append((qid, tid))
        if not q.get("prompt"):
            empty_prompt.append(qid)

        # ---- 第 2 层：可诊断题的内容底线 ----
        if not q.get("usable_for_diagnosis"):
            # 不进诊断的题是储备，只统计不问责
            if q.get("type") == "numeric" and not q.get("verify"):
                no_verify += 1
            if q.get("type") == "short" and not isinstance(q.get("accept"), list):
                no_accept += 1
            continue
        diag_total += 1
        # 新不变式（D40）：能进诊断的外部题必须有证据等级。
        # `usable_for_diagnosis` 是导入时按归属与许可定的，`evidence_level`
        # 是按解析与复算算的。缺后者时题目仍然"可用"，但没人知道答案
        # 有没有被独立验证过——那正是 9005 道题被误当成 cross-checked 的原因。
        lv = q.get("evidence_level")
        if not lv:
            rpt.err(f"[外部题] {qid}: 可诊断但没有 evidence_level，"
                    f"先跑 tools/grade_ext.py --apply")
        elif lv in ("defective", "broken"):
            rpt.err(f"[外部题] {qid}: 证据等级是 {lv}，不应该 usable_for_diagnosis")
        if q.get("type") == "choice":
            options = q.get("choices") or []
            opt_ids = [c.get("id") for c in options]
            if q.get("answer") not in opt_ids:
                rpt.err(f"[外部题] {qid}: 可诊断但答案 {q.get('answer')} "
                        f"不在选项 id 内：{opt_ids}")
            if any(not str(c.get("text", "")).strip() for c in options):
                rpt.err(f"[外部题] {qid}: 可诊断但有空选项"
                        f"（答案内容在源数据里已丢失）")
            if len(options) < 3:
                rpt.err(f"[外部题] {qid}: 可诊断但只有 {len(options)} 个选项")
            # 选项曾经被写成 LaTeX 宏塞在题干里（\choicesfive{...}{...}），
            # 那时候这题没有选项数组、答案是 'C.'，学生看不到任何选项。
            # 修复工具 tools/repair_inline_choices.py 已处理，
            # 这里守住「不许再回潮」。
            if re.search(r"\\choices(?:five|four)?\s*\{", q.get("prompt") or ""):
                rpt.err(f"[外部题] {qid}: 题干里还留着 LaTeX 选项宏，"
                        f"选项没被解析成 choices")
            if any(OPTION_LETTER_PAT.search(q.get("explanation") or "")
                   for OPTION_LETTER_PAT in OPTION_LETTER_PATS):
                rpt.err(f"[外部题] {qid}: 解析用字母指代选项，"
                        f"选项顺序一变就指错（见 D19）")
        elif q.get("type") == "numeric" and not q.get("verify"):
            no_verify += 1
        elif q.get("type") == "short" and not isinstance(q.get("accept"), list):
            no_accept += 1

    for label, lst in (("type 非法", bad_type), ("difficulty 非法", bad_diff),
                       ("题干为空", empty_prompt)):
        if lst:
            rpt.err(f"[外部题] {label} {len(lst)} 道，例如 {lst[:3]}")
    if bad_topic:
        rpt.err(f"[外部题] topicId 不存在 {len(bad_topic)} 道，"
                f"例如 {bad_topic[:3]}")

    # ---- 第 3 层：只报总数的质量缺口 ----
    # 外部题靠「来源证据」准入（原卷答案 + 解析互证），不是靠 verify 机器验算，
    # 所以缺 verify / 缺 accept 是**已知取舍**而不是错误——但必须报出来，
    # 否则「可机器验算 XX%」这个数字会让人误以为全库都能自校验。
    if no_verify:
        rpt.warn(f"[外部题] {no_verify} 道数值题没有 verify 表达式，"
                 f"机器无法自校验其答案（靠来源证据准入，属已知取舍）")
    if no_accept:
        rpt.warn(f"[外部题] {no_accept} 道短答题没有 accept 列表，"
                 f"同义写法会被判错（外部题未做同义归一）")

    print(f"外部题 {len(ext)} 道，来源分布 {dict(per_source)}")
    print(f"  唯一 id {len(seen)} 个（重复 {len(ext) - len(seen)} 道），"
          f"可诊断 {diag_total} 道")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="警告也视为失败")
    args = parser.parse_args()

    rpt = Report()
    print("== 版本化结构契约与资源引用 ==")
    try:
        contract_errors = validate_repository(DATA)
    except (ContractDependencyError, ValueError) as exc:
        print(f"结构契约环境错误：{exc}")
        return 2
    if contract_errors:
        for problem in contract_errors:
            rpt.err(problem)
        rpt.show()
        print(f"\nFAILED：{len(rpt.errors)} 个结构或引用错误；先修复后再运行语义检查。")
        return 1
    print("主题、先修边、题目、讲解卡、单元任务和实践项目契约通过")
    print("== 加载数据 ==")
    topics = load("topics.json")
    deps = load("dependencies.json")
    questions = load("questions.json")
    standards = load("standards.json")
    print(f"topics={len(topics)} dependencies={len(deps)} questions={len(questions)} standards={len(standards)}")

    if not isinstance(topics, list) or not isinstance(deps, list):
        print("顶层必须是 JSON 数组")
        return 2

    outline = load_outline(rpt)
    print("\n== 结构校验 ==")
    ids, by_volume = check_topics(topics, outline, rpt)
    status = collections.Counter(t.get("review_status") for t in topics)
    filled_ids = {t["id"] for t in topics if t.get("review_status") in FILLED_STATUS}
    print(f"知识点 {len(ids)}，按册：{dict(by_volume)}")
    print(f"内容进度：已填 {len(filled_ids)} / 骨架 {status.get('skeleton', 0)}")
    per_volume = collections.defaultdict(lambda: [0, 0])
    for t in topics:
        bucket = per_volume[t["volume"]]
        bucket[0] += 1
        if t.get("review_status") in FILLED_STATUS:
            bucket[1] += 1
    for volume, (total, done) in per_volume.items():
        print(f"  {volume}: {done}/{total}")

    print("\n== 先修边校验 ==")
    check_dependencies(deps, ids, rpt)
    hard = sum(1 for d in deps if d.get("strength") == "hard")
    roots = check_graph_shape(topics, deps, rpt)
    hard_back, soft_back = check_edge_order(topics, deps, rpt)
    print(f"边 {len(deps)}（hard {hard} / soft {len(deps) - hard}），根节点 {roots}")

    print("\n== 课标校验 ==")
    check_standards(standards, topics, rpt)

    print("\n== 题库校验 ==")
    verifiable, per_topic, by_len = check_questions(questions, ids, rpt, filled_ids)

    print("\n== 外部题库校验 ==")
    ext = []
    ext_path = DATA / "questions_ext.json"
    if ext_path.exists():
        ext = json.loads(ext_path.read_text(encoding="utf-8"))
        check_external(ext, {q["id"] for q in questions}, ids, rpt)
    else:
        print("  没有 questions_ext.json，跳过")
    if by_len:
        positions = display_position_counts(questions)
        pooled = collections.Counter()
        for counts in positions.values():
            pooled.update(counts)
        if pooled:
            total = sum(pooled.values())
            top_position, top_count = pooled.most_common(1)[0]
            random_rate = sum(sum(counts.values()) / size for size, counts in positions.items()) / total
            print(f"选择题选项数 {dict(sorted((k, len(v)) for k, v in by_len.items()))}，"
                  f"一路选显示位置 {position_label(top_position)} 的正确率 {top_count / total:.1%}"
                  f"（随机水平约 {random_rate:.1%}）")
    print(f"题目 {len(questions)}，可自动验算 {verifiable}，"
          f"已填点平均 {len(questions) / max(len(filled_ids), 1):.1f} 道")

    print("\n== 结果 ==")
    rpt.show()
    failed = bool(rpt.errors) or (args.strict and bool(rpt.warnings))
    if failed:
        print(f"\nFAILED：{len(rpt.errors)} 个错误，{len(rpt.warnings)} 个警告")
        return 1
    print(f"\nVALIDATION PASSED（{len(rpt.warnings)} 个警告）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
