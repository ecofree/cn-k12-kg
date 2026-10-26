"""按知识点批量生成「计算型」题目。

**为什么自己生成而不是只找外部题**：
小学 202 个知识点里只有 196 个是理解/技能型，而 CMM 的小学题对应的是
具体口诀（"8和9的加、减法"）与具体算法（"笔算乘法(连续进位)"），
与我的知识点粒度对不上。字面检索只有 49% 命中且 1040 条多命中。

但计算型知识点（口诀、笔算、面积周长、估算）是**模板高度固定**的，
可以按知识点批量生成，而且：
1. **答案可机器验算**（带 verify 表达式）——这是相对「生成记忆题」的最大优势
2. **数字每次不同**——同一知识点的不同题不是重复，符合「不同问法」的要求
3. 覆盖的是「熟练度」，与记忆题互补

**生成原则**：
- 每个知识点一个生成器；生成器负责产出题干、答案、verify 表达式
- 题目数字随机，同一知识点生成的题互不相同
- verify 表达式与 answer 由**同一次计算**产生，不可能不一致
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import re
from functools import partial

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOPICS = ROOT / "data" / "topics.json"


def q_mul_digit(rnd: random.Random) -> tuple[str, str, str]:
    """两位数乘一位数（不进位/进位）"""
    a = rnd.randint(11, 49)
    b = rnd.randint(2, 9)
    return f"计算 {a} × {b} = （　　）", str(a * b), f"{a}*{b}"


def q_mul_estimate(rnd: random.Random) -> tuple[str, str, str]:
    """乘法估算（把一个因数看成整十数）"""
    a = rnd.randint(21, 89)
    b = rnd.randint(3, 9)
    est = round(a / 10) * 10
    return (f"估算 {a} × {b}，把 {a} 看成整十数 {est} 来估算，积约是多少？（只填数字）",
            str(est * b), f"{est}*{b}")


def q_div(rnd: random.Random) -> tuple[str, str, str]:
    """除法（能整除）"""
    b = rnd.randint(2, 9)
    q = rnd.randint(11, 89)
    a = b * q
    return f"计算 {a} ÷ {b} = （　　）", str(q), f"{a}/{b}"


def q_rect_area(rnd: random.Random) -> tuple[str, str, str]:
    """长方形面积"""
    a = rnd.randint(3, 20)
    b = rnd.randint(3, 20)
    return (f"一个长方形的长是 {a} 厘米，宽是 {b} 厘米，它的面积是多少平方厘米？（只填数字）",
            str(a * b), f"{a}*{b}")


def q_square_area(rnd: random.Random) -> tuple[str, str, str]:
    """正方形面积"""
    a = rnd.randint(3, 20)
    return (f"一个正方形的边长是 {a} 厘米，它的面积是多少平方厘米？（只填数字）",
            str(a * a), f"{a}*{a}")


def q_rect_perimeter(rnd: random.Random) -> tuple[str, str, str]:
    """长方形周长"""
    a = rnd.randint(3, 20)
    b = rnd.randint(3, 20)
    return (f"一个长方形的长是 {a} 厘米，宽是 {b} 厘米，它的周长是多少厘米？（只填数字）",
            str(2 * (a + b)), f"2*({a}+{b})")


def q_square_perimeter(rnd: random.Random) -> tuple[str, str, str]:
    """正方形周长"""
    a = rnd.randint(3, 20)
    return (f"一个正方形的边长是 {a} 厘米，它的周长是多少厘米？（只填数字）",
            str(4 * a), f"4*{a}")


def q_tri_area(rnd: random.Random) -> tuple[str, str, str]:
    """三角形面积"""
    a = rnd.randint(2, 30)
    h = rnd.choice([x for x in range(2, 30) if x % 2 == 0])
    return (f"一个三角形的底是 {a} 厘米，高是 {h} 厘米，它的面积是多少平方厘米？（只填数字）",
            str(a * h // 2), f"{a}*{h}/2")


def q_trap_area(rnd: random.Random) -> tuple[str, str, str]:
    """梯形面积"""
    a = rnd.randint(2, 20)
    b = a + rnd.randint(2, 16)
    h = rnd.choice([x for x in range(2, 30) if x % 2 == 0])
    return (f"一个梯形的上底是 {a} 厘米，下底是 {b} 厘米，高是 {h} 厘米，它的面积是多少平方厘米？（只填数字）",
            str((a + b) * h // 2), f"({a}+{b})*{h}/2")


def q_dec_add(rnd: random.Random) -> tuple[str, str, str]:
    """小数加法"""
    a = round(rnd.uniform(0.5, 9.5), 1)
    b = round(rnd.uniform(0.5, 9.5), 1)
    v = round(a + b, 1)
    return (f"计算 {a} + {b} = （　　）", f"{v}", f"{a}+{b}")


def q_dec_sub(rnd: random.Random) -> tuple[str, str, str]:
    """小数减法"""
    a = round(rnd.uniform(3.0, 9.5), 1)
    b = round(rnd.uniform(0.5, 2.9), 1)
    v = round(a - b, 1)
    return (f"计算 {a} − {b} = （　　）", f"{v}", f"{a}-{b}")


def q_dec_addsub(rnd: random.Random) -> tuple[str, str, str]:
    """小数加减混合——m4b.5.1

    历史上 GENERATORS 里 m4b.5.1 写过两次（q_dec_add 和 q_dec_sub），
    dict 字面量静默覆盖，实际只有减法进库——「小数加减法」只剩一半练习。
    这个生成器把两者合并，修复该覆盖 bug。
    """
    a = round(rnd.uniform(0.5, 9.5), 1)
    b = round(rnd.uniform(0.5, 9.5), 1)
    if rnd.random() < 0.5:
        v = round(a + b, 1)
        return f"计算 {a} + {b} = （　　）", f"{v}", f"{a}+{b}"
    if a < b:
        a, b = b, a
    v = round(a - b, 1)
    return f"计算 {a} − {b} = （　　）", f"{v}", f"{a}-{b}"


def q_frac_same(rnd: random.Random) -> tuple[str, str, str]:
    """同分母分数加减"""
    d = rnd.choice([4, 5, 6, 7, 8, 9])
    n1 = rnd.randint(1, d - 1)
    n2 = rnd.randint(1, d - n1)
    v = n1 + n2
    return (f"计算 {n1}/{d} + {n2}/{d} = （只填分子，分母不变）", str(v), f"{n1}+{n2}")


def q_mul_law(rnd: random.Random) -> tuple[str, str, str]:
    """乘法运算律（简便计算）"""
    a = rnd.randint(12, 49)
    b = rnd.randint(12, 49)
    c = rnd.randint(2, 9)
    v = (a + c) * b
    return (f"用简便方法计算 ({a} + {c}) × {b} = （　　）", str(v), f"({a}+{c})*{b}")


def q_div_exact(rnd: random.Random) -> tuple[str, str, str]:
    """有余数除法：商与余数

    **不能用 `a/b` 做 verify**——有余数时 a/b 是小数，与答案（商）对不上，
    verify_questions 会报 mismatch。改用「商×除数+余数=被除数」来验算。
    """
    b = rnd.randint(3, 9)
    q = rnd.randint(3, 20)
    r = rnd.randint(1, b - 1)
    a = b * q + r
    # verify 表达式要算出商：先验算式成立，再单独给商
    return (f"{a} ÷ {b} 的商是多少？（只填商）", str(q), f"({a}-{r})/{b}")


def q_percent(rnd: random.Random) -> tuple[str, str, str]:
    """百分数：求一个数的百分之几。

    **必须保证结果为整数**，否则 verify 算出小数（如 37.5），
    与截断后的答案对不上，会被 verify_questions 判为 mismatch。
    """
    p = rnd.choice([5, 10, 20, 25, 50])
    v = rnd.randint(2, 40)
    total = v * 100 // p
    if total * p % 100:
        total = v * 20
        p = 5
    return (f"{total} 的 {p}% 是多少？（只填数字）", str(total * p // 100),
            f"{total}*{p}/100")


def q_dec_mul_int(rnd: random.Random) -> tuple[str, str, str]:
    """小数乘整数"""
    a = round(rnd.uniform(0.5, 9.5), 1)
    b = rnd.randint(2, 9)
    v = round(a * b, 1)
    return (f"计算 {a} × {b} = （　　）", f"{v}", f"{a}*{b}")


def q_int_mul_dec(rnd: random.Random) -> tuple[str, str, str]:
    """整数乘小数"""
    a = rnd.randint(2, 12)
    b = round(rnd.uniform(0.5, 9.5), 1)
    v = round(a * b, 1)
    return (f"计算 {a} × {b} = （　　）", f"{v}", f"{a}*{b}")


def q_dec_div_int(rnd: random.Random) -> tuple[str, str, str]:
    """除数是整数的小数除法（能整除）"""
    b = rnd.randint(2, 9)
    q = round(rnd.uniform(1.2, 9.8), 1)
    a = round(q * b, 1)
    return (f"计算 {a} ÷ {b} = （　　）", f"{q}", f"{a}/{b}")


def q_int_div_dec(rnd: random.Random) -> tuple[str, str, str]:
    """一个数除以小数"""
    b = round(rnd.uniform(1.2, 4.8), 1)
    q = rnd.randint(3, 20)
    a = round(q * b, 1)
    return (f"计算 {a} ÷ {b} = （　　）", str(q), f"{a}/{b}")


def q_fraction_mul_int(rnd: random.Random) -> tuple[str, str, str]:
    """分数乘整数（结果是整数，答案可机器验算）

    **只生成结果为整数的情况**：结果为分数时，
    verify 表达式算出来是小数（1.25），与「化简后的分子」这种答案形式对不上，
    verify_questions 会报 mismatch。所以改成「结果刚好整除」——
    分子分母选成互质且分母能整除整数倍的形式。
    """
    import math
    d = rnd.choice([2, 3, 4, 5, 6, 8, 9])
    n = rnd.randint(1, d - 1)
    # 选 b 使 n*b 能被 d 整除
    b = d // math.gcd(n, d) * rnd.choice([1, 2, 3])
    val = n * b // d
    return (f"计算 {n}/{d} × {b} = （只填结果）", str(val), f"({n}*{b})/{d}")


def q_avg(rnd: random.Random) -> tuple[str, str, str]:
    """平均数（构造整数平均数）

    **为什么改**：旧版随机取 k 个数，`f"{v:g}"` 把 69.333… 截成 "69.3333"，
    与 verify 的精确值差 3e-5 > 1e-6 容差 → mismatch。
    库里现有的 4 道侥幸都是整数/有限小数，但生成器本身是坏的——
    换种子或加题量就会爆。改为先定平均数再凑数，平均必为整数。
    """
    k = rnd.randint(3, 5)
    avg = rnd.randint(60, 90)
    remain = avg * k
    nums = []
    for i in range(k - 1):
        slots = k - 1 - i
        lo = max(1, remain - 100 * slots)
        hi = min(100, remain - slots)
        x = rnd.randint(lo, hi)
        nums.append(x)
        remain -= x
    nums.append(remain)
    rnd.shuffle(nums)
    return (f"{'、'.join(str(x) for x in nums)} 这 {k} 个数的平均数是多少？（只填数字）",
            str(avg), "(" + "+".join(str(x) for x in nums) + f")/{k}")


def q_ratio(rnd: random.Random) -> tuple[str, str, str]:
    """比：求比值（只构造有限小数）

    **为什么改**：旧版 a、b 随机取，8:24=1/3 这类循环小数被 `:.4g`
    截成 "0.3333"，与 verify 差 3e-5 → mismatch。改为分母只取
    2^p·5^q 的数（约分后仍是），比值必为有限小数，%g 安全。
    """
    from math import gcd
    d = rnd.choice([2, 4, 5, 8, 10, 16, 20, 25, 40, 50])
    n = rnd.randint(1, 3 * d)
    g = gcd(n, d)
    n //= g
    d //= g
    k = rnd.randint(2, 9)
    a, b = n * k, d * k
    return (f"{a} : {b} 的比值是多少？（只填数字）", f"{a / b:g}", f"{a}/{b}")


def q_circle_perimeter(rnd: random.Random) -> tuple[str, str, str]:
    """圆的周长（取 π=3.14 的近似值，只填系数）"""
    r = rnd.randint(2, 10)
    return (f"一个圆的半径是 {r} 厘米，它的周长是多少厘米？（π 取 3.14，只填数字）",
            f"{r * 6.28:g}", f"{r}*6.28")


def q_circle_area(rnd: random.Random) -> tuple[str, str, str]:
    """圆的面积（π 取 3.14）"""
    r = rnd.randint(2, 10)
    return (f"一个圆的半径是 {r} 厘米，它的面积是多少平方厘米？（π 取 3.14，只填数字）",
            f"{round(r * r * 3.14, 2):g}", f"{r}*{r}*3.14")


def q_multiplication_rhyme(rnd: random.Random) -> tuple[str, str, str]:
    """乘法口诀（正着背）：几的口诀

    口诀是典型的「必须记住」内容——不是理解题，是提取题。
    """
    table = rnd.choice([2, 3, 4, 5, 6, 7, 8, 9])
    n = rnd.randint(2, 9)
    return (f"{table} 的乘法口诀中，{table} × {n} 等于多少？（只填数字）",
            str(table * n), f"{table}*{n}")


def q_multiplication_rhyme_rev(rnd: random.Random) -> tuple[str, str, str]:
    """乘法口诀（倒着背）：积推因数

    「倒着背」是口诀记忆的实际检验方式——
    正着背会了不代表倒着推会。
    """
    table = rnd.choice([2, 3, 4, 5, 6, 7, 8, 9])
    n = rnd.randint(2, 9)
    return (f"一句乘法口诀的积是 {table * n}，这句口诀是几乘几？（只填第一个因数）",
            str(table), f"{table*n}/{n}")


def q_add_sub_small(rnd: random.Random) -> tuple[str, str, str]:
    """20 以内加减法"""
    a = rnd.randint(5, 19)
    b = rnd.randint(2, 9)
    if rnd.random() < 0.5:
        return f"计算 {a} + {b} = （　　）", str(a + b), f"{a}+{b}"
    if a < b:
        a, b = b, a
    return f"计算 {a} − {b} = （　　）", str(a - b), f"{a}-{b}"


def q_add_sub_100(rnd: random.Random) -> tuple[str, str, str]:
    """100 以内加减法（整十 + 一位数）"""
    a = rnd.randint(21, 89)
    b = rnd.randint(11, 29)
    if rnd.random() < 0.5:
        return f"计算 {a} + {b} = （　　）", str(a + b), f"{a}+{b}"
    if a < b:
        a, b = b, a
    return f"计算 {a} − {b} = （　　）", str(a - b), f"{a}-{b}"


def q_double(rnd: random.Random) -> tuple[str, str, str]:
    """倍的认识：求一个数的几倍"""
    a = rnd.randint(3, 20)
    k = rnd.randint(2, 6)
    return (f"{a} 的 {k} 倍是多少？（只填数字）", str(a * k), f"{a}*{k}")


def q_multiplication_word(rnd: random.Random) -> tuple[str, str, str]:
    """乘法应用题：几个几相加"""
    a = rnd.randint(3, 15)
    k = rnd.randint(2, 8)
    return (f"{a} 个 {k} 相加是多少？（只填数字）", str(a * k), f"{a}*{k}")


# 知识点 → 生成器
# ---------------------------------------------------------------------------
# 第二批：补齐小学计算型知识点的缺口（原 58 个缺口点中可模板化的那些）
#
# 为什么补这批：CMM 小学题只有 4% 的答案能与解析互证，整源留在诊断外，
# 小学诊断实际只有自产题可用。而计算型知识点的题**带 verify 表达式可机器
# 验算**，答案证据远强于抓来的题——这是把小学诊断做实的正确路径。
# ---------------------------------------------------------------------------


def q_teen_minus_small(rnd: random.Random) -> tuple[str, str, str]:
    """十几减 5、4、3、2（一年级退位减法）"""
    a = rnd.randint(11, 19)
    b = rnd.choice([2, 3, 4, 5])
    return f"计算 {a} - {b} = （　　）", str(a - b), f"{a}-{b}"


def q_tens_addsub(rnd: random.Random) -> tuple[str, str, str]:
    """整十数加减整十数、整十数加一位数（100 以内简单加减）"""
    a = rnd.randint(1, 9) * 10
    b = rnd.randint(1, 9) * 10
    if rnd.random() < 0.5:
        return f"计算 {a} + {b} = （　　）", str(a + b), f"{a}+{b}"
    if a < b:
        a, b = b, a
    return f"计算 {a} - {b} = （　　）", str(a - b), f"{a}-{b}"


def q_sub_2digit(rnd: random.Random) -> tuple[str, str, str]:
    """笔算减法（两位数减两位数，含退位）"""
    a = rnd.randint(30, 99)
    b = rnd.randint(11, a - 1)
    return f"用竖式计算 {a} - {b} = （　　）", str(a - b), f"{a}-{b}"


def q_diff(rnd: random.Random) -> tuple[str, str, str]:
    """求两数相差几"""
    a = rnd.randint(20, 90)
    b = rnd.randint(10, a - 1)
    return (f"小红有 {a} 颗糖，小明有 {b} 颗糖，小红比小明多多少颗？（只填数字）",
            str(a - b), f"{a}-{b}")


def q_more_less(rnd: random.Random) -> tuple[str, str, str]:
    """求比一个数多（或少）几的数"""
    base = rnd.randint(10, 80)
    d = rnd.randint(3, 20)
    if rnd.random() < 0.5:
        return f"比 {base} 多 {d} 的数是多少？（只填数字）", str(base + d), f"{base}+{d}"
    return f"比 {base} 少 {d} 的数是多少？（只填数字）", str(base - d), f"{base}-{d}"


def q_mul_intro(rnd: random.Random) -> tuple[str, str, str]:
    """乘法的初步认识：几个几相加"""
    a = rnd.randint(2, 9)
    b = rnd.randint(2, 9)
    return (f"{b} 个 {a} 相加，用乘法算式表示是 {a} × {b} = （　　）",
            str(a * b), f"{a}*{b}")


def q_mul_add_sub(rnd: random.Random) -> tuple[str, str, str]:
    """乘加、乘减"""
    a = rnd.randint(2, 9)
    b = rnd.randint(2, 9)
    c = rnd.randint(1, 20)
    if rnd.random() < 0.5:
        return f"计算 {a} × {b} + {c} = （　　）", str(a * b + c), f"{a}*{b}+{c}"
    v = a * b - c
    return f"计算 {a} × {b} - {c} = （　　）", str(v), f"{a}*{b}-{c}"


def q_div_contain(rnd: random.Random) -> tuple[str, str, str]:
    """除法（包含分）：每几个一份，能分几份"""
    b = rnd.randint(2, 9)
    q = rnd.randint(2, 12)
    a = b * q
    return f"有 {a} 个苹果，每 {b} 个装一袋，可以装几袋？（只填数字）", str(q), f"{a}/{b}"


def q_div_share(rnd: random.Random) -> tuple[str, str, str]:
    """除法（平均分）：平均分成几份，每份几个"""
    b = rnd.randint(2, 9)
    q = rnd.randint(2, 12)
    a = b * q
    return (f"把 {a} 个苹果平均分给 {b} 个小朋友，每人分得几个？（只填数字）",
            str(q), f"{a}/{b}")


def q_addsub_4digit(rnd: random.Random) -> tuple[str, str, str]:
    """万以内简单的加、减法"""
    a = rnd.randint(1000, 9000)
    b = rnd.randint(100, 9000)
    if rnd.random() < 0.5:
        return f"计算 {a} + {b} = （　　）", str(a + b), f"{a}+{b}"
    if a < b:
        a, b = b, a
    return f"计算 {a} - {b} = （　　）", str(a - b), f"{a}-{b}"


def q_order_same(rnd: random.Random) -> tuple[str, str, str]:
    """同级运算（从左往右）"""
    a = rnd.randint(20, 90)
    b = rnd.randint(5, 40)
    c = rnd.randint(5, 30)
    return f"计算 {a} + {b} - {c} = （　　）", str(a + b - c), f"{a}+{b}-{c}"


def q_order_two(rnd: random.Random) -> tuple[str, str, str]:
    """两级运算（先乘除后加减）"""
    a = rnd.randint(2, 9)
    b = rnd.randint(2, 9)
    c = rnd.randint(5, 50)
    return f"计算 {c} + {a} × {b} = （　　）", str(c + a * b), f"{c}+{a}*{b}"


def q_order_paren(rnd: random.Random) -> tuple[str, str, str]:
    """含有小括号的两级运算"""
    a = rnd.randint(10, 50)
    b = rnd.randint(5, 30)
    c = rnd.randint(2, 9)
    return f"计算 ({a} + {b}) × {c} = （　　）", str((a + b) * c), f"({a}+{b})*{c}"


def q_mul_zero(rnd: random.Random) -> tuple[str, str, str]:
    """0 的乘法"""
    a = rnd.randint(1, 999)
    return f"计算 {a} × 0 = （　　）", "0", f"{a}*0"


def q_mul_zero_in(rnd: random.Random) -> tuple[str, str, str]:
    """笔算乘法：因数中间或末尾有 0"""
    a = rnd.choice([101, 202, 303, 404, 505, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 200])
    b = rnd.randint(2, 9)
    return f"用竖式计算 {a} × {b} = （　　）", str(a * b), f"{a}*{b}"


def q_addsub_relation(rnd: random.Random) -> tuple[str, str, str]:
    """加减法各部分间的关系（求加数 / 求减数）"""
    a = rnd.randint(20, 90)
    b = rnd.randint(10, a - 1)
    if rnd.random() < 0.5:
        return (f"已知 一个加数 + {b} = {a + b}，这个加数是多少？（只填数字）",
                str(a), f"{a + b}-{b}")
    return (f"已知 {a} - 减数 = {a - b}，减数是多少？（只填数字）",
            str(b), f"{a}-({a - b})")


def q_add_model(rnd: random.Random) -> tuple[str, str, str]:
    """加法模型：部分数 + 部分数 = 总数"""
    a = rnd.randint(10, 80)
    b = rnd.randint(10, 80)
    return f"男生有 {a} 人，女生有 {b} 人，一共有多少人？（只填数字）", str(a + b), f"{a}+{b}"


def q_price_model(rnd: random.Random) -> tuple[str, str, str]:
    """乘法模型：单价 × 数量 = 总价"""
    price = rnd.randint(2, 99)
    n = rnd.randint(2, 20)
    return f"一支笔 {price} 元，买 {n} 支要多少元？（只填数字）", str(price * n), f"{price}*{n}"


def q_speed_model(rnd: random.Random) -> tuple[str, str, str]:
    """乘法模型：速度 × 时间 = 路程"""
    v = rnd.randint(10, 90)
    t = rnd.randint(2, 9)
    return (f"一辆车每小时行 {v} 千米，{t} 小时行多少千米？（只填数字）",
            str(v * t), f"{v}*{t}")


def q_factor(rnd: random.Random) -> tuple[str, str, str]:
    """因数和倍数"""
    a = rnd.randint(2, 9)
    b = rnd.randint(2, 9)
    return (f"{a} × {b} = {a * b}，所以 {a * b} 是 {a} 的倍数。"
            f"{a * b} ÷ {a} 的商是多少？（只填数字）", str(b), f"{a * b}/{a}")


def q_frac_addsub(rnd: random.Random) -> tuple[str, str, str]:
    """异分母分数加减（分母成倍数关系，先通分）"""
    d1, d2 = rnd.choice([(2, 4), (2, 6), (3, 6), (4, 8), (3, 9), (2, 8)])
    n1 = rnd.randint(1, d1 - 1)
    n2 = rnd.randint(1, d2 - 1)
    lcm = d2  # 上面每组的 d2 都是 d1 的倍数
    v = n1 * (lcm // d1) + n2 * (lcm // d2)
    return (f"计算 {n1}/{d1} + {n2}/{d2}，通分后分母是 {lcm}，"
            f"分子是多少？（只填分子）", str(v), f"{n1}*{lcm // d1}+{n2}*{lcm // d2}")


def q_mul_frac(rnd: random.Random) -> tuple[str, str, str]:
    """一个数乘分数"""
    a = rnd.randint(2, 12)
    d = rnd.randint(2, 6)
    n = rnd.randint(1, d - 1)
    num, den = a * n, d
    g = math.gcd(num, den)
    num = num // g
    # verify 白名单不收 FloorDiv（//），所以这里用普通除法：
    # g 是最大公约数，a*n 必能被 g 整除，浮点结果与整数答案一致。
    return (f"计算 {a} × {n}/{d}，结果化为最简分数，分子是多少？（只填分子）",
            str(num), f"{a}*{n}/{g}")


def q_reciprocal(rnd: random.Random) -> tuple[str, str, str]:
    """倒数的认识"""
    a = rnd.randint(2, 9)
    b = rnd.randint(2, 9)
    return (f"{a}/{b} 的倒数是 {b}/{a}，{b}/{a} 的分子是多少？（只填数字）",
            str(b), f"{b}")


def q_div_int(rnd: random.Random) -> tuple[str, str, str]:
    """分数除以整数"""
    d = rnd.randint(2, 6)
    n = rnd.randint(1, d - 1)
    k = rnd.randint(2, 5)
    return (f"计算 {n}/{d} ÷ {k}，结果的分母是多少？（只填分母）",
            str(d * k), f"{d}*{k}")


def q_div_frac(rnd: random.Random) -> tuple[str, str, str]:
    """一个数除以分数（除数取单位分数，保证整除）"""
    a = rnd.randint(2, 9)
    d = rnd.randint(2, 6)
    return f"计算 {a} ÷ 1/{d} = （只填数字）", str(a * d), f"{a}*{d}"


def q_decimal_cmp_num(rnd: random.Random) -> tuple[str, str, str]:
    """小数的大小比较（用「较大的数」作答，保证可验算）"""
    a = round(rnd.uniform(0.1, 9.9), 1)
    b = round(rnd.uniform(0.1, 9.9), 1)
    if abs(a - b) < 0.3:
        b = round(a + 0.5, 1)
    v = max(a, b)
    return (f"{a} 和 {b} 两个数中较大的那个是多少？（只填数字）",
            f"{v}", f"max({a},{b})")


def q_frac_cmp_num(rnd: random.Random) -> tuple[str, str, str]:
    """简单的分数大小比较（同分子，用「较小的分母」作答）"""
    n = rnd.randint(1, 5)
    d1 = rnd.randint(n + 1, 9)
    d2 = rnd.randint(n + 1, 9)
    if d1 == d2:
        d2 = d1 + 1
    bigger = min(d1, d2)  # 同分子时，分母小的分数大
    return (f"{n}/{d1} 和 {n}/{d2} 中较大的那个分数，它的分母是多少？（只填分母）",
            str(bigger), f"min({d1},{d2})")



# ---------------------------------------------------------------------------
# 第三批：小学「解决问题」类知识点（应用题）
#
# 为什么单独一批：这些知识点的题**靠情境不靠术语**——「解决问题」不是题干里
# 会出现的词，词典法（map_by_lexicon）对它们结构性失效。但应用题的结构高度固定
# （一共/还剩/连续两问/进一法/两种思路），可以模板化生成，且答案仍可机器验算。
#
# 与计算题的区别：题干是文字情境，要让学生自己判断「该用什么运算」。
# 生成器返回 4 元组：(题干, 答案, verify, drill_kind)。
# ---------------------------------------------------------------------------


def q_word_total(rnd: random.Random) -> tuple[str, str, str, str]:
    """一共有多少（进位加法）——m1a.5.4 / m1a.4.4"""
    a = rnd.randint(5, 9)
    b = rnd.randint(11 - a, 9)          # 保证 a+b > 10，需要凑十
    scenes = ["花坛里", "树上", "操场上", "鱼缸里"]
    things = ["蝴蝶", "小鸟", "同学", "金鱼"]
    units = ["只", "只", "名", "条"]          # 量词不能省，否则题干不像人话
    i = rnd.randrange(4)
    return (f"{scenes[i]}有 {a} {units[i]}{things[i]}，又来了 {b} {units[i]}{things[i]}，"
            f"现在一共有多少{units[i]}{things[i]}？（只填数字）",
            str(a + b), f"{a}+{b}", "word")


def q_word_left(rnd: random.Random) -> tuple[str, str, str, str]:
    """还剩多少（退位减法）——m1b.2.4"""
    x = rnd.randint(1, 8)
    b = rnd.randint(x + 1, 9)           # b > 个位 → 必须退位
    a = 10 + x
    scenes = [("妈妈买了", "草莓", "吃了", "个"), ("一共有", "气球", "飞走了", "个"),
              ("树上有", "桃子", "摘下来", "个")]
    s, th, v, u = scenes[rnd.randrange(3)]
    return (f"{s} {a} {u}{th}，{v} {b} {u}{th}，还剩多少{u}{th}？（只填数字）",
            str(a - b), f"{a}-{b}", "word")


def q_word_choose_op(rnd: random.Random) -> tuple[str, str, str, str]:
    """选择一种运算解决问题——m2a.2.6"""
    kind = rnd.choice(["mul", "sub", "add"])
    if kind == "mul":
        a, b = rnd.randint(3, 9), rnd.randint(3, 9)
        return (f"教室每排摆 {a} 张课桌，一共摆了 {b} 排。一共有多少张课桌？"
                f"先判断用什么运算，再计算。（只填数字）",
                str(a * b), f"{a}*{b}", "word")
    if kind == "sub":
        a, b = rnd.randint(20, 60), rnd.randint(5, 19)
        return (f"停车场原来有 {a} 辆汽车，开走了 {b} 辆。还剩多少辆汽车？"
                f"先判断用什么运算，再计算。（只填数字）",
                str(a - b), f"{a}-{b}", "word")
    a, b = rnd.randint(20, 60), rnd.randint(5, 30)
    return (f"图书室有 {a} 本故事书，又买来 {b} 本。现在有多少本故事书？"
            f"先判断用什么运算，再计算。（只填数字）",
            str(a + b), f"{a}+{b}", "word")


def q_word_two_step(rnd: random.Random) -> tuple[str, str, str, str]:
    """连续两问，第二问要用第一问的结果——m2a.5.6"""
    a, b = rnd.randint(3, 9), rnd.randint(2, 6)
    c = rnd.randint(1, a * b - 1)
    return (f"每盒铅笔有 {a} 支，老师买了 {b} 盒。"
            f"（1）老师一共买了几支铅笔？"
            f"（2）发给同学们 {c} 支后，还剩多少支？"
            f"请直接算出第（2）问的答案。（只填数字）",
            str(a * b - c), f"{a}*{b}-{c}", "word")


def q_word_div_ceil(rnd: random.Random) -> tuple[str, str, str, str]:
    """有余数除法的进一法——m2b.1.3

    验证白名单不收 FloorDiv，所以 verify 写成 `(a-a%b)/b+1`：
    先减掉余数使之整除，再加 1。构造时保证有余数，该式才成立。
    """
    b = rnd.randint(4, 9)
    q = rnd.randint(2, 8)
    r = rnd.randint(1, b - 1)
    a = b * q + r
    return (f"二（1）班有 {a} 名同学去公园划船，每条船最多坐 {b} 人。"
            f"至少需要租几条船？（只填数字）",
            str(q + 1), f"({a}-{a}%{b})/{b}+1", "word")


def q_word_same_level(rnd: random.Random) -> tuple[str, str, str, str]:
    """同级运算，按事情发生的顺序列式——m3a.2.4"""
    if rnd.random() < 0.5:
        a, b, c = rnd.randint(30, 80), rnd.randint(5, 20), rnd.randint(5, 20)
        return (f"一本故事书有 {a} 页。小明第一天看了 {b} 页，第二天看了 {c} 页。"
                f"还剩多少页没有看？（只填数字）",
                str(a - b - c), f"{a}-{b}-{c}", "word")
    a, b, c = rnd.randint(10, 40), rnd.randint(10, 40), rnd.randint(5, 20)
    return (f"水果店上午运来 {a} 箱苹果，下午运来 {b} 箱，晚上又运来 {c} 箱。"
            f"一共运来多少箱苹果？（只填数字）",
            str(a + b + c), f"{a}+{b}+{c}", "word")


def q_word_two_level(rnd: random.Random) -> tuple[str, str, str, str]:
    """两级运算：先求一部分，再与另一部分合并——m3a.2.5"""
    if rnd.random() < 0.5:
        a, b = rnd.randint(3, 9), rnd.randint(2, 6)
        c = rnd.randint(a * b + 1, a * b + 40)
        return (f"每支钢笔 {a} 元，小明买了 {b} 支，付给售货员 {c} 元。"
                f"应找回多少元？（只填数字）",
                str(c - a * b), f"{c}-{a}*{b}", "word")
    a, b = rnd.randint(3, 9), rnd.randint(2, 6)
    c = rnd.randint(2, 20)
    return (f"每包饼干 {a} 元，妈妈买了 {b} 包；又买了一瓶 {c} 元的牛奶。"
            f"一共花了多少元？（只填数字）",
            str(a * b + c), f"{a}*{b}+{c}", "word")


def q_word_two_ways(rnd: random.Random) -> tuple[str, str, str, str]:
    """同一问题两种思路，结果应当一致——m3a.2.6

    构造：每排 a 张桌子，先摆 b 排，又搬来 c 排。
    思路一：先算原来的 a×b，再加上新搬的 a×c
    思路二：先算总排数 (b+c)，再乘每排的 a  →  a×(b+c)
    verify 用思路二的式子，answer 用思路一的结果，两条路径必须相等。
    """
    a = rnd.randint(3, 9)
    b = rnd.randint(3, 9)
    c = rnd.randint(2, 6)
    return (f"会议室每排摆 {a} 张桌子，先摆了 {b} 排；"
            f"又搬来 {c} 排同样的桌子。现在一共有多少张桌子？"
            f"（可以先用 {a}×{b}+{a}×{c}，也可以先用 {a}×({b}+{c})，两种思路结果相同）"
            f"（只填数字）",
            str(a * b + a * c), f"{a}*({b}+{c})", "word")


def q_word_fraction(rnd: random.Random) -> tuple[str, str, str, str]:
    """用分数解决实际问题——m3a.6.8"""
    d = rnd.randint(6, 12)
    n1 = rnd.randint(1, 3)
    n2 = rnd.randint(1, d - n1 - 1)
    scenes = ["一块巧克力", "一个披萨", "一张圆饼", "一个西瓜"]
    sc = scenes[rnd.randrange(4)]
    return (f"{sc}被平均分成 {d} 块。姐姐吃了 {n1} 块，妹妹吃了 {n2} 块。"
            f"两人一共吃了几分之几？（只填分子，分母是 {d}）",
            str(n1 + n2), f"{n1}+{n2}", "word")


def q_solid_faces(rnd: random.Random) -> tuple[str, str, str, str]:
    """立体图形的面数（查表，答案来自几何事实）——m1a.3.2

    这类题不是计算题，答案是几何常识。verify 直接写答案数值：
    它保证「答案字段与 verify 一致」，但**没有独立验算价值**，
    所以在 difficulty_basis 里注明这是事实性答案。
    """
    table = {"长方体": 6, "正方体": 6, "三棱柱": 5, "圆柱": 3, "圆锥": 2, "球": 1}
    name = rnd.choice(list(table))
    n = table[name]
    return (f"一个{name}的表面一共有几个面？（只填数字）",
            str(n), str(n), "word")



# ---- 第四批：题量下限补足 ----
# 背景：check_stage_policy 报 45 个点低于学段题量下限（小学 10 / 初中 17 / 高中 18）。
# 本批覆盖其中 28 个「可计算」点；几何识图、活动设计、纯概念类
# （三视图、全等判定、圆设计图案、获取数据的途径等）模板化会牺牲质量，
# 留作手写批次，不用生成器硬凑。
#
# 纪律与前三批相同：
#   1. 每题带 verify 表达式（机器可验算），answer 与 verify 由同一次计算产生
#   2. 构造保证答案是整数或有限小数——q_div_exact / q_percent 的教训：
#      verify 算出 37.5 而答案是 37 会被判 mismatch
#   3. 分数答案不可用：compare() 对 "2/3" 这类文本走字符串比对，
#      与 0.666… 必然 mismatch。概率类答案一律写成有限小数
#   4. 「两种思路」的题用另一条等价路径做 verify（q_word_two_ways 的传统）

def _fmt_frac(n: int, d: int) -> str:
    """约分后显示分数；整数只显示整数。"""
    g = math.gcd(n, d)
    n, d = n // g, d // g
    return str(n) if d == 1 else f"{n}/{d}"


def _fmt_dec(x: float) -> str:
    """有限小数答案去尾零：0.5400 → 0.54，3.0 → 3。

    只用于**构造上就是有限小数**的场景（分母为 2/4/5/8/10 的分数）。
    非有限小数（如 4/7）不要用本函数——4 位小数会被 1e-6 容差判 mismatch。
    """
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _cn_join(parts: list[str]) -> str:
    """中文列举：A、B 和 C。"""
    if len(parts) == 1:
        return parts[0]
    return "、".join(parts[:-1]) + "和" + parts[-1]


# 3-4-5 族直角边（模长构造用）
_PYTH_TRIPLES = [(3, 4, 5), (5, 12, 13), (6, 8, 10), (7, 24, 25), (8, 15, 17),
                 (9, 12, 15), (12, 16, 20), (10, 24, 26), (20, 21, 29), (15, 20, 25)]

# 三维勾股数（x, y, z, |(x,y,z)|）
_SPACE_TRIPLES = [(1, 2, 2, 3), (2, 3, 6, 7), (1, 4, 8, 9), (4, 4, 7, 9),
                  (2, 4, 4, 6), (3, 4, 12, 13), (6, 6, 7, 11), (2, 5, 14, 15)]

_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _sup(n: int) -> str:
    return str(n).translate(_SUP)


# ---------- 小学：识数与分数初步 ----------

def q_compose_number(rnd: random.Random) -> tuple[str, str, str]:
    """几和几组成 n / n 可以分成几和几——m1a.1.4"""
    n = rnd.randint(3, 10)
    a = rnd.randint(1, n - 1)
    if rnd.random() < 0.5:
        return f"{a} 和几组成 {n}？（只填数字）", str(n - a), f"{n}-{a}"
    return f"{n} 可以分成 {a} 和几？（只填数字）", str(n - a), f"{n}-{a}"


def _q_place_value(rnd: random.Random, units: list[tuple[str, int]]) -> tuple[str, str, str]:
    """按位值组成数。units 从高位到低位；首位非零，且至少两位非零。"""
    while True:
        digits = [rnd.randint(1 if i == 0 else 0, 8 if i == 0 else 9)
                  for i in range(len(units))]
        if sum(1 for d in digits if d > 0) >= 2:
            break
    parts = [f"{d} 个{units[i][0]}" for i, d in enumerate(digits) if d > 0]
    total = sum(d * units[i][1] for i, d in enumerate(digits))
    expr = "+".join(f"{d}*{units[i][1]}" for i, d in enumerate(digits))
    return f"由 {_cn_join(parts)}组成的数是（　　）", str(total), expr


def q_place_value_wan(rnd: random.Random) -> tuple[str, str, str]:
    """10000 以内数的组成——m2b.3.3"""
    return _q_place_value(rnd, [("千", 1000), ("百", 100), ("十", 10), ("个", 1)])


def q_place_value_yi_nei(rnd: random.Random) -> tuple[str, str, str]:
    """亿以内数的组成——m4a.1.1"""
    return _q_place_value(rnd, [("千万", 10_000_000), ("百万", 1_000_000),
                                ("十万", 100_000), ("万", 10_000),
                                ("千", 1000), ("百", 100), ("十", 10), ("个", 1)])


def q_place_value_yi_shang(rnd: random.Random) -> tuple[str, str, str]:
    """亿以上数的组成——m4a.1.2"""
    return _q_place_value(rnd, [("百亿", 10_000_000_000), ("十亿", 1_000_000_000),
                                ("亿", 100_000_000), ("千万", 10_000_000),
                                ("百万", 1_000_000), ("十万", 100_000),
                                ("万", 10_000), ("千", 1000), ("百", 100),
                                ("十", 10), ("个", 1)])


_FRACTIONS = {2: "二分之一", 3: "三分之一", 4: "四分之一", 5: "五分之一", 6: "六分之一"}
_NOUNS = [("月饼", "块"), ("苹果", "个"), ("糖果", "块"), ("橘子", "个"),
          ("蛋糕", "块"), ("桃子", "个")]


def _q_frac_context(rnd: random.Random, parts: int) -> tuple[str, str, str, str]:
    """平均分 parts 份，每份是几分之一；问每份几个（可验算）——m3a.6.1 / m3a.6.2"""
    k = rnd.randint(2, 10)
    total = parts * k
    noun, measure = rnd.choice(_NOUNS)
    return (f"把 {total} {measure}{noun}平均分成 {parts} 份，每份是这些{noun}的{_FRACTIONS[parts]}。"
            f"每份有多少{measure}{noun}？（只填数字）", str(k), f"{total}/{parts}", "word")


def q_half_context(rnd: random.Random) -> tuple[str, str, str, str]:
    """认识二分之一——m3a.6.1"""
    return _q_frac_context(rnd, 2)


def q_one_fraction_context(rnd: random.Random) -> tuple[str, str, str, str]:
    """认识几分之一——m3a.6.2"""
    return _q_frac_context(rnd, rnd.choice([3, 4, 5, 6]))


# ---------- 初中 ----------

def q_rational_mul(rnd: random.Random) -> tuple[str, str, str]:
    """有理数乘法（符号法则）——m7a.2.2.1"""
    def signed(lo: int, hi: int) -> int:
        v = rnd.randint(lo, hi)
        return v if rnd.random() < 0.5 else -v
    if rnd.random() < 0.12:
        # 零因子：任何数乘 0 都得 0
        b = signed(2, 9)
        return f"计算 0 × ({b}) = （　　）", "0", f"0*({b})"
    if rnd.random() < 0.25:
        a, b, c = signed(2, 6), signed(2, 6), signed(2, 5)
        return (f"计算 ({a}) × ({b}) × ({c}) = （　　）",
                str(a * b * c), f"({a})*({b})*({c})")
    a, b = signed(2, 12), signed(2, 9)
    return f"计算 ({a}) × ({b}) = （　　）", str(a * b), f"({a})*({b})"


def q_frac_mul_div(rnd: random.Random) -> tuple[str, str, str]:
    """分数乘除（构造整数结果）——m8a.18.2

    构造：(a/b)×(rb/a) = r，(a/b)÷(a/(rb)) = r。
    显示前约分，verify 按显示值写，保证「所见即所验」。
    """
    a, b = rnd.choice([(2, 3), (3, 4), (2, 5), (3, 5), (4, 5), (2, 7), (3, 8), (5, 6)])
    r = rnd.randint(2, 6)
    if rnd.random() < 0.5:
        n2, d2, op = r * b, a, "×"
    else:
        n2, d2, op = a, r * b, "÷"
    f1, f2 = _fmt_frac(a, b), _fmt_frac(n2, d2)
    return (f"计算 {f1} {op} {f2} = （　　）", str(r),
            f"({f1}){'*' if op == '×' else '/'}({f2})")


def q_frac_add_sub(rnd: random.Random) -> tuple[str, str, str]:
    """同分母分数加减（构造整数结果）——m8a.18.3"""
    d = rnd.choice([3, 4, 5, 6, 8, 10, 12])
    r = rnd.randint(2, 5)
    if rnd.random() < 0.5:
        n1 = rnd.randint(1, r * d - 1)
        n2 = r * d - n1
        return f"计算 {n1}/{d} + {n2}/{d} = （　　）", str(r), f"({n1}/{d})+({n2}/{d})"
    n1 = rnd.randint(r * d + 1, r * d + d - 1)
    n2 = n1 - r * d
    return f"计算 {n1}/{d} − {n2}/{d} = （　　）", str(r), f"({n1}/{d})-({n2}/{d})"


def q_word_two_var(rnd: random.Random) -> tuple[str, str, str, str]:
    """二元一次方程组应用：购物混合 / 和差——m7b.10.3"""
    if rnd.random() < 0.6:
        total = rnd.randint(8, 15)
        p1, p2 = rnd.choice([(3, 5), (2, 7), (4, 6), (3, 8), (5, 9), (2, 5)])
        n1, n2 = rnd.choice([("笔记本", "钢笔"), ("铅笔", "橡皮"), ("数学本", "英语本")])
        x = rnd.randint(1, total - 1)
        cost = p1 * x + p2 * (total - x)
        return (f"小明买{n1}和{n2}共 {total} 件，{n1}每件 {p1} 元，{n2}每件 {p2} 元，"
                f"一共花了 {cost} 元。小明买了多少{n1}？（只填数字）",
                str(x), f"({p2}*{total}-{cost})/({p2}-{p1})", "word")
    s = rnd.randint(40, 90)
    diff = rnd.randint(2, 12)
    if (s - diff) % 2:
        diff += 1
    return (f"甲班和乙班共有 {s} 名学生，甲班比乙班多 {diff} 名。乙班有多少名学生？（只填数字）",
            str((s - diff) // 2), f"({s}-{diff})/2", "word")


def q_word_inverse_prop(rnd: random.Random) -> tuple[str, str, str, str]:
    """反比例应用：路程一定 / 总量一定——m9a.27.3"""
    if rnd.random() < 0.5:
        v1 = rnd.randint(40, 80)
        t1 = rnd.randint(3, 8)
        S = v1 * t1
        divs = [d for d in range(2, 10) if S % d == 0 and S // d != v1]
        if not divs:
            return q_word_inverse_prop(rnd)
        t2 = rnd.choice(divs)
        return (f"一辆汽车从甲地开往乙地，每小时行 {v1} 千米，{t1} 小时到达。"
                f"如果每小时行 {S // t2} 千米，多少小时可以到达？（只填数字）",
                str(t2), f"{v1}*{t1}/{S // t2}", "word")
    e1 = rnd.randint(4, 12)
    d1 = rnd.randint(6, 15)
    S = e1 * d1
    divs = [d for d in range(3, 14) if S % d == 0 and S // d != e1]
    if not divs:
        return q_word_inverse_prop(rnd)
    d2 = rnd.choice(divs)
    return (f"修一条公路，甲队每天修 {e1} 米，{d1} 天修完。如果由乙队每天修 {S // d2} 米，"
            f"多少天可以修完？（只填数字）", str(d2), f"{e1}*{d1}/{S // d2}", "word")


def q_quartile(rnd: random.Random) -> tuple[str, str, str]:
    """四分位数（半分法中位数）——m8b.24.3

    8 个数据：Q1 = 第 2、3 个的平均，Q2 = 第 4、5 个的平均，Q3 = 第 6、7 个的平均。
    题干里写明用的是哪个一半的中位数，避免各教材分位方法不一致的歧义。
    """
    vals = sorted(rnd.sample(range(1, 60), 8))
    which = rnd.choice([("Q1", 1), ("Q2", 3), ("Q3", 5)])
    name, i = which
    how = {"Q1": "下四分位数 Q1（较小一半数据的中位数）",
           "Q2": "中位数 Q2（中间两个数的平均数）",
           "Q3": "上四分位数 Q3（较大一半数据的中位数）"}[name]
    val = (vals[i] + vals[i + 1]) / 2
    ans = str(int(val)) if val == int(val) else f"{val}"
    return (f"数据 {'、'.join(map(str, vals))} 已按从小到大排列。把这组数据分成四等份，"
            f"{how}是多少？（只填数字）", ans, f"({vals[i]}+{vals[i+1]})/2")


def q_freq_prob(rnd: random.Random) -> tuple[str, str, str, str]:
    """用频率估计概率——m9b.34.3"""
    n = rnd.choice([100, 200, 400, 500, 1000])
    k = rnd.randint(round(n * 0.25), round(n * 0.75))
    scene = rnd.choice([
        f"某运动员投篮 {n} 次，命中 {k} 次。估计该运动员投篮一次的命中率约是（　　）",
        f"抛掷一枚硬币 {n} 次，正面朝上 {k} 次。估计抛掷这枚硬币正面朝上的概率约是（　　）",
        f"质检部门抽检 {n} 件产品，合格 {k} 件。估计这批产品的合格率约是（　　）",
    ])
    return scene, _fmt_dec(k / n), f"{k}/{n}", "word"


# ---------- 高中 ----------

def q_piecewise(rnd: random.Random) -> tuple[str, str, str]:
    """分段函数求值（分界点及两侧）——mh1.3.1.2

    f(x) = x²（x < c），2x + 1（x ≥ c）。
    问点取分界点 c（右支）、c−1（左支）、c+2（右支内），
    考的是「先判断属于哪一段」——表示法的核心。
    """
    c = rnd.randint(-3, 3)
    branch = "x²（当 x < {c} 时），2x + 1（当 x ≥ {c} 时）".format(c=c)
    if rnd.random() < 0.4:
        return (f"设函数 f(x) = {branch}，则 f({c}) = （　　）",
                str(2 * c + 1), f"2*{c}+1")
    if rnd.random() < 0.5:
        return (f"设函数 f(x) = {branch}，则 f({c} − 1) = （　　）",
                str((c - 1) ** 2), f"({c}-1)**2")
    return (f"设函数 f(x) = {branch}，则 f({c} + 2) = （　　）",
            str(2 * (c + 2) + 1), f"2*({c}+2)+1")


def q_irrational_power(rnd: random.Random) -> tuple[str, str, str]:
    """无理指数幂（分数指数幂）——mh1.4.1.2"""
    mode = rnd.random()
    if mode < 0.5:
        b = rnd.randint(2, 5)
        n = rnd.choice([2, 3])
        m = rnd.randint(1, n)
        a = b ** n
        return f"计算 {a} 的 {m}/{n} 次方 = （　　）", str(b ** m), f"{b}**{m}"
    if mode < 0.75:
        b = rnd.randint(2, 5)
        n = rnd.choice([2, 3])
        return (f"已知 a 的 1/{n} 次方等于 {b}（a > 0），则 a = （　　）",
                str(b ** n), f"{b}**{n}")
    # 运算性质：同底数幂相乘指数相加。verify 走另一条路（直接算 b 的 n 次方）
    b = rnd.randint(2, 4)
    n = rnd.choice([2, 3])
    m = rnd.randint(1, n)
    a = b ** n
    return (f"计算 {a} 的 {m}/{n} 次方 × {a} 的 {n - m}/{n} 次方 = （　　）",
            str(a), f"{b}**{n}")


def q_vector_sub_mag(rnd: random.Random) -> tuple[str, str, str]:
    """向量减法：|a−b|（勾股构造）或反向求坐标——mh2.6.2.2"""
    if rnd.random() < 0.6:
        x1, y1, h = rnd.choice(_PYTH_TRIPLES)
        x2 = rnd.randint(-5, 5)
        y2 = rnd.randint(-5, 5)
        return (f"已知向量 a=({x1 + x2}, {y1 + y2})，b=({x2}, {y2})，则 |a−b| = （　　）",
                str(h), f"sqrt(({x1})**2+({y1})**2)")
    x1, y1 = rnd.randint(-6, 6), rnd.randint(-6, 6)
    x2, y2 = rnd.randint(-6, 6), rnd.randint(-6, 6)
    return (f"已知向量 a=({x1 + x2}, {y1 + y2})，b=({x2}, {y2})，则 a−b 的纵坐标是（　　）",
            str(y1), f"({y1 + y2})-({y2})")


def q_vector_scalar(rnd: random.Random) -> tuple[str, str, str]:
    """向量数乘：模 / 坐标 / 反向求系数——mh2.6.2.3"""
    mode = rnd.random()
    if mode < 0.4:
        x, y, h = rnd.choice(_PYTH_TRIPLES)
        lam = rnd.randint(2, 4)
        return (f"已知 a=({x}, {y})，则 |{lam}a| = （　　）",
                str(lam * h), f"{lam}*sqrt({x}**2+{y}**2)")
    if mode < 0.7:
        x, y = rnd.randint(-5, 5), rnd.randint(-5, 5)
        lam = rnd.choice([-3, -2, 2, 3])
        return (f"已知 a=({x}, {y})，则 {lam}a 的纵坐标是（　　）",
                str(lam * y), f"{lam}*({y})")
    x = rnd.randint(-6, 6) or 2
    y = rnd.randint(-6, 6) or 3
    lam = rnd.choice([-3, -2, 2, 3])
    return (f"已知 a=({x}, {y})，λa=({lam * x}, {lam * y})，则 λ = （　　）",
            str(lam), f"({lam * x})/({x})")


def q_dot_product(rnd: random.Random) -> tuple[str, str, str]:
    """平面向量数量积的坐标表示——mh2.6.3.5"""
    if rnd.random() < 0.3:
        # 反向：垂直反求坐标。a=(k, b1) 与 b=(b1, b2) 垂直 → k = −b2
        b1 = rnd.randint(1, 5)
        b2 = rnd.randint(1, 6)
        return (f"已知平面向量 a=(k, {b1}) 与 b=({b1}, {b2}) 垂直，则 k = （　　）",
                str(-b2), f"-({b1})*({b2})/({b1})")
    a1, a2 = rnd.randint(-5, 5), rnd.randint(-5, 5)
    b1, b2 = rnd.randint(-5, 5), rnd.randint(-5, 5)
    if (a1, a2) == (0, 0):
        a1 = 3
    if (b1, b2) == (0, 0):
        b2 = 4
    return (f"已知平面向量 a=({a1}, {a2})，b=({b1}, {b2})，则 a·b = （　　）",
            str(a1 * b1 + a2 * b2), f"{a1}*{b1}+{a2}*{b2}")


def q_complex_polar(rnd: random.Random) -> tuple[str, str, str]:
    """复数三角形式的乘除：模相乘/相除、辐角相加——mh2.7.3.2"""
    t1 = rnd.choice([30, 45, 60, 90])
    t2 = rnd.choice([30, 45, 60])
    r1, r2 = rnd.randint(2, 6), rnd.randint(2, 6)
    z1 = f"{r1}(cos{t1}° + i sin{t1}°)"
    z2 = f"{r2}(cos{t2}° + i sin{t2}°)"
    kind = rnd.random()
    if kind < 0.4:
        return (f"复数 z₁ = {z1}，z₂ = {z2}，则 z₁·z₂ 的模是（　　）",
                str(r1 * r2), f"{r1}*{r2}")
    if kind < 0.7:
        return (f"复数 z₁ = {z1}，z₂ = {z2}，则 z₁·z₂ 的辐角主值是（　　）度",
                str(t1 + t2), f"{t1}+{t2}")
    # 相除：模相除（构造整除）
    r2b = rnd.randint(2, 4)
    r1b = r2b * rnd.randint(2, 3)
    z1b = f"{r1b}(cos{t1}° + i sin{t1}°)"
    z2b = f"{r2b}(cos{t2}° + i sin{t2}°)"
    return (f"复数 z₁ = {z1b}，z₂ = {z2b}，则 z₁÷z₂ 的模是（　　）",
            str(r1b // r2b), f"{r1b}/{r2b}")


def q_space_vector_linear(rnd: random.Random) -> tuple[str, str, str]:
    """空间向量线性运算：|a+b|（三维勾股）或反向求坐标——mh3.1.1.1"""
    if rnd.random() < 0.5:
        x, y, z, h = rnd.choice(_SPACE_TRIPLES)
        xa, ya, za = rnd.randint(0, x), rnd.randint(0, y), rnd.randint(0, z)
        return (f"已知空间向量 a=({xa}, {ya}, {za})，b=({x - xa}, {y - ya}, {z - za})，"
                f"则 |a+b| = （　　）", str(h), f"sqrt({x}**2+{y}**2+{z}**2)")
    a1, a2, a3 = rnd.randint(-3, 3), rnd.randint(-3, 3), rnd.randint(1, 4)
    lam = rnd.choice([2, 3])
    t3 = lam * a3 + rnd.randint(-4, 4)
    return (f"已知空间向量 a=({a1}, {a2}, {a3})，且 {lam}a + b = ({lam * a1}, {lam * a2}, {t3})，"
            f"则 b 的竖坐标是（　　）", str(t3 - lam * a3), f"{t3}-{lam}*{a3}")


def q_space_dot(rnd: random.Random) -> tuple[str, str, str]:
    """空间向量数量积——mh3.1.1.2"""
    a = [rnd.randint(-4, 4) for _ in range(3)]
    b = [rnd.randint(-4, 4) for _ in range(3)]
    if a == [0, 0, 0]:
        a[0] = 3
    if b == [0, 0, 0]:
        b[1] = 4
    return (f"已知空间向量 a=({a[0]}, {a[1]}, {a[2]})，b=({b[0]}, {b[1]}, {b[2]})，"
            f"则 a·b = （　　）",
            str(a[0] * b[0] + a[1] * b[1] + a[2] * b[2]),
            f"{a[0]}*{b[0]}+{a[1]}*{b[1]}+{a[2]}*{b[2]}")


def q_line_relation(rnd: random.Random) -> tuple[str, str, str]:
    """两直线平行/垂直的判定（反向求系数）——mh3.2.1.2

    平行：kx + b1y = 0 与 b2x + y = 0 平行 → k = b1·b2（A1B2 = A2B1）
    垂直：kx − b1y = 0 与 b2x + y = 0 垂直 → k = b1/b2（A1A2 + B1B2 = 0，b2 | b1）
    """
    if rnd.random() < 0.5:
        b1 = rnd.randint(1, 4)
        b2 = rnd.randint(1, 4)
        return (f"若直线 kx + {b1}y = 0 与直线 {b2}x + y = 0 平行，则 k = （　　）",
                str(b1 * b2), f"{b1}*{b2}")
    b2 = rnd.randint(1, 4)
    k = rnd.randint(2, 6)
    b1 = k * b2
    return (f"若直线 kx − {b1}y = 0 与直线 {b2}x + y = 0 垂直，则 k = （　　）",
            str(k), f"{b1}/{b2}")


def q_point_slope(rnd: random.Random) -> tuple[str, str, str]:
    """点斜式：过定点求截距——mh3.2.2.1"""
    x0 = rnd.randint(-4, 4)
    y0 = rnd.randint(-4, 6)
    k = rnd.randint(-4, 4) or 2
    b = y0 - k * x0
    if rnd.random() < 0.5:
        return (f"直线 y = {k}x + b 经过点 ({x0}, {y0})，且与直线 y = {k}x − 2 平行，"
                f"则 b = （　　）", str(b), f"{y0}-({k})*({x0})")
    return (f"直线经过点 ({x0}, {y0}) 且斜率为 {k}，则它在 y 轴上的截距是（　　）",
            str(b), f"{y0}-({k})*({x0})")


def q_composite_derivative(rnd: random.Random) -> tuple[str, str, str]:
    """简单复合函数的导数——mh4.5.2.3"""
    x0 = rnd.randint(-2, 3)
    mode = rnd.random()
    if mode < 0.5:
        # f(x) = (ax+b)^n，f'(x0) = n(ax0+b)^(n−1)·a
        a = rnd.randint(2, 4)
        b = rnd.randint(-4, 5)
        n = rnd.choice([2, 3])
        inner = f"{a}x" + (f"+{b}" if b >= 0 else f"−{abs(b)}")
        return (f"设 f(x) = ({inner}){_sup(n)}，则 f'({x0}) = （　　）",
                str(n * (a * x0 + b) ** (n - 1) * a),
                f"{n}*({a}*{x0}+{b})**{n-1}*{a}")
    if mode < 0.8:
        # f(x) = (x²+c)^n，f'(x0) = n(x0²+c)^(n−1)·2x0
        # 注意：verify 里 x0 为负时必须写成 ({x0})**2——直接写 -1**2 会被
        # Python 解析成 -(1**2) = -1（一元负号优先级低于 **），与答案对不上。
        c = rnd.randint(1, 4)
        n = rnd.choice([2, 3])
        return (f"设 f(x) = (x{_sup(2)}+{c}){_sup(n)}，则 f'({x0}) = （　　）",
                str(n * (x0 ** 2 + c) ** (n - 1) * 2 * x0),
                f"{n}*(({x0})**2+{c})**{n-1}*2*{x0}")
    # f(x) = √(ax+b)，f'(x0) = a/(2√(ax0+b))；构造 ax0+b 为完全平方，
    # 且 s 只取 2^p·5^q 保证 a/(2s) 是有限小数（分母含 3/7 会循环）
    s = rnd.choice([2, 4, 5, 8, 10])
    a = rnd.randint(2, 5)
    b = s * s - a * x0
    inner = f"{a}x" + (f"+{b}" if b >= 0 else f"−{abs(b)}")
    return (f"设 f(x) = √({inner})，则 f'({x0}) = （　　）",
            _fmt_dec(a / (2 * s)), f"{a}/(2*sqrt({a}*{x0}+{b}))")


def q_permutation(rnd: random.Random) -> tuple[str, str, str, str]:
    """排列数——mh5.6.2.2"""
    n = rnd.randint(4, 8)
    r = rnd.randint(2, min(4, n))
    expr = "*".join(str(n - i) for i in range(r))
    val = eval(expr)  # noqa: S307 —— 本地构造的纯数字表达式
    roles = {2: "一等奖和二等奖", 3: "第一、二、三名", 4: "第一到第四名"}
    scene = rnd.choice([
        f"从 {n} 名同学中选出 {r} 名分别获得{roles[r]}",
        f"从 {n} 本不同的书中选出 {r} 本摆在书架的一排上",
    ])
    return (f"{scene}，有多少种不同的结果？（只填数字）", str(val), expr, "word")


def q_combination(rnd: random.Random) -> tuple[str, str, str, str]:
    """组合数——mh5.6.2.3"""
    n = rnd.randint(5, 8)
    r = rnd.randint(2, min(4, n - 1))
    num = "*".join(str(n - i) for i in range(r))
    den = "*".join(str(i + 1) for i in range(r))
    val = eval(num) // eval(den)  # noqa: S307 —— 本地构造的纯数字表达式
    scene = rnd.choice([
        f"从 {n} 名同学中选出 {r} 名参加竞赛",
        f"从 {n} 支球队中选出 {r} 支进入决赛",
    ])
    return (f"{scene}，有多少种不同的选法？（只填数字）", str(val), f"({num})/({den})", "word")


def q_conditional_prob(rnd: random.Random) -> tuple[str, str, str, str]:
    """条件概率——mh5.7.1.1

    只构造**有限小数**结果（分母 2/4/5）：4/7 这类循环小数
    用 _fmt_dec 截断后会被 1e-6 容差判 mismatch。
    """
    if rnd.random() < 0.5:
        # 骰子：A = 点数大于 2（样本点 3,4,5,6）
        desc, k = rnd.choice([
            ("点数大于 4", 2), ("点数小于 5", 1), ("点数是 3 的倍数", 1), ("点数等于 6", 1)])
        return (f"掷一枚质地均匀的骰子，已知点数大于 2，求{desc}的概率是（　　）",
                _fmt_dec(k / 4), f"{k}/4", "word")
    # 摸球不放回：已知第一次红球，第二次也红球
    red, white = rnd.choice([(3, 2), (4, 2), (4, 1), (2, 1), (5, 1)])
    return (f"袋中有 {red} 个红球和 {white} 个白球，除颜色外完全相同。随机摸出一个球后不放回，"
            f"已知第一次摸到红球，求第二次也摸到红球的概率是（　　）",
            _fmt_dec((red - 1) / (red + white - 1)),
            f"{red - 1}/{red + white - 1}", "word")


def q_total_prob(rnd: random.Random) -> tuple[str, str, str, str]:
    """全概率公式——mh5.7.1.2"""
    p1 = rnd.choice([0.4, 0.5, 0.6, 0.7])
    p2 = round(1 - p1, 2)
    q1 = rnd.choice([0.85, 0.9, 0.95, 0.98])
    q2 = rnd.choice([0.8, 0.85, 0.9, 0.95])
    return (f"某产品由甲、乙两条生产线生产，甲生产线占总产量的 {p1:.0%}，"
            f"乙生产线占 {p2:.0%}。甲生产线的合格率是 {q1:.0%}，乙生产线的合格率是 {q2:.0%}。"
            f"随机抽取一件产品，它是合格品的概率是（　　）",
            _fmt_dec(p1 * q1 + p2 * q2), f"{p1}*{q1}+{p2}*{q2}", "word")


GENERATORS: dict[str, object] = {
    # 三上 / 四上 乘法
    "m3a.4.1": q_mul_digit, "m4a.3.1": q_mul_digit, "m4a.3.2": q_mul_digit,
    "m3a.4.2": q_mul_digit, "m3a.4.3": q_mul_digit, "m3a.4.4": q_mul_digit,
    "m3a.4.7": q_mul_estimate, "m4a.3.5": q_mul_estimate,
    # 除法
    "m3b.2.1": q_div, "m3b.2.2": q_div, "m4b.1.1": q_div,
    # 面积周长
    "m3b.4.2": q_rect_area, "m3b.4.3": q_rect_area,
    "m3b.3.2": q_rect_perimeter,
    "m5a.6.1": q_rect_area, "m5a.6.2": q_tri_area,
    "m5a.6.3": q_trap_area, "m5a.6.4": q_rect_area,
    # 小数
    "m3b.6.3": q_dec_add, "m4b.5.1": q_dec_addsub,
    "m5a.2.1": q_dec_mul_int, "m5a.2.2": q_dec_mul_int, "m5a.2.3": q_int_mul_dec,
    "m5a.3.1": q_dec_div_int, "m5a.3.2": q_int_div_dec,
    # 分数
    "m3a.6.5": q_frac_same, "m3a.6.6": q_frac_same,
    "m6a.2.1": q_fraction_mul_int,
    # 运算律 / 综合
    "m4b.2.1": q_mul_law,
    # 余数除法
    "m2b.1.1": q_div_exact,
    # 百分数 / 统计
    "m6a.6.2": q_percent, "m4b.6.1": q_avg,
    # 比和比例
    "m6b.1.1": q_ratio,
    # 圆
    "m6a.5.2": q_circle_perimeter, "m6a.5.3": q_circle_area,
    # 乘法口诀（正着背 + 倒着背）
    "m2a.5.1": q_multiplication_rhyme, "m2a.2.2": q_multiplication_rhyme,
    "m2a.2.3": q_multiplication_rhyme, "m2a.2.5": q_multiplication_rhyme,
    "m2a.5.2": q_multiplication_rhyme, "m2a.5.3": q_multiplication_rhyme,
    "m2a.3.5": q_multiplication_rhyme_rev, "m2a.3.6": q_multiplication_rhyme_rev,
    "m2a.5.4": q_multiplication_rhyme_rev, "m2a.5.5": q_multiplication_rhyme_rev,
    # 加减法
    "m1a.4.1": q_add_sub_small, "m1a.4.3": q_add_sub_small,
    "m1a.1.5": q_add_sub_small, "m1a.1.6": q_add_sub_small,
    "m1a.1.7": q_add_sub_small, "m1a.2.4": q_add_sub_small,
    "m1a.2.7": q_add_sub_small, "m1a.2.10": q_add_sub_small,
    "m1a.2.11": q_add_sub_small, "m1a.5.2": q_add_sub_small,
    "m1a.5.3": q_add_sub_small, "m1a.5.1": q_add_sub_small,
    "m1b.1.1": q_add_sub_100, "m1b.1.2": q_add_sub_100,
    "m1b.2.1": q_add_sub_100, "m1b.2.2": q_add_sub_100,
    "m1b.3.1": q_add_sub_100, "m1b.4.1": q_add_sub_100,
    "m1b.4.2": q_add_sub_100, "m1b.5.1": q_add_sub_100,
    "m2b.3.1": q_add_sub_100, "m2b.3.2": q_add_sub_100,
    # 倍与乘法应用
    "m2b.4.1": q_double, "m2b.4.2": q_double,
    "m2a.4.1": q_multiplication_word,

    # ---- 第二批：小学计算型缺口 ----
    "m1b.2.3": q_teen_minus_small,
    "m1b.3.7": q_tens_addsub,
    "m1b.5.2": q_sub_2digit,
    "m1b.6.1": q_diff,
    "m1b.6.2": q_more_less,
    "m1b.6.3": q_diff,
    "m1b.4.3": q_more_less,
    "m1a.2.5": q_add_model,
    "m1a.2.6": q_diff,
    "m1a.2.8": q_add_model,
    "m1a.5.5": q_addsub_relation,
    "m2a.2.1": q_mul_intro,
    "m2a.2.4": q_mul_add_sub,
    "m2a.3.3": q_div_contain,
    "m2a.3.4": q_div_share,
    "m2a.3.7": q_div_contain,
    "m2b.1.2": q_div_share,
    "m2b.3.5": q_addsub_4digit,
    "m2b.4.3": q_addsub_relation,
    "m2a.2.6": q_mul_add_sub,
    "m2a.2.7": q_price_model,
    "m3a.2.1": q_order_same,
    "m3a.2.2": q_order_two,
    "m3a.2.3": q_order_paren,
    "m3a.2.4": q_order_same,
    "m3a.2.5": q_order_two,
    "m3a.4.5": q_mul_zero,
    "m3a.4.6": q_mul_zero_in,
    "m3b.6.1": q_decimal_cmp_num,
    "m3b.6.2": q_decimal_cmp_num,
    "m4b.4.1": q_decimal_cmp_num,
    "m4a.4.1": q_add_model,
    "m4a.4.2": q_price_model,
    "m4a.4.3": q_speed_model,
    "m5b.1.1": q_factor,
    "m5b.3.1": q_frac_addsub,
    "m6a.2.2": q_mul_frac,
    "m6a.3.1": q_reciprocal,
    "m6a.3.2": q_div_int,
    "m6a.3.3": q_div_frac,
    "m3a.6.4": q_frac_cmp_num,

    # ---- 第三批：小学「解决问题」类应用题 ----
    "m1a.3.2": q_solid_faces,
    "m1a.4.4": q_word_total,
    "m1a.5.4": q_word_total,
    "m1b.2.4": q_word_left,
    "m2a.2.6": q_word_choose_op,
    "m2a.5.6": q_word_two_step,
    "m2b.1.3": q_word_div_ceil,
    "m3a.2.4": q_word_same_level,
    "m3a.2.5": q_word_two_level,
    "m3a.2.6": q_word_two_ways,
    "m3a.6.8": q_word_fraction,

    # ---- 第四批：题量下限补足（28 个可计算点，缺口共约 107 题）----
    # 小学识数与分数初步
    "m1a.1.4": q_compose_number,
    "m2b.3.3": q_place_value_wan,
    "m4a.1.1": q_place_value_yi_nei,
    "m4a.1.2": q_place_value_yi_shang,
    "m3a.6.1": q_half_context,
    "m3a.6.2": q_one_fraction_context,
    # 初中
    "m7a.2.2.1": q_rational_mul,
    "m8a.18.2": q_frac_mul_div,
    "m8a.18.3": q_frac_add_sub,
    "m7b.10.3": q_word_two_var,
    "m9a.27.3": q_word_inverse_prop,
    "m8b.24.3": q_quartile,
    "m9b.34.3": q_freq_prob,
    # 高中
    "mh1.3.1.2": q_piecewise,
    "mh1.4.1.2": q_irrational_power,
    "mh2.6.2.2": q_vector_sub_mag,
    "mh2.6.2.3": q_vector_scalar,
    "mh2.6.3.5": q_dot_product,
    "mh2.7.3.2": q_complex_polar,
    "mh3.1.1.1": q_space_vector_linear,
    "mh3.1.1.2": q_space_dot,
    "mh3.2.1.2": q_line_relation,
    "mh3.2.2.1": q_point_slope,
    "mh4.5.2.3": q_composite_derivative,
    "mh5.6.2.2": q_permutation,
    "mh5.6.2.3": q_combination,
    "mh5.7.1.1": q_conditional_prob,
    "mh5.7.1.2": q_total_prob,

}

# 题量缺口大于默认 4 道的点：这些点现有 3 题，要 5 道才够到学段下限（高中 8）。
# 默认每点 4 道，这里按缺口覆盖。
COUNT_OVERRIDE: dict[str, int] = {
    "mh1.4.1.2": 5, "mh2.6.2.2": 5, "mh3.1.1.2": 5, "mh4.5.2.3": 5, "mh5.7.1.2": 5,
}


# ---------------------------------------------------------------------------
# 第五批：单一难度点的两步补题
#
# 背景：小学策略「难度按步骤数」——basic 一步、standard 两步、advanced 三步
# 或换向思考。56 个点只有一步题（全 basic），本批各补 1 道两步题：
# verify 里有两个及以上裸运算符，校准器按步骤数自然给出 standard/advanced，
# 难度不靠人工拍。
#
# 纪律（踩坑后固化）：
#   1. **题干不得透露中间结果**——「1 个十和 5 个一合起来是 15，去掉 3」
#      这种写法学生只做一步减法，verify 却算两步，难度名不副实。
#      两步题要么用连续两问口径，要么把中间量藏进情境。
#   2. 第一步是该知识点的核心技能（凑十、口诀、通分、位值组成……）。
#   3. 答案为整数或有限小数：分数答案（5/8）过不了 compare 的字符串
#      比对，分数运算一律用「具体量化」框架（8 米的 3/8）。
#   4. 跳过 2 个点：m3a.6.4 之外的 m6a.3.2（一个数除以整数）——分数除以
#      整数的两步题无法同时满足「整数答案 + 核心技能在第一步」，
#      与其硬凑不如承认这点内容本就一步可止。
# ---------------------------------------------------------------------------

# 情境名词库：(名词, 量词)。两步题统一从这里取，保证语感一致。
_S2_NOUNS = [("苹果", "个"), ("糖果", "块"), ("圆片", "个"), ("气球", "个"),
             ("桃子", "个"), ("贴纸", "张"), ("五角星", "个"), ("雪花片", "片")]


def _s2_add_core(rnd, cores):
    """加法核心（凑十）→ 再用去。a+b−c。"""
    a = rnd.choice(cores)
    b = rnd.randint(2, 9)
    c = rnd.randint(1, min(5, a + b - 1))
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"原来有 {a} {m}{noun}，又拿来 {b} {m}{noun}，用去 {c} {m}{noun}，"
            f"现在有多少{m}{noun}？（只填数字）",
            str(a + b - c), f"{a}+{b}-{c}", "word",
            f"先算 {a}+{b}={a+b}，再算 {a+b}-{c}={a+b-c}。")


def _s2_mixed(rnd):
    """连加连减/加减混合：a+b−c 或 a−b+c。"""
    a = rnd.randint(5, 9)
    b = rnd.randint(2, 6)
    c = rnd.randint(1, 4)
    noun, m = rnd.choice(_S2_NOUNS)
    if rnd.random() < 0.5:
        return (f"原来有 {a} {m}{noun}，又拿来 {b} {m}{noun}，用去 {c} {m}{noun}，"
                f"现在有多少{m}{noun}？（只填数字）",
                str(a + b - c), f"{a}+{b}-{c}", "word",
                f"先算 {a}+{b}={a+b}，再算 {a+b}-{c}={a+b-c}。")
    return (f"原来有 {a} {m}{noun}，用去 {b} {m}{noun}，又拿来 {c} {m}{noun}，"
            f"现在有多少{m}{noun}？（只填数字）",
            str(a - b + c), f"{a}-{b}+{c}", "word",
            f"先算 {a}-{b}={a-b}，再算 {a-b}+{c}={a-b+c}。")


def _s2_teen_sub(rnd, subs):
    """十几减几 → 又拿来。a−b+c。"""
    a = rnd.randint(11, 19)
    b = rnd.choice(subs)
    c = rnd.randint(1, 4)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"有 {a} {m}{noun}，去掉 {b} {m}{noun}，又拿来 {c} {m}{noun}，"
            f"现在有多少{m}{noun}？（只填数字）",
            str(a - b + c), f"{a}-{b}+{c}", "word",
            f"先算 {a}-{b}={a-b}，再算 {a-b}+{c}={a-b+c}。")


def _s2_sub_any(rnd, hi):
    """减法核心 → 又拿来。a−b+c。"""
    a = rnd.randint(hi // 2 + 1, hi)
    b = rnd.randint(2, max(2, a - 5))
    c = rnd.randint(1, 6)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"有 {a} {m}{noun}，用去 {b} {m}{noun}，又拿来 {c} {m}{noun}，"
            f"现在有多少{m}{noun}？（只填数字）",
            str(a - b + c), f"{a}-{b}+{c}", "word",
            f"先算 {a}-{b}={a-b}，再算 {a-b}+{c}={a-b+c}。")


def _s2_two_sub(rnd):
    """连减：a−b−c。"""
    a = rnd.randint(13, 20)
    b = rnd.randint(3, 6)
    c = rnd.randint(1, b - 1)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"有 {a} {m}{noun}，先拿走 {b} {m}{noun}，再拿走 {c} {m}{noun}，"
            f"还剩多少{m}{noun}？（只填数字）",
            str(a - b - c), f"{a}-{b}-{c}", "word",
            f"先算 {a}-{b}={a-b}，再算 {a-b}-{c}={a-b-c}。")


def _s2_ten(rnd):
    """10 的再认识：1 个十和几个一 → 再去掉。10+b−c。"""
    b = rnd.randint(2, 9)
    c = rnd.randint(1, b)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"妈妈买了 1 盒装 10 {m}{noun}，又零买了 {b} {m}{noun}，吃去 {c} {m}{noun}，"
            f"还剩多少{m}{noun}？（只填数字）",
            str(10 + b - c), f"10+{b}-{c}", "word",
            f"先算 1 个十和 {b} 个一合起来是 {10+b}，再算 {10+b}-{c}={10+b-c}。")


def _s2_compose(rnd):
    """分与合：几和几合成 → 再去掉。a+b−c（5 以内）。"""
    a = rnd.randint(1, 4)
    b = rnd.randint(1, 5 - a)
    c = rnd.randint(1, a + b - 1)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"有 {a} {m}红色的{noun}和 {b} {m}蓝色的{noun}，用去 {c} {m}{noun}后，"
            f"还剩多少{m}{noun}？（只填数字）",
            str(a + b - c), f"{a}+{b}-{c}", "word",
            f"先算 {a} 和 {b} 合成 {a+b}，再算 {a+b}-{c}={a+b-c}。")


def _s2_reverse_add(rnd):
    """逆向用加法：剩下 c、被拿走 b → 原来 c+b；又拿来 d。"""
    b = rnd.randint(2, 6)
    c = rnd.randint(b + 1, 9)
    d = rnd.randint(1, 5)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"筐里原来有一些{noun}，被拿走 {b} {m}{noun}后还剩 {c} {m}{noun}。"
            f"又拿来 {d} {m}{noun}，现在有多少{m}{noun}？（只填数字）",
            str(c + b + d), f"{c}+{b}+{d}", "word",
            f"先逆推原来有 {c}+{b}={c+b} {m}{noun}，再算 {c+b}+{d}={c+b+d}。")


def _s2_diff(rnd):
    """求两数相差几 → 一边用去。a−c−b。"""
    a = rnd.randint(8, 15)
    b = rnd.randint(1, a - 5)
    c = rnd.randint(1, 3)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"甲堆有 {a} {m}{noun}，乙堆有 {b} {m}{noun}。甲堆用去 {c} {m}{noun}后，"
            f"甲堆比乙堆还多几个？（只填数字）",
            str(a - c - b), f"{a}-{c}-{b}", "word",
            f"先算甲堆用去后剩 {a}-{c}={a-c} {m}{noun}，再算 {a-c}-{b}={a-c-b}。")


def _s2_more_less(rnd):
    """比一个数多几 → 又用去。y+d−c。"""
    y = rnd.randint(3, 9)
    d = rnd.randint(2, 6)
    c = rnd.randint(1, d - 1)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"乙有 {y} {m}{noun}，甲比乙多 {d} {m}{noun}。甲用去 {c} {m}{noun}后，"
            f"甲还有多少{m}{noun}？（只填数字）",
            str(y + d - c), f"{y}+{d}-{c}", "word",
            f"先算甲有 {y}+{d}={y+d} {m}{noun}，再算 {y+d}-{c}={y+d-c}。")


def _s2_solid_faces(rnd):
    """立体图形面数 → 还要贴几张。6n−m。"""
    n = rnd.randint(2, 4)
    m = rnd.randint(1, 6 * n - 1)
    return (f"一个正方体有 6 个面。贴 {n} 个正方体一共要贴几个面？"
            f"已经贴了 {m} 张纸，还要贴几张？请直接算出第（2）问的答案。（只填数字）",
            str(6 * n - m), f"6*{n}-{m}", "word",
            f"先算一共要贴 6×{n}={6*n} 张，再算 {6*n}-{m}={6*n-m}。")


def _s2_tens(rnd):
    """整十数加减两步。"""
    a = rnd.randint(2, 6) * 10
    b = rnd.randint(1, 4) * 10
    c = rnd.randint(1, 2) * 10
    return (f"有 {a} 个圆片，又拿来 {b} 个，用去 {c} 个，还剩多少个？（只填数字）",
            str(a + b - c), f"{a}+{b}-{c}", "word",
            f"先算 {a}+{b}={a+b}，再算 {a+b}-{c}={a+b-c}。")


def _s2_sub_100(rnd):
    """100 以内减法 → 又拿来。"""
    a = rnd.randint(35, 99)
    b = rnd.randint(6, a - 25)
    c = rnd.randint(1, 9)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"有 {a} {m}{noun}，用去 {b} {m}{noun}，又拿来 {c} {m}{noun}，"
            f"现在有多少{m}{noun}？（只填数字）",
            str(a - b + c), f"{a}-{b}+{c}", "word",
            f"先算 {a}-{b}={a-b}，再算 {a-b}+{c}={a-b+c}。")


def _s2_nine_div(rnd):
    """用 9 的乘法口诀求商 → 还剩几人没分组。total−9×groups。"""
    q = rnd.randint(5, 9)
    total = 9 * q
    groups = rnd.randint(3, q - 1)
    return (f"{total} 个同学做游戏，每 9 人一组。已经分了 {groups} 组，"
            f"还剩多少个同学没分组？（只填数字）",
            str(total - 9 * groups), f"{total}-9*{groups}", "word",
            f"先算已分 9×{groups}={9*groups} 人，再算 {total}-{9*groups}={total-9*groups} 人。")


def _s2_cm(rnd):
    """用厘米量：量了 n 次每次 h 厘米 → 剪去 c。h×n−c。"""
    h = rnd.randint(2, 5)
    n = rnd.randint(4, 8)
    c = rnd.randint(1, h * n - 1)
    return (f"用尺子量一根绳子，量了 {n} 次，每次量的都是 {h} 厘米。"
            f"剪去 {c} 厘米后，这根绳子还剩多少厘米？（只填数字）",
            str(h * n - c), f"{h}*{n}-{c}", "word",
            f"先算绳长 {h}×{n}={h*n} 厘米，再算 {h*n}-{c}={h*n-c} 厘米。")


def _s2_parts_relation(rnd):
    """加减法各部分关系：由和求另一部分 → 再减。"""
    s = rnd.randint(30, 80)
    b = rnd.randint(8, s - 10)
    c = rnd.randint(1, 6)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"两堆{noun}合起来一共 {s} {m}{noun}，一堆是 {b} {m}{noun}。"
            f"另一堆用去 {c} {m}{noun}后，还剩多少{m}{noun}？（只填数字）",
            str(s - b - c), f"{s}-{b}-{c}", "word",
            f"先由和求另一堆：{s}-{b}={s-b}，再算 {s-b}-{c}={s-b-c}。")


def _s2_place(rnd, hi):
    """位值组成（两部分）→ standard。hi=1000 用百/十，hi=10000 用千/百。"""
    d1 = rnd.randint(1, 9)
    d2 = rnd.randint(1, 9)
    if hi == 10000:
        u1, u2, v1, v2 = "千", "百", 1000, 100
    else:
        u1, u2, v1, v2 = "百", "十", 100, 10
    return (f"由 {d1} 个{u1}和 {d2} 个{u2}组成的数是（　　）",
            str(d1 * v1 + d2 * v2), f"{d1}*{v1}+{d2}*{v2}", "calc",
            f"{d1} 个{u1}是 {d1*v1}，{d2} 个{u2}是 {d2*v2}，合起来是 {d1*v1+d2*v2}。")


def _s2_4digit(rnd):
    """万以内简单加减：a−b−c。"""
    a = rnd.randint(3000, 9000)
    b = rnd.randint(500, 2000)
    c = rnd.randint(100, 400)
    return (f"计算 {a} − {b} − {c} = （　　）", str(a - b - c), f"{a}-{b}-{c}", "calc",
            f"先把 {a} 连续减去 {b} 得 {a-b}，再减 {c} 得 {a-b-c}。")


def _s2_mul(rnd):
    """乘法核心 → 再用去。a×b−c。"""
    a = rnd.randint(3, 9)
    b = rnd.randint(3, 9)
    c = rnd.randint(1, 9)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"每行 {a} {m}{noun}，摆了 {b} 行，用去 {c} {m}{noun}，"
            f"还剩多少{m}{noun}？（只填数字）",
            str(a * b - c), f"{a}*{b}-{c}", "word",
            f"先算 {a}×{b}={a*b}，再算 {a*b}-{c}={a*b-c}。")


def _s2_mul_zero(rnd):
    """因数含 0 的乘法 → 得数再减。"""
    a = rnd.choice([102, 205, 304, 450, 608])
    b = rnd.randint(3, 9)
    c = rnd.randint(1, 9)
    return (f"（1）用竖式计算 {a} × {b}。（2）把得数减去 {c}。"
            f"请直接算出第（2）问的答案。（只填数字）",
            str(a * b - c), f"{a}*{b}-{c}", "word",
            f"{a}×{b}={a*b}，再减 {c} 得 {a*b-c}。")


def _s2_div(rnd):
    """除法核心 → 每份再添。a/b+c。"""
    b = rnd.randint(3, 9)
    q = rnd.randint(4, 9)
    a = b * q
    c = rnd.randint(1, 5)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"{a} {m}{noun}平均分成 {b} 份，每份再添上 {c} {m}{noun}，"
            f"每份现在有多少{m}{noun}？（只填数字）",
            str(q + c), f"{a}/{b}+{c}", "word",
            f"先算每份 {a}÷{b}={q}，再算 {q}+{c}={q+c}。")


def _s2_div2(rnd):
    """除数是两位数的除法 → 每袋再添。"""
    b = rnd.randint(11, 25)
    q = rnd.randint(3, 9)
    a = b * q
    c = rnd.randint(1, 6)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"{a} {m}{noun}平均装在 {b} 个袋子里，每袋再添上 {c} {m}{noun}，"
            f"每袋现在有多少{m}{noun}？（只填数字）",
            str(q + c), f"{a}/{b}+{c}", "word",
            f"先算每袋 {a}÷{b}={q}，再算 {q}+{c}={q+c}。")


def _s2_half(rnd):
    """认识二分之一：平均分 2 份 → 一份里吃掉几个。total/2−c。"""
    k = rnd.randint(3, 9)
    total = 2 * k
    c = rnd.randint(1, k - 1)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"{total} {m}{noun}平均分成 2 份，每份是这些{noun}的二分之一。"
            f"从其中一份里吃掉 {c} {m}{noun}，这一份还剩多少{m}{noun}？（只填数字）",
            str(k - c), f"{total}/2-{c}", "word",
            f"先算一半是 {total}÷2={k} {m}{noun}，再算 {k}-{c}={k-c}。")


def _s2_one_frac(rnd):
    """认识几分之一：平均分 parts 份 → 一份里吃掉几个。"""
    parts = rnd.choice([3, 4, 5, 6])
    k = rnd.randint(3, 9)
    total = parts * k
    c = rnd.randint(1, k - 1)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"{total} {m}{noun}平均分成 {parts} 份，每份是这些{noun}的{_FRACTIONS[parts]}。"
            f"从其中一份里吃掉 {c} {m}{noun}，这一份还剩多少{m}{noun}？（只填数字）",
            str(k - c), f"{total}/{parts}-{c}", "word",
            f"先算每份是 {total}÷{parts}={k} {m}{noun}，再算 {k}-{c}={k-c}。")


def _s2_frac_cmp(rnd):
    """分数大小比较（具体量化）：取 hi 份比取 lo 份多几个。"""
    parts = rnd.choice([6, 7, 8, 9])
    hi = rnd.choice([4, 5, 6])
    lo = rnd.randint(1, hi - 1)
    q = rnd.randint(2, 5)
    return (f"一根绳子平均分成 {parts} 份，每份 {q} 米。取 {hi} 份比取 {lo} 份多几米？"
            f"（只填数字）",
            str(hi * q - lo * q), f"{hi}*{q}-{lo}*{q}", "word",
            f"取 {hi} 份是 {hi}×{q}={hi*q} 米，取 {lo} 份是 {lo}×{q}={lo*q} 米，"
            f"相差 {hi*q-lo*q} 米。")


def _s2_frac_same(rnd):
    """同分母分数加减（具体量化）：两次一共用去多少米。"""
    d = rnd.choice([4, 5, 6, 8, 10])
    n1 = rnd.randint(1, d - 2)
    n2 = rnd.randint(1, d - n1 - 1)
    total = d * rnd.randint(2, 5)
    return (f"一根绳子长 {total} 米，第一次用去 {n1}/{d}，第二次用去 {n2}/{d}。"
            f"两次一共用去多少米？（只填数字）",
            str(total // d * (n1 + n2)),
            f"{total}*{n1}/{d}+{total}*{n2}/{d}", "word",
            f"第一次用去 {total}×{n1}/{d}={total//d*n1} 米，"
            f"第二次用去 {total}×{n2}/{d}={total//d*n2} 米，"
            f"一共 {total//d*n1+total//d*n2} 米。")


def _s2_frac_word(rnd):
    """问题解决（分数）：平均分 → 取几份 → 送掉几个。"""
    parts = rnd.choice([3, 4, 5])
    k = rnd.randint(2, 6)
    total = parts * k
    take = rnd.randint(2, parts - 1)
    c = rnd.randint(1, k * take - 1)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"{total} {m}{noun}平均分成 {parts} 份，每份是这些{noun}的{_FRACTIONS[parts]}。"
            f"取 {take} 份，送掉 {c} {m}{noun}后，手里还有多少{m}{noun}？（只填数字）",
            str(k * take - c), f"{total}/{parts}*{take}-{c}", "word",
            f"先算 {take} 份是 {total}÷{parts}×{take}={k*take} {m}{noun}，"
            f"再减 {c} 得 {k*take-c}。")


def _s2_dec_mul_int(rnd):
    """小数乘整数 → 应找回多少钱。paid−x×c。"""
    x = rnd.randint(2, 9) + 0.5          # 一位小数 .5 → 乘偶数得整数
    c = rnd.choice([2, 4, 6, 8])
    paid = int(x * c) + rnd.randint(1, 5)
    return (f"每千克 {x} 元的糖，买 {c} 千克，付 {paid} 元，应找回多少元？（只填数字）",
            str(paid - int(x * c)), f"{paid}-{x}*{c}", "word",
            f"先算 {x}×{c}={int(x*c)} 元，再算 {paid}-{int(x*c)}={paid-int(x*c)} 元。")


def _s2_dec_mul(rnd):
    """小数乘小数 → 另付。x×y+c。"""
    x, y = rnd.choice([(1.2, 2.5), (1.6, 2.5), (2.4, 2.5), (3.2, 2.5),
                       (3.6, 2.5), (4.4, 2.5), (4.8, 2.5)])
    c = rnd.randint(1, 5)
    return (f"（1）每平方米 {x} 元，铺 {y} 平方米要多少元？"
            f"（2）另付 {c} 元后一共多少元？请直接算出第（2）问的答案。（只填数字）",
            _fmt_dec(x * y + c), f"{x}*{y}+{c}", "word",
            f"先算 {x}×{y}={_fmt_dec(x*y)} 元，再算加 {c} 得 {_fmt_dec(x*y+c)} 元。")


def _s2_dec_law(rnd):
    """运算律推广到小数：2.5×(4+0.4) 的思路 → 再减。"""
    return ("用 2.5×4+2.5×0.4 的思路计算 2.5×4.4，再减去 2.5，结果是多少？（只填数字）",
            "8.5", "2.5*4+2.5*0.4-2.5", "word",
            "2.5×4.4=2.5×4+2.5×0.4=10+1=11，再减 2.5 得 8.5。")


def _s2_dec_div(rnd):
    """除数是整数的小数除法：用去 1 瓶还剩。total−total/4。"""
    total = rnd.choice([2.4, 3.6, 4.8, 7.2])
    return (f"{total} 千克酱油平均装在 4 个瓶子里，用去 1 瓶后，"
            f"还剩几千克？（只填数字）",
            _fmt_dec(total - total / 4), f"{total}-{total}/4", "word",
            f"先算每瓶 {total}÷4={_fmt_dec(total/4)} 千克，"
            f"再算 {total}-{_fmt_dec(total/4)}={_fmt_dec(total-total/4)} 千克。")


def _s2_dec_div2(rnd):
    """一个数除以小数：商再加 2。"""
    x, y = rnd.choice([(4.5, 1.5), (7.5, 2.5), (9.0, 1.5), (4.5, 3.0), (6.0, 2.0)])
    return (f"{x} 除以 {y} 的商，再加上 2，是多少？（只填数字）",
            _fmt_dec(x / y + 2), f"{x}/{y}+2", "word",
            f"先算 {x}÷{y}={_fmt_dec(x/y)}，再加 2 得 {_fmt_dec(x/y+2)}。")


def _s2_composite_area(rnd):
    """组合图形面积：大矩形 − 小正方形。"""
    a = rnd.randint(8, 15)
    b = rnd.randint(5, 10)
    c = rnd.randint(2, 5)
    d = rnd.randint(2, 4)
    return (f"一个长方形花坛长 {a} 米、宽 {b} 米，中间有一个 {c} 米宽、{d} 米长的长方形雕塑，"
            f"种花的面积是多少平方米？（只填数字）",
            str(a * b - c * d), f"{a}*{b}-{c}*{d}", "word",
            f"先算花坛面积 {a}×{b}={a*b}，再减雕塑 {c}×{d}={c*d}，得 {a*b-c*d} 平方米。")


def _s2_frac_mul_int(rnd):
    """分数乘整数（具体量化）：n/d×d−c。"""
    n = rnd.randint(2, 5)
    d = rnd.randint(n + 1, 8)
    c = rnd.randint(1, n - 1)
    return (f"每袋糖 {n}/{d} 千克，买 {d} 袋共几千克？吃掉 {c} 千克后还剩几千克？"
            f"请直接算出第（2）问的答案。（只填数字）",
            str(n - c), f"{n}/{d}*{d}-{c}", "word",
            f"先算 {n}/{d}×{d}={n} 千克，再减 {c} 得 {n-c} 千克。")


def _s2_div_frac(rnd):
    """一个数除以分数：total×d/n。"""
    n = rnd.choice([3, 4, 5])
    d = n + 1
    total = n * rnd.randint(2, 5)
    return (f"有 {total} 千克糖，每袋装 {n}/{d} 千克，正好装完。一共装了几袋？（只填数字）",
            str(total * d // n), f"{total}*{d}/{n}", "word",
            f"{total}÷{n}/{d}={total}×{d}/{n}={total*d//n} 袋。")


def _s2_two_ways(rnd):
    """解决问题（两种思路）：整排用乘法 + 零散的用加法。a×b+c。

    不用 a×b+a×c 的写法：它在 count_steps 下数出 3 个运算符会被判
    advanced，而实际上「先部分再合并」就是两步，与下面这批 standard
    两步题同一档。本点的「两种思路」体现在：可以先算整排的总数，
    也可以按「每排 a 张、共 b 排」先补齐再合计。
    """
    a = rnd.randint(3, 9)
    b = rnd.randint(3, 9)
    c = rnd.randint(2, 9)
    noun, m = rnd.choice(_S2_NOUNS)
    return (f"会议室里每排摆 {a} {m}{noun}，摆好了 {b} 排；边上还有一堆零散的 "
            f"{c} {m}{noun}。一共有多少{m}{noun}？（只填数字）",
            str(a * b + c), f"{a}*{b}+{c}", "word",
            f"先算整排的 {a}×{b}={a*b} {m}{noun}，再加零散的 {c} {m}{noun}，"
            f"一共 {a*b+c} {m}{noun}。")


# 分数除以整数的可用组合：(分子, 分母), 除数, 每份的结果。
# 只取**有限两位小数**的结果——brain 配比用 1e-6 容差，
# 循环小数（如 1/3）会被 _fmt_dec 截断成 mismatch。
_DIV_INT_CASES = [((3, 4), 3, 0.25), ((1, 2), 4, 0.125), ((4, 5), 5, 0.16),
                  ((3, 5), 6, 0.1), ((1, 4), 2, 0.125), ((9, 10), 3, 0.3),
                  ((6, 5), 4, 0.3), ((8, 5), 4, 0.4), ((2, 5), 4, 0.1),
                  ((7, 2), 5, 0.7)]


def _s2_div_int(rnd):
    """一个数除以整数 → 每份再吃掉/添上一点。(n/d)÷c ± r。

    分数写成 (n/d) 加括号：count_steps 会先去掉括号内容，
    「一个 fragments 除以整数」才算作一步，与已有的 q1 口径一致。
    """
    (n, d), c, per = rnd.choice(_DIV_INT_CASES)
    if rnd.random() < 0.5:
        # 倒出：r 必须小于每份的量，且保持两位小数
        r = round(rnd.randint(1, int(per * 100) - 1) / 100, 2)
        ans, op = per - r, "-"
        head = f"从其中一袋倒出 {r} 千克后，这袋还剩多少千克？（只填小数）"
        step2 = f"再倒出 {r} 千克"
    else:
        r = round(rnd.randint(1, 9) / 10, 2)
        ans, op = per + r, "+"
        head = f"又往其中一袋里添进 {r} 千克，这一袋现在有多少千克？（只填小数）"
        step2 = f"再添进 {r} 千克"
    return (f"把 {n}/{d} 千克的白糖平均分成 {c} 袋，每袋重多少千克？{head}",
            _fmt_dec(ans), f"({n}/{d})/{c}{op}{r}", "word",
            f"先算每袋 {n}/{d}÷{c}={_fmt_dec(per)} 千克，{step2}得 {_fmt_dec(ans)} 千克。")


def _s2_frac_dec_mix(rnd):
    """分数、小数混合运算：先求「一个数的几分之几」，再连减。

    m6a.2.3 是剩余 18 个单一难度点里唯一明确的可计算内容
    （其余 17 个点全是识别/比较类：第几、立体积木、运动现象、
    自选标准分类、用字母表示数……basic-only 是它们的真实难度，
    硬给两步题反而是假难度）。

    verify 里把「求几分之几」用括号包起来：count_steps 约定括号内先算
    不计步，于是「求部分 + 两次连减」自然落成 standard，而不是字面上
    数出 4 个运算符。
    """
    total, (n, d) = rnd.choice([(0.8, (1, 4)), (1.2, (1, 4)), (1.6, (1, 8)),
                                (2.4, (1, 4)), (4.8, (1, 8)), (1.2, (1, 2))])
    part = total * n / d
    r = round(rnd.randint(1, int((total - part) * 10) - 1) / 10, 2)
    ans = total - part - r
    return (f"一根绳子长 {total} 米，先用去它的 {n}/{d}，再用去 {r} 米，"
            f"还剩多少米？（只填小数）",
            _fmt_dec(ans), f"{total}-({total}*{n}/{d})-{r}", "word",
            f"先算用去 {total}×{n}/{d}={_fmt_dec(part)} 米，"
            f"再算 {total}-{_fmt_dec(part)}-{r}={_fmt_dec(ans)} 米。")


# 点 → 两步题生成器（partial 传参）
STEP2_GENERATORS: dict[str, object] = {
    # 一年级：加减核心
    "m1a.1.4": _s2_compose,
    "m1a.2.4": partial(_s2_add_core, cores=(6, 7)),
    "m1a.2.5": partial(_s2_add_core, cores=(2, 3, 4, 5, 6, 7, 8, 9)),
    "m1a.2.6": partial(_s2_sub_any, hi=10),
    "m1a.2.7": partial(_s2_add_core, cores=(8, 9)),
    "m1a.2.8": _s2_mixed,
    "m1a.2.11": _s2_mixed,
    "m1a.3.2": _s2_solid_faces,
    "m1a.4.1": _s2_ten,
    "m1a.4.4": partial(_s2_add_core, cores=(2, 3, 4, 5, 6, 7, 8, 9)),
    "m1a.5.1": partial(_s2_add_core, cores=(9,)),
    "m1a.5.2": partial(_s2_add_core, cores=(6, 7, 8)),
    "m1a.5.3": partial(_s2_add_core, cores=(2, 3, 4, 5)),
    "m1a.5.4": partial(_s2_add_core, cores=(2, 3, 4, 5, 6, 7, 8, 9)),
    "m1a.5.5": _s2_reverse_add,
    "m1b.2.1": partial(_s2_teen_sub, subs=(9,)),
    "m1b.2.2": partial(_s2_teen_sub, subs=(6, 7, 8)),
    "m1b.2.3": partial(_s2_teen_sub, subs=(2, 3, 4, 5)),
    "m1b.2.4": partial(_s2_sub_any, hi=20),
    "m1b.3.7": _s2_tens,
    "m1b.4.2": _s2_sub_100,
    "m1b.4.3": _s2_two_sub,
    "m1b.6.1": _s2_diff,
    "m1b.6.2": _s2_more_less,
    # 二年级
    "m2a.2.6": _s2_mul,
    "m2a.4.1": _s2_cm,
    "m2a.5.5": _s2_nine_div,
    "m2b.3.1": partial(_s2_place, hi=1000),
    "m2b.3.2": partial(_s2_place, hi=10000),
    "m2b.3.5": _s2_4digit,
    "m2b.4.3": _s2_parts_relation,
    # 三年级：乘除与分数初步
    "m3a.4.1": _s2_mul, "m3a.4.2": _s2_mul, "m3a.4.3": _s2_mul, "m3a.4.4": _s2_mul,
    "m3a.4.6": _s2_mul_zero,
    "m3a.6.1": _s2_half,
    "m3a.6.2": _s2_one_frac,
    "m3a.6.4": _s2_frac_cmp,
    "m3a.6.5": _s2_frac_same, "m3a.6.6": _s2_frac_same,
    "m3a.6.8": _s2_frac_word,
    "m3b.2.1": _s2_div, "m3b.2.2": _s2_div,
    "m3a.2.6": _s2_two_ways,
    # 四年级
    "m4a.3.1": _s2_mul,
    "m4b.1.1": _s2_div2,
    # 五年级：小数
    "m5a.2.1": _s2_dec_mul_int,
    "m5a.2.2": _s2_dec_mul,
    "m5a.2.3": _s2_dec_law,
    "m5a.3.1": _s2_dec_div,
    "m5a.3.2": _s2_dec_div2,
    "m5a.6.4": _s2_composite_area,
    # 六年级：分数
    "m6a.2.1": _s2_frac_mul_int,
    "m6a.2.3": _s2_frac_dec_mix,
    "m6a.3.2": _s2_div_int,
    "m6a.3.3": _s2_div_frac,
}


def build(count_per_topic: int = 4, seed: int = 20261024) -> list[dict]:
    """为每个有生成器的知识点生成 count_per_topic 道题。

    **随机种子固定**：同一参数生成同一批题，可复现、可重放。
    每道题的随机数字不同，所以同一知识点的多道题不是重复题。
    """
    topics = json.loads(TOPICS.read_text(encoding="utf-8"))
    tmap = {t["id"]: t for t in topics}
    out = []
    # 全局已用题干：不同知识点可能共用同一个生成器，
    # 各点的随机种子独立 → 可能撞出同一道题。跨点也要去重。
    used_prompts: set[str] = set()
    for tid, gen in sorted(GENERATORS.items()):
        if tid not in tmap:
            continue
        # 每个知识点用独立的随机流，避免一个点的随机消耗影响别的点
        rnd = random.Random(f"{seed}:{tid}")
        seen = set()
        for k in range(COUNT_OVERRIDE.get(tid, count_per_topic)):
            for _attempt in range(8):
                res = gen(rnd)
                if len(res) == 4:
                    prompt, answer, verify, kind = res
                else:
                    prompt, answer, verify = res
                    kind = "calc"
                if prompt not in seen and prompt not in used_prompts:
                    break
            else:
                continue   # 试了 8 次仍重复，跳过这个点
            seen.add(prompt)
            used_prompts.add(prompt)
            out.append({
                "id": f"{tid}-q{90 + k}",
                "topicId": tid,
                "type": "numeric",
                "prompt": prompt,
                "choices": [],
                "answer": answer,
                "accept": [answer],
                "verify": verify,
                "explanation": f"本题考查{tmap[tid]['name']}。{tmap[tid].get('description', '')}",
                "difficulty": "basic",
                "difficulty_basis": "generated_practice",
                "drill_kind": kind,
                "generated": True,
                "review_status": "ai-generated",
                "source": "self-generated-calc",
            })

    # ---- 第五批：单一难度点的两步补题，每点 1 道 ----
    # 教学逻辑见 STEP2_GENERATORS 上方的注释：这批题的 verify 至少两步，
    # 校准器按运算步数给出 standard / advanced，难度不靠人工拍。
    for tid, gen in sorted(STEP2_GENERATORS.items()):
        if tid not in tmap:
            continue
        rnd = random.Random(f"{seed}:step2:{tid}")
        res = gen(rnd)
        prompt, answer, verify, kind, expl = res
        if prompt in used_prompts:
            continue        # 与一批题撞了题干，跳过（保持幂等）
        used_prompts.add(prompt)
        out.append({
            "id": f"{tid}-q95",
            "topicId": tid,
            "type": "numeric",
            "prompt": prompt,
            "choices": [],
            "answer": answer,
            "accept": [answer],
            "verify": verify,
            "explanation": expl,
            "difficulty": "basic",
            "difficulty_basis": "generated_practice",
            "drill_kind": kind,
            "generated": True,
            "review_status": "ai-generated",
            "source": "self-generated-calc",
        })
    return out


def main() -> None:
    topics = json.loads(TOPICS.read_text(encoding="utf-8"))
    tmap = {t["id"]: t for t in topics}
    known = {k for k in GENERATORS if k in tmap}
    missing = [k for k in GENERATORS if k not in tmap]
    print(f"生成器 {len(GENERATORS)} 个，其中 {len(known)} 个能对上真实知识点 id")
    if missing:
        print(f"  对不上 id 的 {len(missing)} 个（需修正）: {missing[:8]}")
    drills = build()
    import collections
    print(f"可生成题目 {len(drills)} 道，覆盖 {len({d['topicId'] for d in drills})} 个知识点")
    for d in drills[:4]:
        print(f"  {d['id']:<16}{d['prompt'][:52]} → {d['answer']} (verify: {d['verify']})")


if __name__ == "__main__":
    main()
