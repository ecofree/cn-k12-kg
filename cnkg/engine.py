"""诊断引擎：自适应出题、判定、回溯与学习路径汇总。

判定规则（与参考实现同构，但按本项目数据重写）：
  每个知识点最多 2 题。
  第 1 题对              -> 掌握，并推断全部祖先已掌握（标 assumed，不再出题）
  第 1 题错、第 2 题对    -> 不稳（shaky），不回溯
  第 1 题错、第 2 题错    -> 薄弱（weak），向先修回溯，hard 优先于 soft
  第 2 题优先同级或更易；题库没有同级题时会被迫升难度，记 escalated，该点判定存疑。
筛查模式（screen）：每个知识点只问 1 题，错即薄弱，不回溯，用于整章摸底。

会话是可 JSON 序列化的纯 dict，便于存档与回放。
"""
from __future__ import annotations

DIFF_RANK = {"basic": 0, "standard": 1, "advanced": 2}

MODES = {
    "diagnose": {"max_attempts": 2, "descend": True},
    "screen": {"max_attempts": 1, "descend": False},
    # 练习：讲完就练，不判薄弱。
    # 与诊断的区别只有两点：①核心点答对后**一定**追问一道应用题（不受
    # 全库覆盖率限制，因为练习不需要跨会话一致性）；②答错不回溯先修。
    # 换句话说，升档在诊断里是负收益（实测准确率 -1.3pp、漏诊 +4.3pp），
    # 在练习里才是它该待的地方。
    "practice": {"max_attempts": 2, "descend": False, "step_up_always": True},
}

# 第 1 题答对后是否再问一题确认。
# 关掉时沿用旧规则（1 题对即判掌握），误判率约等于"没掌握却猜对"的概率；
# 打开后需两题都对才判掌握，代价是平均多出约 0.7 题。
CONFIRM_ON_CORRECT = True


def create_session(graph, targets, mode="diagnose", limit=None, rotate=0) -> dict:
    """建立一次诊断会话。

    targets：目标知识点 id 列表（单个点或整章）。
    limit：本次最多出多少道题；缺省按范围内题量估算。
    rotate：出题轮转偏移。同一个知识点被反复诊断时，
        每次应该抽到不同的题——否则练习模式下学生每次都遇到同一道，
        题量储备等于白储备（实测：连续 6 次会话抽到同一道题）。

    偏移由调用方给（通常是一个递增的会话计数），不用随机数——
    这样同一组参数可复现，诊断记录可审计。
    """
    if mode not in MODES:
        raise ValueError(f"未知模式：{mode}")
    targets = [t for t in dict.fromkeys(targets) if t in graph.by_id]
    if not targets:
        raise ValueError("没有有效的目标知识点")

    if mode == "screen":
        queue = graph.topo_order(targets)
        default_limit = len(queue)
    else:
        queue = list(targets)
        # 现在每个点最多 2 题、且"不稳"也要往回确认，平均约 3.9 题/点；
        # 范围多大就按 3 倍预留，上限 24 题（约 6 个点），避免一路回溯时把孩子问烦
        default_limit = min(24, max(12, len(graph.scope(targets)) * 3))

    return {
        "mode": mode,
        "targets": targets,
        "limit": int(limit or default_limit),
        "queue": queue,
        "state": {},
        "asked": {},
        "seen": [],
        "log": [],
        "current": None,
        "escalated": 0,
        "rotate": int(rotate),
    }


# 各题型的"瞎猜命中率"：四选一约 1/4；数值填空写错就是错，接近 0。
# 同级题里优先出低猜测题型，能在不增加任何题量的前提下压低漏诊。
TYPE_GUESS = {"numeric": 0.02, "short": 0.05, "choice": 0.25, "self": 0.3}


def _by_guess(pool):
    """按猜测率升序排列，同率保持文件原序（稳定排序）。

    猜测率低的题型优先——数值题瞎猜几乎不会对，选择题 25% 能猜对，
    优先出数值题可以在不增加题量的前提下压低漏诊。

    **同题型内部按 topicId 交错**，让同一点的多道题轮流被抽到。
    之前是纯文件序，结果同一点的第 2 问永远抽到文件序最靠前的那道，
    其余的题（尤其是记忆检验题这种选择题）永远抽不到——
    而那恰恰是最该问学生的题型。题库从每点 2 道扩到 4–7 道之后，
    这个「抽不到」的问题才暴露出来。
    """
    order = sorted(enumerate(pool), key=lambda pair: (
        TYPE_GUESS.get(pair[1].get("type"), 0.25), pair[0]))
    return [q for _, q in order]


