"""迁移：选项用稳定 option_id，字母改为渲染时生成。

为什么做这件事
    D19 的事故是「选项打散 → 字母失效 → 答案指错、解析指错」。
    根因是**字母被存进了数据**：字母既当身份又当显示，重排就必然出错。
    把身份换成稳定的 option_id（o1、o2…），字母只在渲染时按位置生成，
    重排选项就只是重排数组，答案与解析引用的是 id，永远不会错。

改动
    choices: [{"key": "A", "text": "..."}]  →  [{"id": "o1", "text": "..."}]
    answer : "B"                            →  "o2"
    字母从数据里彻底消失，改由前端按下标生成。

幂等：已带 id 的题会被跳过。

用法：
    python tools/migrate_option_ids.py --dry-run
    python tools/migrate_option_ids.py
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LETTERS = "ABCDEFGH"


def migrate_question(q: dict) -> tuple[dict, bool]:
    if q.get("type") != "choice" or not q.get("choices"):
        return q, False
    choices = q["choices"]
    if all("id" in c for c in choices):
        return q, False

    new_choices = []
    key_to_id = {}
    for i, c in enumerate(choices):
        oid = f"o{i + 1}"
        key_to_id[c.get("key")] = oid
        new_choices.append({"id": oid, "text": c.get("text", "")})
    old_answer = str(q.get("answer", "")).strip()
    new_answer = key_to_id.get(old_answer, old_answer)
    out = {k: v for k, v in q.items() if k != "choices"}
    out["choices"] = new_choices
    out["answer"] = new_answer
    return out, True


def dump(path: pathlib.Path, rows) -> None:
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total_changed = 0
    for name in ("questions.json", "questions_ext.json"):
        path = DATA / name
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        unresolved = []
        out = []
        for q in rows:
            nq, did = migrate_question(q)
            if did:
                changed += 1
                # 答案必须落在选项 id 集合内，否则说明原来就有问题
                ids = {c["id"] for c in nq["choices"]}
                if nq["answer"] not in ids:
                    unresolved.append((nq["id"], nq["answer"]))
            out.append(nq)
        total_changed += changed
        print(f"{name}: 迁移 {changed} 道选择题"
              + (f"；{len(unresolved)} 道答案无法映射（保留原值）：{unresolved[:5]}" if unresolved else ""))
        if not args.dry_run:
            dump(path, out)

    # 内容源同步：内容源里也可能存着选择题
    for path in sorted((DATA / "content").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for i, q in enumerate(payload.get("questions", [])):
            nq, did = migrate_question(q)
            if did:
                payload["questions"][i] = nq
                changed += 1
        if changed and not args.dry_run:
            body = ",\n".join(json.dumps(q, ensure_ascii=False, separators=(",", ":"))
                              for q in payload["questions"])
            path.write_text(
                '{\n"volume":' + json.dumps(payload["volume"], ensure_ascii=False)
                + ',\n"topics":' + json.dumps(payload.get("topics", {}), ensure_ascii=False, separators=(",", ":"))
                + ',\n"dependencies":' + json.dumps(payload.get("dependencies", []), ensure_ascii=False, separators=(",", ":"))
                + ',\n"questions":[\n' + body + '\n]\n}\n', encoding="utf-8")
        if changed:
            total_changed += changed
            print(f"  内容源 {path.name}: {changed} 道")

    print(f"合计迁移 {total_changed} 处", "(dry-run)" if args.dry_run else "")


if __name__ == "__main__":
    main()
