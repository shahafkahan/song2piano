from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import fitz  # PyMuPDF
import librosa
from music21 import (
    chord,
    clef,
    duration as m21_duration,
    instrument as m21_instrument,
    key as m21_key,
    layout,
    metadata,
    meter,
    note,
    pitch as m21_pitch,
    stream,
    tempo,
    tie,
)
import numpy as np
import pretty_midi
import verovio

QUANT_GRID_BEATS: dict[str, tuple[float, ...]] = {
    "1/4": (1.0,),
    "1/8": (0.5,),
    "1/16": (0.25,),
    "1/32": (0.125,),
    "triplet": (0.25, 1.0 / 3.0),
}

CLEAN_DURATION_CANDIDATES: dict[str, tuple[float, ...]] = {
    "1/4": (1.0, 2.0, 3.0, 4.0),
    "1/8": (0.5, 1.0, 1.5, 2.0, 3.0, 4.0),
    "1/16": (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0),
    "1/32": (0.125, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0),
    "triplet": (0.25, 1.0 / 3.0, 0.5, 2.0 / 3.0, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0),
}

INSTRUMENT_MIDI_RANGE: dict[str, tuple[int, int]] = {
    "piano": (21, 108),
    "guitar": (40, 88),
    "vocals": (48, 88),
    "bass": (28, 67),
    "strings": (36, 96),
    "other": (24, 104),
}

# Krumhansl-Schmuckler tonal profiles (pitch classes C..B)
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_PC_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", _SVG_NS)
ET.register_namespace("xlink", _XLINK_NS)


@dataclass(frozen=True)
class EngravingResult:
    musicxml_path: Path
    pdf_path: Path
    quantized_midi_path: Path
    notes_json_path: Path
    detected_bpm: float
    detected_key: str
    time_signature: str
    resolved_quantization: str
    note_count: int
    measure_count: int


@dataclass
class BeatGrid:
    bpm: float
    beat_times: np.ndarray
    beat_indices: np.ndarray

    def sec_to_beat(self, sec: float) -> float:
        return float(np.interp(sec, self.beat_times, self.beat_indices))

    def beat_to_sec(self, beat: float) -> float:
        return float(np.interp(beat, self.beat_indices, self.beat_times))

    def shift_phase(self, delta_beats: float) -> None:
        self.beat_indices = self.beat_indices + delta_beats


def extract_beat_grid(audio_path: Path, requested_bpm: float | None) -> BeatGrid:
    """Build a smooth, monotonic beat-to-second time-warp grid covering the full audio."""
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=600.0)
    duration = float(len(y)) / sr if len(y) > 0 else 4.0
    if len(y) < sr:
        bpm = round(float(requested_bpm), 1) if requested_bpm else 120.0
        spb = 60.0 / bpm
        idx = np.arange(0.0, max(16.0, duration / spb + 16.0), 1.0)
        return BeatGrid(bpm=bpm, beat_times=idx * spb, beat_indices=idx)

    start_bpm = float(requested_bpm) if (requested_bpm and 30 <= requested_bpm <= 260) else 110.0
    detected_tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, start_bpm=start_bpm, trim=False)
    raw_bpm = float(np.atleast_1d(detected_tempo)[0])
    if not np.isfinite(raw_bpm) or raw_bpm < 40.0:
        raw_bpm = 120.0
    while raw_bpm < 55.0:
        raw_bpm *= 2.0
    while raw_bpm > 185.0:
        raw_bpm /= 2.0

    bpm = round(float(requested_bpm), 1) if (requested_bpm and 30 <= requested_bpm <= 260) else round(raw_bpm, 1)
    spb = 60.0 / bpm

    raw_beats = librosa.frames_to_time(beat_frames, sr=sr) if len(beat_frames) else np.array([], dtype=np.float64)

    # Use even onset-anchored grid when requested_bpm is explicit or beat_track found too few beats
    if (requested_bpm is not None and 30 <= requested_bpm <= 260) or len(raw_beats) < 4:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True)
        anchor = float(librosa.frames_to_time(onset_frames[0], sr=sr)) if len(onset_frames) else 0.0
        anchor_mod = anchor % spb
        times = np.arange(anchor_mod - 2.0 * spb, duration + 8.0 * spb, spb)
        # Ensure beat_times[0] <= 0.0
        if times[0] > 0.0:
            times = np.insert(times, 0, times[0] - spb)
        indices = np.arange(len(times), dtype=np.float64)
        # Shift so beat 0 is the anchor closest to 0.0 (>= -spb/2)
        zero_idx = int(np.argmin(np.abs(times - anchor_mod)))
        indices = indices - zero_idx
        return BeatGrid(bpm=bpm, beat_times=times, beat_indices=indices)

    # Smooth outlier intervals in tracked beats
    diffs = np.diff(raw_beats)
    valid_diffs = diffs[(diffs > 0.20) & (diffs < 2.2)]
    med_dt = float(np.median(valid_diffs)) if len(valid_diffs) else spb

    # Align raw_beats granularity to clamped bpm (e.g. if beat_track returned half-time or double-time frames)
    if med_dt > 1.5 * spb:
        subdivided: list[float] = []
        for idx in range(len(raw_beats) - 1):
            subdivided.append(float(raw_beats[idx]))
            subdivided.append(0.5 * float(raw_beats[idx] + raw_beats[idx + 1]))
        subdivided.append(float(raw_beats[-1]))
        raw_beats = np.array(subdivided, dtype=np.float64)
        med_dt *= 0.5
    elif med_dt < 0.7 * spb and len(raw_beats) >= 6:
        raw_beats = raw_beats[::2]
        med_dt *= 2.0

    cleaned: list[float] = [float(raw_beats[0])]
    for t in raw_beats[1:]:
        dt = float(t) - cleaned[-1]
        if dt < 0.65 * med_dt:
            # Skip double-time spurious beat
            continue
        if dt > 1.45 * med_dt:
            # Fill missing beat(s) evenly
            n_missing = max(1, int(round(dt / med_dt)))
            step = dt / n_missing
            for k in range(1, n_missing + 1):
                cleaned.append(cleaned[-1] + step)
        else:
            cleaned.append(float(t))

    # Extrapolate backwards to <= 0.0 using local median period
    head_dt = float(np.median(np.diff(cleaned[:6]))) if len(cleaned) >= 3 else med_dt
    head_dt = float(np.clip(head_dt, 0.25, 1.5))
    prepended: list[float] = []
    cur = cleaned[0]
    while cur > 0.0:
        cur -= head_dt
        prepended.append(cur)
    prepended.reverse()

    # Extrapolate forwards past duration + 8s
    tail_dt = float(np.median(np.diff(cleaned[-6:]))) if len(cleaned) >= 3 else med_dt
    tail_dt = float(np.clip(tail_dt, 0.25, 1.5))
    appended: list[float] = []
    cur = cleaned[-1]
    while cur < duration + 8.0:
        cur += tail_dt
        appended.append(cur)

    all_times = np.array(prepended + cleaned + appended, dtype=np.float64)
    all_indices = np.arange(len(all_times), dtype=np.float64) - len(prepended)
    return BeatGrid(bpm=bpm, beat_times=all_times, beat_indices=all_indices)


