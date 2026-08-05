"""Verifies generated speech actually says what it was supposed to, via a local Whisper
transcription pass — the timing-only heuristics elsewhere in this app (chars-per-second
bounds) can't catch a normal-paced clip that just says the wrong thing, since they never
check WHAT was said, only how long it took. Confirmed necessary directly: a Sesame clip that
passed the timing check still had a garbled word in it, and a live Chatterbox test this
session produced pure gibberish that would have sailed through the old check too.

Design follows two real, independently-maintained forks of Chatterbox-TTS-Extended — the
exact TTS model this app also uses for its free tier — that already solved this same problem
in production (github.com/petermg/Chatterbox-TTS-Extended and the hardened
github.com/x90skysn3k/Chatterbox-Pro fork). Several choices here exist specifically to avoid
bugs those forks shipped and later had to fix; noted inline where relevant.
"""
import math
import os

import numpy as np
import scipy.signal
import jiwer
from jiwer.transforms import (
    Compose, RemovePunctuation, ToLowerCase, RemoveMultipleSpaces, Strip, ReduceToListOfListOfWords,
)
from transformers.models.whisper.english_normalizer import EnglishTextNormalizer

import mlx_whisper

# Every real shipped inline-verification tool found in research defaults to "medium" or
# larger specifically for the accuracy-sensitive verification pass — "tiny" only ever shows
# up as the fast/low-accuracy end of a user-selectable range, never as anyone's default for
# this purpose. ".en" (English-only) rather than the multilingual variant, since this app's
# text is English — English-only Whisper checkpoints are measurably more accurate on English
# audio than their multilingual counterparts of the same size.
#
# Loaded from a BUNDLED local path, not a live HF repo-id — main.py sets HF_HUB_OFFLINE=1
# unconditionally at module load (needed to keep Sesame from ever making a real network call),
# which blocks any first-run download just as effectively for this model too. Unlike Sesame's
# much larger asset bundle, this ~137MB is small enough to ship directly rather than needing
# its own download-on-demand flow — and it has to be available for the free tier (Chatterbox
# verification), not gated behind a paid license the way Sesame's download is.
# Same RESOURCEPATH-vs-dev-mode fallback as main.py's own _resourcePath, reimplemented here
# since this module has no access to that PyObjC-bound method.
_RESOURCE_BASE = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
WHISPER_MODEL = os.path.join(_RESOURCE_BASE, "whisper_assets")

WHISPER_SAMPLE_RATE = 16000

# CER (character error rate), not WER — WER is noisy/quantized on the short, single-sentence
# clips this app generates (one wrong word in a 5-word sentence is already a 20%+ swing).
# 0.15 is corroborated two independent ways that arrived at the same number via unrelated
# methods: Coqui/XTTS community hallucination-detection practice uses CER>0.15 as its fail
# line, and two unrelated shipped Chatterbox forks independently converged on ~0.85 fuzzy-
# similarity (~0.15 error) using character-ratio matching instead of CER. Same threshold for
# every retry attempt, deliberately — one of those forks originally shipped a STRICTER
# threshold for retries (0.95 vs 0.85 initial), logged it as a bug, and unified both to one
# value; retries succeeding on a coin flip because the bar quietly moved isn't a real fix.
CER_PASS_THRESHOLD = 0.15

# Below this many words, this app's own short-input special-casing (see main.py) already
# routes text through a more lenient/retry-biased path — Chatterbox has a confirmed,
# unresolved upstream bug (resemble-ai/chatterbox#97) where very short inputs ("Hi!", "Yes",
# single words) reliably produce gibberish, and Whisper's own transcription accuracy is
# independently less stable on very short audio. Verification is weakest exactly where TTS
# is most likely to fail, so short clips get a distinct (not stricter) treatment below.
SHORT_INPUT_WORD_COUNT = 5

# A separate failure signal from CER — a clip can transcribe to roughly the right WORDS while
# still having an enormous dead zone in the middle, since a transcript is just text and
# doesn't carry timing. Confirmed directly and concretely: a real generated clip had a
# 25-SECOND silent gap between two words, both correctly recognized on either side of it, in
# a chunk whose overall CER wasn't flagged as the worst of that attempt — this is exactly the
# CSM/Chatterbox "unreliable end-of-speech detection" instability already documented
# elsewhere in this app, just manifesting as dead air mid-clip instead of a too-long tail.
# 4 seconds is well above any natural spoken pause (even a deliberate dramatic beat rarely
# runs past 1-2s) but well under the confirmed-bad 25s case, leaving headroom either way.
MAX_INTERNAL_GAP_SECONDS = 4.0

_normalizer = EnglishTextNormalizer({})


def normalize(text):
    return _normalizer(text)


