import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from jsonschema import Draft202012Validator

from module.resource_analysis.evidence import Evidence, _timeline_windows, _scene_aligned_windows, _scene_scan, extract_evidence
from module.resource_analysis.timeline import segment_requests, segment_prompt, parse_segment


class TimelineSamplingTest(unittest.TestCase):
    def test_short_deep_video_uses_frame_budget_without_expanding_audio_windows(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("module.resource_analysis.evidence._probe", return_value=180), \
             mock.patch("module.resource_analysis.evidence._scene_scan", return_value=([], {"status": "complete"})), \
             mock.patch("module.resource_analysis.evidence._run") as decoder, \
             mock.patch("module.resource_analysis.evidence._frame_features", return_value=([1], True)), \
             mock.patch("module.resource_analysis.evidence._image_data", return_value="fixture"):
            result = extract_evidence("fixture.mp4", directory, timeline=True, adaptive_sampling=True,
                                      max_frames=48, max_audio_seconds=360)
        self.assertEqual(48, len(result.images), "Deep mode must not silently cap six short windows at three frames each")
        times = result.sampling["timestamps"]
        self.assertEqual(sorted(set(times)), times)
        self.assertLessEqual(max(b-a for a,b in zip(times, times[1:])), 4.2)
        self.assertEqual(180, sum(w["end"] - w["start"] for w in result.sampling["timeline_windows"]))
        seeks = [float(call.args[0][call.args[0].index("-ss") + 1]) for call in decoder.call_args_list]
        self.assertEqual([0.6, 179.4], seeks[:2], "Decode both ends before spending the remaining budget")

    def test_segment_contract_requires_the_chinese_accepted_by_event_parser(self):
        evidence = Evidence("video", images=["synthetic"], sources=["video_frames"],
                            sampling={"timestamps": [1], "timeline_windows": [{"start": 0, "end": 2}]})
        focused, mapping = segment_requests(evidence)[0]
        validator = Draft202012Validator(focused.output_schema)
        english = {"events": [{"action": "Two musicians rehearse together.", "evidence_refs": ["frame_1"]}]}
        self.assertFalse(validator.is_valid(english), "Wire contract must not accept prose the event parser rejects for language")
        self.assertIn("中文", segment_prompt(focused))
        chinese = {"events": [{"action": "两位演奏者配合排练合奏。", "evidence_refs": ["frame_1"]}]}
        self.assertTrue(validator.is_valid(chinese))
        self.assertFalse(parse_segment(chinese, focused, mapping, "music")[1])

    def test_compact_chinese_event_is_not_rejected_for_padding(self):
        from module.resource_analysis.summary import narrative_output_schema, build_narrative
        evidence = Evidence("video", images=["synthetic"], sources=["video_frames"],
                            sampling={"timestamps": [1], "timeline_windows": [{"start": 0, "end": 2}]})
        focused, mapping = segment_requests(evidence)[0]
        row = {"action": "两人协作演奏", "evidence_refs": ["frame_1"]}
        self.assertFalse(parse_segment({"events": [row]}, focused, mapping, "music")[1])
        self.assertTrue(Draft202012Validator(focused.output_schema).is_valid({"events": [row]}))
        ledger = {"version": 2, "mode": "activity", "events": [
            {**row, "importance": "content", "phase": "activity"}]}
        self.assertFalse(build_narrative(ledger, evidence, category="adult")["issues"])
        self.assertTrue(Draft202012Validator(narrative_output_schema(evidence, "adult", mode="activity")).is_valid(ledger))
        from module.resource_analysis.dialogue import dialogue_request, parse_dialogue
        evidence.text = "[speech_1] 请慢一点演奏。"
        evidence.sources.append("transcript")
        evidence.audio_clips = [{"start": 0, "end": 2, "text": "请慢一点演奏。"}]
        request = dialogue_request(evidence)
        raw = {"reason": "specific_context", "contexts": [
            {"action": "一人请求放慢", "evidence_refs": ["speech_1"]}]}
        self.assertTrue(Draft202012Validator(request.output_schema).is_valid(raw))
        self.assertFalse(parse_dialogue(raw, request)[1])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg unavailable")
    def test_real_decoder_on_generated_non_user_media(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generated.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=4", "-t", "4", "-c:v", "mpeg4", str(path)],
                           check=True, capture_output=True, timeout=20)
            _, report = _scene_scan(path)
            self.assertEqual("complete", report["status"])
            result = extract_evidence(str(path), directory, timeline=True, adaptive_sampling=True, max_frames=6)
            self.assertTrue(result.images)
            self.assertEqual("complete", result.sampling["scene_scan"]["status"])
    def test_deep_windows_preserve_time_spread_and_budget(self):
        windows = _timeline_windows(1800, 48, 360)
        self.assertEqual(16, len(windows))
        revised = _scene_aligned_windows(windows, [300, 600, 900, 1200, 1500])
        self.assertEqual(windows[0], revised[0])
        self.assertEqual(windows[-1], revised[-1])
        self.assertLessEqual(sum(row["end"] - row["start"] for row in revised), 360.1)
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(revised, revised[1:])))

    def test_scan_failure_is_nonfatal(self):
        with mock.patch("module.resource_analysis.evidence._run", side_effect=subprocess.TimeoutExpired("ffmpeg", 45)):
            times, report = _scene_scan("fixture.mp4")
        self.assertEqual([], times)
        self.assertEqual("unavailable", report["status"])

    def test_segment_references_are_mapped_without_inventing_times(self):
        evidence = Evidence("video", images=["a", "b", "c"], sources=["video_frames"],
                            sampling={"timestamps": [0, 50, 100], "timeline_windows": [{"start": 45, "end": 55}]})
        focused, mapping = segment_requests(evidence)[0]
        notes, issues = parse_segment({"events": [{"action": "两位演奏者配合排练合奏。", "evidence_refs": ["frame_1"]}]}, focused, mapping, "music")
        self.assertFalse(issues)
        self.assertEqual(["frame_2"], notes[0]["evidence_refs"])
        self.assertEqual(50, notes[0]["start"])

    def test_adaptive_sampling_retains_fixed_windows_on_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("module.resource_analysis.evidence._probe", return_value=1800), \
             mock.patch("module.resource_analysis.evidence._scene_scan", return_value=([], {"status": "unavailable"})), \
             mock.patch("module.resource_analysis.evidence._run"), \
             mock.patch("module.resource_analysis.evidence._frame_features", return_value=([1], True)), \
             mock.patch("module.resource_analysis.evidence._image_data", return_value="fixture"):
            result = extract_evidence("fixture.mp4", directory, timeline=True, adaptive_sampling=True, max_frames=48, max_audio_seconds=360)
        self.assertEqual(48, len(result.images))
        self.assertEqual(16, len(result.sampling["timeline_windows"]))
        self.assertEqual("unavailable", result.sampling["scene_scan"]["status"])