def _interleave_types(pool):
    """按题型轮流排列（**输入必须已经按猜测率排好**）。

    同一个知识点有多道题时，如果一直按「猜测率低的题型优先」取，
    数值题会被反复抽到，记忆检验题（选择题）永远轮不上。

    注意调用顺序：先 `_by_guess`（按猜测率排），再 `_interleave_types`（按题型交错）。
    反过来做的话，`_by_guess` 会把交错结果重排回去，交错就白做了。
    """
    buckets: dict[str, list] = {}
    for q in pool:
        buckets.setdefault(q.get("type", ""), []).append(q)
    if len(buckets) <= 1:
        return pool
    out: list = []
    depth = max(len(b) for b in buckets.values())
    for i in range(depth):
        for bucket in buckets.values():
            if i < len(bucket):
                out.append(bucket[i])
    return out


def _rotate_pool(pool, rot: int):
    """按偏移轮转题序。

    同一知识点被反复诊断时，每次抽到不同的题。
    必须在最终排序（题型交错 + 换问法）之后做，
    否则轮转会被重排冲掉。
    """
    if not rot or len(pool) <= 1:
        return pool
    k = rot % len(pool)
    return pool[k:] + pool[:k]


def _prefer_other_type(pool, used_types):
    """把「没用过的题型」排到前面。

    同一点的第 2 问要换一种问法——如果第 1 问是数值题（算），
    第 2 问就该是记忆检验题（说），这样一次诊断既能看出「会不会算」，
    也能看出「记不记得」。

    这也是题量扩到 4–7 道/点之后才有的选择：
    之前每点只有 2 道，没有挑题型的余地。
    """
    if not used_types:
        return pool
    other = [q for q in pool if q.get("type") not in used_types]
    same = [q for q in pool if q.get("type") in used_types]
    return other + same


def _standard_coverage(graph, scope: str = "all") -> float:
    """有 standard 题的知识点占比。

    scope 决定分母：
      all  —— 477 个点全算。旧口径，被大量工具点稀释（分母虚大）。
      core —— 只算核心点。**升档该看这个数**：工具点的目标是熟练，
              本来就不需要「基础对→应用追」这道工序，把它们算进分母
              会让覆盖率永远够不着门槛，开关就成了死的。
    带缓存——每次出题都重算会拖慢诊断。
    """
    key = "_std_cov" if scope == "all" else f"_std_cov_{scope}"
    cached = getattr(graph, key, None)
    if cached is not None:
        return cached
    topics = graph.topics
    if scope in ("core", "tool"):
        topics = [t for t in topics if graph.tier_of(t["id"]) == scope]
    total = len(topics)
    if not total:
        setattr(graph, key, 0.0)
        return 0.0
    have = sum(1 for t in topics
               if any(q.get("difficulty") == "standard"
                      for q in graph.bank.get(t["id"], ())))
    cov = have / total
    setattr(graph, key, cov)
    return cov


def _step_up_enabled(graph, tid: str, session=None) -> bool:
    """这个点答对后要不要升档追问。

    两道条件：
    1. 层级：默认只对核心点升档。基础点答对就是会了，再追问是浪费时间。
    2. 数据：核心点的 standard 覆盖要够。不够时强行升档，会出现
       「有的点升、有的点不升」，同一学生的两次诊断行为不一致——
       这不是教学问题，是题库还没准备好这个功能。
    """
    if session is not None and MODES[session["mode"]].get("step_up_always"):
        # 练习模式：只要这个核心点有更难的题就追问，不看全库覆盖率。
        # 覆盖率门槛是为「诊断结论可推广」设的，练习不需要这个前提。
        return STEP_UP_SCOPE != "core" or graph.tier_of(tid) == "core"
    if not STEP_UP_ON_CORRECT:
        return False
    if STEP_UP_SCOPE in ("core", "tool") and graph.tier_of(tid) != STEP_UP_SCOPE:
        return False
    return _standard_coverage(graph, STEP_UP_SCOPE) >= STEP_UP_MIN_STANDARD_COVERAGE


