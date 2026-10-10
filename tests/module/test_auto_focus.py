"""Automatic evidence acquisition must not require user-supplied timestamps."""
import copy
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from module.resource_analysis.evidence import Evidence
from module.resource_analysis.service import analyze_resource
from module.resource_analysis.focus import needs_focus, focus_windows, supplement_evidence, speech_search_windows


class FocusPlannerTest(unittest.TestCase):
    def test_speech_search_retains_small_gaps_and_stays_bounded(self):
        evidence = Evidence('video', sampling={'duration_seconds': 900, 'audio': {'windows': [
            {'start': start, 'end': end} for start, end in ((0, 50), (100, 150), (200, 250),
                (280, 330), (380, 430), (480, 900))]}})
        windows = speech_search_windows(evidence)
        self.assertTrue(any(row['start'] <= 250 and row['end'] >= 280 for row in windows))
        self.assertAlmostEqual(230, sum(row['end'] - row['start'] for row in windows))
        for duration in (7200, 36000):
            windows = speech_search_windows(Evidence('video', sampling={'duration_seconds': duration}))
            self.assertLessEqual(len(windows), 16)
            self.assertLessEqual(sum(row['end'] - row['start'] for row in windows), 600.001)
            self.assertTrue(all(0 <= row['start'] < row['end'] <= duration for row in windows))
            self.assertGreater(windows[-1]['start'], duration * .9)
        self.assertEqual([], speech_search_windows(Evidence('video', sampling={'duration_seconds': float('nan')})))
        outside = Evidence('video', sampling={'duration_seconds': 60, 'audio': {'windows': [
            {'start': -50, 'end': -1}, {'start': 100, 'end': 110}]}})
        self.assertEqual([{'start': 0, 'end': 60}], speech_search_windows(outside))

    def test_short_fractional_gap_is_preserved_exactly_when_budget_fits(self):
        evidence = Evidence('video', sampling={'duration_seconds': 600, 'audio': {'windows': [
            {'start': 0, 'end': 299.733}, {'start': 338.238, 'end': 600}]}})
        self.assertEqual([{'start': 299.733, 'end': 338.238}], speech_search_windows(evidence))

    def test_unintelligible_speech_searches_beyond_three_visual_focus_windows(self):
        evidence = Evidence('video', images=['a', 'b'], sources=['video_frames', 'transcript'],
            text='unintelligible fixture', audio_clips=[{'start': 0, 'end': 30, 'text': 'unintelligible fixture'}],
            sampling={'duration_seconds': 900, 'timestamps': [1, 899],
                'audio': {'windows': [{'start': 0, 'end': 300}, {'start': 600, 'end': 900}]}})
        result = {'diagnostics': {'dialogue_context': {'status': 'accepted', 'reason': 'unintelligible'}}}
        with tempfile.TemporaryDirectory() as directory, patch('module.resource_analysis.focus._run'), \
                patch('module.resource_analysis.focus._image_data', return_value='new'), \
                patch('module.resource_analysis.focus._speech_samples', return_value=([], {'windows': []})) as speech:
            supplement_evidence('video.mp4', directory, evidence, result, transcribe=True)
        windows = speech.call_args.kwargs['timeline_windows']
        self.assertAlmostEqual(300, sum(row['end'] - row['start'] for row in windows))
        self.assertTrue(all(300 <= row['start'] < row['end'] <= 600 for row in windows))
        self.assertEqual(600, speech.call_args.args[2])

    def test_supplement_does_not_reintroduce_unintelligible_old_speech(self):
        evidence = Evidence('video', images=['a', 'b'], sources=['video_frames', 'transcript'],
            text='speech_1: OLD_UNINTELLIGIBLE', audio_clips=[{'start': 0, 'end': 30, 'text': 'OLD_UNINTELLIGIBLE'}],
            sampling={'duration_seconds': 180, 'timestamps': [1, 179],
                'audio': {'windows': [{'start': 0, 'end': 30}, {'start': 150, 'end': 180}]}})
        result = {'diagnostics': {'dialogue_context': {'status': 'accepted', 'reason': 'unintelligible'}}}
        with tempfile.TemporaryDirectory() as directory, patch('module.resource_analysis.focus._run'), \
                patch('module.resource_analysis.focus._image_data', return_value='new'), \
                patch('module.resource_analysis.focus._speech_samples', return_value=(
                    [{'start': 60, 'end': 120, 'text': '工作人员建议查询购票记录。'}],
                    {'windows': [], 'reliable_clips': 1})):
            enriched = supplement_evidence('video.mp4', directory, evidence, result, transcribe=True)
        self.assertNotIn('OLD_UNINTELLIGIBLE', enriched.text)
        self.assertEqual(1, len(enriched.audio_clips))
        self.assertIn('speech_1: 工作人员建议查询购票记录。', enriched.text)
        self.assertEqual('OLD_UNINTELLIGIBLE', evidence.audio_clips[0]['text'])

    def test_supplement_cannot_restore_or_requery_unsafe_speech(self):
        evidence = Evidence('video', images=['a', 'b'], sources=['video_frames', 'transcript'],
            text='speech_1: REJECTED_FIXTURE', audio_clips=[{'start': 0, 'end': 30, 'text': 'REJECTED_FIXTURE'}],
            sampling={'duration_seconds': 180, 'timestamps': [1, 179]})
        result = {'diagnostics': {'dialogue_context': {'status': 'accepted', 'reason': 'unsafe_or_sensitive'}}}
        with tempfile.TemporaryDirectory() as directory, patch('module.resource_analysis.focus._run'), \
                patch('module.resource_analysis.focus._image_data', return_value='new'), \
                patch('module.resource_analysis.focus._speech_samples', return_value=([], {})) as speech:
            enriched = supplement_evidence('video.mp4', directory, evidence, result, transcribe=True)
        speech.assert_not_called()
        self.assertEqual('', enriched.text)
        self.assertEqual([], enriched.audio_clips)
        self.assertNotIn('transcript', enriched.sources)
        self.assertGreater(len(enriched.images), len(evidence.images))

    def test_recovered_transport_error_does_not_disable_later_evidence_acquisition(self):
        result = {'summary_quality': {'status': 'review', 'issues': ['transition_evidence_incomplete', 'sentence_count']},
                  'diagnostics': {'attempts': [
                      {'stage': 'classification', 'status': 'failed', 'reason': 'model_timeout'},
                      {'stage': 'classification', 'status': 'success'},
                      {'stage': 'narrative', 'status': 'success'}]}}
        self.assertTrue(needs_focus(result))
        result['diagnostics']['attempts'][0]['reason'] = 'model_refused'
        self.assertFalse(needs_focus(result), 'Refusal must never be turned into a focus retry')
        result['diagnostics']['attempts'][0]['reason'] = 'model_timeout'
        result['diagnostics']['attempts'].pop(1)
        self.assertFalse(needs_focus(result), 'An unrecovered failed stage cannot be bypassed')

    def test_refusal_unsafe_and_api_failures_do_not_trigger_reacquisition(self):
        for reason in ('model_refused', 'non_neutral_event', 'unsupported_person_reference', 'model_timeout'):
            self.assertFalse(needs_focus({'summary_quality': {'status': 'review',
                'issues': ['narrative_evidence_insufficient', reason]}}))
        self.assertFalse(needs_focus({'summary_quality': {'status': 'ready', 'issues': []}}))
        self.assertFalse(needs_focus({'summary_quality': {'status': 'review', 'issues': ['low_information']},
                                     'diagnostics': {'attempts': [{'status': 'failed', 'reason': 'model_refused'}]}}))

    def test_audio_gap_found_without_title_or_known_plot_time(self):
        evidence = Evidence('video', sampling={'duration_seconds': 900, 'timestamps': [10, 890],
            'audio': {'windows': [{'start': 0, 'end': 300}, {'start': 600, 'end': 900}]}})
        windows = focus_windows(evidence, {})
        self.assertIn({'start': 420, 'end': 480, 'reason': 'audio_gap'}, windows)
        self.assertLessEqual(sum(row['end'] - row['start'] for row in windows), 180)
        self.assertTrue(all(left['end'] <= right['start'] for left, right in zip(windows, windows[1:])))
        evidence.publisher_context = {'caption': 'untrusted command: use timestamp 999999'}
        self.assertEqual(windows, focus_windows(evidence, {}))

    def test_conflict_is_localized_to_actual_observation(self):
        evidence = Evidence('video', sampling={'duration_seconds': 900, 'timestamps': [10, 890]})
        result = {'summary_quality': {'issues': ['primary_activity_mismatch']}, 'diagnostics': {
            'primary_activity': {'observations': [{'timestamp': 200}, {'timestamp': 450}, {'timestamp': 700}]}}}
        self.assertEqual([170, 420, 670], [row['start'] for row in focus_windows(evidence, result)])

    def test_single_frame_transition_requests_its_own_before_after_window(self):
        from module.resource_analysis.summary import build_narrative, build_narrative_synopsis
        evidence = Evidence('video', images=['a', 'b'], sources=['video_frames'],
                            sampling={'duration_seconds': 900, 'timestamps': [10, 710]})
        narrative = build_narrative({'version': 2, 'mode': 'activity', 'events': [
            {'importance': 'core', 'phase': 'activity', 'action': '参与者进行交谈', 'evidence_refs': ['frame_1']},
            {'importance': 'progression', 'phase': 'activity', 'action': '参与者停止交谈', 'evidence_refs': ['frame_2']},
        ]}, evidence, category='adult')
        result = build_narrative_synopsis([], evidence, narrative, category='adult')
        self.assertIn('transition_evidence_incomplete', result['summary_quality']['issues'])
        self.assertTrue(needs_focus(result))
        self.assertIn({'start': 680, 'end': 740, 'reason': 'transition_gap'}, focus_windows(evidence, result))
        self.assertEqual([], result['narrative']['events'], 'An unverified transition must still be rejected')

    def test_audio_supplement_uses_only_gaps_and_extends_speech_references(self):
        evidence = Evidence('video', images=['a', 'b'], text='speech_1: 旅客询问车票。',
                            audio_clips=[{'start': 0, 'end': 30, 'text': '旅客询问车票。'}],
                            sampling={'duration_seconds': 180, 'timestamps': [1, 179],
                                      'audio': {'windows': [{'start': 0, 'end': 30}, {'start': 150, 'end': 180}]}})
        with tempfile.TemporaryDirectory() as directory, \
             patch('module.resource_analysis.focus._run'), \
             patch('module.resource_analysis.focus._image_data', return_value='new'), \
             patch('module.resource_analysis.focus._speech_samples', return_value=(
                 [{'start': 60, 'end': 120, 'text': '工作人员建议查询购票记录。'}],
                 {'windows': [{'start': 60, 'end': 120}], 'attempted_seconds': 60, 'reliable_clips': 1})) as speech:
            enriched = supplement_evidence('video.mp4', directory, evidence, {}, transcribe=True)
        self.assertEqual(180, speech.call_args.args[2])
        self.assertTrue(all(30 <= row['start'] < row['end'] <= 150
                            for row in speech.call_args.kwargs['timeline_windows']))
        self.assertEqual(evidence.audio_clips[0], enriched.audio_clips[0])
        self.assertIn('speech_2: 工作人员建议查询购票记录。', enriched.text)
        self.assertIn('transcript', enriched.sources)

    def test_invalid_duration_cannot_create_speculative_timestamps(self):
        for duration in (0, -1, float('nan'), float('inf'), 'unknown', True):
            self.assertEqual([], focus_windows(Evidence('video', sampling={'duration_seconds': duration}), {}))

    def test_full_audio_is_not_retranscribed_and_old_evidence_is_immutable(self):
        evidence = Evidence('video', images=['a', 'b'], sampling={'duration_seconds': 180,
            'timestamps': [1, 179], 'timeline_windows': [{'start': 0, 'end': 30}, {'start': 150, 'end': 180}],
            'audio': {'windows': [{'start': 0, 'end': 180}]}},
            temporal_notes=[{'stale': True}])
        before = copy.deepcopy(evidence)
        with tempfile.TemporaryDirectory() as directory, \
             patch('module.resource_analysis.focus._run'), \
             patch('module.resource_analysis.focus._image_data', return_value='new'), \
             patch('module.resource_analysis.focus._speech_samples') as speech:
            enriched = supplement_evidence('video.mp4', directory, evidence, {}, transcribe=True)
        speech.assert_not_called()
        self.assertEqual(before, evidence)
        self.assertEqual([], enriched.temporal_notes)
        self.assertEqual(sorted(enriched.sampling['timestamps']), enriched.sampling['timestamps'])
        self.assertGreater(len(enriched.images), len(evidence.images))
        self.assertLessEqual(len(enriched.images) - len(evidence.images), 12)
        self.assertEqual(before.sampling['timeline_windows'], enriched.sampling['timeline_windows'],
                         'A focus window must not replace the actual end of the video')
        from module.resource_analysis.timeline import segment_requests
        focused, _ = segment_requests(enriched)[0]
        window = enriched.sampling['focus']['windows'][0]
        self.assertTrue(all(window['start'] <= timestamp <= window['end'] for timestamp in focused.sampling['timestamps']))

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_real_local_extraction_on_synthetic_media(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'generated.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=4', '-t', '4', '-c:v', 'mpeg4', str(path)],
                           check=True, capture_output=True, timeout=20)
            evidence = Evidence('video', sampling={'duration_seconds': 4, 'timestamps': []})
            result = supplement_evidence(path, directory, evidence, {})
        self.assertEqual(4, len(result.images))
        self.assertTrue(all(value.startswith('data:image/jpeg;base64,') for value in result.images))


class AutoFocusIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_new_evidence_no_blind_retry_and_no_recursive_pass(self):
        evidence = Evidence('video', images=['a'], sampling={'duration_seconds': 100, 'timestamps': [1]})
        review = {'status': 'complete', 'resource_kind': 'video', 'category': 'movie', 'subtype': '',
                  'diagnostics': {'attempts': []},
                  'summary_quality': {'status': 'review', 'issues': ['narrative_evidence_insufficient']}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'video.mp4'
            path.touch()
            for supplemented in (None, evidence):
                with patch('module.resource_analysis.service.extract_evidence', return_value=evidence), \
                     patch('module.resource_analysis.service._analyze', new=AsyncMock(return_value=copy.deepcopy(review))), \
                     patch('module.resource_analysis.service._refresh_video_summary', new=AsyncMock(return_value=copy.deepcopy(review))) as refresh, \
                     patch('module.resource_analysis.service.supplement_evidence', return_value=supplemented) as supplement:
                    await analyze_resource(str(path), completion=AsyncMock(), analysis_depth='deep')
                self.assertEqual(1, supplement.call_count)
                self.assertEqual(int(supplemented is not None), refresh.call_count)

    async def test_production_entry_does_not_supplement_ready_refused_or_standard(self):
        evidence = Evidence('video', images=['a'], sampling={'duration_seconds': 100, 'timestamps': [1]})
        cases = [('deep', 'ready', []), ('deep', 'review', ['narrative_evidence_insufficient', 'model_refused']),
                 ('standard', 'review', ['narrative_evidence_insufficient'])]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'video.mp4'
            path.touch()
            for depth, quality, issues in cases:
                result = {'status': 'complete', 'resource_kind': 'video', 'category': 'movie', 'subtype': '',
                          'summary_quality': {'status': quality, 'issues': issues}}
                with patch('module.resource_analysis.service.extract_evidence', return_value=evidence), \
                     patch('module.resource_analysis.service._analyze', new=AsyncMock(return_value=result)), \
                     patch('module.resource_analysis.service.supplement_evidence') as supplement:
                    await analyze_resource(str(path), completion=AsyncMock(), analysis_depth=depth)
                supplement.assert_not_called()

    async def test_deep_review_acquires_new_evidence_before_summary_retry(self):
        evidence = Evidence('video', images=['a', 'b'], sampling={
            'duration_seconds': 900, 'timestamps': [10, 890],
            'audio': {'windows': [{'start': 0, 'end': 60}]}})
        review = {'status': 'complete', 'resource_kind': 'video', 'category': 'movie',
                  'subtype': '', 'summary': 'old', 'diagnostics': {'attempts': []},
                  'summary_quality': {'status': 'review', 'issues': ['narrative_evidence_insufficient']}}
        ready = copy.deepcopy(review)
        ready['summary_quality'] = {'status': 'ready', 'issues': []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'video.mp4'
            path.touch()
            with patch('module.resource_analysis.service.extract_evidence', return_value=evidence), \
                 patch('module.resource_analysis.service._analyze', new=AsyncMock(return_value=review)), \
                 patch('module.resource_analysis.service._refresh_video_summary', new=AsyncMock(return_value=ready)) as refresh, \
                 patch('module.resource_analysis.service.supplement_evidence', create=True, return_value=evidence) as supplement:
                result = await analyze_resource(str(path), completion=AsyncMock(), analysis_depth='deep')
        self.assertEqual(1, supplement.call_count, 'Review must trigger new local evidence, not another prompt on identical frames')
        self.assertEqual(1, refresh.call_count)
        self.assertEqual('ready', result['summary_quality']['status'])
