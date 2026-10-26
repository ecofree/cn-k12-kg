"""修掉 data/questions_ext.json 里的重复题目 id（默认只读，--apply 才写）。

**这是个数据完整性事故，不是洁癖**：2026-10-25 审计 CMM 答案时发现
 `questions_ext.json` 里 **4429 个 id 被 10628 道题共用**（2659 个 id 用两次、
 1770 个用三次），而这些记录**没有一条是同一道题**——它们来自不同年级段的池：

    ext-cmmmath-00001  六年级  '把一个圆柱的侧面展开, 不可能得到的是'  (tag-mapped)
    ext-cmmmath-00001  七年级  '2022 的绝对值是'                  (tag-mapped)
    ext-cmmmath-00001  高二    '已知 sin α > sin β, 下列命题成立的是'  (cross-checked，可诊断)

根因在 `import_mapped.py`：`seq = len(imported) + 1` —— 序号按**本轮导入**
计数，CMM 分三批（小学/初中/高中）导入时每批都从 1 重数，于是撞车。

**影响面（实测，不是猜的）**：
  · 图内存题库（graph.py）按 topicId 把题 append 进列表，**不受影响**；
  · 但任何「按 id 取题」的地方会拿错对象——{q['id']: q} 这类字典普遍存在
    （store.py 建 SQLite 时就是靠着 `ext-{source}-{id}` 的前缀改名才躲开主键冲突，
    代价是库里的 id 与 JSON 里的 id 对不上）；
  · validate.py 的题库校验只覆盖 questions.json，**根本没加载 questions_ext.json**，
    所以这个 bug 一直没被发现。

修法：**保留首次出现的 id 不变**，后续重复项按出现顺序加 `-d2` / `-d3` 后缀。
这样改动面最小（只动 6199 条），且结果在文件顺序不变的前提下可复现。

用法：
    python tools/fix_duplicate_ext_ids.py          # 报告
    python tools/fix_duplicate_ext_ids.py --apply  # 写库
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "questions_ext.json"
OWN = ROOT / "data" / "questions.json"


def plan_ids(ext: list[dict], own_ids: set[str]) -> tuple[dict[int, str], list[dict]]:
    """给每个重复项算新 id，返回 ({原索引: 新id}, 重复组的明细)。"""
    seen: dict[str, int] = {}          # id → 已出现次数
    used = set(own_ids)                # 新 id 不得与自有题撞
    rename: dict[int, str] = {}
    groups: list[dict] = []
    for idx, q in enumerate(ext):
        qid = q.get("id", "")
        n = seen.get(qid, 0) + 1
        seen[qid] = n
        used.add(qid)
        if n == 1:
            continue
        new_id = f"{qid}-d{n}"
        while new_id in used:          # 极端情况：连重命名都撞了
            n += 1
            new_id = f"{qid}-d{n}"
        used.add(new_id)
        rename[idx] = new_id
        groups.append({"id": qid, "new_id": new_id, "type": q.get("type"),
                       "source": q.get("source"),
                       "primaryTopicId": q.get("primaryTopicId"),
                       "prompt": str(q.get("prompt", ""))[:44]})
    return rename, groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写入 questions_ext.json")
    ap.add_argument("--show", type=int, default=8, help="展示几条重复样例")
    args = ap.parse_args()

    ext = json.loads(EXT.read_text(encoding="utf-8"))
    own = json.loads(OWN.read_text(encoding="utf-8"))
    own_ids = {q["id"] for q in own}

    rename, groups = plan_ids(ext, own_ids)
    dup_ids = len({g["id"] for g in groups})
    # 与自有题撞车的（不应存在，但要报出来）
    cross = [q["id"] for q in ext if q.get("id") in own_ids]

    print(f"外部题 {len(ext)} 道，自有题 {len(own)} 道")
    print(f"重复 id：{dup_ids} 个，涉及 {len(groups) + dup_ids} 道题"
          f"（保留首条 {dup_ids} 道，重命名 {len(groups)} 道）")
    if cross:
        print(f"  ⚠ 与自有题 id 撞车：{len(cross)} 道，例如 {cross[:3]}")

    by_src = collections.Counter(g["source"] for g in groups)
    print(f"重复项按来源：{dict(by_src)}")
    by_type = collections.Counter(g["type"] for g in groups)
    print(f"重复项按题型：{dict(by_type)}")

    print(f"\n--- 样例（前 {args.show} 条）---")
    for g in groups[:args.show]:
        print(f"  {g['id']:<22s} → {g['new_id']:<26s} [{g['type']}] {g['prompt']}")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 落地）")
        return 0

    for idx, new_id in rename.items():
        ext[idx]["id"] = new_id
    EXT.write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")

    # 落地后自检
    c = collections.Counter(q["id"] for q in ext)
    still = {k: v for k, v in c.items() if v > 1}
    print(f"\n已写入 {EXT.relative_to(ROOT)}：重命名 {len(rename)} 道")
    print(f"自检：剩余重复 id {len(still)} 个"
          f"，与自有题撞车 {len([q for q in ext if q['id'] in own_ids])} 个")
    return 1 if still else 0


if __name__ == "__main__":
    sys.exit(main())