def _pick_question(graph, session, tid, attempt):
    """挑一道没出过的题，返回 (题目, 是否被迫升难度)。

    第 2 题优先同级或更易；题库没有同级题时退回最接近的一题，并标记 escalated，
    汇总时这类判定要打问号——这是题库薄导致的，不是学生的问题。
    """
    bank = graph.bank.get(tid, [])
    seen = set(session["seen"])
    pool = [q for q in bank if q["id"] not in seen]
    if not pool:
        return None, False
    if attempt == 1:
        for level in ("basic", "standard", "advanced"):
            same = [q for q in pool if q.get("difficulty") == level]
            if same:
                # 第 1 问也要能轮到记忆检验题。
                # 只按猜测率排序的话，数值题永远在前，选择题永远抽不到——
                # 而题量扩到 4–7 道/点后，记忆检验题正是最该问的。
                # 做法：按题型分组，组内按猜测率排，组间**轮流取**，
                # 这样第一次问这个点时抽数值题（或按会话轮转），之后能轮到选择题。
                return _rotate_pool(_interleave_types(_by_guess(same)),
                                    session.get("rotate", 0))[0], False
        return _rotate_pool(_interleave_types(_by_guess(pool)),
                            session.get("rotate", 0))[0], False

    first = [entry for entry in session["log"]
             if entry["topicId"] == tid and entry["attempt"] == 1]
    base = DIFF_RANK.get(first[0].get("difficulty") or "standard", 1) if first else 1
    first_correct = bool(first and first[0].get("verdict") == "correct")

    # 第 1 题答对，就用更高一档的题确认：基础题会做，不等于标准题也会做。
    # 题库没有更高档时静默留在原档，不记 escalated——那是题库现状，不是被逼升难度。
    #
    # **覆盖门槛**：只有当有 standard 题的知识点占比够高时才升档。
    # 覆盖不足时，一部分点会升档、另一部分不会，同一个学生的两次诊断
    # 行为不一致，实验结论也无法推广。门槛不满足就静默不升档——
    # 这不是 bug，是「题库还没准备好这个功能」。
    if first_correct and _step_up_enabled(graph, tid, session):
        up = min(base + 1, max(DIFF_RANK.values()))
        harder = [q for q in pool if DIFF_RANK.get(q.get("difficulty"), 1) == up]
        if harder:
            return _by_guess(harder)[0], False

    allowed = [q for q in pool if DIFF_RANK.get(q.get("difficulty"), 1) <= base]
    escalated = not allowed
    pool = allowed or pool

    # 从上往下找最接近第 1 题难度的题，而不是一律退回基础题
    for level in sorted({DIFF_RANK.get(q.get("difficulty"), 1) for q in pool}, reverse=True):
        same = [q for q in pool if DIFF_RANK.get(q.get("difficulty"), 1) == level]
        if same:
            # 第 2 问换一种问法：把第 1 问没用过的题型排前面。
            # 必须在 _by_guess 之后应用——_by_guess 会按猜测率重排，
            # 换题型的效果会被它冲掉。
            ordered = _by_guess(same)
            used_types = {e.get("questionType") for e in first if e.get("questionType")}
            if used_types and VARY_TYPE_ON_CONFIRM:
                ordered = _prefer_other_type(ordered, used_types)
            return _rotate_pool(ordered, session.get("rotate", 0))[0], escalated
    ordered = _by_guess(pool)
    used_types = {e.get("questionType") for e in first if e.get("questionType")}
    if used_types and VARY_TYPE_ON_CONFIRM:
        ordered = _prefer_other_type(ordered, used_types)
    return _rotate_pool(ordered, session.get("rotate", 0))[0], escalated


def next_item(graph, session):
    """返回下一题 {topicId, question, attempt}；没有则返回 None。"""
    if len(session["log"]) >= session["limit"]:
        session["current"] = None
        return None
    while session["queue"]:
        tid = session["queue"][0]
        if session["state"].get(tid) in ("mastered", "assumed", "shaky", "weak"):
            session["queue"].pop(0)
            continue
        attempt = session["asked"].get(tid, 0) + 1
        if attempt > MODES[session["mode"]]["max_attempts"]:
            session["queue"].pop(0)
            continue
        question, escalated = _pick_question(graph, session, tid, attempt)
        if question is None:  # 没有题可用，跳过该点
            session["queue"].pop(0)
            session["state"].setdefault(tid, "untested")
            continue
        if escalated:
            session["escalated"] = session.get("escalated", 0) + 1
        session["current"] = {
            "topicId": tid,
            "questionId": question["id"],
            "attempt": attempt,
            "difficulty": question.get("difficulty"),
            "questionType": question.get("type"),
            "escalated": escalated,
        }
        return {"topicId": tid, "question": question, "attempt": attempt}
    session["current"] = None
    return None


