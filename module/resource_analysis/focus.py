"""Bounded second-pass acquisition, selected from local coverage, not a title."""
import copy
import math
import os
import shutil
import subprocess

from .evidence import MAX_TEXT, VIDEO_FRAME_FILTER, _image_data, _run, _speech_samples
from .dialogue import without_unintelligible_speech, select_speech


FOCUS_ISSUES = frozenset({
    'primary_activity_mismatch', 'narrative_evidence_insufficient',
    'narrative_events_missing', 'narrative_progression_missing',
    'scene_only_event', 'category_only_summary', 'low_information',
    'transition_evidence_incomplete',
})


def needs_focus(result):
    quality = result.get('summary_quality', {})
    issues = set(quality.get('issues', []))
    # A content gap is not permission to retry a refusal, unsafe output, or an
    # API error. Only known evidence/quality failures qualify.
    allowed = FOCUS_ISSUES | {'sentence_count', 'missing_evidence'}
    attempts = result.get('diagnostics', {}).get('attempts', [])
    transient = {'model_timeout', 'model_connection', 'model_server', 'model_gateway_timeout', 'model_rate_limit'}
    for index, row in enumerate(attempts):
        if row.get('status') != 'failed':
            continue
        recovered = (row.get('reason') in transient and any(
            later.get('stage') == row.get('stage') and later.get('status') == 'success'
            for later in attempts[index + 1:]))
        if not recovered:
            return False
    return (quality.get('status') == 'review' and bool(issues & FOCUS_ISSUES)
            and issues <= allowed)


def focus_windows(evidence, result):
    """Up to three disjoint 60-second windows; no user timestamps required.

    Conflict observations take priority, then uncovered audio, then the largest
    visual gaps. Previously attempted ASR is not mistaken for unvisited audio.
    """
    duration = evidence.sampling.get('duration_seconds', 0)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return []
    candidates = []
    if 'transition_evidence_incomplete' in result.get('summary_quality', {}).get('issues', []):
        for gap in result.get('narrative', {}).get('evidence_gaps', []):
            for timestamp in gap.get('timestamps', []):
                if type(timestamp) in (int, float) and math.isfinite(timestamp) and 0 <= timestamp <= duration:
                    candidates.append((timestamp, 'transition_gap'))
    if 'primary_activity_mismatch' in result.get('summary_quality', {}).get('issues', []):
        for note in result.get('diagnostics', {}).get('primary_activity', {}).get('observations', []):
            timestamp = note.get('timestamp')
            if type(timestamp) in (int, float) and math.isfinite(timestamp) and 0 <= timestamp <= duration:
                candidates.append((timestamp, 'activity_conflict'))
    covered = sorted((max(0, row['start']), min(duration, row['end']))
                     for row in evidence.sampling.get('audio', {}).get('windows', [])
                     if all(type(row.get(key)) in (int, float) and math.isfinite(row[key]) for key in ('start', 'end'))
                     and row['end'] > row['start'])
    cursor, gaps = 0, []
    for start, end in covered + [(duration, duration)]:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    candidates.extend(((start + end) / 2, 'audio_gap') for start, end in
                      sorted(gaps, key=lambda pair: (-(pair[1] - pair[0]), pair[0])))
    times = sorted({0, duration, *(value for value in evidence.sampling.get('timestamps', [])
                                 if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= duration)})
    candidates.extend(((start + end) / 2, 'visual_gap') for start, end in
                      sorted(zip(times, times[1:]), key=lambda pair: (-(pair[1] - pair[0]), pair[0])))
    selected = []
    for center, reason in candidates:
        start = max(0, min(duration - min(60, duration), center - 30))
        end = min(duration, start + 60)
        if any(start < row['end'] and end > row['start'] for row in selected):
            continue
        selected.append({'start': round(start, 3), 'end': round(end, 3), 'reason': reason})
        if len(selected) == 3:
            break
    return sorted(selected, key=lambda row: row['start'])


def speech_search_windows(evidence, budget=600):
    """Search unvisited audio, independently of the three visual focus windows.

    At most 16 bounded windows and 600 seconds; retain short gaps when they fit,
    and distribute the budget rather than repeatedly choosing the largest gaps.
    """
    duration = evidence.sampling.get('duration_seconds', 0)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return []
    covered = sorted((max(0, row['start']), min(duration, row['end']))
        for row in evidence.sampling.get('audio', {}).get('windows', [])
        if all(type(row.get(key)) in (int, float) and math.isfinite(row[key]) for key in ('start', 'end'))
        and row['end'] > row['start'] and row['end'] > 0 and row['start'] < duration)
    cursor, gaps = 0, []
    for start, end in covered + [(duration, duration)]:
        if start - cursor >= 4:
            gaps.append((cursor, start, math.ceil((start - cursor) / 60)))
        cursor = max(cursor, end)
    count = sum(row[2] for row in gaps)
    if not count:
        return []
    selected = {round(i * (count - 1) / max(1, min(16, count) - 1)) for i in range(min(16, count))}
    windows, offset = [], 0
    for start, end, parts in gaps:
        for index in sorted(selected):
            if offset <= index < offset + parts:
                length = (end - start) / parts
                left = start + (index - offset) * length
                windows.append((left, min(end, left + length)))
        offset += parts
    remaining = min(600, max(0, budget))
    planned = []
    for index, (start, end) in enumerate(sorted(windows, key=lambda row: row[1] - row[0])):
        share = remaining / (len(windows) - index)
        if end - start <= share:
            planned.append({'start': start, 'end': end})
            remaining -= end - start
            continue
        seconds = math.floor(share * 1000) / 1000
        remaining -= seconds
        if seconds < 4:
            continue
        left = max(start, round((start + end - seconds) / 2, 3))
        planned.append({'start': left, 'end': min(end, round(left + seconds, 3))})
    return sorted(planned, key=lambda row: row['start'])


