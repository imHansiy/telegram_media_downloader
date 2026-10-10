"""Distributed, reliable speech through extraction; fake decoder/transcriber only."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import module.resource_analysis.evidence as extraction
from module.resource_analysis.summary import evidence_catalog


def segment(text, *, silence=0.1, logprob=-0.2, compression=1.1, start=0.0, end=6.0):
    return SimpleNamespace(
        text=text,
        no_speech_prob=silence,
        avg_logprob=logprob,
        compression_ratio=compression,
        start=start,
        end=end,
    )


class SpeechEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.model = mock.Mock()
        self.model.transcribe.side_effect = [
            ([segment(text)], SimpleNamespace(language="zh", language_probability=0.97))
            for text in (
                "这一段正在介绍室内布置和当天的拍摄安排。",
                "现在介绍房间里的窗帘和灯光位置。",
                "接下来讲述拍摄之后的设备收纳方法。",
            )
        ]
        self.model.detect_language.return_value = ("zh", 0.9, [("zh", 0.9)])
        self.enterContext(mock.patch.object(extraction, "_speech_model", self.model))
        self.enterContext(mock.patch.object(extraction, "_decode_audio", return_value="decoded-fixture"))
        self.runner = self.enterContext(mock.patch.object(extraction, "_run"))
        self.enterContext(mock.patch.object(extraction, "_probe", return_value=900.0))

        def frames(path, count, temporary, evidence):
            evidence.images = ["fixture-frame"]
            evidence.sources = ["video_frames"]
            evidence.sampling = {"duration_seconds": 900.0, "timestamps": [12]}

        self.enterContext(mock.patch.object(extraction, "_frames", side_effect=frames))

    def extract(self):
        return extraction.extract_evidence(
            "fixture.mp4", self.temporary.name, transcribe=True, max_audio_seconds=180
        )

    def test_same_audio_budget_covers_beginning_middle_and_end(self):
        evidence = self.extract()
        self.assertEqual(3, self.model.transcribe.call_count)
        windows = evidence.sampling["audio"]["windows"]
        self.assertEqual([0, 420, 840], [item["start"] for item in windows])
        self.assertEqual(180, sum(item["end"] - item["start"] for item in windows))
        self.assertEqual(3, evidence.sampling["audio"]["reliable_clips"])
        self.assertIn("speech_3", evidence.text)
        self.assertNotIn("前 180 秒", " ".join(evidence.limitations))
        self.assertEqual(
            [0, 420, 840],
            [
                item["start"]
                for item in evidence_catalog(evidence)
                if item["kind"] == "transcript"
            ],
        )
        for call in self.model.transcribe.call_args_list:
            self.assertFalse(call.kwargs["condition_on_previous_text"])
            # One language decision per file, pinned for every window; beam 5.
            self.assertEqual("zh", call.kwargs["language"])
            self.assertEqual(5, call.kwargs["beam_size"])
        self.assertEqual("zh", evidence.sampling["audio"]["language"])
        window = evidence.sampling["audio"]["windows"][0]
        self.assertEqual((1, 6.0), (window["accepted_segments"], window["speech_seconds"]))

    def test_uncertain_language_detection_leaves_windows_unpinned(self):
        self.model.detect_language.return_value = ("ko", 0.4, [("ko", 0.4)])
        evidence = self.extract()
        self.assertEqual("", evidence.sampling["audio"]["language"])
        for call in self.model.transcribe.call_args_list:
            self.assertIsNone(call.kwargs["language"])

    def test_speech_presence_needs_spread_windows_with_real_speech(self):
        from module.resource_analysis.summary import speech_presence, speech_presence_event, evidence_catalog
        def window(start, characters, seconds, status="unreliable"):
            return {"start": start, "end": start + 60, "status": status, "accepted_characters": characters, "speech_seconds": seconds}
        evidence = extraction.Evidence("video", sampling={"duration_seconds": 900, "audio": {"language": "zh", "windows": [
            window(0, 40, 12), window(300, 50, 15), window(600, 30, 9)]}})
        presence = speech_presence(evidence)
        self.assertEqual({"language": "zh", "windows": 3, "speech_seconds": 36, "first": 0, "last": 600}, presence)
        self.assertEqual("片中有中文对话", speech_presence_event(evidence)["action"])
        self.assertEqual(["speech_presence"], speech_presence_event(evidence)["evidence_refs"])
        self.assertEqual("speech_presence", evidence_catalog(evidence)[-1]["id"])
        # Only two windows, or windows in one half only, or too little speech: no claim.
        for windows in ([window(0, 40, 12), window(300, 50, 15)],
                        [window(0, 40, 12), window(60, 50, 15), window(120, 30, 9)],
                        [window(0, 4, 12), window(300, 50, 15), window(600, 30, 9)],
                        [window(0, 40, 2), window(300, 50, 15), window(600, 30, 9)]):
            evidence.sampling["audio"]["windows"] = windows
            self.assertIsNone(speech_presence(evidence), windows)
        evidence.sampling["audio"] = {"language": "", "windows": [window(0, 40, 12), window(300, 50, 15), window(600, 30, 9)]}
        self.assertIsNotNone(speech_presence(evidence))
        self.assertIsNone(speech_presence_event(evidence))

    def test_low_language_confidence_does_not_become_a_language_claim(self):
        self.model.transcribe.side_effect = None
        self.model.transcribe.return_value = (
            [segment("这是一段可能被错误识别的长转写句子。")],
            SimpleNamespace(language="ko", language_probability=0.25),
        )
        evidence = self.extract()
        self.assertNotIn("transcript", evidence.sources)
        self.assertEqual("", evidence.text)
        self.assertEqual(0, evidence.sampling["audio"]["reliable_clips"])
        self.assertFalse(
            any(item["kind"] == "transcript" for item in evidence_catalog(evidence))
        )
        self.assertIn("未获得可靠转写", " ".join(evidence.limitations))

    def test_short_high_confidence_dialogue_is_not_discarded_for_character_count(self):
        self.model.transcribe.side_effect = None
        self.model.transcribe.return_value = (
            [segment("请帮我补票。")],
            SimpleNamespace(language="zh", language_probability=0.98),
        )
        evidence = self.extract()
        self.assertIn("请帮我补票。", evidence.text)
        self.assertEqual(3, evidence.sampling["audio"]["reliable_clips"])

    def test_short_noisy_or_repetitive_sounds_remain_excluded(self):
        for text, score in (("嗯嗯啊啊嗯嗯。", -0.1), ("请帮我补票。", -0.9), ("是的。", -0.1)):
            with self.subTest(text=text, score=score):
                self.model.transcribe.side_effect = None
                self.model.transcribe.return_value = (
                    [segment(text, logprob=score)],
                    SimpleNamespace(language="zh", language_probability=0.98),
                )
                evidence = self.extract()
                self.assertEqual("", evidence.text)
                self.assertEqual(0, evidence.sampling["audio"]["reliable_clips"])

    def test_failed_audio_decode_is_not_counted_as_sampled_or_transcribed(self):
        self.runner.side_effect = OSError("synthetic decoder failure")
        evidence = self.extract()
        self.assertEqual(180, evidence.sampling["audio"]["attempted_seconds"])
        self.assertEqual(0, evidence.sampling["audio"]["sampled_seconds"])
        self.assertEqual([], evidence.audio_clips)
        self.model.transcribe.assert_not_called()

    def test_silence_and_unreliable_segments_do_not_reach_model_context(self):
        bad = "这段错误识别内容不应该进入模型的输入。"
        good = "可靠片段正在讲解灯光位置与房间布置。"
        self.model.transcribe.side_effect = None
        self.model.transcribe.return_value = (
            [
                segment(bad, silence=0.95),
                segment(bad, logprob=-2),
                segment(bad, compression=4),
                segment(good),
            ],
            SimpleNamespace(language="zh", language_probability=0.95),
        )
        evidence = self.extract()
        self.assertNotIn(bad, evidence.text)
        self.assertIn(good, evidence.text)
        self.assertTrue(
            all(
                item["discarded_segments"] == 3
                for item in evidence.sampling["audio"]["windows"]
            )
        )


if __name__ == "__main__":
    unittest.main()
