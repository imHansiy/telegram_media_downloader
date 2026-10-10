"""Video synopsis contract through the real analysis interface, no model/network."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from module.resource_analysis import analyze_resource
from module.resource_analysis.evidence import Evidence
from module.resource_analysis.service import ModelError, analysis_status
from module.resource_analysis.summary import build_synopsis, build_narrative, build_narrative_synopsis, category_only_summary
from module.resource_analysis.taxonomy import analysis_profile


class VideoSummaryQualityTest(unittest.IsolatedAsyncioTestCase):
    def test_story_problem_cannot_disappear_between_first_and_last_events(self):
        evidence = Evidence('video', images=['a', 'b', 'c'], sources=['video_frames'],
                            sampling={'timestamps': [1, 20, 59]})
        narrative = build_narrative({'version': 2, 'mode': 'story', 'events': [
            {'importance': 'core', 'phase': 'setup', 'action': '旅客走到服务台前。', 'evidence_refs': ['frame_1']},
            {'importance': 'core', 'phase': 'development', 'action': '旅客向工作人员出示无法使用的车票。', 'evidence_refs': ['frame_2']},
            {'importance': 'progression', 'phase': 'outcome', 'action': '工作人员将补票交给旅客。', 'evidence_refs': ['frame_3']},
        ]}, evidence)
        narrative['required_event_ids'] = ['event_2']
        candidate = build_narrative_synopsis([
            {'text': '旅客走到服务台前。', 'event_refs': ['event_1']},
            {'text': '工作人员将补票交给旅客。', 'event_refs': ['event_3']},
        ], evidence, narrative)
        self.assertEqual('review', candidate['summary_quality']['status'])
        self.assertIn('summary_drops_progression', candidate['summary_quality']['issues'])

    async def test_unintelligible_dialogue_cannot_be_reused_by_visual_narrative(self):
        await self._assert_rejected_speech_excluded('unintelligible')

    async def test_unsafe_dialogue_cannot_be_reused_by_visual_narrative(self):
        await self._assert_rejected_speech_excluded('unsafe_or_sensitive')

    async def _assert_rejected_speech_excluded(self, reason):
        from module.resource_analysis.service import _summarize_video
        evidence = Evidence('video', images=['synthetic-a', 'synthetic-b'],
            sources=['video_frames', 'transcript'], text='speech_1: UNINTELLIGIBLE_FIXTURE',
            audio_clips=[{'start': 1, 'end': 12, 'text': 'UNINTELLIGIBLE_FIXTURE'}],
            sampling={'duration_seconds': 60, 'timestamps': [1, 59]})
        original = copy.deepcopy(evidence)
        seen = []
        async def complete(prompt, request):
            if prompt.startswith('叙事模式'):
                return {'mode': 'activity', 'basis': 'activity_only', 'evidence_refs': ['frame_1']}
            if prompt.startswith('主活动观察'):
                return {'observations': [{'frame_ref': f'frame_{i+1}', 'activity': 'unclear'} for i in range(len(request.images))]}
            if prompt.startswith('对白含义'):
                return {'reason': reason, 'contexts': []}
            seen.append(copy.deepcopy(request))
            raise ModelError('model_refused', 'synthetic stop')
        result = {'category': 'adult', 'subtype': 'independent', 'diagnostics': {'attempts': []}}
        await _summarize_video(evidence, complete, result, 5, lambda _: None)
        self.assertEqual(1, len(seen))
        self.assertNotIn('transcript', seen[0].sources)
        self.assertEqual([], seen[0].audio_clips)
        self.assertNotIn('UNINTELLIGIBLE_FIXTURE', seen[0].text)
        self.assertNotIn('speech_1', json.dumps(seen[0].output_schema))
        self.assertEqual(original, evidence, 'Screening a pass must not mutate the original evidence')

    def test_location_does_not_turn_a_generic_activity_into_specific_content(self):
        evidence = Evidence('video', images=['synthetic'], sources=['video_frames'], sampling={'timestamps': [1]})
        for action in ('主体在床上进行成人亲密互动', '参与者在房间里进行按摩', '两人在沙发上交谈'):
            with self.subTest(action=action):
                ledger = build_narrative({'version': 2, 'mode': 'activity', 'events': [
                    {'importance': 'content', 'phase': 'activity', 'action': action, 'evidence_refs': ['frame_1']}
                ]}, evidence, category='adult')
                self.assertIn('scene_only_event', ledger['issues'])
                self.assertTrue(category_only_summary(action))
        self.assertFalse(category_only_summary('两人在沙发上交谈，讨论如何安排下一次排练'))
        self.assertFalse(category_only_summary('参与者在房间里演示如何固定模型零件'))
        self.assertTrue(category_only_summary('主体在床上进行成人亲密互动。参与者离开画面，此前的参与者不再出现在画面中。'))

    def test_adult_activity_does_not_promote_body_detail_to_specific_plot(self):
        evidence = Evidence('video', images=['synthetic'], sources=['video_frames'], sampling={'timestamps': [1]})
        for action in ('手部触摸身体表面', '参与者使用按摩设备处理腿部'):
            ledger = build_narrative({'version': 2, 'mode': 'activity', 'events': [
                {'importance': 'content', 'phase': 'activity', 'action': action, 'evidence_refs': ['frame_1']}
            ]}, evidence, category='adult')
            self.assertIn('scene_only_event', ledger['issues'])
        # This rule is scoped to adult activity, not medical/educational material.
        ledger = build_narrative({'version': 2, 'mode': 'activity', 'events': [
            {'importance': 'content', 'phase': 'activity', 'action': '示范使用按摩设备处理腿部', 'evidence_refs': ['frame_1']}
        ]}, evidence, category='education')
        self.assertNotIn('scene_only_event', ledger['issues'])

    def test_short_activity_labels_do_not_become_specific_events(self):
        evidence = Evidence("video", images=["synthetic"], sources=["video_frames"], sampling={"timestamps": [1]})
        for text in ("两人交谈", "参与者正在交谈", "两人进行亲密互动", "一人进行按摩", "主体参与成人亲密互动", "主体在该过程中持续参与",
                     "参与者组织按摩活动", "两人开展亲密互动", "参与者进行按摩过程"):
            with self.subTest(text=text):
                self.assertTrue(category_only_summary(text))
                ledger = build_narrative({"version": 2, "mode": "activity", "events": [
                    {"importance": "content", "phase": "activity", "action": text, "evidence_refs": ["frame_1"]}
                ]}, evidence, category="adult")
                self.assertIn("scene_only_event", ledger["issues"])
        for text in ("两人讨论演奏节奏", "一人请求放慢", "参与者正在交谈，讨论如何安排下一次排练"):
            self.assertFalse(category_only_summary(text))
        self.assertTrue(category_only_summary("参与者进行成人亲密互动，主体在该过程中持续参与。"))

    def test_primary_activity_uses_seven_spread_frames_and_a_three_reading_majority(self):
        from module.resource_analysis.summary import primary_activity_indices, primary_activity_issues
        evidence = Evidence("video", images=[f"f{i}" for i in range(64)], sources=["video_frames"],
                            sampling={"duration_seconds": 960, "timestamps": [i * 15 for i in range(64)]})
        indices = primary_activity_indices(evidence)
        self.assertEqual(7, len(indices))
        self.assertEqual([8, 16, 24, 32, 40, 48, 56], indices)
        small = Evidence("video", images=["a", "b", "c"], sources=["video_frames"], sampling={"duration_seconds": 30, "timestamps": [1, 15, 29]})
        self.assertEqual([0, 1, 2], primary_activity_indices(small))
        ledger = {"mode": "activity", "events": [
            {"importance": "core", "action": "参与者进行按摩", "evidence_refs": ["frame_1"]}]}
        def notes(*codes):
            return [{"frame_ref": f"frame_{i+1}", "timestamp": i, "activity": code} for i, code in enumerate(codes)]
        self.assertEqual([], primary_activity_issues(ledger, notes("intimate_activity", "intimate_activity", "massage", "massage", "massage", "unclear", "unclear")))
        self.assertEqual(["primary_activity_mismatch"], primary_activity_issues(ledger, notes("intimate_activity", "intimate_activity", "intimate_activity", "massage", "massage", "massage", "unclear")))
        self.assertEqual(["primary_activity_mismatch"], primary_activity_issues(ledger, notes("intimate_activity", "intimate_activity", "massage")))
        self.assertEqual([], primary_activity_issues(ledger, notes("intimate_activity", "massage", "unclear")))

    def test_three_distinct_phases_stay_a_sequence_and_a_returning_code_is_interleaved(self):
        from module.resource_analysis.summary import activity_phase_ledger, phase_sentence, category_only_summary
        def notes(*codes):
            return [{"frame_ref": f"frame_{i+1}", "timestamp": i * 10, "activity": code} for i, code in enumerate(codes)]
        empty = {"events": []}
        sequence = activity_phase_ledger(empty, notes("massage", "massage", "intimate_activity", "intimate_activity", "conversation", "conversation"))
        self.assertFalse(sequence["interleaved"])
        # One massage run and one intimate run is a sequence, not a merge.
        self.assertEqual([], sequence["merged_elements"])
        self.assertEqual(["参与者进行按摩", "参与者开始亲密互动", "参与者进行成人亲密互动", "参与者开始交谈", "参与者进行交谈"],
                         [event["action"] for event in sequence["events"]])
        for index, event in enumerate(sequence["events"]):
            event["id"] = f"event_{index+1}"
        self.assertEqual("开头为按摩，随后转为成人亲密互动，末段转为交谈。", phase_sentence(sequence["events"])["text"])
        returning = activity_phase_ledger(empty, notes("massage", "massage", "massage", "intimate_activity", "intimate_activity", "massage", "massage", "massage"))
        # Massage readings alternating with intimate readings are one adult activity.
        self.assertEqual(["massage"], returning["merged_elements"])
        self.assertEqual([{"code": "massage", "frames": 3}, {"code": "intimate_activity", "frames": 2}, {"code": "massage", "frames": 3}], returning["phases_before_merge"])
        self.assertEqual([{"code": "intimate_activity", "frames": 8}], returning["phases"])
        self.assertEqual(["参与者进行成人亲密互动"], [event["action"] for event in returning["events"]])
        self.assertEqual(8, len(returning["events"][0]["evidence_refs"]))
        returning["events"][0]["id"] = "event_1"
        self.assertEqual("所采样画面均为成人亲密互动，其间有按摩环节。", phase_sentence(returning["events"], False, None, None, ["massage"])["text"])
        self.assertTrue(category_only_summary("所采样画面均为成人亲密互动，其间有按摩环节。此前的参与者不再出现在画面中。"))
        self.assertFalse(category_only_summary("所采样画面均为成人亲密互动，其间有按摩环节。开头一人按摩对方的背部。"))
        # Other pairs keep the dominance / alternation wording.
        talk_heavy = activity_phase_ledger(empty, notes("conversation", "conversation", "conversation", "intimate_activity", "intimate_activity", "conversation", "conversation", "conversation"))
        self.assertTrue(talk_heavy["interleaved"])
        self.assertEqual([], talk_heavy["merged_elements"])
        for index, event in enumerate(talk_heavy["events"]):
            event["id"] = f"event_{index+1}"
            event["start"] = index
        self.assertEqual("以交谈为主，其间穿插成人亲密互动。", phase_sentence(talk_heavy["events"], True)["text"])
        close = activity_phase_ledger(empty, notes("conversation", "conversation", "intimate_activity", "intimate_activity", "conversation", "intimate_activity", "conversation", "intimate_activity", "conversation"))
        # Lone readings are not phases but they are still counted per code.
        self.assertEqual([5, 4], [len(event["evidence_refs"]) for event in close["events"]])
        for index, event in enumerate(close["events"]):
            event["id"] = f"event_{index+1}"
            event["start"] = index
        # 5 against 4 is a near-tie: no dominance claim, first-seen code first.
        self.assertEqual("交谈与成人亲密互动交替进行。", phase_sentence(close["events"], True)["text"])
        seven_three = activity_phase_ledger(empty, notes(*(["intimate_activity"] * 4 + ["conversation"] * 3 + ["intimate_activity"] * 3)))
        for index, event in enumerate(seven_three["events"]):
            event["id"] = f"event_{index+1}"
            event["start"] = index
        # 7 against 3 flipped between runs on real frames: still not dominance.
        self.assertEqual("成人亲密互动与交谈交替进行。", phase_sentence(seven_three["events"], True)["text"])
        # Sparse sampling keeps a lone edge reading; dense reads drop it.
        sparse_edge = activity_phase_ledger(empty, notes("conversation", "intimate_activity", "intimate_activity", "intimate_activity", "conversation", "conversation", "intimate_activity"))
        self.assertEqual([{"code": "conversation", "frames": 1}, {"code": "intimate_activity", "frames": 3}, {"code": "conversation", "frames": 2}, {"code": "intimate_activity", "frames": 1}], sparse_edge["phases"])
        dense_edge = activity_phase_ledger(empty, notes(*(["conversation"] * 8 + ["intimate_activity"] * 12 + ["conversation"] * 1)))
        self.assertEqual([{"code": "conversation", "frames": 8}, {"code": "intimate_activity", "frames": 12}], dense_edge["phases"])
        # A massage run beside intimate runs is merged even when it is a lone edge reading.
        merged_edge = activity_phase_ledger(empty, notes("massage", "intimate_activity", "intimate_activity", "intimate_activity", "massage", "massage", "intimate_activity"))
        self.assertEqual([{"code": "intimate_activity", "frames": 7}], merged_edge["phases"])
        noisy = activity_phase_ledger(empty, notes("conversation", "intimate_activity", "intimate_activity", "conversation", "conversation", "performance", "intimate_activity", "intimate_activity"))
        # A single sandwiched "performance" reading never becomes a third activity.
        self.assertEqual({"参与者进行交谈", "参与者进行成人亲密互动"}, {event["action"] for event in noisy["events"]})

    def test_frame_votes_anchored_outline_and_natural_content_wording(self):
        from module.resource_analysis.summary import vote_activity, anchored_outline, natural_content, smooth_activity_notes
        self.assertEqual("massage", vote_activity(["massage", "intimate_activity", "massage"]))
        self.assertEqual("unclear", vote_activity(["massage", "intimate_activity", "unclear"]))
        self.assertEqual("intimate_activity", vote_activity(["intimate_activity"]))
        self.assertEqual("unclear", vote_activity([]))
        raw = ["massage", "massage", "intimate_activity", "massage", "massage", "intimate_activity", "intimate_activity", "unclear", "intimate_activity", "intimate_activity"]
        smoothed = smooth_activity_notes([{"frame_ref": f"frame_{i+1}", "timestamp": i * 10, "activity": code} for i, code in enumerate(raw)])
        # The flipped read at frame 3 is outvoted; the boundary frame 5 is a tie
        # and keeps its own reading; the dark frame 8 inherits its neighbours.
        self.assertEqual(["massage"] * 4 + ["intimate_activity"] * 6, [row["activity"] for row in smoothed])
        self.assertEqual(raw, [row["raw"] for row in smoothed])
        runs = [{"code": "massage", "frames": 2, "start": 59, "end": 124},
                {"code": "intimate_activity", "frames": 2, "start": 180, "end": 244},
                {"code": "massage", "frames": 2, "start": 411, "end": 466},
                {"code": "intimate_activity", "frames": 4, "start": 542, "end": 792},
                {"code": "massage", "frames": 1, "start": 913, "end": 913}]
        # A trailing single reading is a moment, not the ending.
        self.assertEqual("前段按摩与成人亲密互动交替出现，约9–13分钟以成人亲密互动为主，约15分钟时再次出现按摩。", anchored_outline(runs, 971))
        self.assertEqual("开头以按摩为主，末段转为成人亲密互动。", anchored_outline(
            [{"code": "massage", "frames": 4, "start": 10, "end": 300}, {"code": "intimate_activity", "frames": 2, "start": 800, "end": 930}], 971))
        self.assertEqual("开头以按摩为主，约13分钟起转为成人亲密互动。", anchored_outline(
            [{"code": "massage", "frames": 4, "start": 10, "end": 300}, {"code": "intimate_activity", "frames": 2, "start": 800, "end": 880}], 971))
        # Three frames that cover only a sliver of a long video are not a phase.
        self.assertIsNone(anchored_outline([{"code": "massage", "frames": 3, "start": 100, "end": 140}, {"code": "intimate_activity", "frames": 2, "start": 800, "end": 900}], 971))
        self.assertEqual("开头为交谈，末段转为按摩。", anchored_outline(
            [{"code": "conversation", "frames": 2, "start": 10, "end": 100}, {"code": "massage", "frames": 5, "start": 500, "end": 950}], 971))
        self.assertEqual("开头为交谈，约8–13分钟转为按摩。", anchored_outline(
            [{"code": "conversation", "frames": 2, "start": 10, "end": 100}, {"code": "massage", "frames": 5, "start": 500, "end": 800}], 971))
        # No run with three agreeing frames: no anchored claim at all.
        self.assertIsNone(anchored_outline([{"code": "massage", "frames": 2, "start": 0, "end": 60}, {"code": "intimate_activity", "frames": 2, "start": 100, "end": 200}], 300))
        self.assertEqual("开头一人按摩对方的背部", natural_content({"importance": "content", "action": "女子按摩主体的背部", "start": 58.8, "end": 78.8}, 971))
        self.assertEqual("开头一人在床上靠近躺着的对方", natural_content({"importance": "content", "action": "女方在床上靠近躺着的主体", "start": 58.8, "end": 63.8}, 971))
        from module.resource_analysis.summary import _INCIDENTAL_ACTIVITY_DETAIL
        for text in ("坐到床上", "主体坐到床上", "女方起身", "一人走到镜头前", "主体在床上从躺姿转为坐姿", "女子爬上床", "躺卧者坐在床边", "另一方起身"):
            self.assertIsNotNone(_INCIDENTAL_ACTIVITY_DETAIL.fullmatch(text), text)
        self.assertIsNone(_INCIDENTAL_ACTIVITY_DETAIL.fullmatch("一人坐到床上整理器械"))
        from module.resource_analysis.summary import _LABEL_PARAPHRASE
        for text in ("对躺卧在床上的对象进行身体按摩", "女子爬上床后对男子进行成人亲密互动", "参与者进行按摩", "两人在床上进行成人亲密互动",
                     "女子在床上站立对躺着的男子进行按摩", "一人跪在床上为躺着的对方按摩"):
            self.assertIsNotNone(_LABEL_PARAPHRASE.fullmatch(text), text)
        # Which body part is massaged is the massage type: a real detail.
        for text in ("女子按摩主体的背部", "为对方按摩背部", "对对方的背部进行按摩", "一人为另一人示范器械的使用步骤", "两人分工演示设备的操作流程", "参与者提醒对方保持约定的节奏"):
            self.assertIsNone(_LABEL_PARAPHRASE.fullmatch(text), text)
        from module.resource_analysis.summary import label_like_content
        for text in ("参与者组织按摩活动并展示任务变化", "两人进行成人亲密互动，并展示环节的变化", "参与者开展亲密互动并完成活动的推进"):
            self.assertTrue(label_like_content(text, "adult"), text)
        self.assertFalse(label_like_content("一人为另一人示范器械的使用步骤并展示完成后的变化", "adult"))
        strong = [{"code": "massage", "frames": 1, "start": 30, "end": 30},
                  {"code": "intimate_activity", "frames": 11, "start": 59, "end": 283},
                  {"code": "massage", "frames": 8, "start": 299, "end": 466},
                  {"code": "intimate_activity", "frames": 12, "start": 506, "end": 760},
                  {"code": "massage", "frames": 7, "start": 784, "end": 951}]
        self.assertEqual("开头为按摩，约1–5分钟转为成人亲密互动，约5–8分钟回到按摩，约8–13分钟回到成人亲密互动，末段回到按摩。",
                         anchored_outline(strong, 971))
        # Nobody in the last frame: the last phase is anchored, never called the ending.
        self.assertEqual("开头为按摩，约1–5分钟转为成人亲密互动，约5–8分钟回到按摩，约8–13分钟回到成人亲密互动，约13分钟起回到按摩。",
                         anchored_outline(strong, 971, open_ended=True))
        weak_tail = strong[:-1] + [{"code": "massage", "frames": 3, "start": 913, "end": 951}]
        self.assertEqual("开头为按摩，约1–5分钟转为成人亲密互动，约5–8分钟回到按摩，约8–13分钟回到成人亲密互动，约15分钟起回到按摩。",
                         anchored_outline(weak_tail, 971, open_ended=True))
        self.assertEqual("约5分钟时一人整理器械并递给对方", natural_content({"importance": "content", "action": "主体整理器械并递给主体", "start": 300, "end": 330}, 971))
        self.assertEqual("末段一人收起器械", natural_content({"importance": "content", "action": "一人收起器械", "start": 930, "end": 960}, 971))
        # Evidence spread over most of the video has no single time anchor.
        self.assertEqual("一人为对方整理器械", natural_content({"importance": "content", "action": "一人为对方整理器械", "start": 30, "end": 900}, 971))
        self.assertEqual("参与者提醒对方保持约定的节奏", natural_content({"importance": "context", "action": "参与者提醒对方保持约定的节奏", "start": 50, "end": 70}, 971))

    def test_generic_activity_plus_last_frame_absence_is_not_specific_content(self):
        evidence = Evidence("video", images=["synthetic-a", "synthetic-b"], sources=["video_frames"],
                            sampling={"duration_seconds": 60, "timestamps": [1, 59]})
        narrative = build_narrative({"version": 2, "mode": "activity", "events": [
            {"importance": "core", "phase": "activity", "action": "参与者进行按摩", "evidence_refs": ["frame_1"]},
            {"importance": "progression", "phase": "activity", "action": "此前的参与者不再出现在画面中", "evidence_refs": ["frame_2"]},
        ]}, evidence, category="adult")
        candidate = build_narrative_synopsis([
            {"text": "参与者进行按摩。", "event_refs": ["event_1"]},
            {"text": "此前的参与者不再出现在画面中。", "event_refs": ["event_2"]},
        ], evidence, narrative, category="adult")
        self.assertEqual("review", candidate["summary_quality"]["status"])
        self.assertIn("category_only_summary", candidate["summary_quality"]["issues"])
        self.assertEqual(2, len(candidate["narrative"]["events"]), "Keep the observed change, but not as a passing synopsis")
        self.assertTrue(category_only_summary("参与者进行按摩。此前的参与者不再出现在画面中。"))
        self.assertFalse(category_only_summary("参与者进行按摩，并讲解热身前的放松方法。此前的参与者不再出现在画面中。"))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "fixture.mp4"
        self.path.touch()
        self.evidence = Evidence(
            "video",
            images=["synthetic-frame-a", "synthetic-frame-b"],
            sources=["video_frames"],
            sampling={"duration_seconds": 300, "timestamps": [12, 240]},
        )
        self.sentences = [
            {
                "text": "片段中一人拿起工具组装模型，另一人递出零件，两人在桌边配合完成操作。",
                "evidence_refs": ["frame_1"],
                "event_refs": ["event_1"],
            },
            {
                "text": "后段两人放下工具，收起桌上的剩余零件，带着完成的模型离开工作台。",
                "evidence_refs": ["frame_2"],
                "event_refs": ["event_2"],
            },
        ]
        self.details = {
            "resource_kind": "video",
            "category": "adult",
            "subtype": "independent",
            "matches_category": True,
            "evidence_sources": ["video_frames"],
            "facets": {
                key: "仅用于分类核验的中性制作信息。"
                for key in analysis_profile("adult", "independent").directions
            },
            "tags": ["固定机位"],
            "summary_sentences": self.sentences,
        }
        self.prompts = []
        self.ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": item["text"], "evidence_refs": item["evidence_refs"]}
            for item in self.sentences
        ]}

    async def analyze(self, details=None, observations=None, revised=None):
        async def completion(prompt, evidence):
            self.prompts.append(prompt)
            if prompt.startswith("对白含义"):
                return {"reason": "no_specific_context", "contexts": []}
            if prompt.startswith("主活动观察"):
                return {"observations": [{"frame_ref": f"frame_{i+1}", "activity": "unclear"} for i in range(len(evidence.images))]}
            if prompt.startswith("叙事模式"):
                return {"mode": "activity", "basis": "activity_only", "evidence_refs": ["frame_1"]}
            if prompt.startswith("阶段五"):
                candidate = json.loads(prompt.split("待核验数据：", 1)[1])
                return {"mode": candidate["narrative"]["mode"], "coverage_ok": True, "readable": True, "non_redundant": True,
                        "events": [{"id": event["id"], "supported": True} for event in candidate["narrative"]["events"]],
                        "sentences": [{"index": index + 1, "supported": True} for index, _ in enumerate(candidate["summary_sentences"])]}
            if prompt.startswith("阶段三"):
                response = copy.deepcopy(self.ledger)
                response.setdefault("version", 2)
                for index, event in enumerate(response.get("events", [])):
                    event.setdefault("importance", "core" if not index else "progression")
                return response
            if prompt.startswith("阶段四"):
                if isinstance(revised, Exception):
                    raise revised
                rows = copy.deepcopy(revised if revised is not None else
                                     (details if details is not None else self.details).get("summary_sentences", []))
                for index, row in enumerate(rows):
                    row.setdefault("event_refs", [f"event_{min(index + 1, 2)}"])
                return {"summary_sentences": rows}
            if prompt.startswith("阶段一"):
                return {
                    "resource_kind": "video",
                    "category": "adult",
                    "subtype": "independent",
                    "confidence": 0.85,
                    "evidence_sources": ["video_frames"],
                    "observations": observations or [],
                }
            return copy.deepcopy(details if details is not None else self.details)

        with mock.patch(
            "module.resource_analysis.service.extract_evidence",
            return_value=self.evidence,
        ):
            return await analyze_resource(
                str(self.path), completion=completion, min_confidence=0.4
            )

    async def test_short_focused_activity_needs_no_padding(self):
        self.ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "两人进行亲密互动。", "evidence_refs": ["frame_1", "frame_2"]},
        ]}
        result = await self.analyze(revised=[{"text": "片段记录两人的亲密互动。", "event_refs": ["event_1"]}])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("片段记录两人的亲密互动。", result["summary"])
        self.assertEqual(0, result["diagnostics"]["summary_revision"]["repairs"])

    async def test_vague_pose_change_cannot_pad_an_activity_synopsis(self):
        self.ledger = {"mode": "activity", "events": [
            {"phase": "activity", "action": "两人进行亲密互动。", "evidence_refs": ["frame_1", "frame_2"]},
        ]}
        result = await self.analyze(revised=[{"text": "片段记录两人的亲密互动，姿势发生变化。", "event_refs": ["event_1"]}])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertIn("empty_activity_change", result["summary_quality"]["issues"])
        self.assertFalse(result["summary_sentences"])

    async def test_video_gets_evidence_bound_overview_not_classification_dump(self):
        result = await self.analyze()
        self.assertEqual("complete", result["status"])
        self.assertEqual("ready", result.get("summary_quality", {}).get("status"))
        self.assertEqual(
            "".join(item["text"] for item in self.sentences), result["summary"]
        )
        self.assertNotIn("归档线索", result["summary"])
        self.assertNotIn("分类核验", result["summary"])
        self.assertEqual(self.sentences, result["summary_sentences"])
        refs = result["summary_quality"]["evidence_catalog"]
        self.assertEqual([12, 240], [item["timestamp"] for item in refs])
        self.assertIn("summary_sentences", self.prompts[-1])
        self.assertIn("event_2", self.prompts[-1])
        # Two sampled frames, so two single-frame main-activity reads.
        self.assertEqual(8, len(self.prompts))
        self.assertTrue(self.prompts[-2].startswith("阶段四"))
        self.assertTrue(self.prompts[-1].startswith("阶段五"))
        self.assertIn("事件", self.prompts[-3])

    async def test_low_quality_cannot_look_fully_analyzed_but_keeps_category(self):
        variants = {
            "missing": [],
            "english": [
                {
                    "text": "A person appears in an indoor video scene.",
                    "evidence_refs": ["frame_1"],
                }
            ]
            * 2,
            "duplicate": [self.sentences[0], self.sentences[0]],
            "generic": [
                {"text": "这是常见的视频内容，整体具有自己的特点。", "evidence_refs": ["frame_1"]},
                {"text": "内容展现了视频的特点，适合感兴趣的人观看。", "evidence_refs": ["frame_2"]},
            ],
            "advertisement": [
                self.sentences[0],
                {
                    "text": "加入会员群并访问 https://example.invalid 可以获取完整资源。",
                    "evidence_refs": ["frame_2"],
                },
            ],
            "invalid_reference": [
                self.sentences[0],
                {**self.sentences[1], "event_refs": ["event_99"]},
            ],
            "unsupported_audio": [
                self.sentences[0],
                {"text": "对白使用韩语，背景配有轻柔音乐，人物的声音较低。", "evidence_refs": ["frame_2"]},
            ],
            "single_frame_comparison": [
                self.sentences[0],
                {"text": "后段人物动作相比前段变化明显，活动范围和方向有所不同。", "event_refs": ["event_2"]},
            ],
            "unseen_full_sequence": [
                self.sentences[0],
                {"text": "全程采用固定的室内镜头，人物始终位于房间中央的位置。", "evidence_refs": ["frame_2"]},
            ],
        }
        for name, sentences in variants.items():
            with self.subTest(name=name):
                result = await self.analyze(
                    {**self.details, "summary_sentences": sentences}
                )
                self.assertEqual("adult", result["category"])
                self.assertEqual("independent", result["subtype"])
                self.assertEqual(
                    "review", result.get("summary_quality", {}).get("status")
                )
                self.assertIn("简介待完善", analysis_status(result))
                self.assertFalse(result.get("summary_sentences"))
                self.assertNotIn("仅用于分类核验", result["summary"])

    async def test_english_and_promotional_observations_are_not_reused_as_overview(
        self,
    ):
        observations = [
            {
                "text": "A watermark and a person are visible.",
                "sources": ["video_frames"],
            },
            {"text": "加入会员群领取完整视频，联系 @fixture_bot。", "sources": ["video_frames"]},
            {"text": "室内固定机位拍摄，画面可见浅色窗帘。", "sources": ["video_frames"]},
        ]
        result = await self.analyze(observations=observations)
        self.assertEqual(observations[2]["text"], result["content_description"])

    async def test_reference_is_checked_against_actual_images_not_sampling_numbers(
        self,
    ):
        self.evidence.images = self.evidence.images[:1]
        result = await self.analyze()
        self.assertEqual("review", result.get("summary_quality", {}).get("status"))
        self.assertIn("invalid_event_evidence", result["summary_quality"]["issues"])

    async def test_transcript_is_not_evidence_of_voice_or_music_character(self):
        self.evidence.text = "可靠的本地语音文字转写。"
        self.evidence.sources.append("transcript")
        self.evidence.audio_clips = [
            {"start": 0, "end": 30, "text": self.evidence.text}
        ]
        details = {
            **self.details,
            "summary_sentences": [
                self.sentences[0],
                {"text": "对白使用低沉的声线，背景音乐配合房间中的人物互动。", "evidence_refs": ["speech_1"]},
            ],
        }
        result = await self.analyze(details)
        self.assertIn("unsupported_acoustic_claim", result["summary_quality"]["issues"])

    async def test_boilerplate_recap_watermark_and_invented_sequence_are_not_synopsis(
        self,
    ):
        variants = [
            "整体呈现出房间中的人物活动，人物在房间里移动，背景为室内环境。",
            "侧面画面采用较近的构图，暖色光线照亮桌面，带有水印标识。",
            "全程记录两人组装模型，从头到尾都在同一张桌子旁完成操作。",
        ]
        for text in variants:
            with self.subTest(text=text):
                details = {
                    **self.details,
                    "summary_sentences": [
                        self.sentences[0],
                        {"text": text, "evidence_refs": ["frame_1", "frame_2"]},
                    ],
                }
                result = await self.analyze(details)
                self.assertEqual("review", result["summary_quality"]["status"])

    async def test_one_focused_writing_pass_can_fix_quality_without_changing_classification(
        self,
    ):
        bad = {
            **self.details,
            "summary_sentences": [self.sentences[0], self.sentences[0]],
        }
        result = await self.analyze(bad, revised=self.sentences)
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertEqual("independent", result["subtype"])
        self.assertEqual(
            ["classification", "details", "primary_activity", "primary_activity", "narrative_mode", "narrative", "summary", "narrative_audit"],
            [item["stage"] for item in result["diagnostics"]["attempts"]],
        )
        self.assertEqual(
            [],
            result["diagnostics"]["summary_revision"]["initial_issues"],
        )

    async def test_writing_refusal_is_not_retried_and_keeps_confirmed_category(self):
        bad = {
            **self.details,
            "summary_sentences": [self.sentences[0], self.sentences[0]],
        }
        result = await self.analyze(bad, revised=ModelError("model_refused", "模型拒绝"))
        self.assertEqual("complete", result["status"])
        self.assertEqual("review", result["summary_quality"]["status"])
        self.assertEqual("adult", result["category"])
        self.assertIn("model_refused", result["summary_quality"]["issues"])
        self.assertEqual(7, len(self.prompts))

    async def test_summary_refresh_reuses_confirmed_category_without_reclassifying(self):
        previous = await self.analyze()
        previous_snapshot = copy.deepcopy(previous)
        audit = {"mode": "activity", "coverage_ok": True, "readable": True, "non_redundant": True,
                 "events": [{"id": f"event_{index}", "supported": True} for index in (1, 2)],
                 "sentences": [{"index": index, "supported": True} for index in (1, 2)]}
        # Frame readings now precede routing.
        writer = mock.AsyncMock(side_effect=[{"observations": [{"frame_ref": "frame_1", "activity": "unclear"}]},
                                            {"observations": [{"frame_ref": "frame_1", "activity": "unclear"}]},
                                            {"mode": "activity", "basis": "activity_only", "evidence_refs": ["frame_1"]},
                                            previous["narrative"], {"summary_sentences": self.sentences}, audit])
        with mock.patch("module.resource_analysis.service.extract_evidence", return_value=self.evidence):
            result = await analyze_resource(str(self.path), completion=writer, summary_only=True,
                                            previous_analysis=previous)
        self.assertEqual(6, writer.await_count)
        self.assertTrue(writer.call_args.args[0].startswith("阶段五"))
        self.assertEqual(previous_snapshot, previous)
        for key in ("category", "subtype", "confidence", "facets", "tags"):
            self.assertEqual(previous[key], result[key])
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertTrue(result["diagnostics"]["classification_reused"])
        self.assertEqual("reused", result["diagnostics"]["validation"]["status"])

    async def test_summary_only_cannot_force_an_unconfirmed_classification(self):
        previous = await self.analyze()
        previous.update(status="needs_review", category="other")
        writer = mock.AsyncMock()
        result = await analyze_resource(str(self.path), completion=writer, summary_only=True,
                                        previous_analysis=previous)
        writer.assert_not_called()
        self.assertEqual("failed", result["status"])
        self.assertEqual("classification_not_confirmed", result["reason"])

    async def test_real_review_findings_cannot_be_published_as_verified_description(self):
        variants = [
            "一位人物躺在浅色床品上，脸上有泪水，另一人的手靠近下巴。",
            "不同画面中人物姿势从趴伏到躺卧，房间灯光条件也有所变化。",
            "一位戴马尾的女性在房间里整理物件，背景可以看到浅色窗帘。",
        ]
        for text in variants:
            with self.subTest(text=text):
                details = {**self.details, "summary_sentences": [self.sentences[0],
                    {"text": text, "evidence_refs": ["frame_1", "frame_2"]}]}
                result = await self.analyze(details)
                self.assertEqual("review", result["summary_quality"]["status"])

    async def test_adult_synopsis_does_not_publish_gender_guesses_as_visual_facts(self):
        sentences = [
            {"text": "一位女性拿起工具组装模型，另一位男性递出零件，两人在桌边配合操作。", "evidence_refs": ["frame_1"]},
            {"text": "一位男子收起桌上剩余零件，拿起牛仔外套，将杯子放回柜子后离开。", "evidence_refs": ["frame_2"]},
        ]
        result = await self.analyze({**self.details, "summary_sentences": sentences})
        self.assertEqual("ready", result["summary_quality"]["status"])
        self.assertNotRegex(result["summary"], "男性|女性|男子")
        self.assertIn("牛仔外套", result["summary"])
        self.assertIn("杯子", result["summary"])
        self.assertEqual([item["evidence_refs"] for item in sentences],
                         [item["evidence_refs"] for item in result["summary_sentences"]])

    def test_neutral_person_wording_is_idempotent_and_leaves_other_categories_alone(self):
        sentences = [
            {"text": "一位女性在室内与另一位男性互动，背景可见浅色窗帘和床铺。", "evidence_refs": ["frame_1"]},
            {"text": "另有画面显示一位男子身穿牛仔外套，背对镜头坐在摆有杯子的桌前。", "evidence_refs": ["frame_2"]},
        ]
        original = copy.deepcopy(sentences)
        neutral = build_synopsis(sentences, self.evidence, category="adult")
        self.assertEqual(neutral, build_synopsis(neutral["summary_sentences"], self.evidence, category="adult"))
        self.assertIn("一人在室内与另一人互动", neutral["summary"])
        movie = build_synopsis(sentences, self.evidence, category="movie")
        self.assertEqual(original, movie["summary_sentences"])
        self.assertEqual(original, sentences)


if __name__ == "__main__":
    unittest.main()
