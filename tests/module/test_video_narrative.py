"""Narrative acceptance through the production analysis interface, offline."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from module.resource_analysis import analyze_resource
from module.resource_analysis.evidence import Evidence
import module.resource_analysis.evidence as extraction
from module.resource_analysis.taxonomy import analysis_profile
from module.resource_analysis.service import ModelError


class VideoNarrativeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "fixture.mp4"
        self.path.touch()
        self.evidence = Evidence(
            "video", images=["fixture-a", "fixture-b", "fixture-c"],
            sources=["video_frames", "transcript"],
            text="[speech_1] 我找不到车票了，请帮忙查一下。[speech_2] 已查到记录，这是补票。",
            sampling={"duration_seconds": 120, "timestamps": [2, 60, 118]},
            audio_clips=[{"start": 0, "end": 20}, {"start": 100, "end": 120}],
        )
        self.ledger = {
            "mode": "story",
            "events": [
                {"phase": "setup", "action": "旅客发现车票遗失，在站台向工作人员求助。", "evidence_refs": ["frame_1", "speech_1"]},
                {"phase": "development", "action": "工作人员查询购票记录，并将补票交给旅客。", "evidence_refs": ["frame_2", "speech_2"]},
                {"phase": "outcome", "action": "旅客拿起补票离开服务台，返回站台。", "evidence_refs": ["frame_3", "speech_2"]},
            ],
        }
        self.sentences = [
            {"text": "一名旅客发现车票遗失，向工作人员求助，对方随后查询购票记录。", "event_refs": ["event_1", "event_2"]},
            {"text": "工作人员将补票交给旅客，旅客领取后离开服务台，返回站台。", "event_refs": ["event_2", "event_3"]},
        ]
        self.calls = []

    def add_specific_context(self, ledger):
        self.evidence.text = "[speech_1] 请保持约定的节奏。"
        self.evidence.audio_clips = [{"start": 50, "end": 70}]
        ledger["events"].insert(1, {"importance": "context", "phase": "activity",
            "action": "参与者提醒对方保持约定的节奏", "evidence_refs": ["speech_1"]})

    async def analyze(self, ledger=None, sentences=None, *, summary_only=False, previous=None, audit=None, repaired=None, ending=None, route=None, primary=None, final_ending=None, dialogue=None, segment=None, reconciled=None, segment_callable=False, **options):
        ledger = self.ledger if ledger is None else ledger
        sentences = self.sentences if sentences is None else sentences

        async def completion(prompt, evidence):
            self.calls.append((prompt, evidence))
            if prompt.startswith("分段内容观察"):
                if segment is not None:
                    value = segment
                    if isinstance(segment, list):
                        index = sum(p.startswith("分段内容观察") for p, _ in self.calls) - 1
                        value = segment[min(index, len(segment) - 1)]
                    if callable(value) and segment_callable:
                        value = value()
                    if isinstance(value, Exception):
                        raise value
                    return value
                return {"events": [{"action": "旅客在服务台查询购票记录。", "evidence_refs": ["frame_1"]}]}
            if prompt.startswith("对白含义"):
                if isinstance(dialogue, Exception):
                    raise dialogue
                return dialogue or {"reason": "no_specific_context", "contexts": []}
            if prompt.startswith("末帧独立观察"):
                if isinstance(final_ending, Exception):
                    raise final_ending
                if isinstance(final_ending, list):
                    index = sum(p.startswith("末帧独立观察") for p, _ in self.calls) - 1
                    return copy.deepcopy(final_ending[min(index, len(final_ending) - 1)])
                if isinstance(final_ending, dict):
                    return final_ending
                if final_ending is None:
                    return {"presence": ending["observations"][-1].get("presence", "unclear") if isinstance(ending, dict) else "unclear"}
                return {"presence": final_ending}
            if prompt.startswith("主活动观察"):
                if isinstance(primary, Exception):
                    raise primary
                # Production reads one frame per request (three reads per frame
                # in deep mode); fixtures list codes in sampled-frame order and
                # get them served one frame at a time.
                reads = 3 if options.get("analysis_depth") == "deep" else 1
                index = (sum(p.startswith("主活动观察") for p, _ in self.calls) - 1) // reads
                rows = (primary or {}).get("observations", [])
                if index < len(rows) and "raw" in rows[index]:
                    return copy.deepcopy(rows[index]["raw"])
                code = rows[index]["activity"] if index < len(rows) else "unclear"
                return {"observations": [{"frame_ref": "frame_1", "activity": code}]}
            if prompt.startswith("叙事模式"):
                if isinstance(route, Exception):
                    raise route
                return route or {"mode": ledger.get("mode", "insufficient"),
                                 "basis": "goal_or_problem" if ledger.get("mode") == "story" else "activity_only",
                                 "evidence_refs": ["frame_1", "frame_3"],
                                 **({"premise": {key: ledger["events"][0][key] for key in ("action", "evidence_refs")}}
                                    if ledger.get("mode") == "story" else {})}
            if prompt.startswith("末段观察"):
                if isinstance(ending, Exception):
                    raise ending
                response = copy.deepcopy(ending or {"observations": [
                    {"frame_ref": "frame_1", "observation": "几位参与者正在排练合奏。"},
                    {"frame_ref": "frame_2", "observation": "几位参与者收起乐器，离开画面。"},
                ]})
                for row in response.get("observations", []):
                    if isinstance(row, dict):
                        row.setdefault("presence", "unclear")
                return response
            if prompt.startswith("阶段五"):
                if isinstance(audit, Exception):
                    raise audit
                if audit is not None:
                    if isinstance(audit, list):
                        index = sum(value.startswith("阶段五") for value, _ in self.calls) - 1
                        return copy.deepcopy(audit[min(index, len(audit) - 1)])
                    return copy.deepcopy(audit)
                import json
                candidate = json.loads(prompt.split("待核验数据：", 1)[1])
                return {"mode": candidate["narrative"]["mode"], "coverage_ok": True, "readable": True, "non_redundant": True,
                        "events": [{"id": event["id"], "supported": True} for event in candidate["narrative"]["events"]],
                        "sentences": [{"index": index + 1, "supported": True} for index, _ in enumerate(candidate["summary_sentences"])]}
            if prompt.startswith("阶段一"):
                return {"resource_kind": "video", "category": "movie", "subtype": "", "confidence": 0.9,
                        "evidence_sources": ["video_frames"], "observations": []}
            if prompt.startswith("阶段三"):
                reconciliation = '待核验事件候选：' in prompt
                if reconciliation and reconciled is not None:
                    if isinstance(reconciled, Exception):
                        raise reconciled
                    return copy.deepcopy(reconciled)
                if isinstance(ledger, Exception):
                    raise ledger
                response = copy.deepcopy(ledger)
                if "version" not in response:
                    response["version"] = 2
                    for index, event in enumerate(response.get("events", [])):
                        if isinstance(event, dict):
                            event.setdefault("importance", "core" if not index else "progression")
                return response if reconciliation else {**response, "summary_sentences": copy.deepcopy(sentences)}
            if prompt.startswith("阶段四"):
                if repaired is not None and sum(value.startswith("阶段四") for value, _ in self.calls) > 1:
                    return {"summary_sentences": copy.deepcopy(repaired)}
                return {"summary_sentences": copy.deepcopy(sentences)}
            return {"resource_kind": "video", "category": "movie", "subtype": "", "matches_category": True,
                    "evidence_sources": ["video_frames"], "tags": [],
                    "facets": {key: "车站中的补票情节。" for key in analysis_profile("movie").directions},
                    "summary_sentences": copy.deepcopy(sentences)}

        with mock.patch("module.resource_analysis.service.extract_evidence", return_value=self.evidence):
            return await analyze_resource(str(self.path), completion=completion, min_confidence=0.4,
                                          summary_only=summary_only, previous_analysis=previous, **options)

    async def test_publisher_context_is_gated_and_never_reaches_blind_audit(self):
        for allowed in (False, True):
            self.calls.clear()
            await self.analyze(source_context={"caption": "独特背景线索TESTCAPTION", "chat_id": "PRIVATECHAT"}, share_source_text=allowed)
            narrative = next(prompt for prompt, _ in self.calls if prompt.startswith("阶段三"))
            self.assertEqual(allowed, "TESTCAPTION" in narrative)
            for prompt, _ in self.calls:
                self.assertNotIn("PRIVATECHAT", prompt)
                if prompt.startswith(("阶段一", "阶段四", "阶段五")):
                    self.assertNotIn("TESTCAPTION", prompt)

    async def test_deep_mode_uses_focused_windows_with_global_reference_mapping(self):
        self.evidence.sampling["timeline_windows"] = [
            {"start": 0, "end": 30}, {"start": 40, "end": 80}, {"start": 90, "end": 120}]
        result = await self.analyze(analysis_depth="deep")
        self.assertEqual("ready", result["summary_quality"]["status"])
        calls = [(prompt, evidence) for prompt, evidence in self.calls if prompt.startswith("分段内容观察")]
        # Three windows, each read three times; only recurring candidates survive.
        self.assertEqual(9, len(calls))
        self.assertTrue(all(len(evidence.images) == 1 for _, evidence in calls))
        self.assertEqual(3, result["diagnostics"]["segments"]["windows"])
        self.assertEqual(3, result["diagnostics"]["segments"]["reads"])
        self.assertEqual(3, result["diagnostics"]["segments"]["results"][0]["agreed"][0]["reads"])

    async def test_segment_connection_failure_retries_only_that_stage(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 0, "end": 30}]
        with mock.patch("module.resource_analysis.service._retry_pause", new=mock.AsyncMock()):
            result = await self.analyze(analysis_depth="deep", segment=[
                ModelError("model_connection", "连接失败", retryable=True), {"events": []}])
        attempts = result["diagnostics"]["attempts"]
        self.assertEqual(["failed", "success", "success", "success"], [a["status"] for a in attempts if a["stage"] == "segment"])
        self.assertEqual(1, sum(a["stage"] == "classification" for a in attempts))
        self.assertEqual("ready", result["summary_quality"]["status"])

    async def test_segment_candidates_are_audited_without_forcing_them_into_the_ledger(self):
        import json
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "content", "phase": "activity", "action": "两人分工演示设备操作", "evidence_refs": ["frame_2"]}]}
        options = dict(analysis_depth="deep", summary_only=True, ledger=ledger,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"},
            segment={"events": [{"action": "两人分工演示设备的流程", "evidence_refs": ["frame_1"]}]},
            sentences=[{"text": "参与者进行按摩，并分工演示流程。", "event_refs": ["event_1", "event_2"]}])
        result = await self.analyze(**options)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertIn("分工演示设备操作", result["summary"])
        self.assertNotIn("分工演示设备的流程", result["summary"])
        self.assertEqual(2, len(result["narrative"]["events"]))
        audit_prompt = next(p for p, _ in self.calls if p.startswith("阶段五"))
        candidate = json.loads(audit_prompt.split("待核验数据：", 1)[1])
        self.assertEqual("两人分工演示设备的流程", candidate["segment_candidates"][0]["action"])
        self.assertEqual(["frame_2"], candidate["segment_candidates"][0]["evidence_refs"])
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])
        self.calls.clear()
        rejected = await self.analyze(**options, audit=ModelError("model_refused", "拒绝"))
        self.assertEqual("review", rejected["summary_quality"]["status"])
        self.assertIn("model_refused", rejected["summary_quality"]["issues"])
        self.calls.clear()
        unsupported = await self.analyze(**options, audit={"mode": "activity", "coverage_ok": True,
            "readable": True, "non_redundant": True,
            "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": False}],
            "sentences": [{"index": 1, "supported": False}]})
        self.assertEqual("review", unsupported["summary_quality"]["status"])

        self.calls.clear()
        missing = await self.analyze(**options, audit={"mode": "activity", "coverage_ok": False,
            "coverage_reason": "missing_progression", "readable": True, "non_redundant": True,
            "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": True}],
            "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": True}]})
        self.assertEqual("review", missing["summary_quality"]["status"])
        self.assertIn("narrative_coverage_incomplete", missing["summary_quality"]["issues"])
        self.assertEqual([], missing["summary_sentences"])

    async def test_simple_activity_facts_do_not_gain_causality_from_a_second_writer(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "studio"}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1"]},
            {"importance": "content", "phase": "activity", "action": "参与者分工演示设备的操作流程", "evidence_refs": ["frame_2", "frame_3"]}]}
        result = await self.analyze(summary_only=True, previous=previous, ledger=ledger,
            sentences=[{"text": "参与者为了完成考核而展示设备操作。", "event_refs": ["event_1", "event_2"]}])
        self.assertEqual("参与者进行按摩。参与者分工演示设备的操作流程。", result["summary"])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertTrue(any(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_single_read_segment_candidate_does_not_rescue_generic_global_summary(self):
        # A candidate that only one of three reads of the same frames produced is
        # not evidence; only recurring candidates are carried (see ContentCarryTest).
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        result = await self.analyze(analysis_depth="deep", summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"},
            ledger={"version": 2, "mode": "activity", "events": [
                {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]}]},
            segment=[{"events": [{"action": "两人分工演示流程", "evidence_refs": ["frame_1"]}]}, {"events": []}, {"events": []}],
            sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertEqual([], result["summary_sentences"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])

    async def test_single_read_segment_candidates_are_dropped_and_recurring_ones_kept(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        import json
        seen = {"action": "两人分工演示设备的操作流程", "evidence_refs": ["frame_1"]}
        once = {"action": "一人整理桌上的乐谱并翻页", "evidence_refs": ["frame_1"]}
        result = await self.analyze(analysis_depth="deep", summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"},
            ledger={"version": 2, "mode": "activity", "events": [
                {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]},
                {"importance": "content", "phase": "activity", "action": "两人分工演示设备的操作流程", "evidence_refs": ["frame_2"]}]},
            segment=[{"events": [seen, once]}, {"events": [{**seen, "action": "两人分工演示设备操作流程"}]}, {"events": []}],
            sentences=[{"text": "参与者进行按摩，并分工演示设备操作。", "event_refs": ["event_1", "event_2"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        window = result["diagnostics"]["segments"]["results"][0]
        self.assertEqual([{"candidate_count": 2}, {"candidate_count": 1}, {"candidate_count": 0}], window["reads"])
        self.assertEqual(1, len(window["agreed"]))
        self.assertEqual(2, window["agreed"][0]["reads"])
        audit_prompt = next(p for p, _ in self.calls if p.startswith("阶段五"))
        candidates = json.loads(audit_prompt.split("待核验数据：", 1)[1])["segment_candidates"]
        self.assertEqual(["两人分工演示设备的操作流程"], [row["action"] for row in candidates])
        self.assertNotIn("reads", candidates[0])

    async def test_one_scene_only_read_does_not_fail_a_window_with_usable_reads(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        seen = {"action": "两人分工演示设备的操作流程", "evidence_refs": ["frame_1"]}
        result = await self.analyze(analysis_depth="deep", summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"},
            ledger={"version": 2, "mode": "activity", "events": [
                {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]},
                {"importance": "content", "phase": "activity", "action": "两人分工演示设备的操作流程", "evidence_refs": ["frame_2"]}]},
            segment=[{"events": [{"action": "画面背景是明亮的房间。", "evidence_refs": ["frame_1"]}]}, {"events": [seen]}, {"events": [seen]}],
            sentences=[{"text": "参与者进行按摩，并分工演示设备操作。", "event_refs": ["event_1", "event_2"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        window = result["diagnostics"]["segments"]["results"][0]
        self.assertEqual("accepted", window["status"])
        self.assertEqual(["scene_only_event"], window["rejected_reads"][0])
        self.assertEqual(1.0, window["rejected_reads"][-1]["agreement"][0]["best_ratio"])
        self.assertEqual(2, window["agreed"][0]["reads"])

    async def test_scenery_candidate_is_dropped_without_voiding_the_read(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        seen = {"action": "两人分工演示设备的操作流程", "evidence_refs": ["frame_1"]}
        scenery = {"action": "画面背景是明亮的房间。", "evidence_refs": ["frame_1"]}
        result = await self.analyze(analysis_depth="deep", summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"},
            ledger={"version": 2, "mode": "activity", "events": [
                {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]},
                {"importance": "content", "phase": "activity", "action": "两人分工演示设备的操作流程", "evidence_refs": ["frame_2"]}]},
            segment=[{"events": [scenery, seen]}, {"events": []}, {"events": [seen]}],
            sentences=[{"text": "参与者进行按摩，并分工演示设备操作。", "event_refs": ["event_1", "event_2"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        window = result["diagnostics"]["segments"]["results"][0]
        self.assertEqual("accepted", window["status"])
        self.assertEqual([{"candidate_count": 2}, {"candidate_count": 0}, {"candidate_count": 1}], window["reads"])
        self.assertEqual({"dropped_candidates": ["scene_only_event"]}, window["rejected_reads"][0])
        self.assertEqual(2, window["agreed"][0]["reads"])

    async def test_segment_failure_keeps_reason_and_refusal_never_retries(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 0, "end": 30}]
        for reason, count in (("model_connection", 2), ("model_refused", 1)):
            with self.subTest(reason=reason), mock.patch("module.resource_analysis.service._retry_pause", new=mock.AsyncMock()):
                result = await self.analyze(analysis_depth="deep", segment=ModelError(reason, "服务未完成请求", retryable=reason == "model_connection"))
            self.assertIn(reason, result["summary_quality"]["issues"])
            self.assertEqual("rejected", result["diagnostics"]["segments"]["results"][0]["status"])
            self.assertEqual(count, sum(a["stage"] == "segment" for a in result["diagnostics"]["attempts"]))
            self.assertIn("video_frames", result["evidence_sources"])

    async def test_rate_limit_gets_bounded_backoff_retries_other_transients_one(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 0, "end": 30}]
        pauses = []
        async def pause(seconds):
            pauses.append(seconds)
        calls = {"n": 0}
        def throttled_then_ok():
            calls["n"] += 1
            if calls["n"] <= 3:
                raise ModelError("model_rate_limit", "限流", retryable=True, http_status=429, retry_after=0)
            return {"events": []}
        with mock.patch("module.resource_analysis.service._retry_pause", new=pause):
            result = await self.analyze(analysis_depth="deep", segment=[throttled_then_ok, throttled_then_ok, throttled_then_ok, throttled_then_ok],
                                        segment_callable=True)
        attempts = [a for a in result["diagnostics"]["attempts"] if a["stage"] == "segment"]
        self.assertEqual(["failed", "failed", "failed", "success"], [a["status"] for a in attempts][:4])
        self.assertEqual([10, 20, 40], pauses[:3])
        self.assertNotIn("model_rate_limit", result["summary_quality"]["issues"])

    async def test_failed_segment_records_window_and_shape_without_source_prose(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 0, "end": 30}]
        text = "画面背景是明亮的房间。"
        result = await self.analyze(analysis_depth="deep", segment={
            "events": [{"action": text, "evidence_refs": ["frame_1"]}]})
        self.assertEqual("review", result["summary_quality"]["status"])
        diagnostic = result["diagnostics"]["segments"]
        self.assertEqual(1, diagnostic["windows"])
        row = diagnostic["results"][0]
        self.assertEqual(1, row["window"])
        self.assertEqual("rejected", row["status"])
        self.assertIn("scene_only_event", row["issues"])
        self.assertEqual(len(text), row["shape"]["events"][0]["action_length"])
        self.assertNotIn(text, str(diagnostic))

    async def test_specific_visual_content_is_not_reduced_to_category(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "other_adult"}
        from module.resource_analysis.taxonomy import ADULT_SUBTYPES
        previous["subtype"] = next(item.id for item in ADULT_SUBTYPES if "video" in item.kinds)
        ledger = {"mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行舞台表演", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "content", "phase": "activity", "action": "参与者轮流演示道具，并一起完成配合表演。", "evidence_refs": ["frame_2", "frame_3"]}]}
        sentences = [{"text": "几位参与者进行舞台表演，轮流演示道具并配合完成节目。", "event_refs": ["event_1", "event_2"]}]
        result = await self.analyze(ledger, sentences, summary_only=True, previous=previous)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertIn("道具", result["summary"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertTrue(any(prompt.startswith("阶段五") for prompt, _ in self.calls))
        self.assertEqual("activity_projection", result["diagnostics"]["summary_revision"]["strategy"])

    async def test_multiple_content_events_use_consolidation_without_bypassing_audit(self):
        previous = {'status': 'complete', 'resource_kind': 'video', 'category': 'adult', 'subtype': 'independent'}
        ledger = {'version': 2, 'mode': 'activity', 'events': [
            {'importance': 'core', 'phase': 'activity', 'action': '参与者进行舞台表演', 'evidence_refs': ['frame_1']},
            {'importance': 'content', 'phase': 'activity', 'action': '两人轮流演示道具的使用', 'evidence_refs': ['frame_2']},
            {'importance': 'content', 'phase': 'activity', 'action': '两人配合完成道具表演', 'evidence_refs': ['frame_3']}]}
        sentences = [{'text': '两人轮流演示道具，并配合完成舞台表演。', 'event_refs': ['event_1', 'event_2', 'event_3']}]
        result = await self.analyze(ledger, sentences, summary_only=True, previous=previous)
        self.assertEqual('ready', result['summary_quality']['status'])
        self.assertEqual(sentences[0]['text'], result['summary'])
        self.assertEqual('model_writing', result['diagnostics']['summary_revision']['strategy'])
        request = next(e for p, e in self.calls if p.startswith('阶段四'))
        self.assertEqual([], request.images)
        self.assertEqual('validated event ledger', request.text)
        self.assertTrue(any(p.startswith('阶段五') for p, _ in self.calls))
        self.calls.clear()
        rejected = await self.analyze(ledger, sentences, summary_only=True, previous=previous,
            audit=ModelError('model_refused', '拒绝'))
        self.assertIn('model_refused', rejected['summary_quality']['issues'])
        self.assertEqual([], rejected['summary_sentences'])

    async def test_scene_description_cannot_pass_as_narrative(self):
        scene_only = [
            {"text": "两人在房间里，背景可见浅色窗帘和木质桌面，镜头主要呈现室内环境。", "evidence_refs": ["frame_1"]},
            {"text": "另一张画面采用较近构图，人物身后有书架，光线来自侧面的窗户。", "evidence_refs": ["frame_2"]},
        ]
        result = await self.analyze(ledger={"mode": "story", "events": []}, sentences=scene_only)
        self.assertEqual("complete", result["status"])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertNotEqual("".join(item["text"] for item in scene_only), result["summary"])

    async def test_story_is_written_from_ordered_events_not_room_description(self):
        result = await self.analyze()
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("story", result["narrative"]["mode"])
        self.assertEqual([0, 60, 100], [item["start"] for item in result["narrative"]["events"]])
        self.assertEqual("".join(item["text"] for item in self.sentences), result["summary"])
        self.assertTrue(self.calls[-2][0].startswith("阶段四"))
        self.assertFalse(self.calls[-2][1].images, "Writing uses validated events, not another reading of isolated images")
        self.assertIn("event_1", self.calls[-2][0])
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])

    async def test_static_facts_mislabeled_as_events_are_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["events"][0]["action"] = "画面中有浅色窗帘，人物位于房间中央，背景是木质桌面。"
        result = await self.analyze(ledger=ledger)
        self.assertEqual("review", result["summary_quality"]["status"])

    async def test_consolidated_activity_is_not_mistaken_for_scenery_because_of_context(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "穿着便服的几人一起排练一段合奏。", "evidence_refs": ["frame_1", "frame_3"]},
        ]}
        sentences = [{"text": "几位演奏者配合排练一段合奏，片段后段仍在继续排练。", "event_refs": ["event_1"]}]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])

    async def test_incidental_static_context_does_not_discard_valid_activity_events(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "画面中有桌子和窗帘，几人身穿便服。", "evidence_refs": ["frame_1"]},
            {"phase": "activity", "action": "几人配合排练一段合奏。", "evidence_refs": ["frame_2", "frame_3"]},
        ]}
        result = await self.analyze(ledger=ledger, sentences=[
            {"text": "几人配合排练一段合奏，片段以合奏排练为主。", "event_refs": ["event_1"]},
        ])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(1, len(result["narrative"]["events"]))
        self.assertEqual("几人配合排练一段合奏。", result["narrative"]["events"][0]["action"])
        self.assertEqual(1, result["narrative"]["omitted_context_count"])

    async def test_static_context_alone_still_cannot_become_an_activity_synopsis(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "画面中有桌子和窗帘，几人身穿便服。", "evidence_refs": ["frame_1"]},
        ]}
        result = await self.analyze(ledger=ledger)
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertFalse(result["narrative"]["events"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_concise_factual_events_do_not_need_padding_to_reach_the_writer(self):
        ledger = copy.deepcopy(self.ledger)
        for event, action in zip(ledger["events"], ("旅客遗失车票。", "工作人员补票。", "旅客拿票离开。")):
            event["action"] = action
        sentences = [
            {"text": "旅客发现车票遗失，工作人员随后办理补票，帮助旅客取回车票。", "event_refs": ["event_1", "event_2"]},
            {"text": "领到补办的车票后，旅客带着车票离开。", "event_refs": ["event_3"]},
        ]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])
        self.assertEqual([event["action"] for event in ledger["events"]],
                         [event["action"] for event in result["narrative"]["events"]])

    async def test_empty_or_malformed_events_do_not_reach_writing(self):
        for action in (None, {}, [], "", "。。。。", "ok"):
            with self.subTest(action=action):
                self.calls.clear()
                ledger = copy.deepcopy(self.ledger)
                ledger["events"][0]["action"] = action
                result = await self.analyze(ledger=ledger)
                self.assertEqual("review", result["summary_quality"]["status"])
                self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_insufficient_evidence_never_becomes_a_no_plot_claim(self):
        result = await self.analyze(ledger={"mode": "insufficient", "events": []})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertEqual("insufficient", result["narrative"]["mode"])
        self.assertNotIn("没有剧情", result["summary"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_activity_is_labeled_separately_and_still_describes_actions(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "演奏者拿起吉他拨弦，另一人敲击鼓面配合演奏。", "evidence_refs": ["frame_1"]},
            {"phase": "activity", "action": "演奏者放下吉他起身致意，另一人也停下敲击。", "evidence_refs": ["frame_3"]},
        ]}
        sentences = [
            {"text": "片段记录两人合奏，一人拿起吉他拨弦，另一人敲击鼓面配合。", "event_refs": ["event_1"]},
            {"text": "后段画面中演奏者放下吉他起身致意，另一人也停下敲击。", "event_refs": ["event_2"]},
        ]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("activity", result["narrative"]["mode"])

    async def test_long_video_reads_closing_window_without_losing_global_references(self):
        self.evidence.images = [f"fixture-{index}" for index in range(6)]
        self.evidence.sampling = {"duration_seconds": 400, "timestamps": [2, 10, 198, 210, 382, 398],
                                 "timeline_windows": [{"start": 0, "end": 20}, {"start": 190, "end": 220}, {"start": 380, "end": 400}]}
        result = await self.analyze()
        ending_calls = [(prompt, evidence) for prompt, evidence in self.calls if prompt.startswith("末段观察")]
        self.assertEqual(1, len(ending_calls))
        self.assertEqual(["fixture-4", "fixture-5"], ending_calls[0][1].images)
        prompt = next(prompt for prompt, _ in self.calls if prompt.startswith("阶段三"))
        self.assertIn('"frame_ref": "frame_5"', prompt)
        self.assertIn('"frame_ref": "frame_6"', prompt)
        self.assertIn("收起乐器，离开画面", prompt)
        self.assertEqual("accepted", result["diagnostics"]["ending_observation"]["status"])

    async def test_ending_observation_refusal_stops_without_retry_or_fallback(self):
        self.evidence.sampling.update(duration_seconds=400, timeline_windows=[{"start": 100, "end": 120}])
        result = await self.analyze(ending=ModelError("model_refused", "模型拒绝"))
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertEqual(1, sum(prompt.startswith("末段观察") for prompt, _ in self.calls))
        self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_unmatched_ending_observations_cannot_become_mainline_evidence(self):
        self.evidence.sampling.update(duration_seconds=400, timeline_windows=[{"start": 100, "end": 120}])
        result = await self.analyze(ending={"observations": [
            {"frame_ref": "frame_99", "observation": "参与者收起乐器，离开画面。"},
        ]})
        self.assertIn("ending_observation_invalid", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_old_event_contract_is_not_silently_treated_as_mainline(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["version"] = 1
        result = await self.analyze(ledger=ledger)
        self.assertIn("narrative_schema_version", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_incidental_observations_are_not_mandatory_synopsis_events(self):
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "几人一起排练一段合奏。", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "detail", "phase": "activity", "action": "演奏者调整坐姿和手臂位置。", "evidence_refs": ["frame_3"]},
        ]}
        focused = [{"text": "几位演奏者配合排练一段合奏。", "event_refs": ["event_1"]}]
        result = await self.analyze(ledger=ledger, sentences=focused)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(focused[0]["text"], result["summary"])
        self.assertEqual(1, len(result["narrative"]["events"]))
        self.assertEqual(1, result["narrative"]["omitted_detail_count"])
        writer = next(prompt for prompt, _ in self.calls if prompt.startswith("阶段四"))
        self.assertNotIn("调整坐姿", writer)

    async def test_real_progression_remains_mandatory_after_detail_projection(self):
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "几人一起排练一段合奏。", "evidence_refs": ["frame_1"]},
            {"importance": "detail", "phase": "activity", "action": "演奏者调整坐姿和手臂位置。", "evidence_refs": ["frame_2"]},
            {"importance": "progression", "phase": "activity", "action": "几人停止排练并收起乐器离开。", "evidence_refs": ["frame_3"]},
        ]}
        result = await self.analyze(ledger=ledger, sentences=[
            {"text": "几位演奏者配合排练一段合奏。", "event_refs": ["event_1"]},
        ])
        self.assertEqual(2, len(result["narrative"]["events"]))
        self.assertIn("summary_drops_progression", result["summary_quality"]["issues"])

    async def test_adult_core_cannot_smuggle_a_micro_action_list_into_the_writer(self):
        from module.resource_analysis.taxonomy import ADULT_SUBTYPES
        previous = {"status": "complete", "resource_kind": "video", "category": "adult",
                    "subtype": next(item.id for item in ADULT_SUBTYPES if "video" in item.kinds)}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动，一人坐在床上，另一人伸手。", "evidence_refs": ["frame_1"]},
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动，参与者抬起手臂。", "evidence_refs": ["frame_3"]},
        ]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, sentences=[
            {"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]},
        ])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertEqual(1, len(result["narrative"]["events"]))
        self.assertEqual("参与者进行成人亲密互动", result["narrative"]["events"][0]["action"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertEqual(["frame_1", "frame_3"], result["narrative"]["events"][0]["evidence_refs"])

    async def test_short_complete_event_sentences_do_not_need_filler(self):
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "几人正在排练合奏", "evidence_refs": ["frame_1"]},
            {"importance": "progression", "phase": "activity", "action": "几人收起乐器离开", "evidence_refs": ["frame_3"]},
        ]}
        result = await self.analyze(ledger=ledger, sentences=[
            {"text": "几人正在排练合奏", "event_refs": ["event_1"]},
            {"text": "几人收起乐器离开", "event_refs": ["event_2"]},
        ])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("几人正在排练合奏。几人收起乐器离开。", result["summary"])

    async def test_adult_event_extraction_cannot_escape_activity_contract_by_switching_to_story(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        route = {"mode": "activity", "basis": "activity_only", "evidence_refs": ["frame_1", "frame_3"]}
        result = await self.analyze(summary_only=True, previous=previous, route=route)
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("narrative_mode_mismatch", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        request = next(evidence for prompt, evidence in self.calls if prompt.startswith("阶段三"))
        self.assertEqual(["activity"], request.output_schema["properties"]["mode"]["enum"])

    async def test_narrative_route_refusal_does_not_continue_with_free_events(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        result = await self.analyze(summary_only=True, previous=previous, route=ModelError("model_refused", "拒绝"))
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_verified_story_route_still_allows_grounded_non_graphic_plot(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "studio"}
        result = await self.analyze(summary_only=True, previous=previous)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("story", result["narrative"]["mode"])
        request = next(evidence for prompt, evidence in self.calls if prompt.startswith("阶段三"))
        self.assertEqual(["story"], request.output_schema["properties"]["mode"]["enum"])
        self.assertEqual("".join(item["text"] for item in self.sentences), result["summary"])
        self.assertEqual("model_writing", result["diagnostics"]["summary_revision"]["strategy"])
        self.assertEqual(1, sum(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_story_route_cannot_drop_the_problem_that_established_story(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "studio"}
        route = {"mode": "story", "basis": "goal_or_problem", "evidence_refs": ["speech_1", "frame_3"],
                 "premise": {"action": self.ledger["events"][0]["action"], "evidence_refs": ["speech_1"]}}
        ledger = copy.deepcopy(self.ledger)
        ledger["events"][0] = {"phase": "setup", "action": "旅客走到服务台前。", "evidence_refs": ["frame_1"]}
        result = await self.analyze(summary_only=True, previous=previous, route=route, ledger=ledger)
        self.assertIn("story_premise_missing", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertEqual("premise_not_retained", result['diagnostics']['premise_binding']['reason'])

    async def test_linked_premise_reference_failure_has_redacted_diagnostics(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['events'][0]['premise_ref'] = 'premise_1'
        route = {'mode': 'story', 'basis': 'goal_or_problem', 'evidence_refs': ['frame_1', 'frame_3'],
                 'premise': {'action': ledger['events'][0]['action'], 'evidence_refs': ['frame_1', 'frame_3']}}
        result = await self.analyze(summary_only=True, ledger=ledger, route=route,
            previous={'status': 'complete', 'resource_kind': 'video', 'category': 'adult', 'subtype': 'studio'})
        self.assertIn('story_premise_missing', result['summary_quality']['issues'])
        self.assertEqual({'link_mode': 'explicit', 'linked_events': 1, 'premise_ref_count': 2,
                          'linked_union_covers_premise': False, 'reason': 'premise_references_not_bound'},
                         result['diagnostics']['premise_binding'])
        self.assertFalse(any(prompt.startswith(('阶段四', '阶段五')) for prompt, _ in self.calls))

    async def test_story_premise_is_carried_to_writer_and_blind_audit(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "studio"}
        result = await self.analyze(summary_only=True, previous=previous)
        self.assertEqual(["event_1"], result["narrative"]["required_event_ids"])
        prompt = next(p for p, _ in self.calls if p.startswith("阶段三"))
        self.assertIn(self.ledger["events"][0]["action"], prompt)
        audit = next(p for p, _ in self.calls if p.startswith("阶段五"))
        self.assertIn('"required_event_ids": ["event_1"]', audit)

    async def test_explicit_story_link_allows_paraphrase_but_still_audits_the_original_problem(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "studio"}
        route = {"mode": "story", "basis": "goal_or_problem", "evidence_refs": ["speech_1", "frame_3"],
                 "premise": {"action": "旅客发现车票遗失并请求工作人员协助", "evidence_refs": ["speech_1"]}}
        ledger = copy.deepcopy(self.ledger)
        ledger["events"][0]["premise_ref"] = "premise_1"
        result = await self.analyze(summary_only=True, previous=previous, route=route, ledger=ledger)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(["event_1"], result["narrative"]["required_event_ids"])
        audit = next(prompt for prompt, _ in self.calls if prompt.startswith("阶段五"))
        self.assertIn(route["premise"]["action"], audit)
        self.assertNotIn(route["premise"]["action"], str(result["diagnostics"]))

    async def test_explicit_story_link_does_not_override_failed_semantic_audit(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "studio"}
        route = {"mode": "story", "basis": "goal_or_problem", "evidence_refs": ["speech_1", "frame_3"],
                 "premise": {"action": "旅客发现车票遗失并请求工作人员协助", "evidence_refs": ["speech_1"]}}
        ledger = copy.deepcopy(self.ledger)
        ledger["events"][0].update(action="旅客在服务台领取宣传资料。", premise_ref="premise_1")
        result = await self.analyze(summary_only=True, previous=previous, route=route, ledger=ledger,
            audit={"mode": "activity", "coverage_ok": False, "coverage_reason": "missing_progression",
                   "readable": True, "non_redundant": True,
                   "events": [{"id": f"event_{i}", "supported": i != 1} for i in (1, 2, 3)],
                   "sentences": [{"index": i, "supported": False} for i in (1, 2)]})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("narrative_event_not_grounded", result["summary_quality"]["issues"])

    async def test_canonical_activity_prose_cannot_add_beginnings_or_endings_even_if_audit_would_pass(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_3"]}]}
        invented = [{"text": "参与者开始进行亲密互动。", "event_refs": ["event_1"]},
                    {"text": "互动结束后，参与者不再出现在画面中。", "event_refs": ["event_2"]}]
        self.add_specific_context(ledger)
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, sentences=invented)
        self.assertEqual("参与者进行成人亲密互动。音轨中，参与者提醒对方保持约定的节奏。此前的参与者不再出现在画面中。", result["summary"])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("activity_projection", result["diagnostics"]["summary_revision"]["strategy"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_projected_activity_still_requires_independent_factual_acceptance(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_3"]}]}
        rejected = {"mode": "activity", "coverage_ok": False, "coverage_reason": "missing_main_activity",
                    "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": False, "reason": "unsupported_action"}, {"id": "event_2", "supported": True, "reason": "none"}],
                    "sentences": [{"index": 1, "supported": False, "reason": "unsupported_action"}, {"index": 2, "supported": True, "reason": "none"}]}
        self.add_specific_context(ledger)
        rejected["events"].append({"id": "event_3", "supported": True, "reason": "none"})
        rejected["sentences"].append({"index": 3, "supported": True, "reason": "none"})
        for verdict in (rejected, ModelError("model_refused", "拒绝")):
            self.calls.clear()
            result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, audit=verdict)
            self.assertEqual("review", result["summary_quality"]["status"])
            self.assertEqual([], result["summary_sentences"])
            self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
            self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))
            if isinstance(verdict, Exception):
                self.assertIn("model_refused", result["summary_quality"]["issues"])

    async def test_single_activity_label_is_not_a_ready_reader_synopsis(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertNotEqual("参与者进行成人亲密互动。", result["summary"])

    async def test_independent_dialogue_meaning_is_preserved_when_visual_ledger_ignores_speech(self):
        self.evidence.text = "[speech_1] 请再排练一次，注意约定的节奏。"
        self.evidence.audio_clips = [{"start": 50, "end": 70}]
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]}]}
        dialogue = {"reason": "specific_context", "contexts": [
            {"action": "一人要求再排练一次，并提醒对方注意约定的节奏。", "evidence_refs": ["speech_1"]}]}
        sentences = [
            {"text": "片段包含参与者之间的成人亲密互动。", "event_refs": ["event_1"]},
            {"text": "一人要求再排练一次，并提醒对方注意约定的节奏。", "event_refs": ["event_2"]}]
        result = await self.analyze(ledger=ledger, sentences=sentences, dialogue=dialogue, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertIn("注意约定的节奏", result["summary"])
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])
        request = next(evidence for prompt, evidence in self.calls if prompt.startswith("对白含义"))
        self.assertEqual([], request.images, "dialogue meaning is read without competing visual evidence")
        self.assertNotIn("file_name", request.text)
        self.assertEqual(["speech_1"], result["narrative"]["events"][1]["evidence_refs"])

    async def test_independent_dialogue_refusal_stops_without_visual_fallback(self):
        result = await self.analyze(ledger={"mode": "activity", "events": []}, summary_only=True,
            dialogue=ModelError("model_refused", "拒绝"),
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertEqual(1, sum(prompt.startswith("对白含义") for prompt, _ in self.calls))
        self.assertFalse(any(prompt.startswith(("阶段三", "阶段四", "阶段五")) for prompt, _ in self.calls))

    async def test_activity_projection_keeps_both_dialogue_meanings_before_any_writer(self):
        self.evidence.text = "[speech_1] 请再排练一次。明天集合的地点还是原来那里。"
        self.evidence.audio_clips = [{"start": 50, "end": 70}]
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]}]}
        dialogue = {"reason": "specific_context", "contexts": [
            {"action": "一人提出再次排练这一段的要求。", "evidence_refs": ["speech_1"]},
            {"action": "一人说明次日仍在原来的地点集合。", "evidence_refs": ["speech_1"]}]}
        discarded_context = [{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}]
        result = await self.analyze(ledger=ledger, dialogue=dialogue, sentences=discarded_context,
            summary_only=True, previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("ready", result["summary_quality"]["status"])
        for context in dialogue["contexts"]:
            self.assertIn(context["action"], result["summary"])
        self.assertEqual(3, len(result["summary_sentences"]))
        self.assertEqual("activity_projection", result["diagnostics"]["summary_revision"]["strategy"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_projected_dialogue_still_cannot_bypass_factual_refusal(self):
        self.evidence.text = "[speech_1] 请再排练一次。"
        self.evidence.audio_clips = [{"start": 50, "end": 70}]
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]}]}
        result = await self.analyze(ledger=ledger, sentences=[], summary_only=True,
            dialogue={"reason": "specific_context", "contexts": [{
                "action": "一人提出再次排练这一段的要求。", "evidence_refs": ["speech_1"]}]},
            audit=ModelError("model_refused", "拒绝"),
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertEqual([], result["summary_sentences"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_projected_context_and_later_change_keep_all_references_within_three_sentences(self):
        self.evidence.text = "[speech_1] 请再排练一次，明天还在原来的地点集合。"
        self.evidence.audio_clips = [{"start": 50, "end": 70, "text": "请再排练一次，明天还在原来的地点集合。"}]
        contexts = [
            {"action": "一人提出再次排练这一段的要求。", "evidence_refs": ["speech_1"]},
            {"action": "一人说明次日仍在原来的地点集合。", "evidence_refs": ["speech_1"]}]
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            *({"importance": "context", "phase": "activity", **row} for row in contexts),
            {"importance": "progression", "phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_3"]}]}
        # The meanings come from the screening stage; the ledger restates them.
        result = await self.analyze(ledger=ledger, sentences=[], summary_only=True, dialogue={"reason": "specific_context", "contexts": contexts},
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(3, len(result["summary_sentences"]))
        for event in result["narrative"]["events"]:
            self.assertIn(event["action"].rstrip("。"), result["summary"])
            self.assertTrue(any(event["id"] in sentence["event_refs"]
                                and set(event["evidence_refs"]) <= set(sentence["evidence_refs"])
                                for sentence in result["summary_sentences"]))
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_topic_only_speech_or_a_label_premise_cannot_route_to_story(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.text = "[speech_1] 零碎闲聊。"
        self.evidence.audio_clips = [{"start": 50, "end": 70, "text": "零碎闲聊。"}]
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]}]}
        for route, reason in (
                ({"mode": "story", "basis": "goal_or_problem", "evidence_refs": ["speech_1", "frame_1"],
                  "premise": {"action": "一人提出想了解对方以往的经历", "evidence_refs": ["speech_1"]}}, "topics_only"),
                ({"mode": "story", "basis": "goal_or_problem", "evidence_refs": ["frame_1", "frame_3"],
                  "premise": {"action": "女子面对按摩的处境", "evidence_refs": ["frame_1"]}}, "label_premise")):
            with self.subTest(reason=reason):
                self.calls.clear()
                result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, route=route,
                                            dialogue={"reason": "topics", "contexts": [], "topics": ["以往经历"]},
                                            sentences=[{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}])
                self.assertEqual({"from": "story", "reason": reason}, result["diagnostics"]["route_override"])
                self.assertEqual("activity", result["diagnostics"]["narrative_route"]["mode"])
                self.assertEqual("activity", result["narrative"]["mode"])
                request = next(evidence for prompt, evidence in self.calls if prompt.startswith("阶段三"))
                self.assertEqual(["activity"], request.output_schema["properties"]["mode"]["enum"])

    async def test_dominantly_intimate_readings_keep_an_adult_video_on_the_activity_path(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800]}
        self.evidence.text = "[speech_1] 家里有事要早点回去。"
        self.evidence.audio_clips = [{"start": 50, "end": 70, "text": "家里有事要早点回去。"}]
        codes = ["intimate_activity", "intimate_activity", "massage", "massage", "intimate_activity", "intimate_activity", "massage"]
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        route = {"mode": "story", "basis": "goal_or_problem", "evidence_refs": ["speech_1", "frame_3"],
                 "premise": {"action": "一人说明家里有事需要早些回去", "evidence_refs": ["speech_1"]}}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_3"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, route=route, primary=primary,
                                    dialogue={"reason": "specific_context", "contexts": [{"action": "一人说明家里有事需要早些回去", "evidence_refs": ["speech_1"]}]},
                                    sentences=[{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}])
        self.assertEqual({"from": "story", "reason": "dominant_activity"}, result["diagnostics"]["route_override"])
        self.assertEqual("activity", result["narrative"]["mode"])
        # Frames are read before routing, once, and feed the activity ledger.
        self.assertEqual(7, sum(p.startswith("主活动观察") for p, _ in self.calls))
        self.assertEqual(["massage"], result["diagnostics"]["activity_phases"]["merged_elements"])
        self.assertIn("家里有事", result["summary"])

    async def test_model_speech_meanings_beyond_the_screening_are_dropped(self):
        self.evidence.text = "[speech_1] 现场闲聊。"
        self.evidence.audio_clips = [{"start": 50, "end": 70, "text": "现场闲聊。"}]
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            {"importance": "content", "phase": "activity", "action": "参与者讨论亲密体验", "evidence_refs": ["speech_1"]},
            {"importance": "context", "phase": "activity", "action": "参与者讨论亲密相关话题", "evidence_refs": ["speech_1"]},
            {"importance": "content", "phase": "activity", "action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_2"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True,
            sentences=[{"text": "参与者进行成人亲密互动，一人为另一人示范器械的使用步骤。", "event_refs": ["event_1", "event_2"]}],
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(2, result["diagnostics"]["model_speech_events_dropped"])
        self.assertEqual(["参与者进行成人亲密互动", "一人为另一人示范器械的使用步骤"], [event["action"] for event in result["narrative"]["events"]])
        self.assertNotIn("亲密体验", result["summary"])

    async def test_dialogue_context_does_not_rescue_a_missing_main_ledger(self):
        result = await self.analyze(ledger={"mode": "activity", "events": []}, summary_only=True,
            dialogue={"reason": "specific_context", "contexts": [{
                "action": "一人明确提出请工作人员补票的请求。", "evidence_refs": ["speech_1"]}]},
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("narrative_events_missing", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    def test_dialogue_output_schema_requires_chinese_not_just_long_text(self):
        from jsonschema import Draft202012Validator
        from module.resource_analysis.dialogue import dialogue_request
        schema = dialogue_request(self.evidence).output_schema
        raw = {"reason": "specific_context", "contexts": [
            {"action": "A participant asks for another rehearsal.", "evidence_refs": ["speech_1"]}]}
        self.assertFalse(Draft202012Validator(schema).is_valid(raw))
        raw["contexts"][0]["action"] = "一人提出再次排练的要求。"
        self.assertTrue(Draft202012Validator(schema).is_valid(raw))

    async def test_independent_dialogue_rejects_unknown_sources_sensitive_claims_and_labels(self):
        for action, refs, issue in (
            ("一人要求再次排练这一段内容。", ["frame_1"], "dialogue_context_invalid"),
            ("一人显然表现出自愿配合的心理。", ["speech_1"], "non_neutral_event"),
            ("参与者进行成人亲密互动", ["speech_1"], "dialogue_context_generic"),
        ):
            with self.subTest(issue=issue):
                self.calls.clear()
                result = await self.analyze(ledger={"mode": "activity", "events": []}, summary_only=True,
                    dialogue={"reason": "specific_context", "contexts": [{"action": action, "evidence_refs": refs}]},
                    previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
                self.assertIn(issue, result["summary_quality"]["issues"])
                self.assertEqual([], result["summary_sentences"])
                self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_specific_neutral_dialogue_context_survives_into_synopsis(self):
        self.evidence.text = "[speech_1] 这是排练，请按约定节奏配合。[speech_2] 好的，再练一遍。"
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "context", "phase": "activity", "action": "一人说明这是排练，并要求对方按约定节奏配合。", "evidence_refs": ["speech_1"]}]}
        sentences = [
            {"text": "一人说明这是排练，并要求对方按约定节奏配合。", "event_refs": ["event_1"]},
            {"text": "片段包含参与者之间的成人亲密互动。", "event_refs": ["event_2"]}]
        result = await self.analyze(ledger=ledger, sentences=sentences, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertIn("按约定节奏配合", result["summary"])
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])
        self.assertEqual("activity_projection", result["diagnostics"]["summary_revision"]["strategy"])
        request = next(evidence for prompt, evidence in self.calls if prompt.startswith("阶段三"))
        from jsonschema import Draft202012Validator
        self.assertTrue(Draft202012Validator(request.output_schema).is_valid(ledger))
        ledger["events"][1]["evidence_refs"] = ["frame_1"]
        self.assertFalse(Draft202012Validator(request.output_schema).is_valid(ledger))
        self.calls.clear()
        bad = await self.analyze(ledger=ledger, sentences=sentences, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", bad["summary_quality"]["status"])
        self.assertIn("unsupported_dialogue_context", bad["summary_quality"]["issues"])

    async def test_one_static_frame_cannot_prove_an_activity_stopped_even_if_audit_would_pass(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "参与者停止亲密互动", "evidence_refs": ["frame_3"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("transition_evidence_incomplete", result["summary_quality"]["issues"])
        self.assertNotIn("停止亲密互动", result["summary"])
        self.assertEqual([], result["narrative"]["events"])

    async def test_specific_middle_dialogue_cannot_disappear_behind_first_and_last_events(self):
        self.evidence.text = "[speech_1] 请把这一段再排练一遍，注意约定的节奏。"
        self.evidence.audio_clips = [{"start": 50, "end": 70}]
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            # Multi-sentence context deliberately exercises the ordinary writer,
            # while compact contexts are now preserved by direct projection.
            {"importance": "context", "phase": "activity", "action": "一人要求再排练一遍。还提醒对方注意约定的节奏。", "evidence_refs": ["speech_1"]},
            {"importance": "progression", "phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_3"]}]}
        sentences = [
            {"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]},
            {"text": "此前的参与者不再出现在画面中。", "event_refs": ["event_3"]}]
        result = await self.analyze(ledger=ledger, sentences=sentences, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("summary_drops_context", result["summary_quality"]["issues"])
        self.assertNotIn("summary_drops_progression", result["summary_quality"]["issues"])
        self.assertEqual(2, sum(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertFalse(any(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_dialogue_context_requires_actual_speech_and_cannot_bypass_neutrality(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "context", "phase": "activity", "action": "一人表现出自愿配合的心理。", "evidence_refs": ["speech_1"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous)
        self.assertIn("non_neutral_event", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.evidence.audio_clips, self.evidence.text = [], ""
        self.calls.clear()
        ledger["events"][1]["action"] = "一人提出再排练一遍的要求。"
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous)
        self.assertIn("invalid_event_evidence", result["summary_quality"]["issues"])
        from jsonschema import Draft202012Validator
        request = next(evidence for prompt, evidence in self.calls if prompt.startswith("阶段三"))
        self.assertFalse(Draft202012Validator(request.output_schema).is_valid(ledger))
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_dialogue_reference_cannot_cover_flattening_specific_meaning_to_chat(self):
        self.evidence.text = "[speech_1] 请再排练一次，注意约定的节奏。"
        self.evidence.audio_clips = [{"start": 50, "end": 70}]
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "context", "phase": "activity", "action": "一人要求再排练一次。还提醒对方注意约定的节奏。", "evidence_refs": ["speech_1"]}]}
        sentences = [
            {"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]},
            {"text": "参与者进行交谈。", "event_refs": ["event_2"]}]
        result = await self.analyze(ledger=ledger, sentences=sentences, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("summary_drops_context", result["summary_quality"]["issues"])
        self.assertEqual([], result["summary_sentences"])

    async def test_transition_needs_distinct_times_or_reliable_speech_and_still_needs_audit(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        for refs, timestamps, expected in (
            (["frame_3", "frame_3"], [2,60,118], "review"),
            (["frame_2", "frame_3"], [2,118,118], "review"),
            (["frame_2", "frame_3"], [2,60,118], "ready"),
            (["speech_2"], [2,60,118], "ready"),
        ):
            self.calls.clear()
            self.evidence.sampling["timestamps"] = timestamps
            ledger = {"mode": "activity", "events": [
                {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1"]},
                {"phase": "activity", "action": "参与者停止亲密互动", "evidence_refs": refs}]}
            result = await self.analyze(ledger=ledger, summary_only=True, previous=previous)
            self.assertEqual(expected, result["summary_quality"]["status"])
            self.assertEqual(1 if expected == "ready" else 0, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_projected_activity_keeps_all_four_changes_and_their_sources(self):
        self.evidence.images = ["fixture-1", "fixture-2", "fixture-3", "fixture-4"]
        self.evidence.sampling = {"duration_seconds": 120, "timestamps": [2,40,80,118]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1"]},
            {"phase": "activity", "action": "其他参与者加入活动", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "参与者停止按摩", "evidence_refs": ["frame_2", "frame_3"]},
            {"phase": "activity", "action": "参与者离开画面", "evidence_refs": ["frame_3", "frame_4"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("参与者进行按摩。其他参与者加入活动。参与者停止按摩，参与者离开画面。", result["summary"])
        self.assertEqual(["event_3", "event_4"], result["summary_sentences"][-1]["event_refs"])
        self.assertEqual(["frame_2", "frame_3", "frame_4"], result["summary_sentences"][-1]["evidence_refs"])
        self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_removing_disproved_extra_event_cannot_promote_a_remaining_activity_label(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "参与者停止按摩", "evidence_refs": ["frame_2", "frame_3"]}]}
        rejected = {"mode": "activity", "coverage_ok": False, "coverage_reason": "unsupported_ending",
                    "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True, "reason": "none"},
                               {"id": "event_2", "supported": False, "reason": "unsupported_ending"}],
                    "sentences": [{"index": 1, "supported": True, "reason": "none"},
                                  {"index": 2, "supported": False, "reason": "unsupported_ending"}]}
        approved = {**rejected, "coverage_ok": True, "coverage_reason": "none",
                    "events": [{"id": "event_1", "supported": True, "reason": "none"}],
                    "sentences": [{"index": 1, "supported": True, "reason": "none"}]}
        result = await self.analyze(ledger=ledger, summary_only=True, audit=[rejected, approved],
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertEqual("参与者进行按摩", result["narrative"]["events"][0]["action"])
        self.assertEqual(["event_2"], result["diagnostics"]["summary_revision"]["removed_event_ids"])
        self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_independent_activity_observations_define_the_main_line_not_the_model_ledger(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        primary = {"observations": [{"frame_ref": f"frame_{i}", "activity": "intimate_activity"} for i in (1, 2, 3)]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertNotIn("primary_activity_mismatch", result["summary_quality"]["issues"])
        self.assertEqual(["参与者进行成人亲密互动"], [event["action"] for event in result["narrative"]["events"]])
        self.assertEqual(["frame_1", "frame_2", "frame_3"], result["narrative"]["events"][0]["evidence_refs"])
        self.assertEqual({"phases": [{"code": "intimate_activity", "frames": 3}], "interleaved": False, "issues": [], "model_events_replaced": 1,
                          "merged_elements": [], "phases_before_merge": [{"code": "intimate_activity", "frames": 3}]},
                         result["diagnostics"]["activity_phases"])
        self.assertFalse(any("待核验事件候选：" in prompt for prompt, _ in self.calls))
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_phase_boundaries_come_from_coded_frames_and_keep_model_content(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800]}
        codes = ["massage", "massage", "intimate_activity", "massage", "intimate_activity", "intimate_activity", "intimate_activity"]
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_5"]},
            {"importance": "progression", "phase": "activity", "action": "参与者停止亲密互动", "evidence_refs": ["frame_9"]},
            {"importance": "content", "phase": "activity", "action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_2"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        events = result["narrative"]["events"]
        # Seven spread readings land on frames 2,3,4,5,7,8,9 of the nine sampled frames.
        self.assertEqual(["一人为另一人示范器械的使用步骤", "参与者进行按摩", "参与者开始亲密互动", "参与者进行成人亲密互动"],
                         [event["action"] for event in events])
        # The lone intimate reading at frame_4 sits between two massage runs, so
        # it is noise and its neighbours merge; the boundary cites the last frame
        # of one phase and the first of the next.
        self.assertEqual(["frame_2", "frame_3", "frame_5"], events[1]["evidence_refs"])
        self.assertEqual(["frame_5", "frame_7"], events[2]["evidence_refs"])
        self.assertEqual(["frame_7", "frame_8", "frame_9"], events[3]["evidence_refs"])
        self.assertEqual([{"code": "massage", "frames": 3}, {"code": "intimate_activity", "frames": 3}],
                         result["diagnostics"]["activity_phases"]["phases"])
        # Minute anchors come from the coded runs; content gets its own anchor.
        self.assertEqual("开头为按摩，约10分钟起转为成人亲密互动。约2分钟时一人为另一人示范器械的使用步骤。", result["summary"])
        self.assertEqual([["event_2", "event_3", "event_4"], ["event_1"]],
                         [row["event_refs"] for row in result["summary_sentences"]])
        self.assertNotIn("停止亲密互动", result["summary"])
        self.assertTrue(any(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_edge_phases_survive_and_alternating_codes_render_as_one_mixed_activity(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800]}
        codes = ["conversation", "intimate_activity", "intimate_activity", "conversation", "conversation", "intimate_activity", "intimate_activity"]
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_5"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行交谈。", "event_refs": ["event_1"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual([{"code": "conversation", "frames": 1}, {"code": "intimate_activity", "frames": 2},
                          {"code": "conversation", "frames": 2}, {"code": "intimate_activity", "frames": 2}],
                         result["diagnostics"]["activity_phases"]["phases"])
        # Four alternating runs are not a reproducible order; the outline names
        # the dominant reading and what it interleaves with, with no boundaries.
        # 4 intimate readings against 3 conversation is a near-tie: no dominance claim.
        self.assertEqual("交谈与成人亲密互动交替进行。", result["summary"])
        self.assertEqual(1, len(result["summary_sentences"]))
        events = result["narrative"]["events"]
        # Ledger order is by time; the outline ranks by how often each was read.
        self.assertEqual(["参与者进行交谈", "参与者进行成人亲密互动"], [event["action"] for event in events])
        self.assertEqual(["frame_2", "frame_5", "frame_7"], events[0]["evidence_refs"])
        self.assertEqual(["frame_3", "frame_4", "frame_8", "frame_9"], events[1]["evidence_refs"])
        self.assertTrue(result["diagnostics"]["activity_phases"]["interleaved"])

    async def test_speech_presence_becomes_a_content_sentence_only_the_server_can_assert(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800],
                                  "audio": {"language": "zh", "windows": [
                                      {"start": 0, "end": 60, "status": "unreliable", "accepted_characters": 40, "speech_seconds": 12},
                                      {"start": 300, "end": 360, "status": "unreliable", "accepted_characters": 50, "speech_seconds": 15},
                                      {"start": 600, "end": 660, "status": "unreliable", "accepted_characters": 30, "speech_seconds": 9}]}}
        codes = ["massage", "intimate_activity", "intimate_activity", "massage", "massage", "intimate_activity", "intimate_activity"]
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_5"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual([], result["summary_quality"]["issues"])
        self.assertEqual("所采样画面均为成人亲密互动，其间有按摩环节，片中有中文对话。", result["summary"])
        self.assertEqual(1, len(result["summary_sentences"]))
        self.assertEqual({"language": "zh", "windows": 3, "speech_seconds": 36, "first": 0, "last": 600, "issues": []},
                         result["diagnostics"]["speech_presence"])
        presence = next(event for event in result["narrative"]["events"] if event["action"] == "片中有中文对话")
        self.assertEqual(["speech_presence"], presence["evidence_refs"])
        audit_prompt = next(p for p, _ in self.calls if p.startswith("阶段五"))
        self.assertIn('"speech_presence"', audit_prompt)
        # A model ledger cannot assert it on frame evidence.
        self.calls.clear()
        forged = {"version": 2, "mode": "activity", "events": [
            *ledger["events"], {"importance": "content", "phase": "activity", "action": "片中有中文对话", "evidence_refs": ["frame_3"]}]}
        result = await self.analyze(ledger=forged, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertIn("unsupported_audio", result["summary_quality"]["issues"])

    async def test_alternating_massage_and_intimate_readings_are_one_adult_activity(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800]}
        codes = ["massage", "intimate_activity", "intimate_activity", "massage", "massage", "intimate_activity", "intimate_activity"]
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_5"]},
            {"importance": "content", "phase": "activity", "action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_2"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual([], result["summary_quality"]["issues"])
        phases = result["diagnostics"]["activity_phases"]
        self.assertEqual(["massage"], phases["merged_elements"])
        self.assertEqual([{"code": "intimate_activity", "frames": 7}], phases["phases"])
        self.assertEqual("所采样画面均为成人亲密互动，其间有按摩环节。约2分钟时一人为另一人示范器械的使用步骤。", result["summary"])
        events = result["narrative"]["events"]
        # Ledger order is by evidence time; both start at frame_2.
        self.assertEqual({"参与者进行成人亲密互动", "一人为另一人示范器械的使用步骤"}, {event["action"] for event in events})
        core = next(event for event in events if event["importance"] == "core")
        self.assertEqual(7, len(core["evidence_refs"]))
        # Without a content sentence the merged outline is still only a label.
        self.calls.clear()
        result = await self.analyze(ledger={"version": 2, "mode": "activity", "events": ledger["events"][:1]}, summary_only=True,
                                    previous=previous, primary=primary, sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])

    async def test_audit_rejected_absence_claim_is_dropped_instead_of_blocking_the_synopsis(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2, 570, 599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ending = {"observations": [{"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"},
                                   {"frame_ref": "frame_2", "observation": "画面中没有可辨认的参与者。", "presence": "not_visible"}]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_2"]},
            {"importance": "content", "phase": "activity", "action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_2"]}]}
        audits = [
            {"mode": "activity", "coverage_ok": False, "coverage_reason": "unsupported_ending", "readable": True, "non_redundant": True,
             "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": True}, {"id": "event_3", "supported": False, "reason": "unsupported_ending"}],
             "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": True}, {"index": 3, "supported": False, "reason": "unsupported_ending"}]},
            {"mode": "activity", "coverage_ok": True, "coverage_reason": "none", "readable": True, "non_redundant": True,
             "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": True}],
             "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": True}]}]
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, ending=ending, final_ending="not_visible",
                                    audit=audits, sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertNotIn("不再出现", result["summary"])
        self.assertIn("示范器械", result["summary"])
        self.assertEqual(["event_3"], result["diagnostics"]["summary_revision"]["removed_event_ids"])
        self.assertEqual(2, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_deep_last_frame_visibility_is_read_three_times_and_voted(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2, 300, 599], "analysis_depth": "deep",
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ending = {"observations": [{"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"}]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_2"]}]}
        result = await self.analyze(analysis_depth="deep", ledger=ledger, summary_only=True, previous=previous, ending=ending,
                                    final_ending=[{"presence": "not_visible"}, {"presence": "visible"}, {"presence": "not_visible"}],
                                    segment={"events": []}, sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual(3, sum(prompt.startswith("末帧独立观察") for prompt, _ in self.calls))
        presence = result["diagnostics"]["ending_observation"]["presence"]
        self.assertEqual("not_visible", presence[-1]["presence"])
        self.assertEqual(["not_visible", "visible", "not_visible"], result["diagnostics"]["ending_observation"]["presence_reads"])

    async def test_activity_reconciliation_is_bounded_and_still_requires_audit(self):
        # Coded readings without a known activity leave the model ledger in
        # charge; a conflict can then only arise from a lone-code sample.
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        primary = {"observations": [{"frame_ref": f"frame_{i}", "activity": "performance"} for i in (1, 2, 3)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1"]}]}
        result = await self.analyze(ledger=ledger, primary=primary, previous=previous, summary_only=True)
        self.assertEqual(0, sum('待核验事件候选：' in p for p, _ in self.calls))
        self.assertEqual(["参与者表演舞蹈"], [event["action"] for event in result["narrative"]["events"]])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        with mock.patch("module.resource_analysis.service.activity_phase_ledger", return_value=None):
            fixed = {"version": 2, "mode": "activity", "events": [
                {"importance": "core", "phase": "activity", "action": "参与者表演舞蹈", "evidence_refs": ["frame_1", "frame_3"]},
                {"importance": "content", "phase": "activity", "action": "两人轮流示范配合的节奏", "evidence_refs": ["frame_2"]}]}
            for response, failure in ((fixed, None), (ModelError('model_refused', '拒绝'), 'model_refused'),
                                      ({'events': []}, 'activity_reconciliation_invalid')):
                with self.subTest(failure=failure):
                    self.calls.clear()
                    result = await self.analyze(ledger=ledger, primary=primary, reconciled=response,
                        previous=previous, summary_only=True)
                    self.assertEqual(1, sum('待核验事件候选：' in p for p, _ in self.calls))
                    if failure:
                        self.assertIn(failure, result['summary_quality']['issues'])
                        self.assertFalse(any(p.startswith('阶段五') for p, _ in self.calls))
                    else:
                        self.assertEqual('ready', result['summary_quality']['status'])
                        self.assertTrue(any(p.startswith('阶段五') for p, _ in self.calls))
            self.calls.clear()
            rejected = await self.analyze(ledger=ledger, primary=primary, reconciled=fixed,
                previous=previous, summary_only=True, audit=ModelError('model_refused', '拒绝'))
            self.assertIn('model_refused', rejected['summary_quality']['issues'])
            self.assertEqual([], rejected['summary_sentences'])

    async def test_reworded_pose_change_does_not_count_as_specific_content(self):
        for action in ('一人改变姿势继续表演', '一人变换了位置继续表演'):
            with self.subTest(action=action):
                ledger = {'version': 2, 'mode': 'activity', 'events': [
                    {'importance': 'core', 'phase': 'activity', 'action': '参与者表演舞蹈', 'evidence_refs': ['frame_1']},
                    {'importance': 'content', 'phase': 'activity', 'action': action, 'evidence_refs': ['frame_2', 'frame_3']}]}
                result = await self.analyze(ledger=ledger, summary_only=True,
                    previous={'status': 'complete', 'resource_kind': 'video', 'category': 'adult', 'subtype': 'independent'})
                self.assertEqual('review', result['summary_quality']['status'])
                self.assertIn('category_only_summary', result['summary_quality']['issues'])
                self.assertEqual([], result['summary_sentences'])

    async def test_incidental_pose_is_not_a_mandatory_content_event(self):
        ledger = {'version': 2, 'mode': 'activity', 'events': [
            {'importance': 'core', 'phase': 'activity', 'action': '参与者表演舞蹈', 'evidence_refs': ['frame_1']},
            {'importance': 'content', 'phase': 'activity', 'action': '一人改变姿势继续表演', 'evidence_refs': ['frame_2']},
            {'importance': 'content', 'phase': 'activity', 'action': '两人轮流演示道具的使用', 'evidence_refs': ['frame_3']}]}
        result = await self.analyze(ledger=ledger, summary_only=True,
            previous={'status': 'complete', 'resource_kind': 'video', 'category': 'adult', 'subtype': 'independent'})
        self.assertEqual('ready', result['summary_quality']['status'])
        self.assertNotIn('改变姿势', result['summary'])
        self.assertIn('道具', result['summary'])
        self.assertEqual(2, len(result['narrative']['events']))
        self.assertTrue(any(p.startswith('阶段五') for p, _ in self.calls))

    async def test_primary_check_does_not_turn_observed_massage_into_intimacy_by_category(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        primary = {"observations": [{"frame_ref": f"frame_{i}", "activity": "massage"} for i in (1, 2, 3)]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertEqual("参与者进行按摩", result["narrative"]["events"][0]["action"])
        self.assertNotIn("primary_activity_mismatch", result["summary_quality"]["issues"])

    async def test_primary_activity_refusal_is_not_bypassed_with_full_sequence_reading(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        result = await self.analyze(ledger={"mode": "activity", "events": []}, summary_only=True, previous=previous,
                                    primary=ModelError("model_refused", "拒绝"))
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_primary_observation_refs_are_checked_and_untrusted_values_not_persisted(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        for primary in (
            {"observations": [{"frame_ref": f"frame_{i}", "activity": "PRIVATE_REPLY"} for i in (1, 2, 3)]},
        ):
            self.calls.clear()
            result = await self.analyze(ledger={"mode": "activity", "events": []}, summary_only=True, previous=previous, primary=primary)
            self.assertIn("primary_activity_invalid", result["summary_quality"]["issues"])
            self.assertNotIn("PRIVATE_REPLY", str(result))
            self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_deep_mode_reads_forty_frames_once_and_smooths_them_by_neighbours(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(40)]
        # Forty frames placed exactly on the forty spread fractions of 1000 s.
        self.evidence.sampling = {"duration_seconds": 1000, "timestamps": [round((i + 1) * 1000 / 41, 3) for i in range(40)],
                                  "timeline_windows": [{"start": 920, "end": 1000}]}
        codes = ["massage"] * 10 + ["intimate_activity"] * 16 + ["massage"] * 6 + ["intimate_activity"] * 8
        codes[18], codes[35] = "massage", "unclear"  # one flipped read, one dark frame
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        ending = {"observations": [{"frame_ref": f"frame_{i}", "observation": "参与者仍在画面中。", "presence": "visible"} for i in (1, 2, 3)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_12"]},
            {"importance": "content", "phase": "activity", "action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_12"]}]}
        result = await self.analyze(analysis_depth="deep", ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    ending=ending, final_ending="visible", segment={"events": []},
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual([], result["summary_quality"]["issues"])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(120, sum(p.startswith("主活动观察") for p, _ in self.calls))
        diagnostic = result["diagnostics"]["primary_activity"]
        self.assertEqual((3, 2), (diagnostic["reads_per_frame"], diagnostic["smoothing_window"]))
        flipped = next(row for row in diagnostic["observations"] if row["frame_ref"] == "frame_19")
        self.assertEqual(("massage", "intimate_activity"), (flipped["raw"], flipped["activity"]))
        self.assertEqual(["massage"] * 3, flipped["reads"])
        # Four of the five neighbouring frames read intimate_activity.
        self.assertEqual(0.8, flipped["agreement"])
        phases = result["diagnostics"]["activity_phases"]
        self.assertEqual([{"code": "massage", "frames": 10}, {"code": "intimate_activity", "frames": 16},
                          {"code": "massage", "frames": 6}, {"code": "intimate_activity", "frames": 8}], phases["phases_before_merge"])
        # Alternating massage/intimate readings are one adult activity, not phases.
        self.assertEqual([{"code": "intimate_activity", "frames": 40}], phases["phases"])
        self.assertEqual(["massage"], phases["merged_elements"])
        self.assertEqual("所采样画面均为成人亲密互动，其间有按摩环节。约5分钟时一人为另一人示范器械的使用步骤。", result["summary"])
        self.assertNotIn("不再出现", result["summary"])

    async def test_a_few_unreadable_frame_reads_count_as_unclear_but_many_fail_the_stage(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800]}
        codes = ["intimate_activity"] * 7
        broken = {"raw": {"observations": [{"frame_ref": "frame_9", "activity": "intimate_activity"}]}}  # wrong frame id
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        primary["observations"][2] = broken
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_3"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}])
        self.assertNotIn("primary_activity_invalid", result["summary_quality"]["issues"])
        self.assertEqual({"count": 1, "reasons": ["primary_activity_invalid"]}, result["diagnostics"]["primary_activity_unreadable"])
        observations = result["diagnostics"]["primary_activity"]["observations"]
        self.assertEqual(["intimate_activity"] * 2 + ["unclear"] + ["intimate_activity"] * 4, [row["activity"] for row in observations])
        # Three unreadable reads out of seven is too many.
        for position in (0, 1):
            primary["observations"][position] = broken
        self.calls.clear()
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}])
        self.assertIn("primary_activity_invalid", result["summary_quality"]["issues"])

    async def test_spread_primary_samples_preserve_global_timeline_reference_ids(self):
        self.evidence.images = [f"synthetic-{i}" for i in range(15)]
        self.evidence.sampling = {"duration_seconds": 1000, "timestamps": [1,30,60,180,200,220,400,500,600,780,800,820,970,980,999]}
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        primary = {"observations": [{"frame_ref": f"frame_{i}", "activity": "intimate_activity"} for i in range(1, 8)]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_5", "frame_8", "frame_11"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        requests = [evidence for prompt, evidence in self.calls if prompt.startswith("主活动观察")]
        # Nearest frames to 12.5%..87.5% of 1000 s: 180, 220, 400, 500, 600, 780, 820; one frame per request.
        self.assertTrue(all(len(request.images) == 1 for request in requests))
        self.assertEqual([f"synthetic-{i}" for i in (3, 5, 6, 7, 8, 9, 11)], [request.images[0] for request in requests])
        notes = result["diagnostics"]["primary_activity"]["observations"]
        self.assertEqual([f"frame_{i}" for i in (4, 6, 7, 8, 9, 10, 12)], [row["frame_ref"] for row in notes])
        self.assertEqual([180, 220, 400, 500, 600, 780, 820], [row["timestamp"] for row in notes])

    async def test_observed_late_absence_cannot_disappear_from_activity_overview(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        ending = {"observations": [
            {"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"},
            {"frame_ref": "frame_2", "observation": "画面中没有可辨认的参与者。", "presence": "not_visible"}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]}]}
        for keep_change in (False, True):
            self.calls.clear()
            selected = copy.deepcopy(ledger)
            sentences = [{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}]
            if keep_change:
                selected["events"].append({"phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_3"]})
                sentences.append({"text": "此前的参与者不再出现在画面中。", "event_refs": ["event_2"]})
            result = await self.analyze(ledger=selected, summary_only=True, previous=previous, ending=ending, sentences=sentences)
            self.assertEqual("review", result["summary_quality"]["status"])
            self.assertIn("category_only_summary", result["summary_quality"]["issues"])
            self.assertEqual(2, len(result["narrative"]["events"]))
            self.assertEqual("此前的参与者不再出现在画面中", result["narrative"]["events"][-1]["action"])
            self.assertEqual(0, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_isolated_last_frame_restores_observed_change_when_batch_reading_is_unclear(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]}]}
        primary = {"observations": [{"frame_ref": f"frame_{i}", "activity": "intimate_activity"} for i in (1,2)]}
        result = await self.analyze(ledger=ledger, final_ending="not_visible", primary=primary, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        focused = [ev for prompt, ev in self.calls if prompt.startswith("末帧独立观察")]
        self.assertEqual(1, len(focused))
        self.assertEqual(["fixture-c"], focused[0].images)
        self.assertEqual([599], focused[0].sampling["timestamps"])
        self.assertEqual(["frame_3"], result["narrative"]["events"][-1]["evidence_refs"])

    async def test_isolated_ending_refusal_cannot_be_bypassed_with_batch_observations(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        result = await self.analyze(final_ending=ModelError("model_refused", "拒绝"))
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))

    async def test_last_frame_visibility_is_separate_from_activity_description(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]}]}
        primary = {"observations": [{"frame_ref": f"frame_{i}", "activity": "intimate_activity"} for i in (1,2)]}
        result = await self.analyze(ledger=ledger, final_ending={"presence": "not_visible"}, primary=primary, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        focused = next(ev for prompt, ev in self.calls if prompt.startswith("末帧独立观察"))
        self.assertEqual(["presence"], focused.output_schema["required"])
        self.assertFalse(focused.output_schema["additionalProperties"])
        self.assertEqual(["frame_3"], result["narrative"]["events"][-1]["evidence_refs"])

    async def test_last_frame_visibility_never_turns_visible_or_unclear_into_absence(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]}]}
        for presence in ("visible", "unclear"):
            result = await self.analyze(ledger=ledger, final_ending={"presence": presence}, summary_only=True,
                previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
            self.assertEqual("review", result["summary_quality"]["status"])
            self.assertIn("category_only_summary", result["summary_quality"]["issues"])
            self.assertEqual(1, len(result["narrative"]["events"]))
            self.assertEqual("参与者进行成人亲密互动", result["narrative"]["events"][0]["action"])
            self.assertEqual(presence, result["diagnostics"]["ending_observation"]["presence"][-1]["presence"])

    async def test_last_frame_visibility_rejects_unknown_codes_and_unrequested_details(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        for raw in ({"presence": "unknown"}, {"presence": "not_visible", "observation": "unrequested private text"}, {"presence": []}):
            self.calls.clear()
            result = await self.analyze(final_ending=raw)
            self.assertEqual("review", result["summary_quality"]["status"])
            self.assertIn("ending_observation_invalid", result["summary_quality"]["issues"])
            self.assertFalse(any(prompt.startswith("阶段三") for prompt, _ in self.calls))
            self.assertNotIn("unrequested private text", str(result))

    async def test_absence_state_does_not_start_in_earlier_visible_frames(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ending = {"observations": [
            {"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"},
            {"frame_ref": "frame_2", "observation": "画面中未见参与者。", "presence": "not_visible"}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_2", "frame_3"]}]}
        result = await self.analyze(ledger=ledger, ending=ending, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        state = result["narrative"]["events"][-1]
        self.assertEqual(["frame_3"], state["evidence_refs"])
        self.assertEqual((599,599), (state["start"],state["end"]))
        self.assertEqual(["frame_1","frame_2"], result["narrative"]["events"][0]["evidence_refs"])

    async def test_contested_speech_context_is_removed_and_the_rest_re_audited(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.text = "[speech_1] 家里有事要早点回去。"
        self.evidence.audio_clips = [{"start": 50, "end": 70, "text": "家里有事要早点回去。"}]
        context = {"action": "一人说明家里有事需要早些回去", "evidence_refs": ["speech_1"]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_3"]},
            {"importance": "content", "phase": "activity", "action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_2"]},
            {"importance": "context", "phase": "activity", **context}]}
        # Ledger order is by evidence time: the speech context (50 s) is event_2.
        rejected = {"mode": "activity", "coverage_ok": False, "coverage_reason": "unsupported_ending", "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": False, "reason": "unsupported_ending"}, {"id": "event_3", "supported": True}],
                    "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": False, "reason": "unsupported_ending"}, {"index": 3, "supported": True}]}
        approved = {"mode": "activity", "coverage_ok": True, "coverage_reason": "none", "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": True}],
                    "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": True}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, audit=[rejected, approved],
                                    dialogue={"reason": "specific_context", "contexts": [context]},
                                    sentences=[{"text": "参与者进行成人亲密互动，一人为另一人示范器械的使用步骤。", "event_refs": ["event_1", "event_2"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(["event_2"], result["diagnostics"]["summary_revision"]["removed_event_ids"])
        self.assertNotIn("家里有事", result["summary"])
        self.assertIn("示范器械", result["summary"])
        self.assertEqual(2, len(result["diagnostics"]["audited_ledgers"]))
        self.assertEqual(3, len(result["diagnostics"]["audited_ledgers"][0]["events"]))
        self.assertEqual(3, len(result["diagnostics"]["audited_ledgers"][0]["sentences"]))

    async def test_contested_ending_claim_is_removed_rather_than_published_or_insisted_on(self):
        # The absence claim comes from one isolated read; the independent audit
        # rejects it. Neither reading wins: the claim is dropped, the rest of
        # the grounded synopsis is re-audited and can pass.
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ending = {"observations": [
            {"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"},
            {"frame_ref": "frame_2", "observation": "画面中没有可辨认的参与者。", "presence": "not_visible"}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_3"]}]}
        rejected = {"mode": "activity", "coverage_ok": False, "coverage_reason": "unsupported_ending", "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True, "reason": "none"}, {"id": "event_2", "supported": False, "reason": "unsupported_ending"}],
                    "sentences": [{"index": 1, "supported": True, "reason": "none"},
                                  {"index": 2, "supported": False, "reason": "unsupported_ending"}]}
        approved = {**rejected, "coverage_ok": True, "coverage_reason": "none",
                    "events": [{"id": "event_1", "supported": True, "reason": "none"}, {"id": "event_2", "supported": True, "reason": "none"}],
                    "sentences": [{"index": 1, "supported": True, "reason": "none"}, {"index": 2, "supported": True, "reason": "none"}]}
        self.add_specific_context(ledger)
        rejected["events"][1]["id"] = "event_3"
        rejected["events"].insert(1, {"id": "event_2", "supported": True, "reason": "none"})
        rejected["sentences"][1]["index"] = 3
        rejected["sentences"].insert(1, {"index": 2, "supported": True, "reason": "none"})
        result = await self.analyze(ledger=ledger, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}, ending=ending,
            sentences=[{"text": "参与者进行成人亲密互动，此前的参与者不再出现在画面中。", "event_refs": ["event_1", "event_2"]}],
            repaired=[{"text": "参与者进行成人亲密互动。", "event_refs": ["event_1"]}], audit=[rejected, approved])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertNotIn("不再出现", result["summary"])
        self.assertIn("约定的节奏", result["summary"])
        self.assertEqual(["event_3"], result["diagnostics"]["summary_revision"]["removed_event_ids"])
        self.assertEqual(2, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_absent_last_frame_cannot_be_promoted_to_stopping_the_activity(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ending = {"observations": [
            {"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"},
            {"frame_ref": "frame_2", "observation": "画面中没有可辨认的参与者。", "presence": "not_visible"}]}
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "参与者停止亲密互动", "evidence_refs": ["frame_3"]}]}
        result = await self.analyze(ledger=ledger, ending=ending, summary_only=True,
            previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertEqual(["frame_3"], result["narrative"]["events"][-1]["evidence_refs"])
        self.assertEqual(["event_2"], result["diagnostics"]["ending_event_projection"]["event_ids"])
        self.assertEqual(1, sum(prompt.startswith("阶段三") for prompt, _ in self.calls))
        self.assertEqual(0, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_ending_projection_does_not_rewrite_events_with_other_sources(self):
        self.evidence.sampling = {"duration_seconds": 600, "timestamps": [2,570,599],
                                  "timeline_windows": [{"start": 0, "end": 60}, {"start": 540, "end": 600}]}
        ending = {"observations": [
            {"frame_ref": "frame_1", "observation": "参与者仍在画面中。", "presence": "visible"},
            {"frame_ref": "frame_2", "observation": "画面中没有可辨认的参与者。", "presence": "not_visible"}]}
        for refs in (["frame_2"], ["frame_2", "frame_3"], ["frame_3", "speech_2"]):
            self.calls.clear()
            ledger = {"mode": "activity", "events": [
                {"phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1"]},
                {"phase": "activity", "action": "参与者停止亲密互动", "evidence_refs": refs}]}
            result = await self.analyze(ledger=ledger, ending=ending, summary_only=True, audit=ModelError("model_refused", "拒绝"),
                previous={"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"})
            self.assertEqual("review", result["summary_quality"]["status"])
            if refs == ["frame_2"]:
                # This different single frame cannot be relabeled as absence;
                # the new transition guard rejects it before any factual call.
                self.assertIn("transition_evidence_incomplete", result["summary_quality"]["issues"])
                self.assertFalse(any(prompt.startswith("阶段五") for prompt, _ in self.calls))
                continue
            self.assertIn("model_refused", result["summary_quality"]["issues"])
            import json
            prompt = next(prompt for prompt, _ in self.calls if prompt.startswith("阶段五"))
            candidate = json.loads(prompt.split("待核验数据：", 1)[1])
            original = next(event for event in candidate["narrative"]["events"] if event["action"] == "参与者停止亲密互动")
            self.assertEqual(refs, original["evidence_refs"])

    async def test_one_complete_activity_is_not_forced_to_add_padding(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "几人正在排练合奏", "evidence_refs": ["frame_1", "frame_3"]}]}
        result = await self.analyze(ledger=ledger, sentences=[{"text": "几人正在排练合奏", "event_refs": ["event_1"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("几人正在排练合奏。", result["summary"])

    async def test_compact_plot_does_not_need_scenery_to_reach_a_total_word_count(self):
        ledger = {"mode": "story", "events": [
            {"phase": "setup", "action": "旅客发现车票遗失", "evidence_refs": ["frame_1", "speech_1"]},
            {"phase": "outcome", "action": "工作人员交付补票", "evidence_refs": ["frame_3", "speech_2"]}]}
        result = await self.analyze(ledger=ledger, sentences=[
            {"text": "旅客发现车票遗失", "event_refs": ["event_1"]},
            {"text": "工作人员交付补票", "event_refs": ["event_2"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("旅客发现车票遗失。工作人员交付补票。", result["summary"])

    async def test_event_level_is_required_and_details_cannot_hide_bad_references(self):
        for importance, refs in ((None, ["frame_3"]), ("unrecognized", ["frame_3"]), ("detail", ["frame_99"])):
            ledger = {"version": 2, "mode": "activity", "events": [
                {"importance": "core", "phase": "activity", "action": "几人一起排练一段合奏。", "evidence_refs": ["frame_1"]},
                {"importance": importance, "phase": "activity", "action": "演奏者调整坐姿和手臂位置。", "evidence_refs": refs},
            ]}
            result = await self.analyze(ledger=ledger)
            self.assertEqual("review", result["summary_quality"]["status"])
            self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
            self.calls.clear()

    async def test_adult_activity_pose_inventory_is_rewritten_even_when_audit_would_pass(self):
        previous = {
            "status": "complete", "resource_kind": "video", "category": "adult",
            "subtype": "other_adult", "category_label": "成人内容", "summary": "旧简介。",
        }
        from module.resource_analysis.taxonomy import ADULT_SUBTYPES
        previous["subtype"] = next(item.id for item in ADULT_SUBTYPES if "video" in item.kinds)
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "一人靠近另一人，两人开始拥抱。", "evidence_refs": ["frame_1"]},
            {"phase": "activity", "action": "两人在拥抱中调整位置，后段仍然保持亲密接触。", "evidence_refs": ["frame_3"]},
        ]}
        inventory = [
            {"text": "一人躺下，另一人伸手靠近，随后低头、侧身、翻身，两人继续调整姿势并抬起手臂。", "event_refs": ["event_1", "event_2"]},
        ]
        focused = [{"text": "片段记录两人的亲密互动，从主动靠近发展为相互拥抱，后段仍在继续。", "event_refs": ["event_1", "event_2"]}]
        result = await self.analyze(ledger=ledger, sentences=inventory, summary_only=True, previous=previous, repaired=focused)
        self.assertEqual(focused[0]["text"], result["summary"])
        self.assertIn("action_inventory", result["diagnostics"]["summary_revision"]["initial_issues"])
        self.assertEqual(1, result["diagnostics"]["summary_revision"]["repairs"])
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])

    async def test_activity_pose_guard_does_not_reject_instructional_movement(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "教练依次示范伸手、侧身和翻身的连贯动作。", "evidence_refs": ["frame_1", "frame_3"]},
        ]}
        sentences = [{"text": "教练先示范躺下和低头，再伸手、侧身并翻身，逐步展示整套训练动作。", "event_refs": ["event_1"]}]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(0, result["diagnostics"]["summary_revision"]["repairs"])

    async def test_writer_cannot_invent_events_or_discard_the_story_outcome(self):
        for refs in (["event_99"], ["event_1"]):
            with self.subTest(refs=refs):
                sentences = [{**item, "event_refs": refs} for item in self.sentences]
                result = await self.analyze(sentences=sentences)
                self.assertEqual("review", result["summary_quality"]["status"])

    async def test_event_refusal_stops_before_writing_without_erasing_classification(self):
        result = await self.analyze(ledger=ModelError("model_refused", "模型拒绝"))
        self.assertEqual("complete", result["status"])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertEqual(1, sum(prompt.startswith("阶段三") for prompt, _ in self.calls))
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_classification_budget_is_separate_from_richer_event_evidence(self):
        self.evidence.images = [f"fixture-{index}" for index in range(12)]
        self.evidence.sampling["timestamps"] = [index * 10 for index in range(12)]
        result = await self.analyze()
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual([6, 6, 12, 0, 12], [len(evidence.images) for _, evidence in self.calls])
        self.assertNotIn(self.path.name, "".join(prompt for prompt, _ in self.calls))

    async def test_evidence_supported_development_is_not_blocked_by_transition_wording(self):
        sentences = copy.deepcopy(self.sentences)
        sentences[0]["text"] = "情节从旅客发现车票遗失发展到向工作人员求助，随后工作人员查询购票记录。"
        result = await self.analyze(sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])

    async def test_repeated_activity_observations_are_grouped_with_their_time_span(self):
        action = "一人拿起吉他拨弦，另一人敲击鼓面配合演奏。"
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": action, "evidence_refs": ["frame_1"]},
            {"phase": "activity", "action": action, "evidence_refs": ["frame_2"]},
            {"phase": "activity", "action": "两人放下乐器起身，向观众挥手致意。", "evidence_refs": ["frame_3"]},
        ]}
        sentences = [
            {"text": "片段记录两人合奏，一人拿起吉他拨弦，另一人敲击鼓面配合演奏。", "event_refs": ["event_1"]},
            {"text": "后段两人放下乐器起身，向观众挥手致意，结束这段演奏。", "event_refs": ["event_2"]},
        ]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(2, len(result["narrative"]["events"]))
        self.assertEqual(["frame_1", "frame_2"], result["narrative"]["events"][0]["evidence_refs"])
        self.assertEqual((2, 60), (result["narrative"]["events"][0]["start"], result["narrative"]["events"][0]["end"]))

    async def test_similar_wording_must_not_erase_opposite_actions(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["events"][0]["action"] = "旅客来到服务台前，工作人员起身打开旁边的门，旅客随后走入。"
        ledger["events"][1]["action"] = "旅客来到服务台前，工作人员起身关闭旁边的门，旅客随后走出。"
        self.evidence.text = "[speech_1] 请到服务台里面来。[speech_2] 补票已经取好，现在可以回站台。"
        sentences = [
            {"text": "旅客来到服务台前，工作人员打开旁边的门，旅客随后走入。", "event_refs": ["event_1"]},
            {"text": "后段工作人员关闭门，旅客走出后拿起补票离开服务台，返回站台。", "event_refs": ["event_2", "event_3"]},
        ]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(3, len(result["narrative"]["events"]))

    async def test_writer_paragraphs_are_split_without_dropping_event_references(self):
        paragraph = {"text": "".join(row["text"] for row in self.sentences),
                     "event_refs": ["event_1", "event_2", "event_3"]}
        result = await self.analyze(sentences=[paragraph])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(2, len(result["summary_sentences"]))
        self.assertTrue(all(row["event_refs"] == paragraph["event_refs"] for row in result["summary_sentences"]))

    async def test_similar_sentences_with_opposite_actions_are_not_duplicates(self):
        ledger = {"mode": "story", "events": [
            {"phase": "setup", "action": "工作人员打开服务台旁边的门，让旅客进入取回遗失的车票。", "evidence_refs": ["frame_1"]},
            {"phase": "outcome", "action": "工作人员关闭服务台旁边的门，旅客离开并拿着找回的车票。", "evidence_refs": ["frame_3"]},
        ]}
        sentences = [{"text": item["action"], "event_refs": [f"event_{index + 1}"]} for index, item in enumerate(ledger["events"])]
        result = await self.analyze(ledger=ledger, sentences=sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(2, len(result["summary_sentences"]))

    async def test_semantic_audit_rejects_invented_outcome_even_with_valid_refs(self):
        audit = {"mode": "story", "coverage_ok": True, "readable": True, "non_redundant": True,
                 "events": [{"id": f"event_{i}", "supported": True} for i in range(1, 4)],
                 "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": False}]}
        result = await self.analyze(audit=audit)
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("summary_not_grounded", result["summary_quality"]["issues"])
        self.assertFalse(result["summary_sentences"])

    async def test_semantic_audit_cannot_rename_position_changes_as_a_story(self):
        audit = {"mode": "activity", "coverage_ok": True, "readable": True, "non_redundant": True,
                 "events": [{"id": f"event_{i}", "supported": True} for i in range(1, 4)],
                 "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": True}]}
        result = await self.analyze(audit=audit)
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("narrative_mode_mismatch", result["summary_quality"]["issues"])

    async def test_one_factual_repair_removes_only_disproved_extra_event_then_reaudits(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "几人一起排练合奏。", "evidence_refs": ["frame_1", "frame_2"]},
            {"phase": "activity", "action": "几人停止排练合奏。", "evidence_refs": ["frame_2"]},
            {"phase": "activity", "action": "几人随后离开画面。", "evidence_refs": ["frame_3"]}]}
        bad_audit = {"mode": "activity", "coverage_ok": True, "readable": True, "non_redundant": True,
                     "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": False}, {"id": "event_3", "supported": True}],
                     "sentences": [{"index": 1, "supported": False}]}
        good_audit = {**bad_audit, "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": True}],
                      "sentences": [{"index": 1, "supported": True}]}
        first = [{"text": "几人一起排练合奏，中途停止排练，随后离开画面。", "event_refs": ["event_1", "event_2", "event_3"]}]
        repaired = [{"text": "几人一起排练合奏，之后不再出现在画面中。", "event_refs": ["event_1", "event_2"]}]
        result = await self.analyze(ledger=ledger, sentences=first, repaired=repaired, audit=[bad_audit, good_audit])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(repaired[0]["text"], result["summary"])
        self.assertEqual(2, len(result["narrative"]["events"]))
        self.assertEqual(2, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))
        second_writer = [prompt for prompt, _ in self.calls if prompt.startswith("阶段四")][1]
        self.assertNotIn("几人停止排练合奏", second_writer)
        self.assertEqual(["event_2"], result["diagnostics"]["summary_revision"]["removed_event_ids"])

    async def test_unsupported_core_or_incomplete_coverage_cannot_be_pruned_into_success(self):
        for unsupported, coverage in (("event_1", True), ("event_3", False)):
            audit = {"mode": "story", "coverage_ok": coverage, "readable": True, "non_redundant": True,
                     "events": [{"id": f"event_{i}", "supported": f"event_{i}" != unsupported} for i in (1, 2, 3)],
                     "sentences": [{"index": i, "supported": True} for i in (1, 2)]}
            self.calls.clear()
            result = await self.analyze(audit=audit)
            self.assertEqual("review", result["summary_quality"]["status"])
            self.assertEqual(1, sum(prompt.startswith("阶段四") for prompt, _ in self.calls))

    async def test_audit_identified_false_ending_can_be_removed_but_needs_new_coverage_approval(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "几人一起排练合奏。", "evidence_refs": ["frame_1", "frame_3"]},
            {"phase": "activity", "action": "几人停止排练合奏。", "evidence_refs": ["frame_3"]}]}
        rejected = {"mode": "activity", "coverage_ok": False, "coverage_reason": "unsupported_ending",
                    "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True, "reason": "none"},
                               {"id": "event_2", "supported": False, "reason": "unsupported_ending"}],
                    "sentences": [{"index": 1, "supported": False, "reason": "unsupported_ending"}]}
        for approved in (True, False):
            self.calls.clear()
            final_audit = {**rejected, "coverage_ok": approved, "coverage_reason": "none" if approved else "missing_progression",
                           "events": [{"id": "event_1", "supported": True, "reason": "none"}],
                           "sentences": [{"index": 1, "supported": True, "reason": "none"}]}
            result = await self.analyze(ledger=ledger,
                sentences=[{"text": "几人一起排练合奏，随后停止排练。", "event_refs": ["event_1", "event_2"]}],
                repaired=[{"text": "几人一起排练合奏。", "event_refs": ["event_1"]}], audit=[rejected, final_audit])
            self.assertEqual("ready" if approved else "review", result["summary_quality"]["status"])
            self.assertEqual(2, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_writer_only_false_ending_is_rewritten_without_changing_supported_events(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "几人一起排练合奏。", "evidence_refs": ["frame_1", "frame_3"]}]}
        rejected = {"mode": "activity", "coverage_ok": False, "coverage_reason": "unsupported_ending",
                    "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True, "reason": "none"}],
                    "sentences": [{"index": 1, "supported": False, "reason": "unsupported_ending"}]}
        approved = {**rejected, "coverage_ok": True, "coverage_reason": "none",
                    "sentences": [{"index": 1, "supported": True, "reason": "none"}]}
        result = await self.analyze(ledger=ledger,
            sentences=[{"text": "几人一起排练合奏，随后停止了排练。", "event_refs": ["event_1"]}],
            repaired=[{"text": "几人一起排练合奏。", "event_refs": ["event_1"]}], audit=[rejected, approved])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("几人一起排练合奏。", result["summary"])
        self.assertEqual(1, sum(prompt.startswith("阶段三") for prompt, _ in self.calls))
        self.assertEqual(2, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))

    async def test_semantic_audit_requires_every_event_and_sentence(self):
        result = await self.analyze(audit={"mode": "story", "coverage_ok": True, "readable": True, "non_redundant": True, "events": [], "sentences": []})
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("narrative_audit_incomplete", result["summary_quality"]["issues"])

    async def test_semantic_audit_keeps_only_bounded_reason_codes_for_rejected_candidates(self):
        audit = {"mode": "activity", "coverage_ok": False, "coverage_reason": "minor_detail_as_plot",
                 "readable": True, "non_redundant": True,
                 "events": [{"id": f"event_{i}", "supported": True, "reason": "none"} for i in (1, 2, 3)],
                 "sentences": [{"index": 1, "supported": True, "reason": "PRIVATE_REJECTED_PROSE"},
                               {"index": 2, "supported": False, "reason": "unsupported_ending"}],
                 "explanation": "PRIVATE_REJECTED_PROSE"}
        result = await self.analyze(audit=audit)
        verdict = result["summary_quality"]["audit"].get("verdict", {})
        self.assertEqual("activity", verdict.get("mode"))
        self.assertEqual("minor_detail_as_plot", verdict.get("coverage_reason"))
        self.assertEqual("unsupported_ending", verdict["sentences"][1]["reason"])
        self.assertNotIn("PRIVATE_REJECTED_PROSE", str(result))
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertFalse(result["summary_sentences"])
        request = next(evidence for prompt, evidence in self.calls if prompt.startswith("阶段五"))
        self.assertIsNotNone(request.output_schema)

    async def test_semantic_audit_refusal_is_not_retried_or_bypassed(self):
        result = await self.analyze(audit=ModelError("model_refused", "模型拒绝"))
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertEqual(1, sum(prompt.startswith("阶段五") for prompt, _ in self.calls))
        self.assertEqual(["model_refused"], result["summary_quality"]["audit"]["issues"],
                         "A missing verdict cannot establish a semantic mismatch")

    async def test_redundant_prose_cannot_pass_just_because_each_sentence_is_supported(self):
        audit = {"mode": "story", "coverage_ok": True, "readable": True, "non_redundant": False,
                 "events": [{"id": f"event_{i}", "supported": True} for i in range(1, 4)],
                 "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": True}]}
        result = await self.analyze(audit=audit)
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("summary_repetitive", result["summary_quality"]["issues"])

    async def test_a_focused_writer_repair_reuses_the_event_ledger_and_classification(self):
        bad = [{"text": "工作人员查询了记录。", "event_refs": ["event_1"]}]
        result = await self.analyze(sentences=bad, repaired=self.sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(1, sum(prompt.startswith("阶段一") for prompt, _ in self.calls))
        self.assertEqual(1, sum(prompt.startswith("阶段三") for prompt, _ in self.calls))
        self.assertEqual(2, sum(prompt.startswith("阶段四") for prompt, _ in self.calls))
        self.assertTrue(all(not evidence.images for prompt, evidence in self.calls if prompt.startswith("阶段四")))

    async def test_overbroad_full_video_wording_gets_one_grounded_rewrite(self):
        bad = [{"text": "全片记录旅客遗失车票后求助，工作人员查询记录，旅客补票后离开。", "event_refs": ["event_1", "event_2", "event_3"]}]
        result = await self.analyze(sentences=bad, repaired=self.sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertIn("unsupported_coverage", result["diagnostics"]["summary_revision"]["initial_issues"])
        self.assertEqual(1, result["diagnostics"]["summary_revision"]["repairs"])

    async def test_ignoring_last_evidence_cannot_claim_the_activity_continues_at_the_end(self):
        ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "几人配合排练合奏。", "evidence_refs": ["frame_1", "frame_2"]},
        ]}
        result = await self.analyze(ledger=ledger, sentences=[
            {"text": "几人配合排练一段合奏，后段仍在继续。", "event_refs": ["event_1"]},
        ])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("unsupported_late_continuation", result["summary_quality"]["issues"])


class NarrativeSamplingTest(unittest.TestCase):
    def test_long_video_retains_opening_dialogue_without_losing_middle_end_or_budget(self):
        opening = "旅客说自己遗失了车票，请工作人员查询购票记录。"
        def speech(path, directory, seconds, *, start, language=None):
            return {"text": opening if start <= 45 < start + seconds else "",
                    "status": "accepted", "language": "zh", "language_probability": 0.99,
                    "discarded_segments": 0}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(extraction, "_probe", return_value=1800), \
             mock.patch.object(extraction, "_run"), \
             mock.patch.object(extraction, "_frame_features", return_value=([1] * 64, True)), \
             mock.patch.object(extraction, "_image_data", side_effect=lambda path: str(path)), \
             mock.patch.object(extraction, "_detect_language", return_value=None), \
             mock.patch.object(extraction, "_speech", side_effect=speech):
            result = extraction.extract_evidence("fixture.mp4", directory, max_frames=24,
                                                 timeline=True, transcribe=True, max_audio_seconds=180)
        self.assertIn(opening, result.text)
        self.assertLessEqual(result.sampling["audio"]["sampled_seconds"], 180.01)
        self.assertEqual(24, len(result.images))
        windows = result.sampling["timeline_windows"]
        self.assertEqual(60, windows[0]["end"])
        self.assertTrue(any(600 < timestamp < 1200 for timestamp in result.sampling["timestamps"]))
        self.assertGreater(result.sampling["timestamps"][-1], 1798)
        for visual, audio in zip(windows, result.sampling["audio"]["windows"]):
            self.assertEqual((visual["start"], visual["end"]), (audio["start"], audio["end"]))

    def test_long_video_retains_closing_transition_before_the_last_fifteen_seconds(self):
        closing = "补票已经办理好了，现在可以回站台乘车。"
        def speech(path, directory, seconds, *, start, language=None):
            return {"text": closing if start <= 1770 < start + seconds else "",
                    "status": "accepted", "language": "zh", "language_probability": 0.99,
                    "discarded_segments": 0}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(extraction, "_probe", return_value=1800), \
             mock.patch.object(extraction, "_run"), \
             mock.patch.object(extraction, "_frame_features", return_value=([1] * 64, True)), \
             mock.patch.object(extraction, "_image_data", side_effect=lambda path: str(path)), \
             mock.patch.object(extraction, "_detect_language", return_value=None), \
             mock.patch.object(extraction, "_speech", side_effect=speech):
            result = extraction.extract_evidence("fixture.mp4", directory, max_frames=24,
                                                 timeline=True, transcribe=True, max_audio_seconds=180)
        self.assertIn(closing, result.text)
        self.assertTrue(any(1760 <= time <= 1780 for time in result.sampling["timestamps"]))
        self.assertEqual(180, result.sampling["audio"]["sampled_seconds"])
        self.assertEqual(24, len(result.images))

    def test_minimum_frame_budget_still_covers_the_middle(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(extraction, "_probe", return_value=1800), \
             mock.patch.object(extraction, "_run"), \
             mock.patch.object(extraction, "_frame_features", return_value=([1] * 64, True)), \
             mock.patch.object(extraction, "_image_data", side_effect=lambda path: str(path)):
            result = extraction.extract_evidence("fixture.mp4", directory, max_frames=6, timeline=True)
        times = result.sampling["timestamps"]
        self.assertEqual(6, len(times))
        self.assertTrue(any(600 <= value <= 1200 for value in times), "Minimum budget must not omit the middle third")
        self.assertLess(times[0], 2)
        self.assertGreater(times[-1], 1798)

    def test_speech_windows_align_with_events_without_increasing_the_audio_budget(self):
        def speech(path, directory, seconds, *, start, language=None):
            return {"text": f"时间段{start}正在介绍具体操作步骤和完成后的变化。", "status": "accepted",
                    "language": "zh", "language_probability": 0.99, "discarded_segments": 0}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(extraction, "_probe", return_value=1800), \
             mock.patch.object(extraction, "_run"), \
             mock.patch.object(extraction, "_frame_features", return_value=([1] * 64, True)), \
             mock.patch.object(extraction, "_image_data", side_effect=lambda path: str(path)), \
             mock.patch.object(extraction, "_detect_language", return_value=None), \
             mock.patch.object(extraction, "_speech", side_effect=speech):
            result = extraction.extract_evidence("fixture.mp4", directory, max_frames=24,
                                                 timeline=True, transcribe=True, max_audio_seconds=180)
        audio = result.sampling["audio"]
        self.assertEqual("timeline_aligned", audio["method"])
        self.assertEqual(8, audio["reliable_clips"])
        self.assertLessEqual(audio["sampled_seconds"], 180)
        self.assertEqual([item["start"] for item in result.sampling["timeline_windows"]],
                         [item["start"] for item in audio["windows"]])

    def test_timeline_samples_cover_start_end_and_local_progression_with_a_hard_cap(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(extraction, "_probe", return_value=1800), \
             mock.patch.object(extraction, "_run"), \
             mock.patch.object(extraction, "_frame_features", return_value=([1] * 64, True)), \
             mock.patch.object(extraction, "_image_data", side_effect=lambda path: str(path)):
            result = extraction.extract_evidence("fixture.mp4", directory, max_frames=24, timeline=True)
        times = result.sampling["timestamps"]
        self.assertLessEqual(len(times), 24)
        self.assertGreaterEqual(len(times), 16)
        self.assertLess(times[0], 2)
        self.assertGreater(times[-1], 1798)
        self.assertEqual(sorted(times), times)
        windows = result.sampling["timeline_windows"]
        self.assertEqual(8, len(windows))
        self.assertTrue(all(window["frame_count"] >= 2 for window in windows))
        self.assertLessEqual(sum(window["end"] - window["start"] for window in windows), 180.01)


if __name__ == "__main__":
    unittest.main()


class ContentCarryTest(unittest.IsolatedAsyncioTestCase):
    """Recurring window candidates reach the ledger as audited content."""

    setUp = VideoNarrativeTest.setUp
    analyze = VideoNarrativeTest.analyze
    add_specific_context = VideoNarrativeTest.add_specific_context

    previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
    label_only = {"version": 2, "mode": "activity", "events": [
        {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1", "frame_3"]}]}
    segment = {"events": [{"action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_1"]}]}

    def options(self, **extra):
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        return {**dict(analysis_depth="deep", summary_only=True, previous=self.previous, ledger=self.label_only,
                       segment=self.segment, sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}]), **extra}

    async def test_recurring_candidate_is_carried_as_content_and_audited(self):
        result = await self.analyze(**self.options())
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertIn("示范器械", result["summary"])
        events = result["narrative"]["events"]
        self.assertEqual(["参与者进行按摩", "一人为另一人示范器械的使用步骤"], [event["action"] for event in events])
        self.assertEqual(["frame_2"], events[1]["evidence_refs"])
        self.assertEqual({"candidates": 1, "carried": 1, "existing_content": 0, "issues": []}, result["diagnostics"]["content_carry"])
        self.assertTrue(any(p.startswith("阶段五") for p, _ in self.calls))
        self.assertFalse(any("待核验事件候选：" in p for p, _ in self.calls))
        self.assertEqual("passed", result["summary_quality"]["audit"]["status"])
        self.assertEqual([{"revision": 0, "requested": 3, "returned": 3}], result["diagnostics"]["audit_reads"])

    async def test_audit_can_still_reject_a_carried_candidate(self):
        rejected = {"mode": "activity", "coverage_ok": True, "coverage_reason": "none", "readable": True, "non_redundant": True,
                    "events": [{"id": "event_1", "supported": True}, {"id": "event_2", "supported": False, "reason": "unsupported_action"}],
                    "sentences": [{"index": 1, "supported": True}, {"index": 2, "supported": False, "reason": "unsupported_action"}]}
        result = await self.analyze(**self.options(audit=rejected))
        # The rejected carried statement is dropped and the rest re-audited;
        # what remains is only the label, which the gate still refuses.
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertEqual([], result["summary_sentences"])
        self.assertEqual(["event_2"], result["diagnostics"]["summary_revision"]["removed_event_ids"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])

    async def test_single_read_candidate_is_not_carried_and_label_only_still_fails(self):
        result = await self.analyze(**self.options(segment=[self.segment, {"events": []}, {"events": []}]))
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("category_only_summary", result["summary_quality"]["issues"])
        self.assertNotIn("content_carry", result["diagnostics"])
        self.assertFalse(any(p.startswith("阶段五") for p, _ in self.calls))

    async def test_two_simple_contents_on_a_server_outline_use_the_projection(self):
        previous = {"status": "complete", "resource_kind": "video", "category": "adult", "subtype": "independent"}
        self.evidence.images = [f"synthetic-{i}" for i in range(9)]
        self.evidence.sampling = {"duration_seconds": 900, "timestamps": [10, 100, 200, 300, 400, 500, 600, 700, 800]}
        codes = ["massage", "massage", "massage", "intimate_activity", "intimate_activity", "intimate_activity", "intimate_activity"]
        primary = {"observations": [{"frame_ref": f"frame_{i + 1}", "activity": code} for i, code in enumerate(codes)]}
        ledger = {"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_6"]},
            {"importance": "content", "phase": "activity", "action": "女方在床上靠近躺着的主体", "evidence_refs": ["frame_2"]},
            {"importance": "content", "phase": "activity", "action": "主体整理器械并递给主体", "evidence_refs": ["frame_8"]}]}
        result = await self.analyze(ledger=ledger, summary_only=True, previous=previous, primary=primary,
                                    sentences=[{"text": "参与者进行按摩。", "event_refs": ["event_1"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("activity_projection", result["diagnostics"]["summary_revision"]["strategy"])
        self.assertFalse(any(prompt.startswith("阶段四") for prompt, _ in self.calls))
        # Seven spread readings land on frames 2,3,4,6,7,8,9: the boundary is 400 s.
        self.assertEqual("开头为按摩，约7分钟起转为成人亲密互动。约2分钟时一人在床上靠近躺着的对方，约12分钟时一人整理器械并递给对方。", result["summary"])

    async def test_label_like_model_content_is_dropped_instead_of_failing_the_ledger(self):
        with_label = {"version": 2, "mode": "activity", "events": [
            *self.label_only["events"],
            {"importance": "content", "phase": "activity", "action": "两人在床上进行成人亲密互动", "evidence_refs": ["frame_2"]},
            {"importance": "content", "phase": "activity", "action": "躺卧者坐在床边", "evidence_refs": ["frame_3"]}]}
        result = await self.analyze(**self.options(ledger=with_label))
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual(2, result["diagnostics"]["model_content_dropped"])
        self.assertEqual(["参与者进行按摩", "一人为另一人示范器械的使用步骤"], [event["action"] for event in result["narrative"]["events"]])

    async def test_label_paraphrases_and_overlapping_variants_are_not_carried(self):
        self.evidence.sampling["timeline_windows"] = [{"start": 40, "end": 80}]
        label_like = {"action": "对躺卧在床上的对象进行身体按摩", "evidence_refs": ["frame_1"]}
        specific = {"action": "一人为另一人示范器械的使用步骤", "evidence_refs": ["frame_1"]}
        # A differently worded observation of the same frame is a variant, not a
        # second content statement.
        variant = {"action": "两人核对说明书上的操作步骤", "evidence_refs": ["frame_1"]}
        result = await self.analyze(**self.options(segment=[{"events": [label_like, specific]}, {"events": [specific, variant]}, {"events": [variant]}]))
        self.assertEqual([], result["summary_quality"]["issues"])
        self.assertEqual("ready", result["summary_quality"]["status"])
        window = result["diagnostics"]["segments"]["results"][0]
        self.assertEqual({"dropped_candidates": ["scene_only_event"]}, window["rejected_reads"][0])
        self.assertEqual(1, result["diagnostics"]["content_carry"]["carried"])
        contents = [event for event in result["narrative"]["events"] if event["importance"] == "content"]
        self.assertEqual(["一人为另一人示范器械的使用步骤"], [event["action"] for event in contents])
        import json
        audit_prompt = next(p for p, _ in self.calls if p.startswith("阶段五"))
        candidates = json.loads(audit_prompt.split("待核验数据：", 1)[1])["segment_candidates"]
        self.assertTrue(all("reads" not in row for row in candidates))

    async def test_candidates_the_ledger_ignored_are_carried_alongside_its_own_content(self):
        with_content = {"version": 2, "mode": "activity", "events": [
            *self.label_only["events"],
            {"importance": "content", "phase": "activity", "action": "两人分工演示设备操作", "evidence_refs": ["frame_2"]}]}
        result = await self.analyze(**self.options(ledger=with_content, sentences=[
            {"text": "参与者进行按摩，并分工演示设备操作。", "event_refs": ["event_1", "event_2"]},
            {"text": "一人为另一人示范器械的使用步骤。", "event_refs": ["event_3"]}]))
        self.assertEqual({"candidates": 1, "carried": 1, "existing_content": 1, "issues": []}, result["diagnostics"]["content_carry"])
        self.assertEqual(["参与者进行按摩", "两人分工演示设备操作", "一人为另一人示范器械的使用步骤"],
                         [event["action"] for event in result["narrative"]["events"]])
        self.assertIn("示范器械", result["summary"])
        self.assertIn("分工演示", result["summary"])
        self.calls.clear()
        paraphrased = {"events": [{"action": "两人分工演示设备的操作", "evidence_refs": ["frame_1"]}]}
        result = await self.analyze(**self.options(ledger=with_content, segment=paraphrased, sentences=[
            {"text": "参与者进行按摩，并分工演示设备操作。", "event_refs": ["event_1", "event_2"]}]))
        self.assertNotIn("content_carry", result["diagnostics"])
        self.assertEqual(2, len(result["narrative"]["events"]))

    async def test_majority_audit_combines_three_verdicts_per_item(self):
        from module.resource_analysis.summary import majority_audit
        approve = {"mode": "activity", "coverage_ok": True, "coverage_reason": "none", "readable": True, "non_redundant": True,
                   "events": [{"id": "event_1", "supported": True, "reason": "none"}, {"id": "event_2", "supported": True, "reason": "none"}],
                   "sentences": [{"index": 1, "supported": True, "reason": "none"}]}
        reject_ending = {**approve, "coverage_ok": False, "coverage_reason": "unsupported_ending",
                         "events": [{"id": "event_1", "supported": True, "reason": "none"}, {"id": "event_2", "supported": False, "reason": "unsupported_ending"}],
                         "sentences": [{"index": 1, "supported": False, "reason": "unsupported_ending"}]}
        combined = majority_audit([approve, reject_ending, approve])
        self.assertTrue(combined["coverage_ok"])
        self.assertEqual("none", combined["coverage_reason"])
        self.assertEqual([{"id": "event_1", "supported": True, "reason": "none"}, {"id": "event_2", "supported": True, "reason": "none"}], combined["events"])
        combined = majority_audit([reject_ending, reject_ending, approve])
        self.assertFalse(combined["coverage_ok"])
        self.assertEqual("unsupported_ending", combined["coverage_reason"])
        self.assertEqual({"id": "event_2", "supported": False, "reason": "unsupported_ending"}, combined["events"][1])
        self.assertFalse(combined["sentences"][0]["supported"])
        self.assertIsNone(majority_audit([None, None]))
        self.assertIs(approve, majority_audit([approve]))
        # One returned verdict among failures still decides alone; a 1-1 split is not support.
        split = majority_audit([approve, reject_ending])
        self.assertFalse(split["coverage_ok"])
        self.assertFalse(split["events"][1]["supported"])
