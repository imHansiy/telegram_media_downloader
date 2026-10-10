"""Keep audio-only statements distinct from visible actions in projections."""
import unittest

from module.resource_analysis.summary import project_activity_synopsis


class SynopsisSourceBoundaryTests(unittest.TestCase):
    def project(self, tail):
        events = [
            {"id": "event_1", "importance": "content", "action": "一人介绍乐器的使用方法", "evidence_refs": ["frame_1"]},
            {"id": "event_2", "importance": "content", "action": "另一人演示一段合奏", "evidence_refs": ["frame_2"]},
            *tail,
        ]
        return project_activity_synopsis({"mode": "activity", "events": events}, "adult")

    def test_audio_request_is_not_spliced_into_visible_followup(self):
        rows = self.project([
            {"id": "event_3", "importance": "context", "action": "有人要求再排练一次", "evidence_refs": ["speech_1"]},
            {"id": "event_4", "importance": "content", "action": "一人收起乐器", "evidence_refs": ["frame_4"]},
        ])
        self.assertEqual("音轨中，有人要求再排练一次；画面中，一人收起乐器。", rows[2]["text"])
        self.assertEqual(["event_3", "event_4"], rows[2]["event_refs"])

    def test_audio_only_content_also_requires_source_attribution(self):
        rows = self.project([
            {"id": "event_3", "importance": "content", "action": "一人收起乐器", "evidence_refs": ["frame_3"]},
            {"id": "event_4", "importance": "content", "action": "有人讨论下次集合的地点", "evidence_refs": ["speech_1"]},
        ])
        self.assertEqual("一人收起乐器；音轨中，有人讨论下次集合的地点。", rows[2]["text"])


if __name__ == "__main__":
    unittest.main()
