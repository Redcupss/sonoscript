import numpy as np
import parselmouth
import psola


def pitch_shift(audio, sample_rate, semitones):
    """Shift audio's pitch by semitones (+up, -down) without changing its duration or
    touching unvoiced sounds (consonants, breath) that don't have a real pitch to shift.

    Uses TD-PSOLA via Praat (the psola/parselmouth packages) rather than a general-purpose
    time-stretch-and-resample approach: PSOLA operates pitch-synchronously (on the actual
    detected glottal pulse periods, not a fixed analysis-frame grid), which is what makes it
    sound like a natural pitch change instead of the "chorus/ensemble" artifact a phase
    vocoder produces on speech, or the loss of the voice's own timbre a naive pitch-and-
    formant shift produces. Compared several approaches (hand-rolled phase vocoder with
    formant correction, Spotify's pedalboard/Rubber Band) against real speech by ear before
    settling on this one.

    audio: 1D float array, any sample rate. semitones: float, 0 = no change.
    Returns a same-length float32 array."""
    if semitones == 0:
        return audio.astype(np.float32)
    ratio = 2.0 ** (semitones / 12.0)
    sound = parselmouth.Sound(audio.astype(np.float64), sampling_frequency=sample_rate)
    pitch = sound.to_pitch()
    f0 = pitch.selected_array["frequency"]
    # Unvoiced frames are already 0 in Praat's pitch object — scaling them by `ratio` keeps
    # them at exactly 0, which psola.vocode treats as "no pitch target here," leaving
    # consonants/breath sounds untouched rather than pitch-shifting them too.
    target_pitch = f0 * ratio
    shifted = psola.vocode(audio.astype(np.float64), int(sample_rate), target_pitch=target_pitch)
    return shifted.astype(np.float32)


def time_stretch(audio, sample_rate, speed):
    """Change audio's playback speed by `speed` (>1 faster, <1 slower) without changing its
    pitch — for Chatterbox specifically, which (unlike every other provider here) has no
    native speed parameter of its own.

    Uses Praat's Manipulation/DurationTier machinery (parselmouth.praat.call) — the same
    PSOLA engine already validated for pitch_shift above, just driving duration instead of
    pitch, and confirmed by direct listening tests across the full 0.5x-1.5x range before
    being wired in: no new dependency, no new packaging risk, no new license question. This
    is the same "extend the existing tool rather than add a new one" approach the research
    into pyrubberband/librosa/audiotsm alternatives specifically pointed toward — those all
    either required bundling a separate native binary, carried a real commercial-licensing
    cost, or were unmaintained pure-Python ports never tested past Python 3.6.

    audio: 1D float array, any sample rate. speed: float, 1.0 = no change.
    Returns a float32 array of a different length (shorter if speed > 1)."""
    if speed == 1.0:
        return audio.astype(np.float32)
    sound = parselmouth.Sound(audio.astype(np.float64), sampling_frequency=sample_rate)
    manipulation = parselmouth.praat.call(sound, "To Manipulation", 0.01, 75, 600)
    duration_tier = parselmouth.praat.call(manipulation, "Create DurationTier", "stretch", 0, sound.duration)
    # A DurationTier point is the new/old duration RATIO, the inverse of speed (a 1.25x
    # speedup plays each stretch of audio in 1/1.25 = 0.8 of its original time).
    ratio = 1.0 / speed
    parselmouth.praat.call(duration_tier, "Add point", 0, ratio)
    parselmouth.praat.call(duration_tier, "Add point", sound.duration, ratio)
    parselmouth.praat.call([manipulation, duration_tier], "Replace duration tier")
    result = parselmouth.praat.call(manipulation, "Get resynthesis (overlap-add)")
    return result.values[0].astype(np.float32)