# Same tokenization jiwer's own WER default uses (RemovePunctuation, ToLowerCase,
# RemoveMultipleSpaces, Strip, then split into words) — reused here for word ALIGNMENT rather
# than error-rate scoring. Chosen specifically (over the stricter EnglishTextNormalizer used
# for CER above) because it only cleans each token in place; it never merges or drops a token
# for looking "the same" the way number/contraction expansion would, which would silently
# shift every position after it and break the index correspondence _align_words depends on.
_ALIGN_TRANSFORM = Compose(
    [RemovePunctuation(), ToLowerCase(), RemoveMultipleSpaces(), Strip(), ReduceToListOfListOfWords()])
_PUNCT_ONLY = RemovePunctuation()


def _align_words(expected_text, whisper_words):
    """Maps Whisper's own transcribed words (real timestamps, from the audio that was actually
    generated) back onto expected_text's words by position — not full forced alignment (no
    phoneme model involved), but the same "which spoken word corresponds to which written
    word" problem, solved with jiwer's existing word-level edit-distance alignment (the same
    machinery already in this file for CER, just read for its alignment rather than its score).

    Returns a list shaped like main.py's System-voice word_timings (App has one already, built
    from live AVSpeechSynthesizer callbacks): [{"start": float, "text": str}, ...], in
    expected_text's own original word order/spelling — or None if the mapping isn't usable
    (normalization changed the reference's word count, or too few words landed a timestamp to
    make a smooth read-along track). Callers should treat None exactly like "no timing data" —
    the existing chunk-level fallback already handles that for every other provider.
    """
    # A token that's pure punctuation on its own — a standalone em dash between clauses, an
    # ellipsis, etc. (this app's own RECORD_SCRIPT_PRESETS text uses exactly this pattern) —
    # vanishes entirely under RemovePunctuation, which would silently shift every position
    # after it out of alignment if left in a position-indexed list. Filtered out up front,
    # using jiwer's own transform as the ground truth for "does this survive" rather than
    # guessing at a punctuation character set — confirmed directly that an em dash isn't in
    # Python's own ASCII-only string.punctuation, so that guess would have been wrong.
    raw_ref_words = [w for w in expected_text.split() if _PUNCT_ONLY([w])[0].strip()]
    if not raw_ref_words or not whisper_words:
        return None
    hyp_text = " ".join((w.get("word") or "").strip() for w in whisper_words)
    try:
        out = jiwer.process_words(
            expected_text, hyp_text, reference_transform=_ALIGN_TRANSFORM, hypothesis_transform=_ALIGN_TRANSFORM)
    except ValueError:
        return None  # jiwer raises on a reference/hypothesis that's empty after the transform
    # A token that's pure punctuation (e.g. a standalone "--") can vanish entirely under
    # RemovePunctuation, shifting every position after it — if either side's transformed word
    # count doesn't match its own raw split, positions no longer correspond 1:1 and nothing
    # downstream of this can be trusted; bail out to "no timing data" rather than mis-align.
    if len(out.references[0]) != len(raw_ref_words) or len(out.hypotheses[0]) != len(whisper_words):
        return None

    timings = [None] * len(raw_ref_words)
    for chunk in out.alignments[0]:
        if chunk.type == "insert":
            continue  # a Whisper word with no counterpart in the source text — ignore it
        ref_span = chunk.ref_end_idx - chunk.ref_start_idx
        hyp_span = chunk.hyp_end_idx - chunk.hyp_start_idx
        # "equal"/"substitute" blocks aren't guaranteed equal-length on both sides (e.g. one
        # ref word aligning to a two-word hyp block) — zip only as far as both sides go; a
        # "delete" block (hyp_span==0) naturally zips zero pairs, leaving those ref words
        # unfilled below rather than guessing a timestamp for a word Whisper never heard.
        for offset in range(min(ref_span, hyp_span)):
            hyp_word = whisper_words[chunk.hyp_start_idx + offset]
            timings[chunk.ref_start_idx + offset] = {
                "start": hyp_word.get("start", 0.0),
                "text": raw_ref_words[chunk.ref_start_idx + offset],
            }
    result = [t for t in timings if t is not None]
    # Below half the chunk's words landing a real timestamp would make the highlight jump
    # around more than it tracks — not an empirically-tuned cutoff, just the point where
    # falling back to the existing chunk-level scroll (still correct, just coarser) beats
    # showing a highlight that's wrong more often than it's right.
    if len(result) < len(raw_ref_words) * 0.5:
        return None
    return result


def _trim_silence(audio, sample_rate, frame_ms=20, energy_ratio=0.02):
    """Trims leading/trailing near-silence with a simple energy scan. Untrimmed silence is a
    confirmed, independent trigger for Whisper fabricating phantom text on nothing at all —
    reproduced directly: transcribing 1s of pure silence through this exact model returned
    the word "You" rather than an empty string. Feeding that noise to the real comparison
    would fail good audio for a reason that has nothing to do with what the TTS engine did."""
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    n_frames = len(audio) // frame_len
    if n_frames == 0:
        return audio
    frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    energy = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    peak = energy.max() if energy.size else 0.0
    if peak <= 0:
        return audio[:0]
    loud = energy > (peak * energy_ratio)
    if not loud.any():
        return audio[:0]
    first = int(np.argmax(loud))
    last = len(loud) - 1 - int(np.argmax(loud[::-1]))
    return audio[first * frame_len : min(len(audio), (last + 1) * frame_len)]


