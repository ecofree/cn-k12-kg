"""Prediction order and unique physical-token counts are saved learning actions."""
import copy
import json
from http.cookiejar import CookieJar
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from cnkg import manipulatives
from cnkg.learning_store import LearningStore


class PredictionModelTests(unittest.TestCase):
    def test_prediction_comparisons_are_model_specific_and_not_a_mastery_score(self):
        fraction = {"numerator": 1, "denominator": 3, "factor": 3}
        self.assertTrue(manipulatives.evaluate_prediction("fraction_equivalence", fraction, "same")["matches_model"])
        self.assertFalse(manipulatives.evaluate_prediction("fraction_equivalence", fraction, "longer")["matches_model"])
        data = {"values": [5, 6, 8, 10], "baseline": 0}
        first = manipulatives.evaluate_prediction("data_reading_and_mean", data, "29/4")
        shifted = manipulatives.evaluate_prediction("data_reading_and_mean", {**data, "baseline": 5}, "7.25")
        self.assertTrue(first["matches_model"] and shifted["matches_model"])
        self.assertEqual(first["expected"], shifted["expected"])
        linear = manipulatives.evaluate_prediction("linear_function_representations", {"slope": -3, "intercept": 1, "x": 2}, "-5")
        self.assertTrue(linear["matches_model"])
        self.assertFalse(linear["independent_evidence"])
        for text in ("1/0", "1e100000000", "__import__('os')", "3.5"):
            with self.assertRaises(ValueError):
                manipulatives.evaluate_prediction("ten_frame_place_value", {"a": 3, "b": 2}, text)

    def test_repeated_clicks_never_count_as_more_objects(self):
        result = manipulatives.validate_interaction("ten_frame_place_value", {"a": 3, "b": 2}, {"tap_sequence": [0, 0, 2, 4]})
        self.assertEqual(result["unique_count"], 3)
        self.assertEqual(result["repeated_taps"], 1)
        self.assertFalse(result["counted_all"])
        complete = manipulatives.validate_interaction("ten_frame_place_value", {"a": 3, "b": 2}, {"tap_sequence": [4, 2, 0, 1, 3, 1]})
        self.assertTrue(complete["counted_all"])
        self.assertEqual(complete["unique_count"], 5)
        for sequence in ([5], [True], [-1], list(range(201))):
            with self.assertRaises(ValueError):
                manipulatives.validate_interaction("ten_frame_place_value", {"a": 3, "b": 2}, {"tap_sequence": sequence})


