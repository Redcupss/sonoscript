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
