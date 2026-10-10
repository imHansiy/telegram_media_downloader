"""Speech screening precedes story routing, with server-owned reference remapping."""
import copy
import unittest
from unittest.mock import patch
import tempfile

from module.resource_analysis.evidence import Evidence
from module.resource_analysis.dialogue import dialogue_requests, parse_dialogue, combine_dialogue_readings
from module.resource_analysis.focus import supplement_evidence
from module.resource_analysis.service import _summarize_video, ModelError


class DialogueScreeningTest(unittest.IsolatedAsyncioTestCase):
    def evidence(self):
        return Evidence('video', images=['a', 'b'], sources=['video_frames', 'transcript'],
            text='combined text must not cross clip requests',
            audio_clips=[{'start': 1, 'end': 9, 'text': 'UNREADABLE_FIXTURE'},
                         {'start': 11, 'end': 19, 'text': 'SAFE_FIXTURE'},
                         {'start': 21, 'end': 29, 'text': 'EXCLUDED_FIXTURE'}],
            sampling={'duration_seconds': 60, 'timestamps': [1, 59],
                      'audio': {'windows': [{'start': 0, 'end': 30}]}})

    def test_mixed_readings_keep_good_clip_and_remap_its_context(self):
        evidence = self.evidence()
        original = copy.deepcopy(evidence)
        requests = dialogue_requests(evidence)
        self.assertEqual(3, len(requests))
        readings = []
        for index, (request, indices) in enumerate(requests):
            self.assertEqual([index], indices)
            self.assertEqual(1, len(request.audio_clips))
            self.assertNotIn('combined text', request.text)
            raw = ({'reason': 'specific_context', 'contexts': [
                {'action': '工作人员讨论补票安排', 'evidence_refs': ['speech_1']}]} if index == 1
                else {'reason': 'unintelligible' if index == 0 else 'unsafe_or_sensitive', 'contexts': []})
            rows, issues = parse_dialogue(raw, request)
            self.assertEqual([], issues)
            readings.append((indices, raw['reason'], rows))
        selected, contexts, diagnostic = combine_dialogue_readings(evidence, readings)
        self.assertEqual('speech_1: SAFE_FIXTURE', selected.text)
        self.assertEqual(['speech_1'], contexts[0]['evidence_refs'])
        self.assertEqual(['speech_2'], diagnostic['kept_original_refs'])
        self.assertEqual(2, diagnostic['excluded_clips'])
        self.assertEqual(original, evidence)
        self.assertNotIn('工作人员', str(diagnostic))
        with tempfile.TemporaryDirectory() as directory, patch('module.resource_analysis.focus._run'), \
                patch('module.resource_analysis.focus._image_data', return_value='new'), \
                patch('module.resource_analysis.focus._speech_samples', return_value=([], {'windows': []})) as speech:
            focused = supplement_evidence('video.mp4', directory, evidence,
                {'diagnostics': {'dialogue_context': diagnostic}}, transcribe=True)
        self.assertTrue(speech.called)
        self.assertTrue(all(window['start'] >= 30 for window in speech.call_args.kwargs['timeline_windows']))
        self.assertEqual('speech_1: SAFE_FIXTURE', focused.text)

    def test_topic_readings_merge_into_one_neutral_context_and_keep_their_clips(self):
        from module.resource_analysis.dialogue import parse_dialogue, valid_topic
        evidence = self.evidence()
        requests = dialogue_requests(evidence)
        readings = []
        for index, (request, indices) in enumerate(requests):
            raw = ({'reason': 'topics', 'contexts': [], 'topics': ['价格', '以往经历']} if index == 0
                   else {'reason': 'topics', 'contexts': [], 'topics': ['是否有伴侣', '价格']} if index == 1
                   else {'reason': 'unintelligible', 'contexts': []})
            rows, issues = parse_dialogue(raw, request)
            self.assertEqual([], issues)
            readings.append((indices, raw['reason'], rows))
        selected, contexts, diagnostic = combine_dialogue_readings(evidence, readings)
        self.assertEqual(['speech_1', 'speech_2'], diagnostic['kept_original_refs'])
        self.assertEqual(3, diagnostic['topic_count'])
        self.assertEqual('specific_context', diagnostic['reason'])
        self.assertEqual([{'importance': 'context', 'phase': 'activity', 'action': '对话中谈及价格、以往经历和是否有伴侣',
                           'evidence_refs': ['speech_1', 'speech_2']}], contexts)
        self.assertEqual(2, len(selected.audio_clips))
        # Topic words stay neutral and short; explicit, age or consent words are rejected.
        for bad in ('性交', '年龄', '是否自愿', '闲聊', 'ok', '这是一段非常长的话题词超过十个字'):
            self.assertFalse(valid_topic(bad), bad)
        rows, issues = parse_dialogue({'reason': 'topics', 'contexts': [], 'topics': ['价格', '同意']}, requests[0][0])
        self.assertEqual(([], []), (rows, issues))

    async def test_story_routing_only_sees_retained_speech(self):
        evidence = self.evidence()
        calls = []
        async def complete(prompt, request):
            calls.append(prompt)
            if prompt.startswith('主活动观察'):
                return {'observations': [{'frame_ref': 'frame_1', 'activity': 'unclear'}]}
            if prompt.startswith('对白含义'):
                if 'SAFE_FIXTURE' in request.text:
                    return {'reason': 'no_specific_context', 'contexts': []}
                return {'reason': 'unintelligible' if 'UNREADABLE_FIXTURE' in request.text else 'unsafe_or_sensitive', 'contexts': []}
            self.assertTrue(prompt.startswith('叙事模式'))
            self.assertIn('SAFE_FIXTURE', request.text)
            self.assertNotIn('UNREADABLE_FIXTURE', request.text)
            self.assertNotIn('EXCLUDED_FIXTURE', request.text)
            self.assertEqual(1, len(request.audio_clips))
            raise ModelError('model_refused', 'synthetic stop')
        result = {'category': 'adult', 'subtype': 'studio', 'diagnostics': {'attempts': []}}
        await _summarize_video(evidence, complete, result, 5, lambda _: None)
        self.assertEqual(3, sum(prompt.startswith('对白含义') for prompt in calls))
        self.assertEqual(2, result['diagnostics']['dialogue_context']['excluded_clips'])
        self.assertIn('model_refused', result['summary_quality']['issues'])

    async def test_refusal_does_not_advance_to_other_clips_or_route(self):
        calls = []
        async def complete(prompt, request):
            calls.append(prompt)
            raise ModelError('model_refused', 'synthetic stop')
        result = {'category': 'adult', 'subtype': 'studio', 'diagnostics': {'attempts': []}}
        await _summarize_video(self.evidence(), complete, result, 5, lambda _: None)
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].startswith('对白含义'))
        self.assertIn('model_refused', result['summary_quality']['issues'])