class PredictionHTTPTests(unittest.TestCase):
    def setUp(self):
        from web import server
        self.server = server
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.servers = []
        for index in range(2):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            httpd.learning_store = LearningStore(Path(self.temp.name) / f"{index}.sqlite")
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            self.servers.append((httpd, thread))
        self.addCleanup(self.close_servers); self.client = self.make_client()

    def close_servers(self):
        for httpd, thread in self.servers:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)

    @staticmethod
    def make_client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, path, body=None, *, client=None, installation=0):
        url = f"http://127.0.0.1:{self.servers[installation][0].server_port}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            with (client or self.client).open(req, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def payload(self):
        code, unit = self.request("/api/learning/units/ten_frame_place_value")
        self.assertEqual(code, 200)
        return {"unit_id": unit["id"], "resource_version": unit["resource_version"],
                "representation_version": unit["manipulative"]["representation_version"],
                "state": {"a": 3, "b": 2}, "prediction_id": "1" * 32, "prediction": "6"}

    def events(self):
        with self.servers[0][0].learning_store._connect() as db:
            return [{"type": row[0], "at": row[1], "version": row[2], "payload": json.loads(row[3])}
                    for row in db.execute("SELECT event_type, occurred_at, content_sha256, payload_json FROM events ORDER BY seq")]

    def test_unsubmitted_prediction_draft_never_records_feedback_or_assessment(self):
        payload = self.payload()
        payload.pop("prediction_id"); payload.pop("prediction")
        before = len(self.events())
        code, result = self.request("/api/manipulative", {**payload, "prediction_draft": "未写完", "reflection": "还在想"})
        self.assertEqual(200, code, result)
        _, returned = self.request("/api/manipulative?unit_id=ten_frame_place_value")
        self.assertEqual(returned["saved"]["payload"]["prediction_draft"], "未写完")
        self.assertIsNone(returned["saved"]["payload"]["prediction"])
        self.assertIsNone(returned["pending_prediction"])
        self.assertEqual([event["type"] for event in self.events()[before:]], ["manipulation_saved"])
        self.assertEqual(self.events()[-1]["payload"]["evidence"], "learner_saved_draft_not_assessment")

    def test_reread_and_retry_expose_only_frozen_feedback_at_the_real_return_time(self):
        payload = self.payload()
        _, result = self.request("/api/manipulative/predict", payload)
        frozen = result["prediction"]["reference_family_ids"]
        self.assertEqual(frozen, manipulatives.CONFIG["ten_frame_place_value"]["reference_family_ids"])
        self.assertEqual(200, self.request("/api/manipulative", payload)[0])
        later = "2032-02-03T04:05:06+00:00"
        with patch.object(self.server, "_now", return_value=later), patch.dict(manipulatives.CONFIG["ten_frame_place_value"],
                {"reference_family_ids": ["not-an-original-family"]}):
            before = len(self.events())
            self.assertTrue(self.request("/api/manipulative/predict", payload)[1]["duplicate"])
            with patch.object(manipulatives, "representation_version", return_value="changed-renderer"):
                _, returned = self.request("/api/manipulative?unit_id=ten_frame_place_value")
                self.assertFalse(returned["same_version"])
        references = self.events()[before:]
        self.assertEqual(len(references), 2)
        for event in references:
            self.assertEqual(event["type"], "reference_opened")
            self.assertEqual(event["at"], later)
            self.assertEqual(event["version"], payload["resource_version"])
            self.assertEqual(event["payload"]["family_ids"], frozen)
        self.assertEqual(returned["saved"]["payload"]["prediction"], result["prediction"])

    def test_prediction_is_recorded_first_and_cannot_be_rewritten_or_borrowed(self):
        payload = self.payload()
        code, result = self.request("/api/manipulative/predict", payload)
        self.assertEqual(code, 200, result)
        self.assertFalse(result["prediction"]["result"]["matches_model"])
        self.assertFalse(result["prediction"]["independent_evidence"])
        _, pending = self.request("/api/manipulative?unit_id=ten_frame_place_value")
        self.assertEqual(pending["pending_prediction"]["payload"], result["prediction"])
        self.assertTrue(self.request("/api/manipulative/predict", payload)[1]["duplicate"])
        self.assertEqual(409, self.request("/api/manipulative/predict", {**payload, "prediction": "5"})[0])
        operation = {**payload, "interaction": {"tap_sequence": [0, 0, 1, 4]}}
        self.assertEqual(409, self.request("/api/manipulative", operation, client=self.make_client())[0])
        self.assertEqual(409, self.request("/api/manipulative", {**operation, "state": {"a": 4, "b": 2}})[0])
        self.assertEqual(200, self.request("/api/manipulative", operation)[0])
        _, saved = self.request("/api/manipulative?unit_id=ten_frame_place_value")
        self.assertIsNone(saved["pending_prediction"])
        self.assertEqual(saved["saved"]["payload"]["prediction"], result["prediction"])
        self.assertEqual(saved["saved"]["payload"]["interaction"]["unique_count"], 3)
        with self.servers[0][0].learning_store._connect() as db:
            kinds = [row[0] for row in db.execute("SELECT event_type FROM events ORDER BY seq")]
        self.assertEqual(kinds.count("manipulation_prediction_saved"), 1)
        self.assertLess(kinds.index("manipulation_prediction_saved"), kinds.index("manipulation_saved"))
        self.assertNotIn("answer_submitted", kinds)

    def test_prediction_wrong_attempt_and_count_order_survive_a_new_installation(self):
        payload = self.payload()
        self.assertEqual(200, self.request("/api/manipulative/predict", payload)[0])
        operation = {**payload, "interaction": {"tap_sequence": [4, 2, 0, 0, 1, 3]}, "reflection": "重复点同一片没有增加数量。"}
        self.assertEqual(200, self.request("/api/manipulative", operation)[0])
        _, original = self.request("/api/manipulative?unit_id=ten_frame_place_value")
        _, profile = self.request("/api/profile/enable", {"label": "预测恢复测试"})
        _, backup = self.request("/api/profile/backup", {"recovery_code": profile["recovery_code"]})
        client = self.make_client()
        code, restored = self.request("/api/profile/restore", {"envelope": backup, "recovery_code": profile["recovery_code"]}, client=client, installation=1)
        self.assertEqual(200, code, restored)
        _, imported = self.request("/api/manipulative?unit_id=ten_frame_place_value", client=client, installation=1)
        self.assertEqual(original["saved"], imported["saved"])
        with patch.object(self.server.manipulatives, "representation_version", return_value="new-renderer"):
            _, changed = self.request("/api/manipulative?unit_id=ten_frame_place_value", client=client, installation=1)
            self.assertFalse(changed["same_version"])
            self.assertEqual(changed["saved"], original["saved"])
            self.assertEqual(409, self.request("/api/manipulative", operation, client=client, installation=1)[0])


if __name__ == "__main__":
    unittest.main()