# 回溯时是否也追 soft 先修。soft 只表示"相关、可迁移"，不是"不会它的原因"，
# 追 soft 会让根因跑到无关分支上（实测根因命中率因此掉约 10 个点）。
DESCEND_SOFT = False

# "不稳"时是否继续往上确认。打开后根因命中率上升，代价是题量增加。
DESCEND_SHAKY = True

# 第 1 题答对后，第 2 题是否升一档确认。
#
# 实测（tools/ladder_experiment.py，5 种子）：假设每个点都有 standard 题时，
# 三档准确率只从 69.2% 升到 69.5%（在 ±1.8% 的噪声内），根因命中却从 68.6% 掉到 55.6%，
# 每题还多问 1.12 题——学生会因为标准题失手被判"不稳"，进而把根因推到更远的祖先。
#
# 难度分层做完之后又复测了一次（tools/ladder_experiment_real.py，
# 8 种子 × 400 学生，用**真实题库**而不是注入假题）：
#   准确率 70.3% → 69.4%（-0.9%）
#   根因命中 68.3% → 66.7%（-1.6%）
#   漏诊 2.0% → 1.7%（-0.2%，唯一变好的指标）
#   每题多问 +0.22 题
#
# 结论：**难度真被校准过之后，升档确认依然不该开**。
# 原因不是校准不准，而是升档的收益结构本身不利——
# 基础题答对本来就大概率是会的，升一档问的却是自己没把握的题，
# 于是「会」被判成「不稳」，根因被推到更远的祖先。
# 漏诊率确实降了 0.2 个点，但准确率和根因命中的损失更大。
#
# 什么时候可以再考虑：standard 题覆盖到**绝大多数**知识点之后。
# 覆盖率是硬指标，不是感觉。写这条结论时只有 20% 的点有 standard 题
# （94/477）；2026-10-26 实测按旧口径（分母 477）是 52.6%，
# 只算自有题 14.3%。分层之后再拆一次分母，才看清问题在哪：
#
#                     全库存(含外部证据题)   只算自有题
#   全部 477 个点            52.6%             14.3%
#   核心点 309 个            61.8%              7.1%
#   工具点 168 个            35.7%             27.4%
#
# 也就是说：**外部题不开，升档无论怎么算都不该开**（核心点 7.1%）；
# 外部题开了，核心点刚好过 60% 线，但工具点仍低——这正好印证
# STEP_UP_SCOPE="core"：升档本来就只该发生在核心点上。
# 上面的负结论是在 20% 覆盖、每点只问 2 题的库上跑出来的，条件变了不能直接沿用。
# 要重开，必须先用 tools/ladder_experiment_real.py 在当前库上复跑，
# 而不是看到 0.618 > 0.6 就把开关打开。
STEP_UP_ON_CORRECT = False

# 升档只对核心点生效。工具点（一年级认数、口诀、单位换算）答对就是会了，
# 再追问一道更难的题不产生信息，只增加题量和挫败。
#
# 覆盖率的**分母也跟着变**：旧口径把 477 个点全算进去，157 个工具点
# 天然不会有 standard 题，等于给这个门槛预扣了 33 个百分点。
# 分层之后按 core 算，才是「应用点有没有题可升」的真实读数。
STEP_UP_SCOPE = "core"

# 升档题答错时怎么判：不稳（shaky）还是「只掌握到基础层」（mastered/basic）？
#
# **判不稳是错的**。学生基础题答对、应用题答错，说明他正好会到「基础」这一层，
# 不是「掌握得不牢」。判 shaky 会让他被追问先修、根因被推到更远的祖先——
# 而那里其实没问题。
#
# 这个开关只在 STEP_UP_ON_CORRECT 打开时才生效；升档关着时
# harder_failed 恒为 False，行为与旧版逐位相同。
STEP_UP_FAIL_IS_BASIC_ONLY = True

