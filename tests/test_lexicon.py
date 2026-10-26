"""词表（kc_lexicon）的结构与正则合法性检查。

**为什么必须有这个测试**：词表里每条规则都是正则，
手写正则时转义层级极易出错（\\\\log vs \\log、字符类没闭合）。
一次非法正则会让整个映射工具抛异常，
而抛出点在跑批的循环里，报错信息看不出是哪条规则坏了。

这条测试把「规则不合法」变成「构建时就失败」。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEXICON = ROOT / "data" / "kc_lexicon.json"
TOPICS = ROOT / "data" / "topics.json"


class TestLexicon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lex = json.loads(LEXICON.read_text(encoding="utf-8"))
        cls.topics = {x["id"]: x for x in json.loads(TOPICS.read_text(encoding="utf-8"))}

    def test_all_regex_compile(self):
        """每条规则的正则都必须能编译——否则运行时才炸。"""
        bad = []
        for tid, entry in self.lex.items():
            for group in ("must", "any", "not"):
                for pat in entry.get(group, []):
                    try:
                        re.compile(pat)
                    except re.error as exc:
                        bad.append(f"{tid}[{group}] {pat!r} → {exc}")
        self.assertFalse(bad, "非法正则：\n" + "\n".join(bad))

    def test_topic_ids_exist(self):
        """词表里的知识点 id 必须真实存在。"""
        bad = [tid for tid in self.lex if tid not in self.topics]
        self.assertFalse(bad, f"词表引用了不存在的知识点：{bad}")

    def test_stage_matches_grade(self):
        """stage 要和知识点的年级对得上。"""
        bad = []
        for tid, entry in self.lex.items():
            t = self.topics.get(tid)
            if not t:
                continue
            stage = entry.get("stage")
            g = t["grade"]
            if stage == "primary" and g > 6:
                bad.append(f"{tid} 标为 primary 但年级 {g}")
            if stage == "junior" and not (7 <= g <= 9):
                bad.append(f"{tid} 标为 junior 但年级 {g}")
            if stage == "senior" and g < 10:
                bad.append(f"{tid} 标为 senior 但年级 {g}")
        self.assertFalse(bad, f"stage 与年级不符：{bad}")

    def test_entry_has_required_keys(self):
        """每条规则至少要有 any 或 must，否则永远匹配不上（或永远匹配）。"""
        bad = [tid for tid, e in self.lex.items()
               if not e.get("any") and not e.get("must")]
        self.assertFalse(bad, f"既无 must 也无 any 的规则（永远不匹配）：{bad}")

    def test_rules_are_not_empty_strings(self):
        bad = []
        for tid, e in self.lex.items():
            for group in ("must", "any", "not"):
                for pat in e.get(group, []):
                    if not pat or not pat.strip():
                        bad.append(f"{tid}[{group}] 空模式")
        self.assertFalse(bad, f"空模式：{bad}")


if __name__ == "__main__":
    unittest.main()