def _resample_to_16k(audio, sample_rate):
    if sample_rate == WHISPER_SAMPLE_RATE:
        return audio.astype(np.float32)
    # resample_poly (polyphase, FIR-filtered), not the FFT-based resample() — the latter
    # assumes a periodic signal and rings at the edges of non-periodic audio like speech.
    g = math.gcd(sample_rate, WHISPER_SAMPLE_RATE)
    up, down = WHISPER_SAMPLE_RATE // g, sample_rate // g
    return scipy.signal.resample_poly(audio, up, down).astype(np.float32)


class VerifyResult:
    __slots__ = ("passed", "cer", "transcript", "word_timings")

    def __init__(self, passed, cer, transcript, word_timings=None):
        self.passed = passed
        self.cer = cer
        self.transcript = transcript
        # See _align_words — None on every early-exit before a transcript/timestamps exist at
        # all; a real (possibly None, if alignment wasn't usable) value on every path after.
        self.word_timings = word_timings


def verify(audio, sample_rate, expected_text):
    """Transcribes `audio` (a numpy array at `sample_rate`, any dtype) and scores it against
    `expected_text`. Must be called from the same persistent MLX worker thread already used
    for TTS generation — mlx_whisper's own model cache has no internal locking and assumes
    the caller serializes access on one consistent thread; calling this from a different
    thread (e.g. via a thread pool) risks the exact "no Stream(gpu, N) in current thread"
    crash this app's Chatterbox integration already hit once before, for the same reason."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0 or not np.isfinite(audio).all():
        return VerifyResult(False, 1.0, "")

    trimmed = _trim_silence(audio, sample_rate)
    if len(trimmed) < sample_rate * 0.1:  # under ~100ms of real signal: dropout/silence
        return VerifyResult(False, 1.0, "")

    resampled = _resample_to_16k(trimmed, sample_rate)

    result = mlx_whisper.transcribe(
        resampled,
        path_or_hf_repo=WHISPER_MODEL,
        # Hallucination-suppression overrides — all targeted at Whisper fabricating text on
        # ambiguous/edge audio rather than reporting "nothing here." Lower no_speech_threshold
        # (default 0.6) makes it MORE willing to report no-speech instead of inventing
        # something; stricter logprob_threshold (default -1.0) rejects low-confidence guesses;
        # condition_on_previous_text=False stops one segment's hallucination from poisoning
        # the next, relevant for any clip long enough to span multiple internal segments.
        no_speech_threshold=0.2,
        logprob_threshold=-0.5,
        compression_ratio_threshold=2.4,
        condition_on_previous_text=False,
        word_timestamps=True,
        verbose=False,
    )
    transcript = (result.get("text") or "").strip()
    segments = result.get("segments") or []

    # Cheap early-exit checks before spending time on the full comparison. These signals are
    # a known-weak classifier on their OWN (one study found a classifier built purely on
    # compression_ratio/avg_logprob/no_speech_prob scored ~24% F1) — used here only to catch
    # the most obvious cases fast, never as a substitute for the real text comparison below.
    expected_word_count = len(expected_text.split())
    if len(transcript.split()) < 2 and expected_word_count >= 3:
        return VerifyResult(False, 1.0, transcript)
    if any((seg.get("compression_ratio") or 0) > 2.4 for seg in segments):
        return VerifyResult(False, 1.0, transcript)

    # See MAX_INTERNAL_GAP_SECONDS's own comment — a transcript can look right while the
    # audio still has an enormous dead zone in the middle, since text alone carries no timing.
    words = [w for seg in segments for w in (seg.get("words") or [])]
    # Computed once here, regardless of which return below fires — an attempt that fails
    # verification can still end up being the least-bad "best of N" fallback a caller actually
    # plays (see _generateChatterboxAudio/_generateSesameAudio), and a read-along track is
    # still better than none for whatever the app ultimately uses.
    word_timings = _align_words(expected_text, words)
    for prev_w, next_w in zip(words, words[1:]):
        if (next_w.get("start", 0) - prev_w.get("end", 0)) > MAX_INTERNAL_GAP_SECONDS:
            return VerifyResult(False, 1.0, transcript, word_timings)

    ref = normalize(expected_text)
    hyp = normalize(transcript)
    if not ref.strip():
        return VerifyResult(True, 0.0, transcript, word_timings)
    cer = jiwer.process_characters(ref, hyp).cer
    return VerifyResult(cer <= CER_PASS_THRESHOLD, cer, transcript, word_timings)