def _clean_raw_midi(
    raw_midi_path: Path,
    instrument: str,
    strict_piano_denoise: bool = False,
) -> list[pretty_midi.Note]:
    """Filter ghost notes, extend sustain-pedaled notes, merge re-triggers, and cluster chord rolls."""
    pm = pretty_midi.PrettyMIDI(str(raw_midi_path))
    low_pitch, high_pitch = INSTRUMENT_MIDI_RANGE.get(instrument, (21, 108))

    notes: list[pretty_midi.Note] = []
    pedal_ccs: list[pretty_midi.ControlChange] = []
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            p = int(max(low_pitch, min(high_pitch, n.pitch)))
            notes.append(pretty_midi.Note(velocity=int(n.velocity), pitch=p, start=float(n.start), end=float(n.end)))
        for cc in inst.control_changes:
            if cc.number == 64:
                pedal_ccs.append(cc)

    if not notes:
        return []

    notes.sort(key=lambda n: (n.start, n.pitch))

    # 1. Extend note endings with CC64 sustain pedal intervals (piano)
    if instrument == "piano" and pedal_ccs:
        pedal_ccs.sort(key=lambda c: c.time)
        pedal_intervals: list[tuple[float, float]] = []
        down_t: float | None = None
        for cc in pedal_ccs:
            if cc.value >= 64 and down_t is None:
                down_t = float(cc.time)
            elif cc.value < 64 and down_t is not None:
                if float(cc.time) > down_t:
                    pedal_intervals.append((down_t, float(cc.time)))
                down_t = None
        if down_t is not None:
            pedal_intervals.append((down_t, max(n.end for n in notes) + 0.5))

        if pedal_intervals:
            next_same_pitch: dict[int, list[float]] = defaultdict(list)
            for n in notes:
                next_same_pitch[n.pitch].append(n.start)
            for p_list in next_same_pitch.values():
                p_list.sort()

            for n in notes:
                for p_start, p_end in pedal_intervals:
                    if p_start - 0.05 <= n.end <= p_end:
                        target_end = min(p_end, n.start + 3.0)
                        # Cap before next onset of the same pitch
                        same_starts = next_same_pitch[n.pitch]
                        for s in same_starts:
                            if s > n.start + 0.04:
                                target_end = min(target_end, s - 0.03)
                                break
                        if target_end > n.end:
                            n.end = target_end
                        break

    # 2. Adaptive velocity, micro-note, and high-register hiss gate
    med_vel = float(np.median([n.velocity for n in notes]))
    min_vel = max(25, int(med_vel * 0.38)) if strict_piano_denoise else max(22, int(med_vel * 0.34))
    min_dur = 0.060
    filtered: list[pretty_midi.Note] = []
    for n in notes:
        dur = n.end - n.start
        if dur < min_dur:
            continue
        if n.velocity < min_vel:
            continue
        if dur < 0.11 and n.velocity < int(med_vel * 0.55):
            continue
        if instrument == "piano" and n.pitch >= 90 and (dur < 0.18 or n.velocity < int(med_vel * 0.80)):
            continue
        filtered.append(n)

    if not filtered:
        return []

    # 3. Merge immediate overlapping / decay double-triggers (<55ms), preserving staccato 16th repeats
    by_pitch: dict[int, list[pretty_midi.Note]] = defaultdict(list)
    for n in filtered:
        by_pitch[n.pitch].append(n)

    retrig_gap = 0.055
    retrig_vel_ratio = 0.68
    merged: list[pretty_midi.Note] = []
    for p_notes in by_pitch.values():
        p_notes.sort(key=lambda x: x.start)
        stack: list[pretty_midi.Note] = []
        for n in p_notes:
            if stack:
                prev = stack[-1]
                gap = n.start - prev.end
                if gap <= retrig_gap and (n.velocity < prev.velocity * retrig_vel_ratio or gap < 0.0):
                    prev.end = max(prev.end, n.end)
                    prev.velocity = max(prev.velocity, n.velocity)
                    continue
            stack.append(n)
        merged.extend(stack)

    # 4. Short octave/12th ghosts + upper-register harmonic overtone suppression
    merged.sort(key=lambda n: (n.start, -n.velocity))
    keep = [True] * len(merged)
    left = 0
    for i, ni in enumerate(merged):
        while left < i and (ni.start - merged[left].start) > 0.042:
            left += 1
        dur_i = ni.end - ni.start
        neighbors: list[pretty_midi.Note] = []
        for j in range(left, len(merged)):
            if j == i or not keep[j]:
                continue
            nj = merged[j]
            if nj.start - ni.start > 0.042:
                break
            neighbors.append(nj)

        # Short ghost notes within ±35ms
        if dur_i < 0.18:
            is_short_ghost = any(
                abs(ni.start - nj.start) <= 0.035
                and (ni.pitch - nj.pitch) in (12, -12, 19)
                and ni.velocity < 0.52 * nj.velocity
                for nj in neighbors
            )
            if is_short_ghost:
                keep[i] = False
                continue

        # Piano upper-register harmonic overtones (e.g. A5+A6, B5+B6, C6+C7)
        if instrument == "piano" and ni.pitch >= 84:
            drop_overtone = False
            if ni.pitch >= 88:
                lower_pitches = {nj.pitch for nj in neighbors}
                for nj in neighbors:
                    diff = ni.pitch - nj.pitch
                    if diff == 12 and ni.velocity <= nj.velocity * 1.25:
                        drop_overtone = True
                        break
                    if diff == 24 and (ni.velocity <= nj.velocity * 1.25 or (ni.pitch - 36) in lower_pitches):
                        drop_overtone = True
                        break
                    if diff == 19 and ni.velocity <= nj.velocity * 1.15:
                        drop_overtone = True
                        break
            else:
                # C6..D#6 (84..87): drop 3-octave harmonic stack when both -12 and -24 sound louder
                oct1 = [nj for nj in neighbors if nj.pitch == ni.pitch - 12 and abs(nj.start - ni.start) <= 0.035]
                oct2 = [nj for nj in neighbors if nj.pitch == ni.pitch - 24 and abs(nj.start - ni.start) <= 0.035]
                if oct1 and oct2:
                    max_lower_vel = max(nj.velocity for nj in (*oct1, *oct2))
                    if ni.velocity < max_lower_vel * 0.90:
                        drop_overtone = True
            if drop_overtone:
                keep[i] = False

    deghosted = [n for idx, n in enumerate(merged) if keep[idx]]
    deghosted.sort(key=lambda n: (n.start, n.pitch))

    # 5. Chord roll clustering (30ms window -> semitone bleed suppression + velocity-weighted onset)
    clusters: list[list[pretty_midi.Note]] = []
    for n in deghosted:
        if not clusters or (n.start - clusters[-1][0].start) > 0.030:
            clusters.append([n])
        else:
            clusters[-1].append(n)

    final_notes: list[pretty_midi.Note] = []
    for cl in clusters:
        # Suppress 1-semitone bleed inside a chord onset cluster (keep stronger/longer note)
        by_strength = sorted(cl, key=lambda c: (-c.velocity, -(c.end - c.start)))
        kept_cl: list[pretty_midi.Note] = []
        for cand in by_strength:
            clash = any(
                abs(cand.pitch - k.pitch) == 1
                and (cand.velocity < k.velocity * 0.88 or (cand.end - cand.start) < 0.24)
                for k in kept_cl
            )
            if not clash:
                kept_cl.append(cand)

        weights = np.array([max(1, c.velocity) for c in kept_cl], dtype=np.float64)
        avg_start = float(np.sum([c.start * w for c, w in zip(kept_cl, weights)]) / np.sum(weights))
        for c in kept_cl:
            c.start = avg_start
            if c.end <= c.start + 0.06:
                c.end = c.start + 0.06
        final_notes.extend(kept_cl)

    final_notes.sort(key=lambda n: (n.start, n.pitch))
    return final_notes


