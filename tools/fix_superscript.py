"""把中考题里的「上标丢失」还原成 LaTeX。

源数据的公式是位图，导出成纯文本后上标变成紧邻的普通数字：
    「抛物线y＝x2」  实际是  y=x²
    「方程x2﹣6x」   实际是  x²−6x

**不能全局替换**，因为：
    「A．1：4」   这是比，不是 A⁴
    「（o4）」   这是选项编号，不是 o⁴
    「a2」       可能是 a²，也可能是一个变量的名字

判据（只在高置信上下文里替换）：
1. 字母 ∈ {x, y, z, a, b, m, n, k, t}（单字母变量）
2. 数字是 2 或 3（初中只出现这两个指数）
3. 前文紧邻 ＝ / ﹣ / ＋ / （ / 方程 / 函数 等「表达式位置」

其余情况保留原样——**宁可显示成 x2，也不要把 A．1：4 改成 A⁴**。
上标还原错了比不还原更糟，因为用户会读到一个错误的数学表达式。
"""
from __future__ import annotations

import re

# 变量字母
VAR = "xyzabmnktc"
# 指数只能是 2 或 3
# 允许「2x3」这种「系数 + 变量 + 指数」形态：
# 系数存在时，变量前面是数字而不是行首。
# 负向前视只排除「ASCII 字母」和「数字+字母」之外的组合：
#   2x3    → x 前面是数字 2，允许（系数）
#   AB2    → B 前面是字母 A，不允许
#   x2     → x 前面是行首，允许
EXP = re.compile(rf"(?<![A-Za-z])([{VAR}])(\d)(?![0-9])")
LEAD_ALPHA = re.compile(r"[A-Za-z]$")
# 前置上下文：这些位置出现时，字母+数字几乎必然是幂
PRE = re.compile(
    r"(?:＝|﹣|－|\+|＋|\(|（|方程|函数|式|幂|的|与|和|÷|×|\*|"
    r"[0-9]|）|\))"          # 数字或右括号后接字母 → 是幂（如 2x3、(x-3)2）
    r"[\s　]*$"
)
# 除号后接变量再接数字，不一定是幂（a6÷a3 里 a3 是除数）
DIVISOR = re.compile(r"÷\s*[{VAR}]\d\s*$")


# 下标语境：句中已经出现了带下标的变量（如 x1、y1），
# 此时的「字母+数字」是下标不是幂。
# 「比较y1与y2的大小」里的 y2 是 y₂，不是 y²。
# 只有「1」能明确判定为下标（x1、y1 不会是 x¹ 的幂写法）。
# 「2」「3」两边都可能，所以不能拿它们当下标证据。
HAS_SUBSCRIPT = re.compile(r"[a-zA-Z]1(?![0-9])")


# 「（x﹣3）2」形态：整个括号是一个底数，后面跟的是指数。
PAREN_POW = re.compile(r"([（(][^（）()]{1,20}[）)])(\d)(?![0-9])")


def restore(text: str) -> tuple[str, int]:
    """返回 (还原后的文本, 替换处数)。"""
    n = 0
    # 全篇只要出现过下标变量，就整体禁用上标还原——
    # 这种题里「x1、x2、y1」到处都是，逐个判断上下文反而容易漏
    has_sub = bool(HAS_SUBSCRIPT.search(text))

    def sub(m: re.Match) -> str:
        nonlocal n
        letter, digit = m.group(1), m.group(2)
        if digit not in ("2", "3"):
            return m.group(0)
        if has_sub:
            return m.group(0)
        # 检查前置上下文
        head = text[: m.start()]
        if not PRE.search(head):
            return m.group(0)
        # 「a6÷a3」里的 a3 是除数不是幂
        if DIVISOR.search(head):
            return m.group(0)
        # 前一个字符是 ASCII 字母则不是幂（如 AB2）。
        # 注意：不能用 str.isalpha()——中文的 isalpha() 也是 True，
        # 「方程x2」会被误判成「方x的程2」那种情况。
        if head and LEAD_ALPHA.search(head):
            return m.group(0)
        n += 1
        return f"{letter}^{{{digit}}}"

    text = EXP.sub(sub, text)

    # 「（x﹣3）2」→ 「（x﹣3）^{2}」
    n2 = 0

    def sub2(m: re.Match) -> str:
        nonlocal n2
        if m.group(2) not in ("2", "3"):
            return m.group(0)
        n2 += 1
        return f"{m.group(1)}^{{{m.group(2)}}}"

    text = PAREN_POW.sub(sub2, text)
    return text, n + n2


def restore_all(text: str) -> tuple[str, int]:
    return restore(text)