# 升档确认的启用门槛：standard 题至少要覆盖这个比例的知识点。
# 低于此值时升档只在部分点生效，会造成行为不一致。
STEP_UP_MIN_STANDARD_COVERAGE = 0.6

# 第 2 问是否换一种问法（题型）。
# 开启后同一点的第 2 问会避开第 1 问的题型，让「算」和「说」都被检验到。
#
# **为什么要开**：题量从每点 2 道扩到 4–7 道之后，
# 如果还按「猜测率低的题型优先」出题，同一点的第 1、2 问会都是数值题，
# 记忆检验题（选择题）永远抽不到——而那正是「记不记得公式」最直接的检验。
#
# **对照实验**（tools/simulate.py，同一代码库只切这一个开关）：
#   指标            关闭        开启
#   根因命中(基准)   84.8%      84.8%    ← 相同
#   假警报(基准)     12.8%      12.8%    ← 相同
#   根因命中(低噪声) 86.8%      87.5%    ← +0.7
#   严重误判(低噪声)  1.7%       1.7%    ← 相同
#   平均猜测率        9.8%      14.3%    ← +4.5
#   题型构成        填65/选28  填45/选47
#
# **结论：诊断准确性没有因为题型多样化而下降**（根因命中、假警报、严重误判全平或更好），
# 只有「猜测率」这个假设指标上升——而模拟里的猜测率是设定值，不是实测值。
# 所以这是净改善：换来的题型多样性是实打实的，付出的代价只是纸面指标。
VARY_TYPE_ON_CONFIRM = True

# 升档确认失败（基础对、标准错）时是否继续往上回溯。
# 这种失败更可能来自"题难"，不是"先修缺"；照常回溯会把根因推到更远的祖先。
DESCEND_ON_HARDER_FAILURE = False


def _enqueue_prereqs(graph, session, tid):
    """把未判定的 hard 先修插到队首。"""
    pending = []
    for edge in graph.prereqs.get(tid, ()):
        if not DESCEND_SOFT and edge["strength"] != "hard":
            continue
        pid = edge["prerequisiteId"]
        if session["state"].get(pid) in ("mastered", "assumed", "shaky", "weak"):
            continue
        if pid in session["queue"]:
            continue
        pending.append(pid)
    for pid in reversed(pending):
        session["queue"].insert(0, pid)