def _snap_beat(beat_val: float, grid_steps: tuple[float, ...]) -> float:
    best = round(beat_val / grid_steps[0]) * grid_steps[0]
    best_dist = abs(beat_val - best)
    for step in grid_steps[1:]:
        cand = round(beat_val / step) * step
        dist = abs(beat_val - cand)
        if dist < best_dist:
            best = cand
            best_dist = dist
    return round(max(0.0, best), 4)


def _snap_clean_duration(dur_beats: float, quantization: str) -> float:
    cands = CLEAN_DURATION_CANDIDATES.get(quantization, CLEAN_DURATION_CANDIDATES["1/8"])
    best = min(cands, key=lambda c: abs(dur_beats - c))
    return round(best, 4)


def _detect_meter_and_align_phase(
    raw_notes: list[pretty_midi.Note],
    grid: BeatGrid,
    requested_time_signature: str,
) -> tuple[str, float]:
    """Align sub-beat phase, detect 3/4 vs 4/4 when 'auto', and pad pickup bars so chord downbeats land on beat 1."""
    req = (requested_time_signature or "auto").strip().lower()
    if not raw_notes:
        resolved = "4/4" if req == "auto" else req
        ts_obj = meter.TimeSignature(resolved)
        return resolved, float(ts_obj.barDuration.quarterLength or 4.0)

    # 1. Sub-beat phase alignment using median fractional offset of prominent notes
    med_vel = float(np.median([n.velocity for n in raw_notes]))
    prominent = [n for n in raw_notes if n.velocity >= 0.60 * med_vel] or raw_notes
    frac_offsets: list[float] = []
    for n in prominent[:40]:
        b = grid.sec_to_beat(n.start)
        diff = b - round(b)
        reps = 2 if n.pitch <= 55 else 1
        frac_offsets.extend([diff] * reps)
    if frac_offsets:
        sub_shift = float(np.median(frac_offsets))
        if abs(sub_shift) > 0.02:
            grid.shift_phase(-sub_shift)

    # 2. Integer-beat weight map for meter & downbeat phase scoring
    beat_weights: dict[int, float] = defaultdict(float)
    beat_span = max(grid.sec_to_beat(n.end) for n in raw_notes) - min(grid.sec_to_beat(n.start) for n in raw_notes)
    for n in raw_notes:
        b = grid.sec_to_beat(n.start)
        bi = int(round(b))
        if abs(b - bi) <= 0.30:
            dur_b = max(0.25, grid.sec_to_beat(n.end) - b)
            bass_bonus = 2.8 if n.pitch <= 53 else 1.0
            beat_weights[bi] += (n.velocity / 127.0) * min(dur_b, 3.0) * bass_bonus

    total_w = sum(beat_weights.values()) or 1.0

    def best_phase_for(blen: int) -> tuple[float, int]:
        best_score = -1.0
        best_p = 0
        for p in range(blen):
            dw = sum(w for bi, w in beat_weights.items() if (bi - p) % blen == 0)
            score = (dw / total_w) * blen
            if score > best_score:
                best_score = score
                best_p = p
        return best_score, best_p

    if req in ("2/4", "3/4", "4/4", "6/8"):
        resolved_ts = req
        ts_obj = meter.TimeSignature(resolved_ts)
        bar_len_float = float(ts_obj.barDuration.quarterLength or 4.0)
        bar_int = max(2, int(round(bar_len_float)))
        _, best_phase = best_phase_for(bar_int)
    else:
        s3, p3 = best_phase_for(3)
        s4, p4 = best_phase_for(4)
        if beat_span >= 6.0 and s3 > s4 * 1.15:
            resolved_ts, bar_int, bar_len_float, best_phase = "3/4", 3, 3.0, p3
        else:
            resolved_ts, bar_int, bar_len_float, best_phase = "4/4", 4, 4.0, p4

    # 3. Ensure earliest note >= 0 and shift by pickup padding so downbeats sit on beat 0 mod bar_int
    earliest_beat = min(grid.sec_to_beat(n.start) for n in raw_notes)
    if earliest_beat < -0.25:
        shift_bars = int(np.ceil(-earliest_beat / bar_int)) * bar_int
        grid.shift_phase(shift_bars)

    pad_beats = (bar_int - (best_phase % bar_int)) % bar_int
    if pad_beats > 0:
        grid.shift_phase(float(pad_beats))

    # Avoid an entire leading silent measure if first note sits >= bar_int
    new_earliest = min(round(grid.sec_to_beat(n.start)) for n in raw_notes)
    if new_earliest >= bar_int:
        drop_bars = int(new_earliest // bar_int) * bar_int
        grid.shift_phase(-float(drop_bars))

    return resolved_ts, bar_len_float


def _resolve_quantization(
    cleaned_notes: list[pretty_midi.Note],
    grid: BeatGrid,
    requested_quantization: str,
) -> str:
    """Resolve 'auto' quantization to '1/16' for fast 16th-note runs or '1/8' for ballads."""
    req = (requested_quantization or "auto").strip().lower()
    if req != "auto" and req in QUANT_GRID_BEATS:
        return req
    if not cleaned_notes:
        return "1/8"

    ordered = sorted(cleaned_notes, key=lambda n: n.start)
    cluster_starts: list[float] = []
    for n in ordered:
        if not cluster_starts or (n.start - cluster_starts[-1]) > 0.032:
            cluster_starts.append(n.start)
    if len(cluster_starts) < 5:
        return "1/8"

    beats = np.array([grid.sec_to_beat(t) for t in cluster_starts], dtype=np.float64)
    diffs = np.diff(beats)
    diffs = diffs[(diffs >= 0.08) & (diffs <= 2.5)]
    if len(diffs) < 4:
        return "1/8"

    p50_beats = float(np.median(diffs))
    frac_16th = float(np.mean((diffs >= 0.14) & (diffs <= 0.34)))
    if p50_beats < 0.35 or frac_16th >= 0.35:
        return "1/16"
    return "1/8"


def _cap_hand_voicing(group: list[dict], staff: str) -> list[dict]:
    """Keep chord voicings playable within a comfortable pianist hand span and note cap."""
    if len(group) <= 1:
        return group
    if staff == "treble":
        top_pitch = max(n["midi"] for n in group)
        in_span = [n for n in group if top_pitch - n["midi"] <= 12]
        if len(in_span) <= 4:
            return in_span
        top_n = max(in_span, key=lambda n: n["midi"])
        bot_n = min(in_span, key=lambda n: n["midi"])
        mid = [n for n in in_span if n is not top_n and n is not bot_n]
        mid.sort(key=lambda n: (-n["velocity"], -n["midi"]))
        return sorted([bot_n, *mid[:2], top_n], key=lambda n: n["midi"])
    else:
        bot_pitch = min(n["midi"] for n in group)
        in_span = [n for n in group if n["midi"] - bot_pitch <= 14]
        if len(in_span) <= 3:
            return in_span
        bot_n = min(in_span, key=lambda n: n["midi"])
        rest = [n for n in in_span if n is not bot_n]
        rest.sort(key=lambda n: (-n["velocity"], n["midi"]))
        return sorted([bot_n, *rest[:2]], key=lambda n: n["midi"])


def _split_piano_hands_and_cap(quantized: list[dict], cap: bool = True) -> list[dict]:
    """Assign treble/bass staves dynamically based on hand reachability and optionally cap voicings."""
    by_onset: dict[float, list[dict]] = defaultdict(list)
    for item in quantized:
        by_onset[item["start_beat"]].append(item)

    voiced_out: list[dict] = []
    for _, group in sorted(by_onset.items()):
        lh_notes: list[dict] = []
        rh_notes: list[dict] = []
        middle: list[dict] = []

        for item in group:
            p = item["midi"]
            if p <= 55:
                item["staff"] = "bass"
                lh_notes.append(item)
            elif p >= 67:
                item["staff"] = "treble"
                rh_notes.append(item)
            else:
                middle.append(item)

        middle.sort(key=lambda x: x["midi"])
        for item in middle:
            p = item["midi"]
            lh_min = min((x["midi"] for x in lh_notes), default=None)
            rh_max = max((x["midi"] for x in rh_notes), default=None)
            if rh_max is not None and (rh_max - p) > 12:
                if lh_min is None or (p - lh_min) <= 14 or p < 62:
                    item["staff"] = "bass"
                    lh_notes.append(item)
                else:
                    item["staff"] = "treble"
                    rh_notes.append(item)
            elif lh_min is not None and (p - lh_min) <= 12 and (rh_max is None or (rh_max - p) > 9):
                item["staff"] = "bass"
                lh_notes.append(item)
            elif p <= 57 and lh_min is None and (rh_max is not None and rh_max >= 69):
                item["staff"] = "bass"
                lh_notes.append(item)
            else:
                item["staff"] = "treble"
                rh_notes.append(item)

        if cap:
            voiced_out.extend(_cap_hand_voicing(lh_notes, "bass"))
            voiced_out.extend(_cap_hand_voicing(rh_notes, "treble"))
        else:
            voiced_out.extend(lh_notes)
            voiced_out.extend(rh_notes)

    voiced_out.sort(key=lambda x: (x["start_beat"], x["midi"]))
    return voiced_out


def _quantize_and_split_notes(
    cleaned_notes: list[pretty_midi.Note],
    grid: BeatGrid,
    instrument: str,
    quantization: str,
    requested_time_signature: str,
) -> tuple[list[dict], str, float]:
    resolved_ts, bar_len = _detect_meter_and_align_phase(cleaned_notes, grid, requested_time_signature)
    if not cleaned_notes:
        return [], resolved_ts, bar_len

    quantized = _quantize_onto_grid(
        cleaned_notes,
        grid,
        quantization,
        staff_default="bass" if instrument == "bass" else "treble",
        role=None,
    )

    if instrument == "vocals" and quantized:
        by_onset: dict[float, list[dict]] = defaultdict(list)
        for item in quantized:
            by_onset[item["start_beat"]].append(item)
        quantized = [
            max(group, key=lambda x: (x["velocity"], x["midi"]))
            for _, group in sorted(by_onset.items())
        ]
    elif instrument == "piano" and quantized:
        quantized = _split_piano_hands_and_cap(quantized, cap=True)

    return quantized, resolved_ts, bar_len


def _detect_key_ks(notes: list[dict], requested_key: str | None) -> m21_key.Key:
    if requested_key:
        tokens = requested_key.strip().split()
        try:
            if len(tokens) >= 2:
                return m21_key.Key(tokens[0], tokens[1].lower())
            return m21_key.Key(tokens[0])
        except Exception:
            pass

    if not notes:
        return m21_key.Key("C", "major")

    pc_weights = np.zeros(12, dtype=np.float64)
    for n in notes:
        w = float(n["duration_beats"]) * (float(n["velocity"]) / 127.0)
        # Slightly emphasize bass root notes
        if n["midi"] < 55:
            w *= 1.35
        pc_weights[n["midi"] % 12] += w

    if np.sum(pc_weights) <= 0:
        return m21_key.Key("C", "major")

    best_corr = -1e9
    best_pc = 0
    best_mode = "major"

    for shift in range(12):
        rot = np.roll(pc_weights, -shift)
        c_maj = float(np.corrcoef(rot, _KS_MAJOR)[0, 1])
        c_min = float(np.corrcoef(rot, _KS_MINOR)[0, 1])
        if c_maj > best_corr:
            best_corr = c_maj
            best_pc = shift
            best_mode = "major"
        if c_min > best_corr:
            best_corr = c_min
            best_pc = shift
            best_mode = "minor"

    return m21_key.Key(_PC_NAMES[best_pc], best_mode)


def _write_quantized_midi(notes: list[dict], bpm: float, out_path: Path, instrument: str) -> None:
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    program_map = {
        "piano": 0,
        "guitar": 24,
        "vocals": 52,
        "bass": 33,
        "strings": 48,
        "other": 0,
    }
    inst = pretty_midi.Instrument(program=program_map.get(instrument, 0), name=instrument.title())
    for item in notes:
        inst.notes.append(
            pretty_midi.Note(
                velocity=item["velocity"],
                pitch=item["midi"],
                start=item["start_sec"],
                end=max(item["start_sec"] + 0.05, item["end_sec"]),
            )
        )
    pm.instruments.append(inst)
    pm.write(str(out_path))


def _clean_vocal_melody(
    vocal_midi_path: Path,
    vocals_wav_path: Path | None = None,
    other_wav_path: Path | None = None,
) -> list[pretty_midi.Note]:
    """Extract a clean monophonic singing melody line and gate bleed during instrumental passages."""
    pm = pretty_midi.PrettyMIDI(str(vocal_midi_path))
    raw = [
        pretty_midi.Note(
            velocity=int(n.velocity),
            pitch=int(max(45, min(88, n.pitch))),
            start=float(n.start),
            end=float(n.end),
        )
        for inst in pm.instruments
        if not inst.is_drum
        for n in inst.notes
    ]
    if not raw:
        return []

    # Vocal-bleed energy gate using isolated Demucs vocals vs accompaniment WAVs
    if vocals_wav_path is not None and vocals_wav_path.is_file():
        v_y, v_sr = librosa.load(str(vocals_wav_path), sr=22050, mono=True, duration=600.0)
        hop = 512
        v_rms = librosa.feature.rms(y=v_y, hop_length=hop)[0] if len(v_y) else np.array([], dtype=np.float32)
        if len(v_rms) == 0:
            return []
        v_p98 = float(np.percentile(v_rms, 98))
        if v_p98 < 0.008:
            return []

        o_rms: np.ndarray | None = None
        if other_wav_path is not None and other_wav_path.is_file():
            o_y, _ = librosa.load(str(other_wav_path), sr=22050, mono=True, duration=600.0)
            if len(o_y):
                o_rms = librosa.feature.rms(y=o_y, hop_length=hop)[0]

        gated_raw: list[pretty_midi.Note] = []
        n_frames = len(v_rms)
        for n in raw:
            i0 = max(0, min(n_frames - 1, int(n.start * v_sr / hop)))
            i1 = max(i0 + 1, min(n_frames, int(np.ceil(n.end * v_sr / hop))))
            mean_v = float(np.mean(v_rms[i0:i1]))
            if mean_v < 0.14 * v_p98:
                continue
            if o_rms is not None and len(o_rms) >= i1:
                mean_o = float(np.mean(o_rms[i0:i1]))
                if mean_v < 0.22 * mean_o:
                    continue
            gated_raw.append(n)
        raw = gated_raw
        if not raw:
            return []

    med_vel = float(np.median([n.velocity for n in raw]))
    min_vel = max(42, int(med_vel * 0.50))
    filtered = [
        n for n in raw
        if (n.end - n.start) >= 0.085 and n.velocity >= min_vel
    ]
    if not filtered:
        return []

    filtered.sort(key=lambda n: (n.start, -(n.velocity * np.sqrt(max(0.05, n.end - n.start)))))
    mono: list[pretty_midi.Note] = []
    for n in filtered:
        if not mono:
            mono.append(n)
            continue
        prev = mono[-1]
        if n.start < prev.end:
            prev_score = prev.velocity * float(np.sqrt(max(0.05, prev.end - prev.start)))
            cur_score = n.velocity * float(np.sqrt(max(0.05, n.end - n.start)))
            if n.pitch == prev.pitch and (n.start - prev.end) > -0.05:
                prev.end = max(prev.end, n.end)
                prev.velocity = max(prev.velocity, n.velocity)
            elif n.start > prev.start + 0.06 and cur_score >= prev_score * 0.68:
                prev.end = n.start
                if (prev.end - prev.start) < 0.085:
                    mono[-1] = n
                else:
                    mono.append(n)
            elif cur_score > prev_score * 1.15:
                mono[-1] = n
            continue

        # Merge rapid vibrato splits of the exact same pitch
        gap = n.start - prev.end
        if n.pitch == prev.pitch and gap < 0.045 and n.velocity < prev.velocity * 0.78:
            prev.end = max(prev.end, n.end)
            prev.velocity = max(prev.velocity, n.velocity)
            continue

        mono.append(n)

    if not mono:
        return []

    # Lift male / low alto singing melodies (+1 octave) so they sit cleanly above RH accompaniment chords
    med_pitch = float(np.median([n.pitch for n in mono]))
    if med_pitch < 63.0:
        for n in mono:
            n.pitch = int(min(88, n.pitch + 12))

    return mono


def _clean_bass_stem(bass_midi_path: Path) -> list[pretty_midi.Note]:
    """Extract clean monophonic bass roots from an isolated bass stem."""
    pm = pretty_midi.PrettyMIDI(str(bass_midi_path))
    raw = [
        pretty_midi.Note(
            velocity=int(n.velocity),
            pitch=int(max(28, min(55, n.pitch))),
            start=float(n.start),
            end=float(n.end),
        )
        for inst in pm.instruments
        if not inst.is_drum
        for n in inst.notes
    ]
    if not raw:
        return []

    med_vel = float(np.median([n.velocity for n in raw]))
    min_vel = max(36, int(med_vel * 0.50))
    filtered = [n for n in raw if (n.end - n.start) >= 0.12 and n.velocity >= min_vel]
    if not filtered:
        return []

    filtered.sort(key=lambda n: (n.start, n.pitch))
    mono: list[pretty_midi.Note] = []
    for n in filtered:
        if mono and n.start < mono[-1].end:
            prev = mono[-1]
            if n.start > prev.start + 0.08 and n.velocity >= prev.velocity * 0.75:
                prev.end = n.start
                if (prev.end - prev.start) < 0.10:
                    mono[-1] = n
                else:
                    mono.append(n)
            elif n.velocity > prev.velocity * 1.2:
                mono[-1] = n
            continue
        mono.append(n)
    return mono


def _quantize_onto_grid(
    notes_list: list[pretty_midi.Note],
    grid: BeatGrid,
    quantization: str,
    staff_default: str,
    role: str | None,
    boost_melody_vel: bool = False,
) -> list[dict]:
    grid_steps = QUANT_GRID_BEATS.get(quantization, (0.5,))
    best_by_key: dict[tuple[float, int], dict] = {}

    for n in notes_list:
        b_start = grid.sec_to_beat(n.start)
        b_end = grid.sec_to_beat(n.end)
        if b_start < -0.25:
            continue
        start_beat = _snap_beat(max(0.0, b_start), grid_steps)
        dur_beat = _snap_clean_duration(max(0.05, b_end - b_start), quantization)

        vel = int(max(24, min(127, n.velocity)))
        if boost_melody_vel:
            vel = min(122, max(92, vel + 18))

        s_sec = max(0.0, float(n.start))
        e_sec = max(s_sec + 0.06, float(n.end))

        entry: dict = {
            "midi": int(n.pitch),
            "pitch_name": pretty_midi.note_number_to_name(int(n.pitch)),
            "start_beat": start_beat,
            "duration_beats": dur_beat,
            "start_sec": round(s_sec, 4),
            "end_sec": round(e_sec, 4),
            "velocity": vel,
            "staff": staff_default,
        }
        if role is not None:
            entry["role"] = role

        dedup_key = (round(start_beat, 3), int(n.pitch))
        prev = best_by_key.get(dedup_key)
        if prev is None or (vel, e_sec - s_sec) > (prev["velocity"], prev["end_sec"] - prev["start_sec"]):
            best_by_key[dedup_key] = entry

    out = list(best_by_key.values())
    out.sort(key=lambda x: (x["start_beat"], x["midi"]))
    return out


def _merge_piano_cover_notes(
    accomp_quantized: list[dict],
    vocal_quantized: list[dict],
    bass_quantized: list[dict],
) -> list[dict]:
    """Arrange vocal melody on top of RH chords and bass/LH accompaniment underneath."""
    _split_piano_hands_and_cap(accomp_quantized, cap=False)

    # 1. Treble: combine vocal melody + accompaniment treble with melody-on-top voicing rule
    mel_by_onset: dict[float, list[dict]] = defaultdict(list)
    for v in vocal_quantized:
        mel_by_onset[v["start_beat"]].append(v)

    acc_rh_by_onset: dict[float, list[dict]] = defaultdict(list)
    for a in accomp_quantized:
        if a["staff"] == "treble":
            acc_rh_by_onset[a["start_beat"]].append(a)

    all_rh_onsets = sorted(set(mel_by_onset.keys()) | set(acc_rh_by_onset.keys()))
    rh_final: list[dict] = []

    for onset in all_rh_onsets:
        mels = mel_by_onset.get(onset, [])
        accs = acc_rh_by_onset.get(onset, [])

        # Determine active melody ceiling pitch (either starting here or sustaining across onset)
        active_mel_pitch: int | None = None
        if mels:
            top_mel = max(mels, key=lambda x: x["midi"])
            active_mel_pitch = top_mel["midi"]
        else:
            for v in vocal_quantized:
                if v["start_beat"] < onset < (v["start_beat"] + v["duration_beats"] - 1e-4):
                    active_mel_pitch = v["midi"]
                    break

        if active_mel_pitch is not None:
            below_mel = [
                a for a in accs
                if a["midi"] <= active_mel_pitch - 2 and (active_mel_pitch - a["midi"]) <= 12
            ]
            below_mel.sort(key=lambda a: (-a["velocity"], a["midi"]))
            kept_acc = below_mel[:3]
            if mels:
                top_mel = max(mels, key=lambda x: x["midi"])
                rh_final.append(top_mel)
            rh_final.extend(kept_acc)
        else:
            rh_final.extend(_cap_hand_voicing(accs, "treble"))

    # 2. Bass: merge accompaniment LH + isolated bass stem (dedup per onset+pitch)
    bass_by_onset: dict[float, dict[int, dict]] = defaultdict(dict)
    for item in [*bass_quantized, *[a for a in accomp_quantized if a["staff"] == "bass"]]:
        onset = item["start_beat"]
        p = item["midi"]
        if p not in bass_by_onset[onset] or item["velocity"] > bass_by_onset[onset][p]["velocity"]:
            bass_by_onset[onset][p] = item

    lh_final: list[dict] = []
    for onset in sorted(bass_by_onset.keys()):
        lh_final.extend(_cap_hand_voicing(list(bass_by_onset[onset].values()), "bass"))

    combined = [*lh_final, *rh_final]
    combined.sort(key=lambda x: (x["start_beat"], x["midi"]))
    return combined


def _make_m21_duration(dur_beats: float) -> m21_duration.Duration:
    # Handle exact triplets cleanly via Fraction
    frac_approx = Fraction(dur_beats).limit_denominator(24)
    if abs(float(frac_approx) - dur_beats) < 0.006:
        try:
            d = m21_duration.Duration(quarterLength=frac_approx)
            if d.quarterLength > 0:
                return d
        except Exception:
            pass
    try:
        d = m21_duration.Duration(quarterLength=round(dur_beats, 4))
        if d.quarterLength > 0:
            return d
    except Exception:
        pass
    return m21_duration.Duration(quarterLength=0.25)


_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def _spell_pitch(midi_num: int, prefer_flats: bool) -> m21_pitch.Pitch:
    octave = (midi_num // 12) - 1
    name = (_FLAT_NAMES if prefer_flats else _SHARP_NAMES)[midi_num % 12]
    return m21_pitch.Pitch(f"{name}{octave}")


def _decompose_rest_span(offset_in_bar: float, span: float) -> list[tuple[float, float]]:
    """Break a rest gap inside a measure into clean beat-aligned musical rest durations."""
    out: list[tuple[float, float]] = []
    pos = round(offset_in_bar, 4)
    rem = round(span, 4)

    while rem > 1e-4:
        # Align to next integer beat boundary first if currently fractional
        frac = round(pos % 1.0, 4)
        if frac > 1e-4:
            to_next_beat = round(1.0 - frac, 4)
            step = min(rem, to_next_beat)
            # Break sub-beat into standard binary parts (0.5, 0.25, 0.125)
            for cand in (0.5, 0.25, 0.125):
                if step >= cand - 1e-4:
                    step = cand
                    break
            out.append((pos, step))
            pos = round(pos + step, 4)
            rem = round(rem - step, 4)
            continue

        # At an integer beat: prefer whole-bar / half / quarter rests aligned to beat boundaries
        chosen = None
        for cand in (4.0, 2.0, 1.0, 0.5, 0.25, 0.125):
            if rem >= cand - 1e-4:
                # Keep 2-beat rest aligned on beat 0 or beat 2 in 4/4
                if cand == 2.0 and round(pos % 2.0, 4) > 1e-4:
                    continue
                chosen = cand
                break
        if chosen is None:
            chosen = rem
        out.append((pos, chosen))
        pos = round(pos + chosen, 4)
        rem = round(rem - chosen, 4)

    return out


def _build_bar_slices(
    items: list[dict],
    total_measures: int,
    bar_len: float,
    min_step: float,
) -> dict[int, list[tuple[float, float, list[int], int, str | None]]]:
    """Convert note items into per-measure non-overlapping slices split cleanly across barlines."""
    by_onset: dict[float, list[dict]] = defaultdict(list)
    for it in items:
        by_onset[it["start_beat"]].append(it)

    sorted_onsets = sorted(by_onset.keys())
    events: list[tuple[float, float, list[int], int]] = []

    for idx, onset in enumerate(sorted_onsets):
        group = sorted(by_onset[onset], key=lambda x: x["midi"])
        next_onset = sorted_onsets[idx + 1] if idx + 1 < len(sorted_onsets) else (total_measures * bar_len)
        max_dur = max(min_step, next_onset - onset)
        raw_dur = float(np.median([g["duration_beats"] for g in group]))
        dur = min(raw_dur, max_dur)
        dur = max(min_step, round(dur / min_step) * min_step)
        dur = round(min(dur, max_dur), 4)
        if dur <= 1e-4:
            continue
        pitches = sorted({g["midi"] for g in group})
        avg_vel = int(np.mean([g["velocity"] for g in group]))
        events.append((round(onset, 4), dur, pitches, avg_vel))

    bar_slices: dict[int, list[tuple[float, float, list[int], int, str | None]]] = defaultdict(list)
    for onset, dur, pitches, vel in events:
        rem = dur
        cur_beat = onset
        segments: list[tuple[int, float, float]] = []
        while rem > 1e-4:
            bar_idx = int(cur_beat // bar_len)
            if bar_idx >= total_measures:
                break
            bar_end = (bar_idx + 1) * bar_len
            slice_dur = round(min(rem, bar_end - cur_beat), 4)
            if slice_dur <= 1e-4:
                cur_beat = bar_end
                continue
            segments.append((bar_idx, round(cur_beat - bar_idx * bar_len, 4), slice_dur))
            cur_beat = round(cur_beat + slice_dur, 4)
            rem = round(rem - slice_dur, 4)

        for seg_idx, (bar_idx, offset_in_bar, slice_dur) in enumerate(segments):
            if len(segments) == 1:
                tie_type = None
            elif seg_idx == 0:
                tie_type = "start"
            elif seg_idx == len(segments) - 1:
                tie_type = "stop"
            else:
                tie_type = "continue"
            bar_slices[bar_idx].append((offset_in_bar, slice_dur, pitches, vel, tie_type))

    return bar_slices


def _populate_voice_in_measure(
    container: stream.Stream,
    slices: list[tuple[float, float, list[int], int, str | None]],
    bar_len: float,
    prefer_flats: bool,
    stem_direction: str | None = None,
) -> None:
    """Populate a Measure or Voice with exact bar-length aligned notes, chords, rests, and ties."""
    ordered = sorted(slices, key=lambda x: x[0])
    if not ordered:
        container.insert(0.0, note.Rest(duration=_make_m21_duration(bar_len)))
        return

    cursor = 0.0
    for offset_in_bar, slice_dur, pitches, vel, tie_type in ordered:
        if offset_in_bar > cursor + 1e-4:
            for r_off, r_dur in _decompose_rest_span(cursor, offset_in_bar - cursor):
                container.insert(r_off, note.Rest(duration=_make_m21_duration(r_dur)))
            cursor = offset_in_bar

        dur_obj = _make_m21_duration(slice_dur)
        if len(pitches) == 1:
            el: note.NotRest = note.Note(_spell_pitch(pitches[0], prefer_flats), duration=dur_obj)
        else:
            el = chord.Chord([_spell_pitch(p, prefer_flats) for p in pitches], duration=dur_obj)
        el.volume.velocity = vel
        if stem_direction is not None:
            el.stemDirection = stem_direction
        if tie_type is not None:
            el.tie = tie.Tie(tie_type)
        container.insert(offset_in_bar, el)
        cursor = round(offset_in_bar + slice_dur, 4)

    if cursor < bar_len - 1e-4:
        for r_off, r_dur in _decompose_rest_span(cursor, bar_len - cursor):
            container.insert(r_off, note.Rest(duration=_make_m21_duration(r_dur)))


def _build_part_measures(
    part: stream.Part,
    items: list[dict],
    total_measures: int,
    bar_len: float,
    min_step: float,
    clef_obj: clef.Clef,
    key_obj: m21_key.Key,
    time_sig_str: str,
    tempo_obj: tempo.MetronomeMark | None,
    split_melody_voice: bool = False,
) -> None:
    """Populate a Part with explicit, exact-length Measures split cleanly across barlines."""
    prefer_flats = key_obj.sharps < 0

    if split_melody_voice:
        mel_items = [it for it in items if it.get("role") == "melody"]
        acc_items = [it for it in items if it.get("role") != "melody"]
        mel_slices = _build_bar_slices(mel_items, total_measures, bar_len, min_step)
        acc_slices = _build_bar_slices(acc_items, total_measures, bar_len, min_step)
        single_slices: dict[int, list[tuple[float, float, list[int], int, str | None]]] = {}
    else:
        mel_slices = {}
        acc_slices = {}
        single_slices = _build_bar_slices(items, total_measures, bar_len, min_step)

    for bar_idx in range(total_measures):
        m = stream.Measure(number=bar_idx + 1)
        if bar_idx == 0:
            m.insert(0.0, clef_obj)
            m.insert(0.0, m21_key.KeySignature(key_obj.sharps))
            m.insert(0.0, meter.TimeSignature(time_sig_str))
            if tempo_obj is not None:
                m.insert(0.0, tempo_obj)

        if split_melody_voice:
            m_sl = mel_slices.get(bar_idx, [])
            a_sl = acc_slices.get(bar_idx, [])
            if m_sl and a_sl:
                v1 = stream.Voice(id="1")
                _populate_voice_in_measure(v1, m_sl, bar_len, prefer_flats, stem_direction="up")
                try:
                    v1.makeBeams(inPlace=True)
                except Exception:
                    pass
                v2 = stream.Voice(id="2")
                _populate_voice_in_measure(v2, a_sl, bar_len, prefer_flats, stem_direction="down")
                try:
                    v2.makeBeams(inPlace=True)
                except Exception:
                    pass
                m.insert(0.0, v1)
                m.insert(0.0, v2)
            elif m_sl or a_sl:
                _populate_voice_in_measure(m, m_sl or a_sl, bar_len, prefer_flats)
                try:
                    m.makeBeams(inPlace=True)
                except Exception:
                    pass
            else:
                m.insert(0.0, note.Rest(duration=_make_m21_duration(bar_len)))
        else:
            sl = single_slices.get(bar_idx, [])
            _populate_voice_in_measure(m, sl, bar_len, prefer_flats)
            if sl:
                try:
                    m.makeBeams(inPlace=True)
                except Exception:
                    pass

        part.append(m)

    try:
        part.makeAccidentals(
            inPlace=True,
            cautionaryPitchClass=False,
            cautionaryAll=False,
        )
    except Exception:
        pass


def _build_music21_score(
    notes: list[dict],
    title: str,
    bpm: float,
    time_signature: str,
    instrument: str,
    quantization: str,
    requested_key: str | None,
    is_piano_cover: bool = False,
) -> tuple[stream.Score, str, int]:
    score = stream.Score()
    score.metadata = metadata.Metadata(title=title, composer="Transcribed by SongScribe")

    grid_steps = QUANT_GRID_BEATS.get(quantization, (0.5,))
    min_step = min(grid_steps)

    ts_obj = meter.TimeSignature(time_signature)
    bar_len = float(ts_obj.barDuration.quarterLength or 4.0)
    tempo_obj = tempo.MetronomeMark(number=round(bpm))

    key_obj = _detect_key_ks(notes, requested_key)
    detected_key_label = f"{key_obj.tonic.name} {key_obj.mode}"

    max_end_beat = max((n["start_beat"] + n["duration_beats"] for n in notes), default=bar_len)
    total_measures = max(1, int(np.ceil(max_end_beat / bar_len)))

    if instrument == "piano":
        treble_part = stream.PartStaff(id="P1")
        treble_part.partName = "Piano"
        treble_part.partAbbreviation = "Pno."
        treble_part.insert(0.0, m21_instrument.Piano())

        bass_part = stream.PartStaff(id="P2")
        bass_part.partName = ""
        bass_part.partAbbreviation = ""
        bass_part.insert(0.0, m21_instrument.Piano())

        treble_notes = [n for n in notes if n["staff"] == "treble"]
        bass_notes = [n for n in notes if n["staff"] == "bass"]

        _build_part_measures(
            treble_part, treble_notes, total_measures, bar_len, min_step,
            clef.TrebleClef(), key_obj, time_signature, tempo_obj,
            split_melody_voice=is_piano_cover,
        )
        _build_part_measures(
            bass_part, bass_notes, total_measures, bar_len, min_step,
            clef.BassClef(), key_obj, time_signature, None,
        )

        score.insert(0.0, treble_part)
        score.insert(0.0, bass_part)
        score.insert(
            0.0,
            layout.StaffGroup(
                [treble_part, bass_part],
                name="Piano",
                symbol="brace",
                barTogether=True,
            ),
        )
    else:
        part = stream.Part(id="P1")
        inst_map = {
            "guitar": (m21_instrument.AcousticGuitar(), clef.Treble8vbClef(), "Guitar"),
            "vocals": (m21_instrument.Vocalist(), clef.TrebleClef(), "Vocals"),
            "bass": (m21_instrument.ElectricBass(), clef.BassClef(), "Bass"),
            "strings": (m21_instrument.Violin(), clef.TrebleClef(), "Strings"),
            "other": (m21_instrument.Instrument(), clef.TrebleClef(), "Lead"),
        }
        m21_inst, m21_clef, pname = inst_map.get(
            instrument, (m21_instrument.Instrument(), clef.TrebleClef(), instrument.title())
        )
        part.partName = pname
        part.insert(0.0, m21_inst)
        _build_part_measures(
            part, notes, total_measures, bar_len, min_step,
            m21_clef, key_obj, time_signature, tempo_obj,
        )
        score.insert(0.0, part)

    return score, detected_key_label, total_measures


_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_LTR_RUN_RE = re.compile(r"[A-Za-z0-9]+(?:[ ._-][A-Za-z0-9]+)*")


def _visual_rtl(text: str) -> str:
    """Convert logical RTL Hebrew/mixed text into visual LTR order for non-BiDi SVG renderers (MuPDF)."""
    if not _HEBREW_RE.search(text):
        return text
    # Temporarily mask LTR runs (e.g. 'mp3cut.net', 'Piano Only') so they stay left-to-right
    ltr_tokens: list[str] = []

    def _mask(m: re.Match[str]) -> str:
        idx = len(ltr_tokens)
        ltr_tokens.append(m.group(0))
        return f"\uE000{idx}\uE001"

    masked = _LTR_RUN_RE.sub(_mask, text)
    rev = masked[::-1].replace("(", "\uE002").replace(")", "(").replace("\uE002", ")")
    # Restore masked placeholders (reversed placeholder delimiters)
    def _unmask(m: re.Match[str]) -> str:
        idx = int(m.group(1)[::-1])
        return ltr_tokens[idx]

    return re.sub(r"\uE001([0-9]+)\uE000", _unmask, rev)


def _normalize_verovio_svg(svg_str: str) -> bytes:
    """Promote Verovio's nested definition-scale viewBox onto root <svg>, clean placeholders & RTL text."""
    root = ET.fromstring(svg_str)
    for elem in list(root):
        if elem.tag == f"{{{_SVG_NS}}}svg" and "viewBox" in elem.attrib:
            root.attrib["viewBox"] = elem.attrib.pop("viewBox")
            elem.tag = f"{{{_SVG_NS}}}g"
            elem.attrib.setdefault("fill", "#000000")
            break

    # Remove Verovio section milestone placeholders (floating dots)
    for parent in root.iter():
        for child in list(parent):
            cls = child.attrib.get("class", "")
            if "placeholder" in cls:
                parent.remove(child)

    for el in root.iter():
        if "stroke-width" in el.attrib and "stroke" not in el.attrib:
            el.attrib["stroke"] = "#000000"
        if el.tag == f"{{{_SVG_NS}}}tspan" and el.text:
            if "\uECA5" in el.text:
                el.text = el.text.replace("\uECA5", "Tempo")
                if el.attrib.get("font-family") == "Leipzig":
                    el.attrib["font-family"] = "Times, serif"
                    el.attrib["font-size"] = "405px"
            elif _HEBREW_RE.search(el.text):
                el.text = _visual_rtl(el.text)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _engrave_pdf_from_musicxml(musicxml_path: Path, pdf_path: Path) -> None:
    tk = verovio.toolkit()
    tk.setOptions(
        {
            "pageWidth": 2100,
            "pageHeight": 2970,
            "scale": 38,
            "adjustPageHeight": False,
            "breaks": "auto",
            "footer": "none",
            "header": "auto",
            "pageMarginTop": 100,
            "pageMarginBottom": 100,
            "pageMarginLeft": 100,
            "pageMarginRight": 100,
        }
    )
    loaded = tk.loadFile(str(musicxml_path))
    if not loaded:
        raise RuntimeError("Verovio failed to parse generated MusicXML.")

    page_count = tk.getPageCount()
    if page_count < 1:
        raise RuntimeError("Verovio produced zero pages from MusicXML.")

    merged_pdf = fitz.open()
    try:
        for page_idx in range(1, page_count + 1):
            svg_bytes = _normalize_verovio_svg(tk.renderToSVG(page_idx))
            svg_doc = fitz.open(stream=svg_bytes, filetype="svg")
            try:
                page_pdf_bytes = svg_doc.convert_to_pdf()
            finally:
                svg_doc.close()

            page_pdf_doc = fitz.open(stream=page_pdf_bytes, filetype="pdf")
            try:
                merged_pdf.insert_pdf(page_pdf_doc)
            finally:
                page_pdf_doc.close()

        merged_pdf.save(str(pdf_path), deflate=True)
    finally:
        merged_pdf.close()


def quantize_and_engrave(
    raw_midi_path: Path,
    source_audio_path: Path,
    work_dir: Path,
    title: str,
    instrument: str,
    quantization: str,
    requested_bpm: float | None,
    time_signature: str,
    requested_key: str | None,
    strict_piano_denoise: bool = False,
    vocal_midi_path: Path | None = None,
    bass_midi_path: Path | None = None,
    vocals_wav_path: Path | None = None,
    other_wav_path: Path | None = None,
    beat_source_audio_path: Path | None = None,
    progress_cb: Callable[[int, str], None] | None = None,
) -> EngravingResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    if progress_cb:
        progress_cb(80, "Tracking beat grid, cleaning MIDI ghosts, and quantizing...")

    grid = extract_beat_grid(beat_source_audio_path or source_audio_path, requested_bpm)
    is_piano_cover = vocal_midi_path is not None or bass_midi_path is not None

    if is_piano_cover:
        accomp_raw = _clean_raw_midi(
            raw_midi_path,
            "piano",
            strict_piano_denoise=strict_piano_denoise,
        ) if raw_midi_path.is_file() else []
        vocal_raw = (
            _clean_vocal_melody(
                vocal_midi_path,
                vocals_wav_path=vocals_wav_path,
                other_wav_path=other_wav_path,
            )
            if (vocal_midi_path and vocal_midi_path.is_file())
            else []
        )
        bass_raw = _clean_bass_stem(bass_midi_path) if (bass_midi_path and bass_midi_path.is_file()) else []

        meter_seed = [*accomp_raw, *bass_raw] or vocal_raw
        resolved_ts, _bar_len = _detect_meter_and_align_phase(meter_seed, grid, time_signature)
        quant_seed = [*accomp_raw, *vocal_raw, *bass_raw]
        resolved_quant = _resolve_quantization(quant_seed, grid, quantization)

        accomp_q = _quantize_onto_grid(accomp_raw, grid, resolved_quant, "treble", "accompaniment")
        vocal_q = _quantize_onto_grid(vocal_raw, grid, resolved_quant, "treble", "melody", boost_melody_vel=True)
        bass_q = _quantize_onto_grid(bass_raw, grid, resolved_quant, "bass", "bass")
        notes = _merge_piano_cover_notes(accomp_q, vocal_q, bass_q)
    else:
        cleaned_notes = _clean_raw_midi(
            raw_midi_path,
            instrument,
            strict_piano_denoise=strict_piano_denoise,
        )
        resolved_quant = _resolve_quantization(cleaned_notes, grid, quantization)
        notes, resolved_ts, _bar_len = _quantize_and_split_notes(
            cleaned_notes=cleaned_notes,
            grid=grid,
            instrument=instrument,
            quantization=resolved_quant,
            requested_time_signature=time_signature,
        )

    quantized_midi_path = work_dir / "quantized.mid"
    _write_quantized_midi(notes, grid.bpm, quantized_midi_path, instrument)

    if progress_cb:
        progress_cb(
            86,
            f"Engraving MusicXML score ({len(notes)} notes, {resolved_ts}, {resolved_quant} at {grid.bpm} BPM)...",
        )

    score, detected_key, measure_count = _build_music21_score(
        notes=notes,
        title=title,
        bpm=grid.bpm,
        time_signature=resolved_ts,
        instrument=instrument,
        quantization=resolved_quant,
        requested_key=requested_key,
        is_piano_cover=is_piano_cover,
    )

    musicxml_path = work_dir / "score.musicxml"
    score.write("musicxml", fp=str(musicxml_path))

    notes_json_path = work_dir / "notes.json"
    notes_payload = {
        "bpm": grid.bpm,
        "key_signature": detected_key,
        "time_signature": resolved_ts,
        "instrument": instrument,
        "quantization": resolved_quant,
        "note_count": len(notes),
        "measure_count": measure_count,
        "beat_times": [round(float(t), 4) for t in grid.beat_times],
        "beat_indices": [round(float(b), 4) for b in grid.beat_indices],
        "notes": notes,
    }
    notes_json_path.write_text(json.dumps(notes_payload), encoding="utf-8")

    if progress_cb:
        progress_cb(93, "Rendering vector PDF sheet music via Verovio...")

    pdf_path = work_dir / "score.pdf"
    _engrave_pdf_from_musicxml(musicxml_path, pdf_path)

    return EngravingResult(
        musicxml_path=musicxml_path,
        pdf_path=pdf_path,
        quantized_midi_path=quantized_midi_path,
        notes_json_path=notes_json_path,
        detected_bpm=grid.bpm,
        detected_key=detected_key,
        time_signature=resolved_ts,
        resolved_quantization=resolved_quant,
        note_count=len(notes),
        measure_count=measure_count,
    )