def supplement_evidence(path, temp_dir, evidence, result, *, transcribe=False):
    windows = focus_windows(evidence, result)
    if not windows:
        return None
    enriched = copy.deepcopy(evidence)
    dialogue = result.get('diagnostics', {}).get('dialogue_context', {})
    speech_unintelligible = dialogue.get('status') == 'accepted' and dialogue.get('reason') == 'unintelligible'
    speech_unsafe = dialogue.get('status') == 'accepted' and (dialogue.get('reason') == 'unsafe_or_sensitive'
        or any(row.get('reason') == 'unsafe_or_sensitive' for row in dialogue.get('clips', [])))
    precise_screening = dialogue.get('status') == 'accepted' and dialogue.get('version') == 2
    if precise_screening:
        enriched = select_speech(enriched, dialogue['kept_original_refs'])
    elif speech_unintelligible or speech_unsafe:
        enriched = without_unintelligible_speech(enriched, reason=dialogue['reason'])
    # This is a fresh summary evidence namespace: rebuild all references and
    # observations, never carry first-pass event IDs into the new pass.
    enriched.temporal_notes = []
    enriched.activity_notes = []
    enriched.output_schema = None
    times = list(enriched.sampling.get('timestamps', []))
    frames = list(zip(times, enriched.images))
    added = []
    for window_index, window in enumerate(windows):
        for index, fraction in enumerate((.1, .35, .65, .9)):
            timestamp = round(window['start'] + (window['end'] - window['start']) * fraction, 3)
            if any(abs(timestamp - prior) < .5 for prior in times + added):
                continue
            target = os.path.join(temp_dir, f'focus-{window_index}-{index}.jpg')
            try:
                _run([shutil.which('ffmpeg') or 'ffmpeg', '-y', '-ss', str(timestamp), '-i', str(path),
                      '-frames:v', '1', '-vf', VIDEO_FRAME_FILTER, '-threads', '1', target], timeout=10)
                frames.append((timestamp, _image_data(target)))
                added.append(timestamp)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
    frames.sort(key=lambda pair: pair[0])
    enriched.sampling['timestamps'] = [pair[0] for pair in frames]
    enriched.images = [pair[1] for pair in frames]
    # Read only previously unvisited audio portions; do not spend another full
    # budget retranscribing the same unreliable speech (e.g. a short clip).
    attempted = evidence.sampling.get('audio', {}).get('windows', [])
    audio_windows = []
    for window in windows:
        pieces = [(window['start'], window['end'])]
        for prior in attempted:
            next_pieces = []
            for start, end in pieces:
                if prior['end'] <= start or prior['start'] >= end:
                    next_pieces.append((start, end))
                else:
                    if start < prior['start']:
                        next_pieces.append((start, prior['start']))
                    if end > prior['end']:
                        next_pieces.append((prior['end'], end))
            pieces = next_pieces
        audio_windows.extend({'start': start, 'end': end} for start, end in pieces if end - start >= 4)
    audio = {'windows': [], 'attempted_seconds': 0, 'reliable_clips': 0}
    clips = []
    no_usable_speech = speech_unintelligible or (precise_screening and not dialogue['kept_original_refs'])
    if no_usable_speech:
        audio_windows = speech_search_windows(evidence)
    audio_budget = 600 if no_usable_speech else 180
    # Precise screening can examine genuinely unvisited intervals without ever
    # restoring/requerying rejected clips. Legacy aggregate exclusion stays shut.
    if transcribe and audio_windows and (not speech_unsafe or precise_screening):
        clips, audio = _speech_samples(path, temp_dir, audio_budget, evidence.sampling['duration_seconds'], timeline_windows=audio_windows)
        prior_clip_count = len(enriched.audio_clips)
        enriched.audio_clips.extend(clips)
        if clips:
            if 'transcript' not in enriched.sources:
                enriched.sources.append('transcript')
            # Keep original text (including subtitles, if separately authorized)
            # and its IDs intact. New clips get new IDs after existing clips.
            remaining = max(0, MAX_TEXT - len(enriched.text))
            for index, clip in enumerate(clips, prior_clip_count + 1):
                clip['text'] = clip['text'][:remaining]
                extra = f"\n\nspeech_{index}: {clip['text']}"
                enriched.text += extra
                remaining = max(0, remaining - len(extra))
    if not added and not clips:
        return None
    enriched.sampling['focus'] = {'windows': windows, 'added_frames': len(added), 'audio': audio,
                                  'reference_scope': 'rebuilt_for_second_pass'}
    # Keep the original timeline, particularly its real final window. Segment
    # reads use focus.windows, but ending checks must still inspect the ending.
    enriched.limitations.append('已自动补采样部分证据缺口；这些位置不是已确认剧情时间点，仍不能代表未采样部分。')
    return enriched