def apply_answer(graph, session, verdict, text=None) -> dict:
    """提交当前题的作答结果。verdict: correct / wrong / skip。"""
    if verdict not in ("correct", "wrong", "skip"):
        raise ValueError("verdict 只能是 correct / wrong / skip")
    current = session.get("current")
    if not current:
        raise ValueError("当前没有待作答的题目，请先调用 next_item")
    tid = current["topicId"]
    attempt = current["attempt"]
    if attempt != session["asked"].get(tid, 0) + 1:
        raise ValueError("作答顺序与出题记录不一致")

    session["asked"][tid] = attempt
    session["log"].append({
        "topicId": tid,
        "questionId": current["questionId"],
        "attempt": attempt,
        "verdict": verdict,
        "text": text,
        "difficulty": current.get("difficulty"),
        "questionType": current.get("questionType"),
        "escalated": current.get("escalated", False),
    })
    session["seen"].append(current["questionId"])
    session["current"] = None

    max_attempts = MODES[session["mode"]]["max_attempts"]

    if verdict == "correct":
        if attempt == 1 and CONFIRM_ON_CORRECT and max_attempts >= 2:
            return session  # 留一道确认题，先不判定
        first = next((e for e in session["log"]
                      if e["topicId"] == tid and e["attempt"] == 1), None)
        first_correct = bool(first and first["verdict"] == "correct")
        if attempt == 1 or first_correct:
            session["state"][tid] = "mastered"
            for anc in graph.ancestors(tid):
                session["state"].setdefault(anc, "assumed")
        else:
            session["state"][tid] = "shaky"
            # "不稳"说明这个点本身不牢，上游未必没问题，继续往上确认一层，
            # 否则一次猜对就会把真正的根因截断在中途。
            # 例外：如果第 2 题是升档题且答错了，更可能是题难而非先修缺，不回溯。
            # 注意（2026-10-26 实测）：这个 else 分支只在 first_correct 为 False
            # 时才会到达，所以 harder_failed 恒为 False，DESCEND_ON_HARDER_FAILURE
            # 目前是**死开关**（B/C 两组实验结果逐位相同）。
            # 真正「升档题答错」的路径在下面 attempt >= max_attempts 的分支，
            # 那里没有这个保护，shaky 会照常回溯。改语义前不要相信这个开关。
            first_diff = DIFF_RANK.get((first or {}).get("difficulty") or "basic", 0)
            now_diff = DIFF_RANK.get(current.get("difficulty") or "basic", 0)
            harder_failed = first_correct and now_diff > first_diff
            if (DESCEND_SHAKY and MODES[session["mode"]]["descend"]
                    and not (harder_failed and not DESCEND_ON_HARDER_FAILURE)):
                _enqueue_prereqs(graph, session, tid)
        _remove(session, tid)
        return session

    if attempt >= max_attempts:
        # 对称性：先对后错与先错后对都是一错一对，都只能算"不稳"。
        # 例外：后一题是升档题（比第一题更难）时，「后错」证明的是
        # 没到应用层，不是基础不牢——判「掌握到基础层」更准确，
        # 也不该继续往上回溯先修。见 STEP_UP_FAIL_IS_BASIC_ONLY。
        first = next((e for e in session["log"]
                      if e["topicId"] == tid and e["attempt"] == 1), None)
        if first and first["verdict"] == "correct":
            first_diff = DIFF_RANK.get(first.get("difficulty") or "basic", 0)
            now_diff = DIFF_RANK.get(current.get("difficulty") or "basic", 0)
            if now_diff > first_diff and STEP_UP_FAIL_IS_BASIC_ONLY:
                session["state"][tid] = "mastered"
                session.setdefault("basic_only", []).append(tid)
            else:
                session["state"][tid] = "shaky"
                if DESCEND_SHAKY and MODES[session["mode"]]["descend"]:
                    _enqueue_prereqs(graph, session, tid)
        else:
            session["state"][tid] = "weak"
            if MODES[session["mode"]]["descend"]:
                _enqueue_prereqs(graph, session, tid)
        _remove(session, tid)
    return session


def _remove(session, tid):
    while tid in session["queue"]:
        session["queue"].remove(tid)


def _depth_of(session, tid) -> str | None:
    """这个知识点上是**答对到哪一档**才算掌握的。

    为什么需要它：`STEP_UP_ON_CORRECT = False` 时，第 2 问只会出同档或更易的题，
    所以「 mastered 」的实际含义是「基础题连对两题」。
    题库 92.5% 是 basic，若不把这一层写出来，报告会告诉家长「已掌握」，
    而学生只是会做一步题——这是产品层面最不能犯的错。

    返回 None 表示没有可用的答对记录（例如 assumed 的祖先点）。
    """
    best = None
    for e in session["log"]:
        if e["topicId"] != tid or e.get("verdict") != "correct":
            continue
        r = DIFF_RANK.get(e.get("difficulty") or "basic", 0)
        best = r if best is None else max(best, r)
    if best is None:
        return None
    return {0: "basic", 1: "standard", 2: "advanced"}[best]


