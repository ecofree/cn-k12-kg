"""Historical fixture writers must never reach the canonical data directory."""
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import apply_content, apply_fixes


ROOT = Path(__file__).resolve().parents[1]


class LegacyFixtureWriterGuardTest(unittest.TestCase):
    def cases(self):
        return ((apply_content.dump, [{"fixture": True}]),
                (apply_fixes.dump, [{"fixture": True}]),
                (apply_fixes.dump_content, {"volume": "fixture", "questions": []}))

    def assert_protected(self, target):
        for writer, payload in self.cases():
            with self.subTest(writer=f"{writer.__module__}.{writer.__name__}", target=str(target)):
                # Even a regressed guard cannot modify production during this
                # test: the underlying write is independently intercepted.
                with patch.object(Path, "write_text", side_effect=AssertionError("write reached")):
                    with self.assertRaisesRegex(ValueError, "正式data"):
                        writer(target, payload)

    def test_direct_and_nested_canonical_targets_are_rejected_before_any_write(self):
        self.assert_protected(ROOT / "data/questions.json")
        self.assert_protected(ROOT / "data/content/writer-guard-must-not-create.json")

    def test_fixture_symlink_pointing_back_to_canonical_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            alias = Path(temporary) / "canonical-alias"
            alias.symlink_to(ROOT / "data", target_is_directory=True)
            self.assert_protected(alias / "questions.json")
            linked_file = Path(temporary) / "file-alias.json"
            linked_file.symlink_to(ROOT / "data/questions.json")
            self.assert_protected(linked_file)

    def test_isolated_fixture_writes_still_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index, (writer, payload) in enumerate(self.cases()):
                target = Path(temporary) / f"fixture-{index}.json"
                writer(target, payload)
                actual = json.loads(target.read_text())
                if isinstance(payload, dict):
                    self.assertEqual(actual["volume"], payload["volume"])
                    self.assertEqual(actual["questions"], payload["questions"])
                else:
                    self.assertEqual(actual, payload)

    def test_imported_main_still_rejects_canonical_data(self):
        with redirect_stderr(StringIO()), patch.object(Path, "write_text", side_effect=AssertionError("write reached")):
            self.assertEqual(apply_content.main(["--file", "unused"]), 2)
            self.assertEqual(apply_fixes.main(["--fixes", "unused"]), 2)


if __name__ == "__main__":
    unittest.main()
