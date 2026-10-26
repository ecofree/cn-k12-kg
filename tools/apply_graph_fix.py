"""按 data/graph_fixes.json 修正先修边，并同步所有内容源文件。

为什么单独一个工具：
  apply_content 对已存在的边是"跳过"，无法表达"升级强度"或"删除"；
  而这次要改的正是已存在的边。做完之后内容文件也一起改，
  否则下次 apply_content --update 会把强度改回去。

用法：
    python tools/apply_graph_fix.py --dry-run
    python tools/apply_graph_fix.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def dump(path: pathlib.Path, rows: list):
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    fixes = json.loads((DATA / "graph_fixes.json").read_text(encoding="utf-8"))
    deps = json.loads((DATA / "dependencies.json").read_text(encoding="utf-8"))
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    known = {t["id"] for t in topics}

    idx = {(d["topicId"], d["prerequisiteId"]): d for d in deps}
    errors = []

    for item in fixes.get("upgrade", []):
        key = (item["topicId"], item["prerequisiteId"])
        if key not in idx:
            errors.append(f"要升级的边不存在：{key}")
            continue
        if idx[key]["strength"] == "hard":
            continue
        idx[key]["strength"] = "hard"
        idx[key]["reason"] = item.get("why") or idx[key]["reason"]

    for item in fixes.get("remove", []):
        key = (item["topicId"], item["prerequisiteId"])
        if key not in idx:
            continue   # 已经删过，幂等重跑时不算错误
        deps.remove(idx.pop(key))

    for item in fixes.get("add", []):
        key = (item["topicId"], item["prerequisiteId"])
        if key[0] not in known or key[1] not in known:
            errors.append(f"新增边的端点不存在：{key}")
            continue
        if key in idx:
            continue
        edge = {"topicId": key[0], "prerequisiteId": key[1],
                "strength": item.get("strength", "soft"), "reason": item.get("reason", "")}
        deps.append(edge)
        idx[key] = edge

    if errors:
        print("修正失败：")
        for e in errors:
            print("  -", e)
        return 1

    print(f"升级 {len(fixes.get('upgrade', []))} 条、删除 {len(fixes.get('remove', []))} 条、"
          f"新增 {len(fixes.get('add', []))} 条；边总数 {len(deps)}")
    if args.dry_run:
        return 0

    dump(DATA / "dependencies.json", deps)

    remove_pairs = {(x["topicId"], x["prerequisiteId"]) for x in fixes.get("remove", [])}
    upgrade_map = {(u["topicId"], u["prerequisiteId"]): u for u in fixes.get("upgrade", [])}

    # 同步内容源文件，避免下次合并把强度改回去
    changed = 0
    for path in sorted((DATA / "content").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        edges = payload.get("dependencies") or []
        if not edges:
            continue
        before = len(edges)
        dirty = False
        kept = []
        for e in edges:
            key = (e["topicId"], e["prerequisiteId"])
            if key in remove_pairs:
                changed += 1
                dirty = True
                continue
            fix = upgrade_map.get(key)
            if fix and e.get("strength") != "hard":
                e["strength"] = "hard"
                e["reason"] = fix.get("why") or e.get("reason", "")
                changed += 1
                dirty = True
            kept.append(e)
        added = 0
        have = {(e["topicId"], e["prerequisiteId"]) for e in kept}
        for item in fixes.get("add", []):
            key = (item["topicId"], item["prerequisiteId"])
            if key in have:
                continue
            # 只把边加回它所在册次的内容文件
            vol = next(t["volume"] for t in topics if t["id"] == key[0])
            if vol != payload.get("volume"):
                continue
            kept.append({"topicId": key[0], "prerequisiteId": key[1],
                         "strength": item.get("strength", "soft"),
                         "reason": item.get("reason", "")})
            added += 1
            dirty = True
        if dirty:
            payload["dependencies"] = kept
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            print(f"  同步 {path.name}：{before} → {len(kept)} 条（新增 {added}）")
    print(f"内容源文件共修改 {changed + 0} 条边")
    print("下一步：python tools/validate.py && python tools/build_db.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