def summarize(graph, session) -> dict:
    """汇总：薄弱根因、不稳点、未测点、学习路径、按章分布。"""
    state = session["state"]
    # **筛查模式的范围就是目标本身**，不是目标 ∪ 全部祖先。
    #
    # 两种模式的语义不同：
    #   screen   —— 整册/整章摸底，问的就是这一册这些章，
    #               按章分布要反映「这一册考得怎么样」。
    #   diagnose —— 追根因，需要把祖先纳入范围来看谁没测。
    #
    # 之前 screen 也用了 scope(targets)，一直没暴露问题，
    # 因为当时跨学段边只有 11 条，七上点的祖先几乎不出七上。
    # 补齐跨学段承接链后（小学→初中 14 条），scope 会把整册小学都拉进来，
    # 七上筛查的按章分布从 6 章膨胀到 34 章——全是「未测」，
    # 报告变成噪音。所以这里按模式区分范围。
    if session["mode"] in ("screen", "practice"):
        covered = set(session["targets"])
    else:
        covered = graph.scope(session["targets"])
    weak = [tid for tid, st in state.items() if st == "weak"]
    weak_set = set(weak)
    shaky = [tid for tid, st in state.items() if st == "shaky"]
    mastered = [tid for tid, st in state.items() if st == "mastered"]
    assumed = [tid for tid, st in state.items() if st == "assumed"]
    untested = [tid for tid in covered if tid not in state]

    # 根因：本身薄弱或不稳，且上游没有同样薄弱/不稳的点（即最靠近源头的那一层）。
    # 只从 weak 里选会漏掉"上游只有一个不稳点"的常见情形。
    unstable = weak_set | set(shaky)
    roots = [tid for tid in (weak + shaky)
             if not (graph.ancestors(tid) & unstable)]

    # 学习路径：薄弱点 + 其未被验证的祖先，拓扑序（先修在前）
    candidates = set(weak)
    for tid in weak:
        candidates |= {a for a in graph.ancestors(tid) if state.get(a) in (None, "untested")}
    path = graph.topo_order([t for t in candidates if t in covered])

    by_chapter = {}
    first_index = {}
    for tid in covered:
        chapter = graph.by_id[tid].get("chapter", "未分章")
        first_index.setdefault(chapter, graph.index[tid])
        bucket = by_chapter.setdefault(chapter, {"mastered": 0, "shaky": 0, "weak": 0, "untested": 0})
        status = state.get(tid, "untested")
        bucket[status if status in bucket else "untested"] += 1
    by_chapter = dict(sorted(by_chapter.items(), key=lambda kv: first_index[kv[0]]))

    return {
        "mode": session["mode"],
        "targets": session["targets"],
        "asked_count": len(session["log"]),
        "escalated": session.get("escalated", 0),
        "mastered": graph.curriculum_order(mastered),
        # 掌握的分层口径：basic / standard / advanced。
        # 「已掌握」不再是一个二值结论，而是「掌握到哪一档」。
        "mastery_level": {tid: _depth_of(session, tid) for tid in mastered},
        "mastered_by_level": {
            lv: graph.curriculum_order([t for t in mastered if _depth_of(session, t) == lv])
            for lv in ("basic", "standard", "advanced")
        },
        "shaky": graph.curriculum_order(shaky),
        "weak": graph.curriculum_order(weak),
        "assumed": graph.curriculum_order(assumed),
        # 升档题问了但没答对：不是薄弱，是「到基础层为止」。
        # 与「只问了基础题就判掌握」不同——后者是题库没题，前者是学生实测。
        "step_up_failed": graph.curriculum_order(session.get("basic_only") or []),
        "untested": graph.curriculum_order(untested),
        "roots": graph.curriculum_order(roots),
        "path": path,
        "by_chapter": by_chapter,
        # 升档能力的自述：报告里要能回答「为什么这次只验证到基础层」。
        # 覆盖不够时不是学生的问题，是题库还没准备好。
        "step_up": {
            "scope": STEP_UP_SCOPE,
            "enabled": STEP_UP_ON_CORRECT,
            "coverage_all": round(_standard_coverage(graph, "all"), 4),
            "coverage_core": round(_standard_coverage(graph, "core"), 4),
            "threshold": STEP_UP_MIN_STANDARD_COVERAGE,
            "ready": _standard_coverage(graph, STEP_UP_SCOPE)
                     >= STEP_UP_MIN_STANDARD_COVERAGE,
        },
    }


def verdict_label(summary) -> str:
    """一句话结论，供 CLI / 报告使用。

    掌握必须带分层：只验证到基础层的点单独计数。
    不加这一句，"没有薄弱点"就会被读成"全都会了"——
    而题库九成是基础题，多数结论其实只到基础层。
    """
    lv = summary.get("mastered_by_level") or {}
    basic_only = len(lv.get("basic") or [])
    tail = (f"其中 {basic_only} 个只验证到基础层（未做标准题确认）。"
            if basic_only else "")
    if summary["weak"]:
        return (f"本次共出 {summary['asked_count']} 题，发现 {len(summary['weak'])} 个薄弱知识点，"
                f"根因 {len(summary['roots'])} 个，学习路径 {len(summary['path'])} 步。{tail}")
    if summary["shaky"]:
        return (f"本次共出 {summary['asked_count']} 题，没有明显漏洞，"
                f"但有 {len(summary['shaky'])} 个知识点不稳。{tail}")
    return f"本次共出 {summary['asked_count']} 题，范围内没有发现薄弱点。{tail}"
