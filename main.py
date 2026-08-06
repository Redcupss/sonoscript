# SonoScript — main.py
# UI rewrite matching the approved mockup (see handoff/HANDOFF-NOTES.md + screenshots).

import io
import json
import math
import os
import queue
import re
import shlex
import ssl
import string
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import wave

import certifi
import AppKit
import AVFoundation
import Quartz
import objc
from Foundation import (
    NSObject, NSMakeRect, NSMakeSize, NSMakePoint, NSURL,
    NSRunLoop, NSDate, NSDefaultRunLoopMode,
)

import bug_report
import speech_verify
from chunking import chunk_text, CHUNK_TARGET_CHARS, chatterbox_chunk_target
from config import SESAME_ASSETS_DIR, load_config, save_config, sesame_voices_path
import history
import saved
from text_prep import sanitize_for_speech, normalize_paragraph_breaks
from ui_helpers import (
    white, fix_anchor, build_waveform_bars, make_label, symbol_image, format_playback_time,
    format_relative_time,
)
from widgets import (
    ClickThroughTextField, ScrubberView, HoverButton, icon_button, text_button, cta_button,
    FlatPopUpButton, ControlRow, FocusTextView, BackdropView, CardView, DropdownPanel,
    LevelMeterView, EditableNameField, RecordButton, text_button_brighten, BrightenOnHoverButton,
    PulsingLabel, ShimmerBorderView, ContextMenuButton, SegmentedPillControl,
)

APP_NAME = "SonoScript"
APP_VERSION = "1.12.3"
APP_BUILD = "45"
GITHUB_REPO = "Redcupss/sonoscript"
GITHUB_URL = "https://github.com/Redcupss"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
# Belt-and-suspenders alongside SSL_CONTEXT above: SSL_CONTEXT only covers urlopen() calls this
# app makes directly. Third-party libraries (confirmed directly: huggingface_hub, via httpx,
# when transformers' AutoTokenizer falls back to a network lookup) build their OWN SSL context
# via the stdlib ssl module directly, which doesn't know about SSL_CONTEXT at all — but does
# respect these two env vars, so setting them process-wide covers every such case at once
# instead of needing to patch each library's own networking code individually.
# A plain assignment, NOT setdefault(): confirmed directly, via a diagnostic print in a real
# packaged build, that py2app's own bootstrap already sets SSL_CERT_FILE/SSL_CERT_DIR to a
# bogus, nonexistent placeholder (Contents/Resources/openssl.ca/no-such-file) before this line
# ever runs — setdefault() saw a value already present and silently kept that broken one instead
# of certifi's real bundle, which is exactly why this fix looked correct but never actually took
# effect in the shipped app.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["SSL_CERT_DIR"] = os.path.dirname(certifi.where())
# Sesame's model code loads a LLaMA3 tokenizer and an audio codec ("Mimi") separately, each by
# HuggingFace repo id, independent of the CSM model weights themselves (see the long comment in
# _sesameEngine). These MUST be set here, at module load — before literally anything else in
# this file runs — not lazily inside _sesameEngine() right before importing mlx_audio: confirmed
# directly that huggingface_hub bakes some of these into its own module-level constants at
# IMPORT time (so setting them any later than a module's first import has zero effect for the
# rest of the process), while ANOTHER of its own code paths (hf_hub_download, used by the Mimi
# codec specifically) has its own independent default that isn't fully governed by the env var
# at all under some call shapes — trying to chase each individual code path's own exact timing/
# precedence quirk inside _sesameEngine() proved unreliable across two separate rebuild-and-
# retest rounds. Setting these unconditionally, this early, removes the ordering question
# entirely: nothing in this whole process can import huggingface_hub before this line runs.
os.environ["HF_HOME"] = os.path.join(SESAME_ASSETS_DIR, "hf_cache")
os.environ["HF_HUB_CACHE"] = os.path.join(SESAME_ASSETS_DIR, "hf_cache", "hub")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(SESAME_ASSETS_DIR, "hf_cache", "hub")
os.environ["HF_HUB_OFFLINE"] = "1"
PLACEHOLDER_TEXT = "Paste text here (⌘V)..."
SHADOW_OPACITY = 0.6  # About/Update card drop shadow

EL_API = "https://api.elevenlabs.io/v1"
OPENAI_API = "https://api.openai.com/v1"
OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
PROVIDERS = ["System", "Chatterbox", "Sesame", "ElevenLabs", "OpenAI", "Other"]
KEYLESS_PROVIDERS = ("System", "Chatterbox")  # no API key needed — bundled/on-device
# Sesame is NOT in KEYLESS_PROVIDERS — it still needs a pasted credential (a license key) that
# flows through the same key_field/_validateKeyWorker/_isConfigured path every other provider
# uses; it's just verified offline (see license.py) instead of over the network.

CHATTERBOX_VOICES = [
    {"id": "nova", "label": "Nova (American, Female)", "ref_audio": None},
    {"id": "sadie", "label": "Sadie (American, Female)", "ref_audio": "sadie.wav"},
    {"id": "manny", "label": "Manny (American, Male)", "ref_audio": "manny.wav"},
]
SPEEDS = ["0.5x", "0.8x", "1.0x", "1.25x", "1.5x"]  # real multipliers actually applied

# Chatterbox's natural speaking pace runs a little fast — a genuine 0.8x slowdown is what
# actually sounds like an unhurried "normal" pace for this specific voice model, confirmed by
# direct listening tests across the full speed range. Showing "0.8x" as the default would read
# as "this is playing slow" even though it's the best-sounding default, so the menu shows every
# real value shifted up one label for this provider only — real 0.8x appears as "1.0x", real
# 1.0x as "1.25x", etc. — while every other provider's menu still shows its true value.
CHATTERBOX_SPEED_DISPLAY = {"0.5x": "0.8x", "0.8x": "1.0x", "1.0x": "1.25x", "1.25x": "1.5x", "1.5x": "1.75x"}
CHATTERBOX_SPEED_REAL = {v: k for k, v in CHATTERBOX_SPEED_DISPLAY.items()}

# Chatterbox occasionally (even at this file's already-low temperature=0.05) produces a
# "runaway" generation 2-3x longer than normal for a given chunk of text — confirmed by
# direct repeated trials (identical input, same voice, same settings) to be a real model
# instability, not something tied to one specific reference clip: it reproduced with Nova,
# which has no reference audio at all. A confirmed-bad generation measured ~5.8 chars/sec
# (184 chars taking 31.7s instead of the normal ~10-14s); dozens of clean generations across
# both voices ranged ~12.8-20+ chars/sec. Only applied to chunks long enough for the ratio to
# be meaningful — a very short chunk's fixed generation overhead would trip this on its own.
CHATTERBOX_MIN_CHARS_PER_SEC = 11.5
CHATTERBOX_MIN_CHARS_FOR_CHECK = 50
# Same reasoning as SESAME_MAX_RETRIES below, now measured directly for this model too: an
# isolated 8-trial batch on a real chunk that repeatedly failed in production (same text,
# same voice, same settings) passed only 2/8 attempts — a ~75% per-attempt failure rate, and
# strikingly bimodal, not a smooth quality gradient: every failure scored CER=1.000 (total,
# unrelated-to-the-source garbage — matching the "autoregressive collapse" pattern documented
# elsewhere for this model family), every pass scored under 0.14. At the old MAX_RETRIES=2 (3
# attempts total), that 75% rate gives roughly a 42% chance EVERY attempt fails and a
# completely garbled chunk plays anyway. 4 (5 attempts total, matching Sesame's own budget)
# brings that down to roughly 24% — a real, measured improvement, not a full fix; this specific
# voice+content combination can still exhaust every attempt, same honest caveat as Sesame's.
CHATTERBOX_MAX_RETRIES = 4

# Sesame's 3 built-in voices — cloned from real, single continuous reference recordings (never
# spliced fragments, confirmed by earlier testing to hurt clone quality). Unlike Chatterbox,
# Sesame's generate() pairs each ref_audio with its own transcript (ref_text) as conditioning
# context, so every entry needs one; these were transcribed directly from the reference clips
# rather than guessed. "Ben" is the developer's own voice — labeled Ben everywhere in the app,
# never the developer's real name.
SESAME_VOICES = [
    {"id": "sadie", "label": "Sadie (American, Female)", "ref_audio": "sadie.wav",
     "ref_text": ("Yes, my dad is stereotypically Danish. I think there's kind of a mysterious "
                  "element to the Danes. They're very funny and dry and witty and guarded. "
                  "That's how my dad is, he's all those things.")},
    {"id": "manny", "label": "Manny (American, Male)", "ref_audio": "manny.wav",
     "ref_text": ("And Solomon gets to walk in a certain level of authority that is only "
                  "possible because of David. I got a revelation like really early that like...")},
    {"id": "ben", "label": "Ben (American, Male)", "ref_audio": "ben.wav",
     "ref_text": ("To Henry, the journey of a thousand miles begins with a single step. For "
                  "wherever he lived, he would place to place and he kept his dream alive and "
                  "burning.")},
    # Sesame's own official demo/reference clips (from sesame/csm-1b's gated repo — pulled via
    # the ungated mlx-community/csm-1b mirror instead, which re-hosts the same files) rather
    # than a bundled or recorded one. 30s each, right in the middle of the model's own
    # documented ideal reference-length range — see RECORD_MIN/MAX_SECONDS' own comment. Voice
    # gender/character unconfirmed (never listened to directly) — labeled neutrally rather
    # than guessed. Their transcripts are casual, unpunctuated, mid-thought speech (Sesame's
    # own conditioning source), which may carry into a more conversational, less formal
    # reading style than Ben/Sadie/Manny when reading arbitrary book-style text — worth a
    # direct listen before treating them as equivalent alternatives.
    {"id": "conversational_a", "label": "Alex (Conversational)", "ref_audio": "conversational_a.wav",
     "ref_text": ("like revising for an exam I'd have to try and like keep up the momentum because I'd "
                  "start really early I'd be like okay I'm gonna start revising now and then like "
                  "you're revising for ages and then I just like start losing steam I didn't do that "
                  "for the exam we had recently to be fair that was a more of a last minute scenario "
                  "but like yeah I'm trying to like yeah I noticed this yesterday that like Mondays I "
                  "sort of start the day with this not like a panic but like a")},
    {"id": "conversational_b", "label": "Jordan (Conversational)", "ref_audio": "conversational_b.wav",
     "ref_text": ("like a super Mario level. Like it's very like high detail. And like, once you get "
                  "into the park, it just like, everything looks like a computer game and they have all "
                  "these, like, you know, if, if there's like a, you know, like in a Mario game, they "
                  "will have like a question block. And if you like, you know, punch it, a coin will "
                  "come out. So like everyone, when they come into the park, they get like this little "
                  "bracelet and then you can go punching question blocks around.")},
]

# Same defensive pattern as Chatterbox's runaway-generation guard — CSM's default sampler
# (temperature=0.9) is far more stochastic than Chatterbox's tuned 0.05, and 5 direct trials
# with Sadie's reference clip already spanned 15.2-20.5 chars/sec on the same input, a wider
# spread than Chatterbox showed even before its own instability was found. Thresholds are
# provisional (based on a small sample) pending more real-world usage data.
SESAME_MIN_CHARS_PER_SEC = 11.5
# The check used to be floor-only — catches runaway/bloated generation (too SLOW) but nothing
# stopped a too-FAST, truncated/garbled generation from passing on its very first attempt.
# Confirmed as a real, separate gap (not the same bug as the floor guards against): a clean,
# well-formed input still produced one generation at 49.4 chars/sec — more than double the
# 15.2-20.5 range above — whose Whisper transcription didn't match the input text at all.
# Headroom above 20.5 chosen to match the floor's own proportional margin below 15.2 (roughly
# 24% in both directions).
SESAME_MAX_CHARS_PER_SEC = 26.0
SESAME_MIN_CHARS_FOR_CHECK = 50
# A real, isolated-process 16-trial batch measured this failure mode at a ~44% per-attempt
# rate (confirmed a known, unfixed CSM base-model bug — see _generateSesameAudio) — at the
# old MAX_RETRIES=2 (3 attempts total), that's roughly a 1-in-13 chance EVERY attempt fails
# and a garbled chunk plays anyway, which compounds fast across a multi-chunk document. 4
# (5 attempts total) brings a single chunk's all-fail odds down to roughly 1-in-60 — worth
# the extra worst-case wait now that max_audio_length_ms caps how long each failed attempt
# takes, instead of running all the way to the library's old 90-second default first.
SESAME_MAX_RETRIES = 4

# How many chunks to keep queued ahead of the one currently playing. Was implicitly 1 (prefetch
# only ever queued a single chunk, once, when the current one started playing) until the
# content-verification retry loop (see speech_verify.py) made that budget too tight — confirmed
# directly on a real story where 4 of 5 chunks needed their full retry allowance, and
# generation time for those chunks EXCEEDED their own playback duration, so the single-chunk
# buffer ran dry mid-document and playback audibly stalled. 3 gives real slack for a couple of
# hard chunks in a row to be absorbed by whatever time easier neighboring chunks freed up,
# without eagerly generating an entire long document up front (wasted work if playback never
# gets there, same reasoning against unlimited retries elsewhere in this file).
PREFETCH_LOOKAHEAD_CHUNKS = 3

CREATE_VOICE_SENTINEL = "__sesame_create_your_own__"
RECORD_SAMPLE_RATE = 44100
# Sesame's own official demo reference clips run 20-45s — a real, direct research finding
# (confirmed by reading the model's actual conditioning mechanism: the reference audio and
# its transcript get concatenated into one unbroken segment the model uses to learn where the
# reference ends and new speech begins, so a short clip gives it less to work with) — the old
# 5-10s window traded stability for a quicker recording experience. Matches the model's own
# full ~45s ceiling now rather than a self-imposed lower cap — asked directly, and the answer
# was to favor a longer recording over a shorter one if it gets a better result.
RECORD_MIN_SECONDS = 15.0
RECORD_MAX_SECONDS = 45.0
# A fixed, app-dictated script per style — never user-editable — means the app always knows
# the exact ground-truth transcript with zero risk of a mismatch, and no ASR/transcription
# step is ever needed (keeping voice creation fully offline, matching the rest of the app).
#
# Multiple styles, not one script: confirmed directly against real generated output — Ben's
# formal, complete-sentence reference produced a narrator-style clone, while Sesame's own
# casual, self-correcting "conversational" demo reference (see conversational_a/b above)
# produced something much closer to a natural presenter/lecturer reading the same book text.
# The word content itself is what a reader has to work with — direction alone ("read this
# casually") only goes so far if the words themselves are stylistically neutral, the same way
# a screenplay's actual dialogue shapes a performance more than a stage direction does. Each
# script below is written to embody its own style through word choice and structure, not just
# labeled with one.
#
# Word counts (and pace assumptions) are calibrated from two real, directly-reported reading
# times, not guessed: casual text (the original 59-word script) read in 15s (~3.9 words/sec),
# vs. dense informational text (the "Reading" script below) read in 44.4s for 99 words (~2.2
# words/sec) — nearly HALF the pace. Formal/informational registers are read noticeably slower
# than casual ones by the same person, so each script here is sized for its own register's
# real pace against the 15-45s window, not a single flat words-per-second assumption. Trimmed
# for margin under the 45s ceiling even at a slower-than-estimated pace — landing right at the
# edge risks the recording buffer cutting off mid-sentence, which would leave ref_text (the
# exact transcript this app pairs with the audio) claiming words that were never actually
# captured, corrupting the exact alignment the model's cloning depends on.
RECORD_SCRIPT_PRESETS = [
    {
        "id": "narrator",
        "label": "Narrator",
        "description": "Warm and deliberate — like a documentary voiceover.",
        "script": (
            "Deep in the heart of every great story lies a single, defining moment — the "
            "moment everything changes. For years, this place stood quiet, its secrets "
            "waiting patiently to be uncovered, hidden from every eye that dared to look. "
            "Explorers came and went, each one certain they had found the truth, and each "
            "one leaving with more questions than answers. But today, at last, that story "
            "can finally be told, and it will stay with you long after the final word."
        ),
    },
    {
        "id": "conversational",
        "label": "Conversational",
        "description": "Casual and natural — like explaining something to a friend.",
        "script": (
            "Okay, so — this is going to sound random, but I've been thinking about this "
            "all day, and I just have to say it out loud. You know that feeling when you "
            "plan something out perfectly, like down to the smallest detail, and then it "
            "just... doesn't go that way at all? That actually happened to me this week, "
            "which was kind of funny, honestly. I mean, not in a bad way, just — it turned "
            "out completely different than I expected, you know? And normally that would "
            "stress me out, but this time I was like, actually, you know what, this is kind "
            "of fine. Anyway, that's basically where my head's been at today."
        ),
    },
    {
        "id": "reading",
        "label": "Reading",
        "description": "Clear and steady — like reading a book or article aloud.",
        # Real excerpt, not written for this — a direct suggestion, from the same business-
        # textbook chapter used to test this whole feature. Trimmed by one sentence from the
        # original (per that same suggestion: "you could even cut the last sentence if it's
        # too long") for margin under the 45s ceiling at this register's slower real pace.
        "script": (
            "Depending on the degree of novelty involved, there are two main types of new "
            "offerings: revolutionary offerings that deliver new-to-the-world benefits, and "
            "evolutionary offerings that involve relatively minor modifications of existing "
            "offerings, such as different colors, sizes, or packaging. Revolutionary "
            "offerings — like Netflix, Uber, and Airbnb — can disrupt entire industries with "
            "benefits no competitor can easily match."
        ),
    },
]


# ---------- app ----------

class _SpeechTimingDelegate(NSObject):
    """A fresh, call-scoped AVSpeechSynthesizerDelegate — created new per _requestSystemTTS
    call rather than reusing AppDelegate itself as the delegate, so its on_range callback can
    close over that ONE call's own local text/collected-buffer state with no shared/global
    state and no risk of a stale callback from a previous generation ever firing. Confirmed via
    a standalone spike that willSpeakRangeOfSpeechString fires interleaved with (and just
    before) the buffer callback for that word's own audio — the cumulative sample count in
    the buffer AT THE MOMENT this fires is the word's real, exact start time, not an estimate."""

    def init(self):
        self = objc.super(_SpeechTimingDelegate, self).init()
        self.on_range = None
        return self

    def speechSynthesizer_willSpeakRangeOfSpeechString_utterance_(self, synth, range_val, utterance):
        if self.on_range is not None:
            self.on_range(range_val.location, range_val.length)


class AppDelegate(NSObject):
    # ----- lifecycle -----
    def applicationDidFinishLaunching_(self, notification):
        self.config = load_config()
        if self.config.get("provider") == "Kokoro":  # v1.7.x -> v1.8: Chatterbox replaced Kokoro
            self.config["provider"] = "Chatterbox"
            self.config.pop("voice_id", None)  # Kokoro's voice ids don't exist in the new list
            save_config(self.config)
        self.voice_ids = []
        self._voice_labels = []  # cached by _populateVoiceMenu, restored by showMainScreen
        self.player = None
        self._chatterbox_engine = None  # lazy-loaded once, reused for every chunk — see _chatterboxEngine
        self._chatterbox_lock = threading.Lock()
        self._sesame_engine = None  # lazy-loaded once, reused for every chunk — see _sesameEngine
        self._sesame_lock = threading.Lock()
        # Every chunk/prefetch/seek used to spawn its own brand-new threading.Thread to call
        # into the local MLX engine (Chatterbox/Sesame) — fine for a short passage, but a long
        # one (a whole book chapter) creates enough distinct native threads over a session to
        # exhaust MLX's own per-thread GPU stream pool, surfacing as a real, reproducible
        # crash: "Couldn't generate speech: There is no Stream(gpu, 2) in current thread."
        # Routing every chunk-generation job through ONE persistent worker thread instead
        # means MLX only ever sees a single thread identity for the app's entire lifetime.
        self._tts_job_queue = queue.Queue()
        threading.Thread(target=self._ttsWorkerLoop, daemon=True).start()
        # Voice-recording flow state (see _showRecordingCaptureCard and friends). Guards
        # dismissOverlay() against an accidental backdrop-click/Esc silently discarding an
        # in-progress take — only the flow's own explicit Cancel/Use actions clear this.
        self._rec_recording_active = False
        self._rec_stream = None
        self._rec_buffer = None
        self._rec_write_pos = 0
        self._rec_preview_audio = None  # (float32 ndarray, sample_rate) once a take passes validation
        self._rec_preview_player = None  # AVAudioPlayer, scoped to this flow only — never touches self.player
        self._pending_delete_voice_id = None  # set right before the Manage Voices delete-confirm card opens
        self._pending_delete_history_id = None  # set right before the History row delete-confirm card opens
        self._pending_delete_saved_path = None  # set right before the Saved row delete-confirm card opens
        self._recordings_seg = None  # the persistent SegmentedPillControl instance, so tab switches can animate
        self._recordings_list_box = None  # the persistent list container tab switches rebuild content into
        self.current_recordings_tab = "recent"
        self._manage_voice_fields = {}  # voice_id -> its NSTextField in the Manage Voices card, for rename commits
        self._rec_return_to = None  # callable to reopen instead of dismissing to the main screen, or None
        self._rec_selected_script = None  # one of RECORD_SCRIPT_PRESETS, chosen on _showStyleChoiceCard
        # Bug-report diagnostic log (see bug_report.py) — one entry per _requestTTS call this
        # session, across every provider. Deliberately holds no raw text, only its length (see
        # bug_report.py's own header for why). In-memory only, never written to disk, cleared
        # on relaunch — a report only ever reflects the current session, matching the
        # disclaimer shown before a tester sends one.
        self.session_report_log = []
        self._report_fields = {}
        self._report_field_refs = {}
        self._report_draft = None
        self._rec_script_fade_observer = None
        # Settings > Data & Storage pending state — what the user is currently configuring,
        # separate from what's actually stored in history.py until storageConfirmClicked_
        # applies it. Init'd from real config the first time showSettingsScreen runs.
        self._settings_storage_mode = None
        self._settings_storage_value = None
        self._pending_storage_mode = None
        self._pending_storage_value = None
        self._list_scroll_observer = []  # see _installScrollReclamp
        self._width_rebuild_observer = None  # see _installWidthRebuildTrigger
        self._width_rebuild_timer = None
        # Chunked playback state — see playPauseClicked_/_beginChunkPlayback for the pipeline.
        # playback_token identifies one Play session; background chunk results carrying a
        # stale token (from a Stop or a new Play superseding it) are dropped on arrival.
        self.playback_token = None
        self.all_chunks = []
        self.chunk_durations = []  # parallel to all_chunks; None until that chunk's real audio duration is known
        self.avg_chars_per_sec = None  # running speech-rate estimate, refined as real durations come in
        self.chunk_index = 0
        self.next_chunk_audio = None
        self._prefetch_frontier = 0  # highest chunk index a prefetch job has been queued for
        self.chunk_audio_cache = {}  # index -> already-generated audio bytes, so scrubbing back
                                      # to a chunk you've already heard replays it exactly
                                      # (same bytes, same real duration) instead of a fresh,
                                      # not-necessarily-identical regeneration, and doesn't
                                      # spend a fresh request on content you already paid for.
        self.chunk_word_timings = {}  # index -> that chunk's word_timings list (System voice
                                       # only, see _requestSystemTTS) — parallels chunk_audio_cache
        self._last_word_timings = None  # side-channel _requestSystemTTS uses to hand its result
                                         # to _chunkWorker without changing _requestTTS's signature
        self.session_text = None  # text the current all_chunks/cache were generated from — lets
                                   # playPauseClicked_ tell "replay what just finished" (same
                                   # text, still cached) apart from "text changed, start fresh"
        self.waiting_for_next = False
        self.progress_timer = None
        self.is_scrubbing = False
        self.overlay = None
        self.overlay_card = None
        self.esc_monitor = None
        self.welcome_esc_monitor = None
        self.welcome_pill_observer = None
        self._fade_timers = {}
        self.update_info = None  # dict: tag, notes, asset_url, asset_size
        self.current_screen = None
        self.dropdown_panel = None
        self.dropdown_monitor = None
        self.dropdown_local_monitor = None
        self.dropdown_anchor = None
        self.dropdown_scroll_observers = []

        self.build_main_menu()
        self.build_window()

        if self._isConfigured():
            self.showMainScreen()
            self.fetchVoices()
            # Loading the local model is a real, unavoidable few-tens-of-seconds cost — the
            # first Play of a session was paying it inline, on top of actual generation time,
            # making that first wait look far worse than the model actually is. Queuing the
            # load now overlaps it with however long the user spends reading/pasting text
            # instead, so by the time they hit Play it may already be warm — queued (not a
            # separate thread) so it runs on the same persistent MLX thread as everything else.
            provider = self.config.get("provider")
            if provider == "Chatterbox":
                self._tts_job_queue.put(self._chatterboxEngine)
            elif provider == "Sesame":
                self._tts_job_queue.put(self._sesameEngine)
        else:
            self.showWelcomeScreen(show_intro=True)

        # silent update check on launch
        threading.Thread(target=self._checkUpdateWorker, args=(True,), daemon=True).start()

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        # Standard Mac app lifecycle: closing the window is not the same as quitting. Staying
        # alive in the Dock after the window closes is also what lets an in-progress
        # background save/generation actually finish instead of being killed mid-flight —
        # only a real Quit (Cmd-Q, or Quit from the Dock menu) should end the process.
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible_windows):
        if not has_visible_windows:
            self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        return True

    # ----- menu bar -----
    def build_main_menu(self):
        main_menu = AppKit.NSMenu.alloc().init()
        app_item = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(app_item)
        app_menu = AppKit.NSMenu.alloc().init()
        items = [
            (f"About {APP_NAME}", "showAbout:", ""),
            ("Check for Updates", "checkForUpdatesClicked:", ""),
            (None, None, None),
            ("Recordings", "recordingsClicked:", ""),
            ("Voice Provider", "resetApiKey:", ""),
            ("Settings", "settingsClicked:", ""),
        ]
        # sys.frozen is set by py2app on the actual packaged .app, never on a plain `python3
        # main.py` dev run — this is a testing tool for comparing resize behavior against a
        # known-good starting size/aspect ratio, not a real feature, and must never reach a
        # real user's copy of the app.
        if not getattr(sys, "frozen", False):
            items.append((None, None, None))
            items.append(("Reset Window Size (Dev)", "devResetWindowSizeClicked:", ""))
        items.append((None, None, None))
        items.append((f"Quit {APP_NAME}", "terminate:", "q"))
        for title, action, key in items:
            if title is None:
                app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            if action != "terminate:":
                item.setTarget_(self)
            app_menu.addItem_(item)
        app_item.setSubmenu_(app_menu)

        edit_item = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(edit_item)
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key in [
            ("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None),
            ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a"),
        ]:
            if title is None:
                edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            edit_menu.addItem_(AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key))
        edit_item.setSubmenu_(edit_menu)
        AppKit.NSApp.setMainMenu_(main_menu)

    def devResetWindowSizeClicked_(self, sender):
        # Keeps the top-left corner fixed and just resets the content size — matches
        # build_window's own initial NSMakeRect(0, 0, 440, 520) exactly, so this is a real
        # "back to launch size" rather than an approximation.
        self.window.setContentSize_(NSMakeSize(440, 520))

    # ----- window shell -----
    def build_window(self):
        rect = NSMakeRect(0, 0, 440, 520)
        style = (
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskFullSizeContentView
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        self.window.setTitle_(APP_NAME)
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        self.window.setAppearance_(AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        # Dev-testing convenience, not a real app feature — SONOSCRIPT_DEV_QUIET moves a
        # dev-mode launch to the right edge of the screen instead of dead center, so it doesn't
        # land on top of whatever else is on screen. No real user would ever set this.
        if os.environ.get("SONOSCRIPT_DEV_QUIET"):
            screen = AppKit.NSScreen.mainScreen().visibleFrame()
            self.window.setFrameOrigin_(NSMakePoint(screen.origin.x + screen.size.width - 460, screen.origin.y + 80))
        else:
            self.window.center()
        # 360 wide: fits the control row without overlap. 434 tall: fits the welcome screen's
        # fixed-size centered container (386pt, including the Cancel button's row) with margin —
        # below that the container's flexible-margin autoresizing can't distribute negative
        # space and it snaps into a corner.
        self.window.setMinSize_(NSMakeSize(360, 458))
        self.window.setMovableByWindowBackground_(True)
        # A programmatically-created NSWindow defaults to isReleasedWhenClosed=YES — without
        # this, closing the window (now that the app itself no longer quits when you do)
        # would deallocate the window and its whole content view hierarchy for good, leaving
        # no way to bring it back via the Dock icon and a dangling self.window reference used
        # everywhere else in this file.
        self.window.setReleasedWhenClosed_(False)

        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self.window.setContentView_(effect)
        self.root = effect

        # The "behind window" blur pulls in whatever is actually behind the app (a light
        # desktop, a bright browser window, etc.), which can wash out the dark theme and hurt
        # text contrast. A dark tint on top of the blur guarantees a baseline darkness no
        # matter what's behind the window, while still showing the blur texture through it.
        tint = AppKit.NSView.alloc().initWithFrame_(rect)
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.0, 0.45).CGColor())
        tint.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        effect.addSubview_(tint)

        # wordmark button, top-right of title bar; opens the app popup menu
        h = rect.size.height
        font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold)
        # Measured the traffic lights directly (pixel-analyzed a real screenshot): their dot
        # bounding box sits exactly 8pt from both the left and top window edges.
        #
        # Tried narrowing a CENTERED frame to match the text width instead — that's a losing
        # game: three different ways of measuring "SonoScript"'s width (raw attributed-string,
        # a plain field's cellSizeForBounds_, and the actual BrightenOnHoverButton's own field)
        # gave three different answers (67.6 / 71.6 / 75.6pt), and centering math amplifies
        # whichever one is wrong into visible drift on both edges — confirmed directly, one
        # attempt clipped the final "t", the next left ~13pt of margin instead of the 8pt
        # targeted. Right-aligning the text within a generously-wide frame sidesteps needing an
        # exact width at all: the frame's own right edge (a real, known number) IS the text's
        # right edge, whatever the glyphs actually measure. Same technique already used for the
        # sidebar's own left-aligned rows.
        # y is 8pt higher than the "obvious" h-34 — configureBrighten's labels now correctly
        # center vertically within whatever frame they're given (see _VerticallyCenteredTextField),
        # and centering the text within this button's full 26pt height reads as extra empty
        # space above it versus the old (buggy) top-alignment this position was originally
        # tuned against. Tried shrinking the label_frame instead first, expecting the text to
        # then sit flush with the button's own top edge — measured that directly and it did NOT
        # land where predicted (NSAttributedString's reported size already includes the font's
        # own leading, which isn't eliminated just by matching the frame to it). Measuring the
        # actual simple case instead (full-height frame, centered) gave a clean, real 16pt
        # margin — 8pt more than the 8pt target — so the outer frame itself is shifted up by
        # exactly that measured gap instead of fighting text metrics with a shrunk frame.
        self.wordmark = BrightenOnHoverButton.alloc().initWithFrame_(
            NSMakeRect(rect.size.width - 108, h - 26, 100, 26))
        self.wordmark.configureBrighten(
            APP_NAME, font, white(0.55), white(1.0),
            align=AppKit.NSTextAlignmentRight, label_frame=NSMakeRect(0, 0, 100, 26))
        self.wordmark.setTarget_(self)
        self.wordmark.setAction_("wordmarkClicked:")
        self.wordmark.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
        self.root.addSubview_(self.wordmark)

        self.screen_view = None
        self.window.orderFront_(None)
        # activateIgnoringOtherApps_ is what steals keyboard focus from whatever app you're
        # actually using — skipped in the same dev-testing mode as the window position above,
        # so a background test launch doesn't interrupt anything. makeKeyAndOrderFront_ (the
        # normal path) both shows AND focuses the window; orderFront_ alone just shows it.
        if not os.environ.get("SONOSCRIPT_DEV_QUIET"):
            self.window.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)

    @objc.python_method
    def swap_screen(self, view):
        if self.screen_view is not None:
            self.screen_view.removeFromSuperview()
        view.setFrame_(self.root.bounds())
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self.root.addSubview_positioned_relativeTo_(view, AppKit.NSWindowBelow, self.wordmark)
        self.screen_view = view

    def wordmarkClicked_(self, sender):
        if self.dropdown_anchor is sender and self.dropdown_panel is not None:
            self._closeDropdown()
            return
        rows = [
            {"title": f"About {APP_NAME}", "on_click": lambda: self.showAbout_(None)},
            {"title": "Check for Updates", "on_click": lambda: self.checkForUpdatesClicked_(None)},
            None,
            {"title": "Recordings", "on_click": lambda: self.recordingsClicked_(None)},
            {"title": "Voice Provider", "on_click": lambda: self.resetApiKey_(None)},
            {"title": "Settings", "on_click": lambda: self.settingsClicked_(None)},
        ]
        if self.config.get("provider") == "Sesame":
            rows.append(None)
            rows.append({"title": "Manage Voices", "on_click": lambda: self.showSettingsScreen("voices")})
        self._showDropdown(sender, rows, align="right", direction="down")

    # ----- custom dropdown / menu panel (matches mockup card styling; no native NSMenu chrome) -----
    @objc.python_method
    def _teardownDropdownMonitors(self):
        mon = getattr(self, "dropdown_monitor", None)
        if mon is not None:
            AppKit.NSEvent.removeMonitor_(mon)
            self.dropdown_monitor = None
        lmon = getattr(self, "dropdown_local_monitor", None)
        if lmon is not None:
            AppKit.NSEvent.removeMonitor_(lmon)
            self.dropdown_local_monitor = None
        observers = getattr(self, "dropdown_scroll_observers", None)
        if observers:
            nc = AppKit.NSNotificationCenter.defaultCenter()
            for token in observers:
                nc.removeObserver_(token)
            self.dropdown_scroll_observers = []

    @objc.python_method
    def _closeDropdownImmediate(self):
        """No fade — used only when a new dropdown is about to replace an already-open one,
        where the old panel is being instantly superseded rather than actually dismissed."""
        panel = getattr(self, "dropdown_panel", None)
        if panel is not None:
            self.window.removeChildWindow_(panel)
            panel.orderOut_(None)
            self.dropdown_panel = None
            self.dropdown_anchor = None
        self._teardownDropdownMonitors()

    @objc.python_method
    def _closeDropdown(self):
        panel = getattr(self, "dropdown_panel", None)
        if panel is None:
            return
        self.dropdown_panel = None
        self.dropdown_anchor = None
        self._teardownDropdownMonitors()

        def fade_out(ctx):
            ctx.setDuration_(0.1)
            panel.animator().setAlphaValue_(0.0)

        def done(ctx=None):
            self.window.removeChildWindow_(panel)
            panel.orderOut_(None)
            self.window.makeKeyAndOrderFront_(None)
        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(fade_out, done)

    def volumeClicked_(self, sender):
        if self.dropdown_anchor is sender and self.dropdown_panel is not None:
            self._closeDropdown()
            return
        self._showVolumePopover(sender)

    @objc.python_method
    def _showVolumePopover(self, anchor):
        """Small custom popover (not a row menu, so it doesn't go through _showDropdown) —
        reuses the exact same panel-shell styling and dismiss-monitor machinery (dropdown_panel/
        dropdown_anchor/_closeDropdown) so it opens/closes/dismisses identically to every other
        menu in the app, just with a slider instead of rows."""
        self._closeDropdownImmediate()
        w, h, pad = 190.0, 78.0, 14.0

        anchor_screen = anchor.window().convertRectToScreen_(
            anchor.convertRect_toView_(anchor.bounds(), None))
        x = anchor_screen.origin.x + anchor_screen.size.width / 2.0 - w / 2.0
        y = anchor_screen.origin.y + anchor_screen.size.height + 14.0

        panel = DropdownPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h), AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setLevel_(AppKit.NSPopUpMenuWindowLevel)
        panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))

        outer = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        outer.setWantsLayer_(True)
        outer.layer().setBorderColor_(white(0.14).CGColor())
        outer.layer().setBorderWidth_(1.0)
        outer.layer().setCornerRadius_(12.0)
        outer.layer().setMasksToBounds_(True)

        blur = AppKit.NSVisualEffectView.alloc().initWithFrame_(outer.bounds())
        blur.setMaterial_(AppKit.NSVisualEffectMaterialPopover)
        blur.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        blur.setState_(AppKit.NSVisualEffectStateActive)
        blur.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        outer.addSubview_(blur)

        tint = AppKit.NSView.alloc().initWithFrame_(outer.bounds())
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.08, 0.28).CGColor())
        tint.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        outer.addSubview_(tint)

        vol = max(0.0, min(1.0, self.config.get("volume", 1.0)))
        label = make_label("Volume", 12, 0.6)
        label.setFrame_(NSMakeRect(pad, h - pad - 16, w - pad * 2 - 40, 16))
        self.volume_pct_label = make_label(f"{int(round(vol * 100))}%", 12, 0.85, align=AppKit.NSTextAlignmentRight)
        self.volume_pct_label.setFrame_(NSMakeRect(w - pad - 36, h - pad - 16, 36, 16))
        outer.addSubview_(label)
        outer.addSubview_(self.volume_pct_label)

        self.volume_slider = ScrubberView.alloc().initWithFrame_(NSMakeRect(pad, pad, w - pad * 2, 24))
        self.volume_slider.configure()
        self.volume_slider.setFraction(vol)
        self.volume_slider.on_scrub = self._volumeDragged
        self.volume_slider.on_scrub_end = self._volumeReleased
        outer.addSubview_(self.volume_slider)

        panel.setContentView_(outer)
        self.dropdown_panel = panel
        self.dropdown_anchor = anchor
        self.window.addChildWindow_ordered_(panel, AppKit.NSWindowAbove)
        panel.setAlphaValue_(0.0)
        panel.makeKeyAndOrderFront_(None)

        def fade_in(ctx):
            ctx.setDuration_(0.12)
            panel.animator().setAlphaValue_(1.0)
        AppKit.NSAnimationContext.runAnimationGroup_(fade_in)

        self.dropdown_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskRightMouseDown, lambda e: self._closeDropdown())

        def local_handler(event):
            p = getattr(self, "dropdown_panel", None)
            if p is None:
                return event
            if event.window() is p:
                return event  # click lands inside the popover (the slider); let it handle itself
            a = getattr(self, "dropdown_anchor", None)
            if a is not None and event.window() is a.window():
                pt = a.convertPoint_fromView_(event.locationInWindow(), None)
                if AppKit.NSPointInRect(pt, a.bounds()):
                    return event  # click is on the anchor button itself; its own action toggles it closed
            self._closeDropdown()
            return event
        self.dropdown_local_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, local_handler)

    @objc.python_method
    def _volumeDragged(self, fraction):
        vol = max(0.0, min(1.0, fraction))
        self.volume_slider.setFraction(vol)
        self.volume_pct_label.setStringValue_(f"{int(round(vol * 100))}%")
        if self.player is not None:
            self.player.setVolume_(vol)

    @objc.python_method
    def _volumeReleased(self, fraction):
        vol = max(0.0, min(1.0, fraction))
        self.config["volume"] = vol
        save_config(self.config)

    @objc.python_method
    def _showDropdown(self, anchor, rows, width=None, align="right", direction="up"):
        """rows: list of {title, selected, on_click} dicts, or None for a separator line."""
        self._closeDropdownImmediate()
        # pad is the margin between the panel's own border and the first/last row's text —
        # smaller here (not max_h) is what actually brings a scrolled-to-the-edge row's fading
        # text closer to the panel border, reinforcing "there's more this way" rather than
        # leaving a gap of empty padding between the fade and the edge.
        row_h, sep_h, pad, gap, max_h = 30, 10, 3, 14, 260
        if width is not None:
            w = width
        else:
            # size to the longest row's actual text instead of a flat minimum — "0.7x" shouldn't
            # get the same width as "Daniel - Steady Broadcaster (british)"
            row_font = AppKit.NSFont.systemFontOfSize_(13)
            max_text_w = max(
                (AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    r["title"], {AppKit.NSFontAttributeName: row_font}).size().width
                 for r in rows if r is not None),
                default=0.0)
            # +32 for the row's own left/right inset, +8 extra safety margin: measuring text
            # width via NSAttributedString.size() can slightly undershoot actual rendering
            # (kerning/subpixel rounding), which was clipping "Check for Updates" to
            # "Check for" since its label had zero slack beyond the measured width.
            w = max(max_text_w + 40, anchor.frame().size.width, 90)
        heights = [sep_h if r is None else row_h for r in rows]
        content_h = pad * 2 + sum(heights)
        needs_scroll = content_h > max_h
        if needs_scroll:
            # Snap the visible height down to a whole number of rows (counted from the
            # list's own end) instead of an arbitrary max_h cutoff — otherwise whatever
            # row lands right at the scroll boundary gets sliced mid-row, leaving a
            # useless sliver of a name poking out below the fade. With a whole-row
            # height, the row closest to the edge is always complete: it just fades via
            # opacity, the same as every other faded row, never gets physically cut.
            running = 0
            for h in reversed(heights):
                if running + h + pad * 2 > max_h:
                    break
                running += h
            height = pad * 2 + running
        else:
            height = content_h

        anchor_screen = anchor.window().convertRectToScreen_(
            anchor.convertRect_toView_(anchor.bounds(), None))
        x = anchor_screen.origin.x + anchor_screen.size.width - w if align == "right" else anchor_screen.origin.x
        if direction == "up":
            y = anchor_screen.origin.y + anchor_screen.size.height + gap
        else:
            y = anchor_screen.origin.y - height - gap

        panel = DropdownPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, height), AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setLevel_(AppKit.NSPopUpMenuWindowLevel)
        # Popover material (unlike HUDWindow) follows the system's light/dark setting rather
        # than always being dark — force dark explicitly so this doesn't flip light under a
        # light-mode system.
        panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))

        # real vibrancy blur (matches the About/Update backdrop) rather than a flat translucent
        # fill. NSVisualEffectView doesn't respect a wrapper layer's cornerRadius/masksToBounds
        # for its own internal blur rendering, so the *window's* actual square shape showed
        # through/behind the rounded border as a visible squared-off outline. A plain layer
        # (which properly masks to its own rounded corners) avoids that entirely.
        # NSVisualEffectView doesn't reliably respect its OWN layer's cornerRadius/masksToBounds
        # when it's the window's root content view directly — the blur compositing bled past
        # the rounded shape as a square outline. Making it a subview of a separately-masked
        # plain wrapper (the actual root content view) clips it correctly.
        outer = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, height))
        outer.setWantsLayer_(True)
        outer.layer().setBorderColor_(white(0.14).CGColor())
        outer.layer().setBorderWidth_(1.0)
        outer.layer().setCornerRadius_(12.0)
        outer.layer().setMasksToBounds_(True)

        blur = AppKit.NSVisualEffectView.alloc().initWithFrame_(outer.bounds())
        blur.setMaterial_(AppKit.NSVisualEffectMaterialPopover)
        blur.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        blur.setState_(AppKit.NSVisualEffectStateActive)
        blur.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        outer.addSubview_(blur)

        tint = AppKit.NSView.alloc().initWithFrame_(outer.bounds())
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.08, 0.28).CGColor())
        tint.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        outer.addSubview_(tint)

        suppress_hover = {"active": False}
        hover_rows = []
        if needs_scroll:
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, w, height))
            scroll.setBorderType_(AppKit.NSNoBorder)
            scroll.setHasVerticalScroller_(True)
            scroll.setDrawsBackground_(False)
            scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, content_h))
            scroll.setDocumentView_(container)
            outer.addSubview_(scroll)

            # AppKit re-evaluates tracking areas against the cursor whenever a view's geometry
            # changes, including rows scrolling underneath a cursor that never moved — that
            # was lighting up whatever row happened to pass under it. Suppress hover fill for
            # the duration of an actual (trackpad/wheel) scroll gesture.
            def scroll_started(note):
                suppress_hover["active"] = True
                for r in hover_rows:
                    r._bright_label.setAlphaValue_(0.0)

            def scroll_ended(note):
                suppress_hover["active"] = False

            nc = AppKit.NSNotificationCenter.defaultCenter()
            t1 = nc.addObserverForName_object_queue_usingBlock_(
                AppKit.NSScrollViewWillStartLiveScrollNotification, scroll, None, scroll_started)
            t2 = nc.addObserverForName_object_queue_usingBlock_(
                AppKit.NSScrollViewDidEndLiveScrollNotification, scroll, None, scroll_ended)
            self.dropdown_scroll_observers = [t1, t2]
        else:
            container = outer

        cy = content_h - pad
        selected_center_y = None
        for r in rows:
            if r is None:
                cy -= sep_h
                line = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(12, cy + sep_h / 2.0, w - 24, 1))
                line.setWantsLayer_(True)
                line.layer().setBackgroundColor_(white(0.1).CGColor())
                container.addSubview_(line)
                continue
            cy -= row_h
            is_selected = bool(r.get("selected"))
            if is_selected:
                selected_center_y = cy + row_h / 2.0
            row = BrightenOnHoverButton.alloc().initWithFrame_(NSMakeRect(0, cy, w, row_h))
            # No background pill at all, for selected or hovered — three distinct text-only
            # brightness tiers: dim (plain row) < hover < selected-at-rest. Selected is the
            # brightest/topmost tier here (a trial swap vs. the previous hover-is-brightest
            # version) — hovering the selected row itself will read as a slight DIM rather
            # than a brighten, which is the natural side effect of putting selected on top.
            row_font = AppKit.NSFont.systemFontOfSize_(13)
            dim_color = white(1.0 if is_selected else 0.42)
            row.configureBrighten(
                r["title"], row_font, dim_color, white(0.8),
                align=AppKit.NSTextAlignmentLeft,
                label_frame=NSMakeRect(16, (row_h - 18) / 2.0, w - 32, 18))
            row.setTarget_(self)
            row.setAction_("_dropdownRowClicked:")
            row._on_click = r["on_click"]
            row._suppress_hover = suppress_hover
            hover_rows.append(row)
            container.addSubview_(row)

        if needs_scroll:
            # Rows are laid out with the first one at the TOP of the container (highest y,
            # since cy counts down from content_h) and the last one at the bottom (near y=0)
            # — but NSScrollView's clip view defaults its visible origin to (0, 0), which in
            # this layout is exactly the BOTTOM of the list, not the top. Every dropdown that
            # actually needs scrolling (28 Kokoro voices, long ElevenLabs voice lists) was
            # opening pre-scrolled to its last few rows instead of its first.
            #
            # If one row is the current selection, center it in the viewport instead — on a
            # long list (System's dozens of voices, ElevenLabs' voice library) reopening the
            # menu should show you where you already are, not force a re-scroll to confirm it.
            # Falls back to the plain top-of-list behavior above when nothing is selected
            # (e.g. the wordmark's app menu, which has no concept of a "current" row).
            clip = scroll.contentView()
            if selected_center_y is not None:
                target_origin_y = max(0.0, min(content_h - height, selected_center_y - height / 2.0))
            else:
                target_origin_y = content_h - height
            clip.scrollToPoint_(NSMakePoint(0, target_origin_y))
            scroll.reflectScrolledClipView_(clip)

            # Edge fade signals "more rows this way" the same way many chat-app scroll views
            # do — rows visibly fade out approaching whichever edge still has hidden content,
            # gone entirely at whichever edge is the actual end of the list. This has to be a
            # real alpha MASK on the scroll view's own layer, not a colored gradient drawn on
            # top of it: the panel's background is a live translucent blur (NSVisualEffectView),
            # not a flat color, so painting an opaque patch — even one color-matched to a
            # screenshot sample — reads as a visible layer sitting on top rather than the rows
            # actually dissolving into the real backdrop behind them. A mask instead reveals
            # whatever's truly behind the scroll view at each masked-out pixel.
            fade_h = 72.0
            scroll.setWantsLayer_(True)
            mask = Quartz.CAGradientLayer.layer()
            mask.setFrame_(scroll.bounds())
            mask.setStartPoint_(NSMakePoint(0.5, 0.0))
            mask.setEndPoint_(NSMakePoint(0.5, 1.0))
            # 4 fixed stops (bottom edge / bottom-of-plateau / top-of-plateau / top edge) —
            # the plateau (full white, fully visible) is everything more than fade_h away from
            # either edge. Only the two EDGE stops' own alpha changes as scrolling happens; the
            # locations themselves never move.
            mask.setLocations_([0.0, fade_h / height, 1.0 - fade_h / height, 1.0])
            scroll.layer().setMask_(mask)

            def edge_colors(bottom_alpha, top_alpha):
                # Empirically, location 0.0 renders at the visual TOP of the scroll view and
                # location 1.0 at the visual BOTTOM here — the opposite of a plain content
                # layer's usual bottom-to-top convention, most likely because NSScrollView's
                # own layer has a flipped coordinate space. Confirmed by testing (not
                # re-derived blind a second time): scrolled to the true top, the top row was
                # showing the fade meant for the bottom edge, and vice versa.
                return [
                    AppKit.NSColor.whiteColor().colorWithAlphaComponent_(top_alpha).CGColor(),
                    AppKit.NSColor.whiteColor().CGColor(),
                    AppKit.NSColor.whiteColor().CGColor(),
                    AppKit.NSColor.whiteColor().colorWithAlphaComponent_(bottom_alpha).CGColor(),
                ]

            def update_edge_mask(note=None):
                try:
                    origin_y = clip.bounds().origin.y
                    max_origin = max(0.0, content_h - height)
                    # 1.0 (fully opaque, no visible fade) right at a true edge with nothing
                    # more that way; ramps down to 0 once scrolled fade_h away from it — so
                    # the fade genuinely shrinks to nothing approaching the true edge, rather
                    # than snapping off at some fixed point. Since height is now snapped to a
                    # whole number of rows, the row nearest the edge is always complete, so
                    # fading it to 0 reads as "dissolving into the backdrop," not a dead gap.
                    bottom_alpha = 1.0 - max(0.0, min(1.0, origin_y / fade_h))
                    top_alpha = 1.0 - max(0.0, min(1.0, (max_origin - origin_y) / fade_h))
                    # CALayer property changes are implicitly animated by default — without
                    # disabling that here, the very first call (setting up the initial
                    # scrolled-to-top state) doesn't actually take visual effect until some
                    # later transaction commits, e.g. the next real scroll gesture. Which read
                    # as "the bottom fade doesn't show up until you scroll a little," exactly
                    # backwards from the intent (it should be there immediately, since being
                    # scrolled to the top is precisely when there's the most content below).
                    AppKit.CATransaction.begin()
                    AppKit.CATransaction.setDisableActions_(True)
                    mask.setColors_(edge_colors(bottom_alpha, top_alpha))
                    AppKit.CATransaction.commit()
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    sys.stderr.flush()

            update_edge_mask()
            clip.setPostsBoundsChangedNotifications_(True)
            shadow_observer = AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                AppKit.NSViewBoundsDidChangeNotification, clip, None, update_edge_mask)
            self.dropdown_scroll_observers.append(shadow_observer)

        panel.setContentView_(outer)
        self.dropdown_panel = panel
        self.dropdown_anchor = anchor
        self.window.addChildWindow_ordered_(panel, AppKit.NSWindowAbove)
        panel.setAlphaValue_(0.0)
        panel.makeKeyAndOrderFront_(None)

        def fade_in(ctx):
            ctx.setDuration_(0.12)
            panel.animator().setAlphaValue_(1.0)
        AppKit.NSAnimationContext.runAnimationGroup_(fade_in)

        self.dropdown_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskRightMouseDown, lambda e: self._closeDropdown())

        def local_handler(event):
            p = getattr(self, "dropdown_panel", None)
            if p is None:
                return event
            if event.window() is p:
                return event  # click lands on one of the dropdown's own rows; let it handle itself
            a = getattr(self, "dropdown_anchor", None)
            if a is not None and event.window() is a.window():
                pt = a.convertPoint_fromView_(event.locationInWindow(), None)
                if AppKit.NSPointInRect(pt, a.bounds()):
                    return event  # click is on the anchor button itself; its own action toggles the menu closed
            self._closeDropdown()
            return event
        self.dropdown_local_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, local_handler)

    def _dropdownRowClicked_(self, sender):
        # This row is itself a subview of the dropdown panel window. Closing/destroying that
        # panel (and everything downstream, like opening an overlay card) must not happen
        # synchronously here — that would tear down the row's own containing window in the
        # middle of its native click handling, which is the same class of hang as the earlier
        # mouseDown/CATransaction freeze. Defer the entire close+callback to the next run-loop
        # tick so this click fully finishes and control returns to AppKit first.
        cb = getattr(sender, "_on_click", None)
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.0, False, lambda t: self._dropdownRowChosen(cb))

    @objc.python_method
    def _dropdownRowChosen(self, cb):
        try:
            self._closeDropdown()
            if cb:
                cb()
        except Exception:
            # This runs inside an NSTimer block callback, one boundary removed from PyObjC's
            # normal method-dispatch trampoline — an uncaught exception here can silently abort
            # mid-way through opening a window (e.g. leaving an invisible backdrop stuck in place,
            # blocking every future click) instead of printing like a normal action-method crash.
            traceback.print_exc(file=sys.stderr)

    # ----- welcome / API key screen -----
    def showWelcomeScreen(self, show_intro=False):
        self.stopPlayback_(None)
        v = AppKit.NSView.alloc().initWithFrame_(self.root.bounds())
        b = v.bounds()

        # Reaching this screen (first launch, or "Set API Key" from the menu) must never be a
        # dead end that forces re-entering a key from scratch. If a session was already working,
        # offer a real way back out — a visible Cancel button (top-left, mirroring the
        # wordmark) plus Escape — instead of trapping the user here with no undo.
        can_cancel = self._isConfigured()
        self._teardownWelcomeEscMonitor()
        if can_cancel:
            def handler(event):
                if event.keyCode() == 53:  # Esc
                    self.cancelWelcome_(None)
                    return None
                return event
            self.welcome_esc_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskKeyDown, handler)

        # Everything below lives in one fixed-size, fixed-internal-layout container that is
        # itself centered in the window. Autoresizing masks only center an INDIVIDUAL view
        # within its superview — applying them to each element separately (the previous
        # approach) let every element drift by its own proportional margin on resize, so the
        # whole group fell out of alignment with itself. Centering one container instead keeps
        # every internal gap exactly fixed no matter the window size.
        # my is a fixed local-y reference every element's position is offset from. ch (the
        # container's own height, used only to center it in the window) is derived from my so
        # the empty margin above the icon always matches the empty margin below the lowest
        # visible element — with vs without Cancel changes what that lowest element is, so ch
        # must be recomputed per case or the whole cluster reads as top- or bottom-heavy.
        # Provider pills are measured up front to lay out the scrollable strip below, but no
        # longer widen the window itself. That worked while there were only 3-4 short names,
        # but adding "Other" pushed the row past a comfortable width, and the whole window grew
        # to fit it — which read as "the window got wider," even though the window's own sizing
        # never changed. Pills now live in a fixed-width strip that scrolls horizontally
        # instead, with the same edge-fade affordance as the scrollable dropdowns.
        pill_h, gap, h_pad = 26, 10, 18
        pill_font = AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
        pill_widths = []
        for name in PROVIDERS:
            text_w = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                name, {AppKit.NSFontAttributeName: pill_font}).size().width
            pill_widths.append(text_w + h_pad * 2)
        pills_total = sum(pill_widths) + gap * (len(PROVIDERS) - 1)

        cw = 340
        pill_strip_w = 300
        mx, my = cw / 2.0, 237.0  # my: local y equivalent to the old window-center reference
        icon_top = my + 103.0
        bottom_edge = 32.0 if can_cancel else (my - 175.0)
        ch = icon_top + bottom_edge
        container = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect((b.size.width - cw) / 2.0, (b.size.height - ch) / 2.0, cw, ch))
        container.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)

        # Every gap in this screen (icon->title, title->tagline, tagline->caption,
        # caption->pills, pills->field, field->continue, continue->cancel) is the same 12pt,
        # rather than each pair having its own independently-eyeballed spacing.
        # Box is sized to hug the visible bars (not the old 52pt circle) so the 12pt gap to
        # the title below is measured from the bars themselves, not from empty box padding.
        icon_bg = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(mx - 26, my + 79, 52, 24))
        icon_bg.setWantsLayer_(True)
        build_waveform_bars(icon_bg)

        # Title + tagline read as one lockup (headline + its label), so they sit close
        # together — tighter than the 12pt rhythm used everywhere else on this screen.
        title = make_label(f"Welcome to {APP_NAME}", 17, 0.95, AppKit.NSFontWeightBold, AppKit.NSTextAlignmentCenter)
        title.setFrame_(NSMakeRect(0, my + 45, cw, 24))
        tagline = make_label("TEXT TO SPEECH", 11, 0.35, AppKit.NSFontWeightMedium, AppKit.NSTextAlignmentCenter)
        tagline.setFrame_(NSMakeRect(0, my + 27, cw, 14))

        # self.provider must be set before the caption is built, since its text depends on it
        self.provider = self.config.get("provider", "System")

        # the caption's own two lines need more breathing room between them than the font's
        # natural leading gives — a plain make_label can't express that, so build it manually
        # with an explicit paragraph line-spacing
        caption = AppKit.NSTextField.alloc().init()
        caption.setBezeled_(False)
        caption.setDrawsBackground_(False)
        caption.setEditable_(False)
        caption.setSelectable_(False)
        self.caption = caption
        self._updateCaptionText()
        # A wider break than the 12pt rhythm used below it — this is the seam between the
        # branding half (icon/title/tagline) and the usable half (instructions down through
        # Cancel). The frame is 44pt tall but the two lines of actual text only fill about
        # 35pt of it with the empty remainder sitting below the last line (NSTextField top-
        # aligns multi-line content) — so a frame-to-frame gap of 12pt below this box was
        # actually an ~20pt VISIBLE gap down to the pills. Shifted down 8pt to compensate, so
        # the visible text-to-pills gap actually matches the pills/field/continue rhythm.
        caption.setFrame_(NSMakeRect(mx - 160, my - 45, 320, 44))

        # provider pills — content-sized (not fixed-width) so short labels like "Other" don't
        # carry the same padding as "ElevenLabs", matching the reference's pill proportions.
        # Font/widths already measured above. Laid out into a document view that's wider than
        # the visible strip; the strip scrolls (trackpad/wheel) to reveal whatever doesn't fit.
        self.pill_buttons = []
        pill_doc = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, pills_total, pill_h))
        px = 0.0
        for name, w_ in zip(PROVIDERS, pill_widths):
            btn = text_button(name, NSMakeRect(px, 0, w_, pill_h), "providerClicked:", self,
                              pill_font, 0.04, 0.14, 13.0, white(0.55))
            btn.layer().setBorderWidth_(1.0)
            self.pill_buttons.append(btn)
            pill_doc.addSubview_(btn)
            px += w_ + gap

        needs_pill_scroll = pills_total > pill_strip_w
        if needs_pill_scroll:
            pill_scroll = AppKit.NSScrollView.alloc().initWithFrame_(
                NSMakeRect(mx - pill_strip_w / 2.0, my - 75, pill_strip_w, pill_h))
            pill_scroll.setBorderType_(AppKit.NSNoBorder)
            pill_scroll.setHasHorizontalScroller_(False)
            pill_scroll.setHasVerticalScroller_(False)
            pill_scroll.setDrawsBackground_(False)
            pill_scroll.setDocumentView_(pill_doc)
            container.addSubview_(pill_scroll)

            # Reveal whichever provider is already selected even if it would otherwise land
            # off-strip — e.g. re-opening this screen with "Other" already chosen shouldn't
            # bury it at the far edge with no hint it's there.
            sel_idx = PROVIDERS.index(self.provider) if self.provider in PROVIDERS else 0
            sel_x = sum(pill_widths[:sel_idx]) + gap * sel_idx
            sel_right = sel_x + pill_widths[sel_idx]
            max_origin_x = max(0.0, pills_total - pill_strip_w)
            origin_x = 0.0
            if sel_right > origin_x + pill_strip_w:
                origin_x = sel_right - pill_strip_w
            if sel_x < origin_x:
                origin_x = sel_x
            origin_x = max(0.0, min(origin_x, max_origin_x))
            clip = pill_scroll.contentView()
            clip.scrollToPoint_(NSMakePoint(origin_x, 0))
            pill_scroll.reflectScrolledClipView_(clip)

            self._installHorizontalEdgeFade(pill_scroll, pills_total, pill_strip_w)
        else:
            pill_doc.setFrame_(NSMakeRect(mx - pills_total / 2.0, my - 75, pills_total, pill_h))
            container.addSubview_(pill_doc)
        self._stylePills()

        field_w, field_h = 300, 38
        self.key_field_box = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(mx - field_w / 2.0, my - 125, field_w, field_h))
        self.key_field_box.setWantsLayer_(True)
        self.key_field_box.layer().setBackgroundColor_(white(0.06).CGColor())
        self.key_field_box.layer().setBorderColor_(white(0.12).CGColor())
        self.key_field_box.layer().setBorderWidth_(1.0)
        self.key_field_box.layer().setCornerRadius_(10.0)

        # the secure field itself is a short, vertically-centered strip inside the box — a
        # field frame as tall as the box left text (and especially the secure-entry bullets)
        # sitting off-center and oversized
        self.key_field = AppKit.NSSecureTextField.alloc().initWithFrame_(NSMakeRect(14, 0, field_w - 28, 20))
        self.key_field.setBezeled_(False)
        self.key_field.setDrawsBackground_(False)
        self.key_field.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        self.key_field.setTextColor_(white(0.92))
        self.key_field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
        self.key_field.setTarget_(self)
        self.key_field.setAction_("saveApiKey:")  # Enter submits
        self.key_field.setDelegate_(self)
        self.key_field.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
        self.key_field_box.addSubview_(self.key_field)
        # let AppKit compute its own natural height for this font, then center exactly that —
        # guessing a fixed inset was fragile: the field's actual glyph position doesn't sit
        # centered within an arbitrary frame height, so it kept drifting toward one edge.
        self.key_field.sizeToFit()
        fitted = self.key_field.frame()
        self.key_field.setFrame_(NSMakeRect(14, (field_h - fitted.size.height) / 2.0, field_w - 28, fitted.size.height))
        # Pre-fill with whatever key is already saved for this provider — landing on this
        # screen (first launch, or "Set API Key" from the menu) must never present as an empty
        # field the user has to go refill from their browser. Each provider's key is kept
        # independently, so switching providers and switching back doesn't lose anything either.
        self.key_field.setStringValue_(self._storedKeyFor(self.provider))
        self._updateKeyPlaceholder()

        # Shown INSIDE the key field's own box, in the same spot the key/placeholder occupies —
        # a rejected key gets cleared (see keyValidationFailedMain_) and this appears in its
        # place, rather than a separate message elsewhere on the screen. The box is narrower
        # than the longest real message needs on one line, so this wraps to two lines instead
        # of truncating — the box (38pt tall) has enough room for two lines at this size.
        # ClickThroughTextField, not make_label: this sits directly on top of key_field (see
        # below) and must never block clicks/typing meant for the field underneath — including
        # while an error is showing, since typing to correct the key is exactly what dismisses it.
        self.key_error_label = ClickThroughTextField.alloc().init()
        self.key_error_label.setStringValue_("")
        self.key_error_label.setBezeled_(False)
        self.key_error_label.setDrawsBackground_(False)
        self.key_error_label.setEditable_(False)
        self.key_error_label.setSelectable_(False)
        self.key_error_label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightRegular))
        self.key_error_label.setTextColor_(white(0.85))
        self.key_error_label.setAlignment_(AppKit.NSTextAlignmentLeft)
        self.key_error_label.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        self.key_error_label.setFrame_(NSMakeRect(14, (field_h - fitted.size.height) / 2.0, field_w - 28, fitted.size.height))
        self.key_error_label.setAlphaValue_(0.0)
        self.key_field_box.addSubview_(self.key_error_label)

        # Shown in the exact same spot as the key field/error, when "System" is selected —
        # there's nothing to type, so this replaces the field entirely rather than sitting on
        # top of it (unlike key_error_label, which overlays a still-usable field).
        self.system_info_label = make_label(
            "Free & offline — no API key needed", 12, 0.55, align=AppKit.NSTextAlignmentCenter)
        self.system_info_label.setFrame_(NSMakeRect(14, (field_h - 16) / 2.0, field_w - 28, 16))
        self.key_field_box.addSubview_(self.system_info_label)
        self._updateKeyFieldMode()

        # 12pt gap below the key field, matching the pills-to-field gap exactly (both measure
        # 12pt from the element above), so pills/field/continue/cancel all have identical
        # spacing rather than three visually-different-sized gaps
        # text_button, NOT cta_button — cta_button deliberately disables HoverButton's native
        # hover-fill mechanism (it wants a flat static color), which is exactly why this button
        # had zero hover/press feedback despite looking interactive. Same base as the pills
        # (0.04/white(0.1) border), but hover dialed back to 0.10 (pills use 0.14) — this
        # button covers a lot more area than a pill does, and the identical alpha read as
        # noticeably brighter here simply from covering more of the visual field, confirmed
        # directly.
        continue_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold)
        self.continue_btn = text_button(
            "Continue", NSMakeRect(mx - field_w / 2.0, my - 175, field_w, 38), "saveApiKey:", self,
            continue_font, 0.04, 0.10, 9.0, white(0.95))
        self.continue_btn.layer().setBorderWidth_(1.0)
        self._updateContinueState()

        # Shown in continue_btn's exact slot only while sesame_assets (the ~1.6GB CSM model +
        # stock voices) are being downloaded after a valid Sesame key is entered — see
        # _validateKeyWorker/sesameDownloadProgressMain_. Same slot-swap idea as
        # system_info_label replacing key_field for keyless providers: one state visible at a
        # time in the same spot, not a separate area of the screen to make room for.
        self.sesame_download_box = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(mx - field_w / 2.0, my - 175, field_w, 38))
        self.sesame_download_box.setWantsLayer_(True)
        # Genuinely opaque, NOT the white(alpha) helper — white() is white-at-alpha, so even
        # white(0.04) is a barely-there tint that let continue_btn's own text show straight
        # through it once brought to front. colorWithWhite_alpha_(_, 1.0) is fully opaque at
        # this same dark brightness, actually hiding what's behind it.
        self.sesame_download_box.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithWhite_alpha_(0.08, 1.0).CGColor())
        self.sesame_download_box.layer().setCornerRadius_(9.0)
        self.sesame_download_box.layer().setMasksToBounds_(True)  # clips the bottom progress bar to the rounded corners
        self.sesame_download_box.setHidden_(True)
        self.sesame_download_label = make_label(
            "Downloading Sesame voices…", 11, 0.7, align=AppKit.NSTextAlignmentCenter)
        self.sesame_download_label.setFrame_(NSMakeRect(0, 12, field_w, 14))  # vertically centered in the 38pt box
        self.sesame_download_box.addSubview_(self.sesame_download_label)
        # A thin accent strip along the bottom edge, not a full-height bar — same track/fill
        # widget as the mic level meter, just much shorter.
        self.sesame_download_bar = LevelMeterView.alloc().initWithFrame_(NSMakeRect(0, 0, field_w, 3))
        self.sesame_download_bar.configure()
        self.sesame_download_box.addSubview_(self.sesame_download_bar)

        extras = [icon_bg, title, tagline, caption, self.key_field_box, self.continue_btn, self.sesame_download_box]
        if can_cancel:
            # same look as before (font/colors/hover untouched) — just moved from the top-left
            # corner to directly under Continue
            cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold)
            # NSButton centers its title within the frame, so an equal 12pt frame-to-frame gap
            # from Continue reads as a visibly bigger gap than pills->field/field->continue —
            # shifted up 8pt so the visible text sits the same distance from Continue as those.
            cancel_btn = text_button(
                "Cancel", NSMakeRect(mx - 32, 32, 64, 26),
                "cancelWelcome:", self, cancel_font, 0.0, 0.12, 7.0, white(0.55))
            extras.append(cancel_btn)

        if show_intro:
            # The splash phase shows only the logo + title + tagline (see _presentWelcomeIntro)
            # — the caption is instructions FOR the api controls, so it belongs with them, not
            # with the hero. The hero (icon/title/tagline) just fades in at its already-correct
            # small position once the splash shrinks away; caption + the api controls fade in
            # AND slide up into their final positions.
            reveal_views = [caption] + list(self.pill_buttons) + [self.key_field_box, self.continue_btn]
            for view in [icon_bg, title, tagline] + reveal_views:
                view.setAlphaValue_(0.0)
            slide_from = []
            for view in reveal_views:
                f = view.frame()
                slide_from.append((view, NSMakePoint(f.origin.x, f.origin.y)))
                view.setFrameOrigin_(NSMakePoint(f.origin.x, f.origin.y + 55))

        for sub in extras:
            container.addSubview_(sub)
        v.addSubview_(container)

        # Small version stamp pinned to the window's own bottom edge (independent of the
        # centered container, which resizes with/without Cancel) — a fixed detail like this
        # reads as a finished, versioned product rather than a work-in-progress screen.
        version_label = make_label(f"v{APP_VERSION} ({APP_BUILD})", 10, 0.28, AppKit.NSFontWeightRegular, AppKit.NSTextAlignmentCenter)
        version_label.setFrame_(NSMakeRect(0, 14, b.size.width, 14))
        version_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMaxYMargin)
        v.addSubview_(version_label)

        self.current_screen = "welcome"
        self.swap_screen(v)

        if show_intro:
            self._presentWelcomeIntro(v, [icon_bg, title, tagline], slide_from)

    @objc.python_method
    def _presentWelcomeIntro(self, v, hero_views, slide_from):
        # First-launch only: a big, properly-sized (not transform-scaled, so it stays crisp for
        # the whole hold instead of just the transition) splash of the animated logo + title +
        # tagline, shown alone for a few seconds, then shrinks + fades away while the real hero
        # fades in at its normal size and the caption + api controls fade in while sliding up
        # into their final positions. No caption here — that's instructions FOR the api
        # controls, so it belongs with them, not with this identity-only splash.
        b = v.bounds()
        gw, gh = 320, 140
        intro_group = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect((b.size.width - gw) / 2.0, (b.size.height - gh) / 2.0, gw, gh))
        intro_group.setAutoresizingMask_(
            AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)

        icon_bg2 = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(gw / 2.0 - 42, 84, 84, 40))
        icon_bg2.setWantsLayer_(True)
        build_waveform_bars(icon_bg2, scale=84.0 / 52.0)

        title2 = make_label(f"Welcome to {APP_NAME}", 26, 0.95, AppKit.NSFontWeightBold, AppKit.NSTextAlignmentCenter)
        title2.setFrame_(NSMakeRect(0, 40, gw, 32))
        tagline2 = make_label("TEXT TO SPEECH", 13, 0.35, AppKit.NSFontWeightMedium, AppKit.NSTextAlignmentCenter)
        tagline2.setFrame_(NSMakeRect(0, 16, gw, 16))

        for sub in (icon_bg2, title2, tagline2):
            intro_group.addSubview_(sub)
        v.addSubview_(intro_group)
        intro_group.setWantsLayer_(True)
        fix_anchor(intro_group)
        natural_position = intro_group.layer().position()

        def reveal(t):
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setAnimationDuration_(0.7)
            AppKit.CATransaction.setAnimationTimingFunction_(
                Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseInEaseOut))
            intro_group.layer().setPosition_(NSMakePoint(natural_position.x, natural_position.y + 90))
            intro_group.layer().setTransform_(Quartz.CATransform3DMakeScale(0.4, 0.4, 1.0))
            AppKit.CATransaction.commit()

            def anim(ctx):
                ctx.setDuration_(0.7)
                intro_group.animator().setAlphaValue_(0.0)
                for view in hero_views:
                    view.animator().setAlphaValue_(1.0)
                for view, origin in slide_from:
                    view.animator().setFrameOrigin_(origin)
                    view.animator().setAlphaValue_(1.0)
            AppKit.NSAnimationContext.runAnimationGroup_(anim)

            def cleanup(t2):
                intro_group.removeFromSuperview()
            AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.8, False, cleanup)
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(3.5, False, reveal)

    @objc.python_method
    def _keyPlaceholder(self):
        return {"OpenAI": "OpenAI API key (sk-...)", "Other": "API key", "Sesame": "SonoScript license key"}.get(
            self.provider, "ElevenLabs API key")

    @objc.python_method
    def _captionText(self):
        if self.provider == "System":
            return "Uses your Mac's built-in text-to-speech.\nFree, offline, no account needed."
        if self.provider == "Chatterbox":
            return "A higher-quality offline voice, built into the app.\nFree, no account needed, no downloads required."
        if self.provider == "Sesame":
            return "Clone your own voice — offline, private to this Mac.\nPaste your license key (first time downloads ~1.6GB)."
        return ("Connect a text-to-speech provider to get started.\n"
                "Paste an API key from your provider's account page.")

    @objc.python_method
    def _updateCaptionText(self):
        style = AppKit.NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(AppKit.NSTextAlignmentCenter)
        style.setLineSpacing_(6.0)
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(12),
            AppKit.NSForegroundColorAttributeName: white(0.5),
            AppKit.NSParagraphStyleAttributeName: style,
        }
        self.caption.setAttributedStringValue_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(self._captionText(), attrs))

    @objc.python_method
    def _updateKeyFieldMode(self):
        # System/Chatterbox need no key at all — swap the secure field for a plain explanatory
        # line in the exact same spot, rather than reflowing the whole screen's layout around it.
        is_keyless = self.provider in KEYLESS_PROVIDERS
        self.key_field.setHidden_(is_keyless)
        self.key_error_label.setHidden_(is_keyless)
        self.system_info_label.setHidden_(not is_keyless)

    @objc.python_method
    def _storedKeyFor(self, provider):
        return self.config.get("api_keys", {}).get(provider, "")

    @objc.python_method
    def _isConfigured(self):
        # System/Chatterbox need no key at all — an api_key is only required for the other providers.
        provider = self.config.get("provider")
        if provider in KEYLESS_PROVIDERS:
            return True
        if provider == "Sesame":
            from license import verify_license, LicenseError
            try:
                verify_license(self.config.get("api_key", ""))
                return True
            except LicenseError:
                return False
        return bool(self.config.get("api_key"))

    @objc.python_method
    def _updateKeyPlaceholder(self):
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(12),
            AppKit.NSForegroundColorAttributeName: white(0.45),
        }
        self.key_field.setPlaceholderAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(self._keyPlaceholder(), attrs))

    @objc.python_method
    def _stylePills(self):
        for btn in self.pill_buttons:
            sel = str(btn.title()) == self.provider
            btn.layer().setBackgroundColor_(white(0.16 if sel else 0.04).CGColor())
            btn.layer().setBorderColor_(white(0.3 if sel else 0.1).CGColor())
            font = AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
            attrs = {AppKit.NSFontAttributeName: font,
                     AppKit.NSForegroundColorAttributeName: white(0.95 if sel else 0.55)}
            btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(str(btn.title()), attrs))
            btn._base_alpha = 0.16 if sel else 0.04

    @objc.python_method
    def _installHorizontalEdgeFade(self, scroll, content_w, viewport_w):
        # Same idea as the scrollable dropdown's vertical edge fade (see _showDropdown), turned
        # 90 degrees: pills dissolve toward whichever side still has more scrolled off, and go
        # fully opaque at the true left/right end of the strip. Kept as its own copy rather than
        # sharing code with _showDropdown — that implementation is tuned and shipped, and this
        # is a different axis on a different screen, not worth the risk of a shared abstraction
        # regressing it.
        fade_w = 48.0
        scroll.setWantsLayer_(True)
        mask = Quartz.CAGradientLayer.layer()
        mask.setFrame_(scroll.bounds())
        mask.setStartPoint_(NSMakePoint(0.0, 0.5))
        mask.setEndPoint_(NSMakePoint(1.0, 0.5))
        mask.setLocations_([0.0, fade_w / viewport_w, 1.0 - fade_w / viewport_w, 1.0])
        scroll.layer().setMask_(mask)
        clip = scroll.contentView()

        def edge_colors(left_alpha, right_alpha):
            return [
                AppKit.NSColor.whiteColor().colorWithAlphaComponent_(left_alpha).CGColor(),
                AppKit.NSColor.whiteColor().CGColor(),
                AppKit.NSColor.whiteColor().CGColor(),
                AppKit.NSColor.whiteColor().colorWithAlphaComponent_(right_alpha).CGColor(),
            ]

        def update_edge_mask(note=None):
            try:
                origin_x = clip.bounds().origin.x
                max_origin = max(0.0, content_w - viewport_w)
                left_alpha = 1.0 - max(0.0, min(1.0, origin_x / fade_w))
                right_alpha = 1.0 - max(0.0, min(1.0, (max_origin - origin_x) / fade_w))
                AppKit.CATransaction.begin()
                AppKit.CATransaction.setDisableActions_(True)
                mask.setColors_(edge_colors(left_alpha, right_alpha))
                AppKit.CATransaction.commit()
            except Exception:
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()

        update_edge_mask()
        clip.setPostsBoundsChangedNotifications_(True)
        self.welcome_pill_observer = AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AppKit.NSViewBoundsDidChangeNotification, clip, None, update_edge_mask)

    def providerClicked_(self, sender):
        self.provider = str(sender.title())
        self._stylePills()
        self._updateKeyPlaceholder()
        self._updateCaptionText()
        self._updateKeyFieldMode()
        # switching providers shows THAT provider's own saved key (if any) instead of clearing
        # the field — each provider's key is remembered independently, so hopping between
        # ElevenLabs and OpenAI never means retyping either one
        self.key_field.setStringValue_(self._storedKeyFor(self.provider))
        self._updateContinueState()

    def controlTextDidChange_(self, notification):
        if notification.object() is getattr(self, "key_field", None):
            self._updateContinueState()
            # typing again makes any error message stale — hide it immediately (instead of
            # leaving it to its own timer) and bring back the normal placeholder
            error_label = getattr(self, "key_error_label", None)
            if error_label is not None and error_label.alphaValue() > 0:
                timer = self._fade_timers.pop(id(error_label), None)
                if timer is not None:
                    timer.invalidate()
                error_label.setAlphaValue_(0.0)
                self._updateKeyPlaceholder()
        elif notification.object() is getattr(self, "rec_name_field", None):
            self._updateRecordSaveState()

    def controlTextDidEndEditing_(self, notification):
        # Commits a Manage Voices rename once a name field loses focus (click away, Tab, or
        # Enter) — a per-keystroke commit would mean racing to save a half-typed name against
        # every other row's field on the same screen.
        field = notification.object()
        # A raw NSTextField (unlike a custom subclass such as HoverButton) rejects arbitrary
        # Python attributes outright — self._manage_voice_fields (voice_id -> field) is the
        # forward mapping built in _buildVoicesSection; find this field's id by identity
        # instead of trying to tag the field itself.
        voice_id = next((vid for vid, f in self._manage_voice_fields.items() if f is field), None)
        if voice_id is None:
            return
        name = str(field.stringValue()).strip()
        entries = self.config.get("sesame_custom_voices", [])
        entry = next((e for e in entries if e["id"] == voice_id), None)
        if entry is not None:
            if name:
                entry["label"] = name
                save_config(self.config)
            else:
                field.setStringValue_(entry["label"])  # blank name — restore rather than keep it empty
        field.endEditingAppearance()

    @objc.python_method
    def _updateContinueState(self):
        enabled = self.provider in KEYLESS_PROVIDERS or bool(str(self.key_field.stringValue()).strip())
        # Deliberately NOT calling self.continue_btn.setEnabled_(enabled) — NSButton applies
        # its own automatic dimming to a disabled control's content on top of whatever
        # attributed-title color is set, which was compounding with white(0.45) below and
        # rendering darker than the actual placeholder text it was supposed to match.
        # saveApiKey_ already no-ops on an empty key, so disabling isn't needed for correctness.
        # Background is NOT touched here anymore — continue_btn is a real HoverButton now
        # (configure(0.06, 0.14, ...) at construction), so its own _fill mechanism already
        # owns the resting/hover background; setting it again here would just be redundant.
        # Enabled/disabled is conveyed by text brightness only, same as before.
        self.continue_btn.layer().setBorderColor_(white(0.1).CGColor())
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold),
            # disabled: exact same luminosity as the key field's placeholder text (white(0.45)
            # in _updateKeyPlaceholder) — that's the explicit reference point that was asked for
            AppKit.NSForegroundColorAttributeName: white(0.95 if enabled else 0.45),
        }
        self.continue_btn.setAttributedTitle_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("Continue", attrs))

    def saveApiKey_(self, sender):
        if self.provider in KEYLESS_PROVIDERS:
            # Nothing to validate over the network — just commit the choice and go.
            self.config["provider"] = self.provider
            save_config(self.config)
            self.showMainScreen()
            self.fetchVoices()
            return
        key = str(self.key_field.stringValue()).strip()
        if not key:
            return
        self.continue_btn.setEnabled_(False)
        # Sesame's worker can take a while (a fresh ~1.6GB asset download, see below) — without
        # also disabling the field, Enter still fires saveApiKey_ again mid-download (the
        # field's own action, independent of continue_btn), spinning up a second worker that
        # downloads into the same temp files as the first.
        self.key_field.setEnabled_(False)
        threading.Thread(target=self._validateKeyWorker, args=(key, self.provider), daemon=True).start()

    @objc.python_method
    def _validateKeyWorker(self, key, provider):
        try:
            if provider == "ElevenLabs":
                self._request_json(f"{EL_API}/user", {"xi-api-key": key})
            elif provider == "OpenAI":
                self._request_json(f"{OPENAI_API}/models", {"Authorization": f"Bearer {key}"})
            elif provider == "Sesame":
                from license import verify_license, LicenseError
                try:
                    verify_license(key)
                except LicenseError as e:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "keyValidationFailedMain:", str(e), False)
                    return
                import sesame_download
                if not sesame_download.sesame_assets_ready():
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "sesameDownloadStartingMain:", None, False)
                    # Marshaling every 1MB chunk to the main thread would be ~1600 dispatches
                    # for the full download — cheap individually, but pointless that often;
                    # only crossing a whole percentage point actually changes what's on screen.
                    last_reported = -1

                    def on_progress(downloaded, total):
                        nonlocal last_reported
                        pct = int(downloaded * 100 / total) if total else 0
                        if pct != last_reported:
                            last_reported = pct
                            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                                "sesameDownloadProgressMain:", {"downloaded": downloaded, "total": total}, False)
                    try:
                        sesame_download.download_sesame_assets(progress_cb=on_progress)
                    except sesame_download.DownloadError as e:
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "sesameDownloadFailedMain:", str(e), False)
                        return
            # "Other": no known shape to validate against; accept as entered
        except urllib.error.HTTPError as e:
            msg = (
                "Double check and try again."
                if e.code in (401, 403) else f"Request failed (HTTP {e.code})."
            )
            self.performSelectorOnMainThread_withObject_waitUntilDone_("keyValidationFailedMain:", msg, False)
            return
        except urllib.error.URLError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "keyValidationFailedMain:", f"Could not reach {provider}: {e.reason}", False)
            return
        self.performSelectorOnMainThread_withObject_waitUntilDone_("keyValidatedMain:", key, False)

    def keyValidationFailedMain_(self, message):
        try:
            self.key_field.setEnabled_(True)
            self.continue_btn.setEnabled_(True)
            self.key_field.setStringValue_("")  # clear the rejected key
            self.key_field.setPlaceholderString_("")  # don't let it show through the error text
            self._updateContinueState()
            self._flashInlineError(self.key_error_label, str(message))
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def sesameDownloadStartingMain_(self, _):
        try:
            self.sesame_download_label.setStringValue_("Downloading Sesame voices…")
            self.sesame_download_bar.setLevel_(0.0)
            self.sesame_download_box.setHidden_(False)
            # setHidden_ alone was confirmed (via direct testing) to not reliably take visual
            # effect for continue_btn in this window — re-adding an already-present subview
            # moves it to the front of the z-order, which AppKit DOES respect here regardless.
            # sesame_download_box has its own opaque background (see construction above), so
            # bringing it to front fully covers continue_btn — both visually and for hit-testing,
            # since the frontmost view at a given point wins mouse clicks — without depending on
            # continue_btn's hidden flag actually working.
            self.sesame_download_box.superview().addSubview_(self.sesame_download_box)
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def sesameDownloadProgressMain_(self, payload):
        try:
            downloaded = float(payload["downloaded"])
            total = float(payload["total"]) or 1.0
            gb = lambda n: f"{n / (1024 ** 3):.2f} GB"
            self.sesame_download_label.setStringValue_(
                f"Downloading Sesame voices — {gb(downloaded)} / {gb(total)}")
            self.sesame_download_bar.setLevel_(downloaded / total)
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def sesameDownloadFailedMain_(self, message):
        try:
            self.sesame_download_box.setHidden_(True)
            self.continue_btn.superview().addSubview_(self.continue_btn)  # bring back to front — see sesameDownloadStartingMain_
            self.key_field.setEnabled_(True)
            self.continue_btn.setEnabled_(True)
            # Unlike keyValidationFailedMain_, the key itself was valid — only the download
            # failed (network hiccup, etc.) — clearing a perfectly good key here just to force
            # retyping it would be actively unhelpful, so it's left in place. Continue re-runs
            # saveApiKey_ from scratch, which re-validates (fast, local) then retries the
            # download, since sesame_assets_ready() still reads false.
            self._flashInlineError(self.key_error_label, str(message))
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def keyValidatedMain_(self, key):
        try:
            self.config["api_key"] = str(key)
            self.config["provider"] = self.provider
            # keep every provider's key, not just the active one, so switching providers later
            # (or reopening "Set API Key" on a different provider) never has to be retyped
            api_keys = self.config.setdefault("api_keys", {})
            api_keys[self.provider] = str(key)
            save_config(self.config)
            self.showMainScreen()
            self.fetchVoices()
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def resetApiKey_(self, sender):
        self.showWelcomeScreen()

    def cancelWelcome_(self, sender):
        self._teardownWelcomeEscMonitor()
        self.showMainScreen()
        self.fetchVoices()

    @objc.python_method
    def _teardownWelcomeEscMonitor(self):
        if self.welcome_esc_monitor is not None:
            AppKit.NSEvent.removeMonitor_(self.welcome_esc_monitor)
            self.welcome_esc_monitor = None
        # The pill strip's bounds-change observer is registered on the clip view every time
        # this screen is (re)built (resetApiKey_ can re-enter it repeatedly) — without removing
        # the old one first, each re-entry leaks another observer whose block still holds a
        # strong reference to the torn-down scroll view and its clip.
        if self.welcome_pill_observer is not None:
            AppKit.NSNotificationCenter.defaultCenter().removeObserver_(self.welcome_pill_observer)
            self.welcome_pill_observer = None

    # ----- main screen -----
    def showMainScreen(self):
        # Captured before text_view gets reassigned below (a fresh NSTextView every call, same
        # as every other screen's own content) — without this, any round-trip through History/
        # Settings/Recordings that DOESN'T end in an explicit setString_ call of its own (e.g.
        # just clicking Back after changing a Settings pill, with nothing typed and never
        # played) silently lost whatever was typed but never generated. Confirmed via direct
        # reproduction: text was genuinely empty after such a round-trip, not just visually.
        prior_text = str(self.text_view.string()) if getattr(self, "text_view", None) is not None else ""
        self._teardownWelcomeEscMonitor()
        if self._list_scroll_observer:
            nc = AppKit.NSNotificationCenter.defaultCenter()
            for token in self._list_scroll_observer:
                nc.removeObserver_(token)
            self._list_scroll_observer = []
        if self._width_rebuild_observer is not None:
            AppKit.NSNotificationCenter.defaultCenter().removeObserver_(self._width_rebuild_observer)
            self._width_rebuild_observer = None
        if self._width_rebuild_timer is not None:
            self._width_rebuild_timer.invalidate()
            self._width_rebuild_timer = None
        v = AppKit.NSView.alloc().initWithFrame_(self.root.bounds())
        b = v.bounds()
        W = b.size.width

        self.usage_label = make_label("", 11, 0.5)
        self.usage_label.setFrame_(NSMakeRect(20, b.size.height - 58, W - 40, 16))
        self.usage_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)

        # text card (flexible height); controls live below it. 156 instead of 128 leaves room
        # for the scrubber row (124-148) between the card and the transport controls (70-112).
        card_bottom = 156
        self.card = ShimmerBorderView.alloc().initWithFrame_(NSMakeRect(20, card_bottom, W - 40, b.size.height - 58 - 8 - card_bottom))
        self.card.setWantsLayer_(True)
        self.card.layer().setBackgroundColor_(white(0.06).CGColor())
        self.card.layer().setBorderColor_(white(0.10).CGColor())
        self.card.layer().setBorderWidth_(1.0)
        self.card.layer().setCornerRadius_(14.0)
        self.card.layer().setMasksToBounds_(True)
        self.card.configureShimmerBorder(14.0, white(0.35))
        self.card.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        cb = self.card.bounds()
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 26, cb.size.width, cb.size.height - 26)
        )
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        self.text_view = FocusTextView.alloc().initWithFrame_(scroll.bounds())
        self._body_font = AppKit.NSFont.systemFontOfSize_(14)
        self.text_view.setFont_(self._body_font)
        self.text_view.setRichText_(False)
        self.text_view.setDrawsBackground_(False)
        # Layer-backed so a word-highlight overlay (System voice, "Highlight" style — see
        # _syncWordHighlightNow) can be added as a sublayer. Rebuilt fresh here since text_view
        # itself is rebuilt fresh every showMainScreen() call — _highlight_overlay is reset to
        # None alongside it so it gets lazily recreated against the new text_view/layer.
        self.text_view.setWantsLayer_(True)
        self._highlight_overlay = None
        self._highlight_word_index = -1
        self._highlight_chunk_index = None
        self._highlight_search_cursor = 0
        self._highlight_prev_range = None
        # A bare reassignment here (rather than _invalidateHighlightTimer()) leaves whatever
        # real NSTimer this pointed to still scheduled on the run loop — an NSTimer added via
        # addTimer_forMode_ is retained by the run loop itself, independent of this Python
        # attribute, so dropping the reference orphans it rather than cancelling it. Confirmed
        # directly as the cause of a real reported bug: return from Settings mid-playback and
        # the highlight starts jumping erratically — that orphaned timer keeps firing on its own
        # schedule after _syncWordHighlightNow (below) has already armed a second, independent
        # chain, both unconditionally rescheduling themselves off the same shared state and
        # stepping on each other.
        self._invalidateHighlightTimer()
        self.text_view.setTextContainerInset_(NSMakeSize(10, 10))
        self.text_view.setVerticallyResizable_(True)
        self.text_view.setHorizontallyResizable_(False)
        self.text_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        # allowsUndo defaults to NO for a plain (non-field-editor) NSTextView — without this,
        # Cmd-Z/Cmd-Shift-Z are silent no-ops no matter how the undo manager itself resolves.
        self.text_view.setAllowsUndo_(True)
        # A programmatically-created NSTextView defaults spell-checking off. Continuous
        # checking (the red squiggle) only, not automatic correction — this box is mostly
        # pasted text from elsewhere, and silently rewriting someone's pasted words would be
        # far worse than leaving an actual typo unflagged.
        self.text_view.setContinuousSpellCheckingEnabled_(True)
        self.text_view.setGrammarCheckingEnabled_(True)
        self.text_view.setAutomaticSpellingCorrectionEnabled_(False)
        self.text_view.setDelegate_(self)
        self.text_view.focus_callback = self._cardFocusChanged
        scroll.setDocumentView_(self.text_view)
        self.scroll_view = scroll
        # A layer-backed NSView always composites its sublayers on top of its own drawn content,
        # so the word-highlight overlay (_ensureHighlightOverlay) can never sit behind text_view's
        # own glyphs if it's parented to text_view's own layer. The clip view is text_view's real
        # superview and sits behind it in the actual view hierarchy — giving it its own layer lets
        # the overlay be inserted there instead, genuinely behind the text.
        self.scroll_view.contentView().setWantsLayer_(True)
        self.scroll_view.contentView().layer().setMasksToBounds_(True)

        # ClickThroughTextField, not make_label: this sits on top of the scroll view's top
        # edge (see below), and setHidden_ only keeps it out of the way once there's text —
        # on a blank card it's still there, and it's the exact spot a first-time user is
        # invited to click, so it must never be able to swallow that click.
        self.placeholder_label = ClickThroughTextField.alloc().init()
        self.placeholder_label.setStringValue_(PLACEHOLDER_TEXT)
        self.placeholder_label.setBezeled_(False)
        self.placeholder_label.setDrawsBackground_(False)
        self.placeholder_label.setEditable_(False)
        self.placeholder_label.setSelectable_(False)
        self.placeholder_label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(14, AppKit.NSFontWeightRegular))
        self.placeholder_label.setTextColor_(white(0.4))
        self.placeholder_label.setFrame_(NSMakeRect(14, cb.size.height - 32, cb.size.width - 28, 20))
        self.placeholder_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)

        self.char_count_label = make_label("0 characters", 11, 0.35, align=AppKit.NSTextAlignmentRight)
        self.char_count_label.setFrame_(NSMakeRect(0, 8, cb.size.width - 14, 14))
        self.char_count_label.setAutoresizingMask_(AppKit.NSViewWidthSizable)

        self.card.addSubview_(scroll)
        self.card.addSubview_(self.placeholder_label)
        self.card.addSubview_(self.char_count_label)

        # control row
        row = ControlRow.alloc().initWithFrame_(NSMakeRect(20, 70, W - 40, 42))
        row.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        row.delegate = self
        self.paste_btn = icon_button("clipboard", 14, NSMakeRect(0, 0, 38, 38), "pasteClicked:", self)
        self.stop_btn = icon_button("stop.fill", 11, NSMakeRect(0, 0, 38, 38), "stopPlayback:", self)
        self.back_btn = icon_button("gobackward.15", 16, NSMakeRect(0, 0, 40, 40), "skipBack:", self, base=0.0, hover=0.10, corner=20.0)
        self.play_btn = icon_button("play.fill", 12, NSMakeRect(0, 0, 38, 38), "playPauseClicked:", self, base=0.14, hover=0.24, corner=19.0, tint=1.0)
        self.fwd_btn = icon_button("goforward.15", 16, NSMakeRect(0, 0, 40, 40), "skipForward:", self, base=0.0, hover=0.10, corner=20.0)
        self.speed_popup = FlatPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 66, 38), False)
        self.speed_popup.setTarget_(self)
        self.speed_popup.setAction_("speedChanged:")
        self._populateSpeedMenu()
        for sub in (self.paste_btn, self.stop_btn, self.back_btn, self.play_btn, self.fwd_btn, self.speed_popup):
            row.addSubview_(sub)
        row.setNeedsLayout_(True)

        # scrubber row — sits between the text card and the transport controls, 12pt gap on
        # both sides (matching the rhythm used elsewhere). Whole-document position, not just
        # the currently loaded chunk: see _seekToVirtualTime/_updateScrubberUI.
        self.elapsed_label = make_label("0:00", 10, 0.5)
        self.elapsed_label.setFrame_(NSMakeRect(20, 130, 36, 14))
        self.remaining_label = make_label("0:00", 10, 0.5, align=AppKit.NSTextAlignmentRight)
        self.remaining_label.setFrame_(NSMakeRect(W - 56, 130, 36, 14))
        self.remaining_label.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        self.scrubber = ScrubberView.alloc().initWithFrame_(NSMakeRect(64, 124, W - 128, 24))
        self.scrubber.configure()
        self.scrubber.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.scrubber.on_scrub = self._scrubberDragged
        self.scrubber.on_scrub_end = self._scrubberReleased

        self.status_label = PulsingLabel.alloc().init()
        self.status_label.configurePulse(
            AppKit.NSFont.systemFontOfSize_weight_(10, AppKit.NSFontWeightRegular),
            white(0.32), white(0.95), align=AppKit.NSTextAlignmentCenter)
        self.status_label.setFrame_(NSMakeRect(20, 52, W - 40, 13))
        self.status_label.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.status_label.setAlphaValue_(0.0)

        voice_lbl = make_label("Voice", 13, 0.85)
        voice_lbl.setFrame_(NSMakeRect(20, 20, 44, 20))
        self.volume_btn = icon_button("speaker.wave.2.fill", 13, NSMakeRect(W - 20 - 32, 15, 32, 32),
                                       "volumeClicked:", self, base=0.0, hover=0.10, corner=16.0)
        self.volume_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        self.voice_popup = FlatPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(72, 14, W - 92 - 40, 36), False)
        self.voice_popup.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.voice_popup.setTarget_(self)
        self.voice_popup.setAction_("voiceChanged:")

        for sub in (self.usage_label, self.card, row, self.elapsed_label, self.remaining_label,
                    self.scrubber, self.status_label, voice_lbl, self.voice_popup, self.volume_btn):
            v.addSubview_(sub)
        # Restores what a freshly-rebuilt voice_popup/text_view would otherwise silently lose —
        # see the comments where prior_text and _voice_labels are captured/cached. Callers that
        # want something ELSE showing (e.g. _playHistoryEntry, _playSavedEntry) already call
        # setString_ themselves right after showMainScreen() returns, which still correctly
        # overrides this.
        if self._voice_labels:
            self._populateVoiceMenu(self._voice_labels)
        if prior_text:
            self.text_view.setString_(prior_text)
        self.current_screen = "main"
        self.swap_screen(v)
        self._syncPlaybackUI()
        # Re-announces whatever status was showing before this rebuild (e.g. "Generating...")
        # on the brand-new status_label above — see setStatus's own comment for why this is
        # needed at all: nothing else re-fires that call just because the view came back.
        self.setStatus(getattr(self, "_status_text", ""))
        self.updateCharCount()
        # Handles a Settings/History/etc round-trip that rebuilds text_view mid-playback (e.g.
        # changing highlight style while something is already playing) — its own guard clauses
        # make this a safe no-op when nothing is actually playing.
        self._syncWordHighlightNow()

    @objc.python_method
    def _cardFocusChanged(self, focused):
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setAnimationDuration_(0.25)
        self.card.layer().setBorderColor_(white(0.25 if focused else 0.10).CGColor())
        AppKit.CATransaction.commit()

    def textDidChange_(self, notification):
        self.updateCharCount()

    def undoManagerForTextView_(self, view):
        # Explicit undo-manager source for the text view's delegate, rather than relying
        # implicitly on responder-chain resolution up to the window.
        return self.window.undoManager()

    @objc.python_method
    def updateCharCount(self):
        text = str(self.text_view.string())
        self.placeholder_label.setHidden_(bool(text))
        self.char_count_label.setStringValue_(f"{len(text):,} characters")

    @objc.python_method
    def setStatus(self, text):
        # Tracked independently of status_label itself — showMainScreen() rebuilds a brand-new
        # status_label (same as text_view/scroll_view) whenever returning from Settings/History/
        # etc, which otherwise silently drops whatever was showing. A background generation job
        # already in flight (kicked off before that screen switch) has no OTHER trigger that
        # would ever re-call setStatus() on the new label — nothing re-announces "Generating..."
        # just because the view came back, since the call that originally set it already
        # happened once, in the past. Confirmed directly: without this, opening Settings while a
        # chunk is generating and returning before it finishes makes the app look like nothing
        # is happening at all, even though the exact same background job is still running fine —
        # a real, confirmed pipeline continuity issue (verified via a diagnostic trace: dispatch/
        # chunkResultMain_/beginChunkPlayback all fire normally with Settings open the whole
        # time), just with no visible sign of it after the round-trip.
        self._status_text = text
        # A previous error may have left textColor red via _flashInlineError, which never
        # restores it on its own — every real status update must reclaim the label's normal
        # look, not just whichever one happens to say "Generating...".
        self.status_label.resetBaseColor()
        self.status_label.setStringValue_(text)
        card = getattr(self, "card", None)
        if text == "Generating...":
            self.status_label.startPulsing()
            if card is not None:
                card.startBorderShimmer()
        else:
            self.status_label.stopPulsing()
            if card is not None:
                card.stopBorderShimmer()

        def anim(ctx):
            ctx.setDuration_(0.35)
            self.status_label.animator().setAlphaValue_(1.0 if text else 0.0)
        AppKit.NSAnimationContext.runAnimationGroup_(anim)

    def setStatusMain_(self, text):
        self.setStatus(str(text))

    def showError_(self, message):
        if self.overlay is not None:
            self.dismissOverlay()
        if self.current_screen == "welcome" and getattr(self, "key_error_label", None) is not None:
            self._flashInlineError(self.key_error_label, str(message))
        elif getattr(self, "status_label", None) is not None:
            self._flashInlineError(self.status_label, str(message))

    @objc.python_method
    def _flashInlineError(self, label, message):
        # cancel any still-pending fade-out from a previous error on this same label — without
        # this, a second error shown quickly after the first could get its timer stolen and
        # faded out early by the first one's now-stale callback.
        # The timer is tracked in a plain dict on self, keyed by id(label), rather than as an
        # attribute set directly on the NSTextField — NSControl's KVC machinery rejected an
        # arbitrary Python attribute assigned straight onto the field ("no attribute
        # '_fade_timer'" even though it was being SET, not read), so self is the safe place.
        key = id(label)
        existing = self._fade_timers.get(key)
        if existing is not None:
            existing.invalidate()
        # status_label may be mid-"Generating..." pulse when an error interrupts it (e.g. a
        # generation failure) — plain NSTextFields like key_error_label don't have this method.
        if hasattr(label, "stopPulsing"):
            label.stopPulsing()
            card = getattr(self, "card", None)
            if card is not None:
                card.stopBorderShimmer()
        label.setTextColor_(AppKit.NSColor.systemRedColor())
        label.setStringValue_(message)
        label.setAlphaValue_(0.0)

        def fade_in(ctx):
            ctx.setDuration_(0.6)
            label.animator().setAlphaValue_(1.0)
        AppKit.NSAnimationContext.runAnimationGroup_(fade_in)

        def fade_out(t):
            self._fade_timers.pop(key, None)
            def do_fade(ctx):
                ctx.setDuration_(0.9)
                label.animator().setAlphaValue_(0.0)
            AppKit.NSAnimationContext.runAnimationGroup_(do_fade)
            # the key field's placeholder was blanked out so it wouldn't show through the
            # error text (both are plain text with no background) — bring it back once the
            # error itself has finished fading, whether or not the user typed anything
            if label is getattr(self, "key_error_label", None):
                self._updateKeyPlaceholder()
        self._fade_timers[key] = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(4.0, False, fade_out)

    def pasteClicked_(self, sender):
        pb = AppKit.NSPasteboard.generalPasteboard()
        text = pb.stringForType_(AppKit.NSStringPboardType)
        if text:
            self.text_view.setString_(text)
            self.updateCharCount()

    def speedChanged_(self, sender):
        displayed = str(self.speed_popup.titleOfSelectedItem())
        real = CHATTERBOX_SPEED_REAL.get(displayed, displayed) if self.config.get("provider") == "Chatterbox" else displayed
        self.config["speed"] = real
        save_config(self.config)
        self._invalidateUngeneratedChunks()

    @objc.python_method
    def _populateSpeedMenu(self):
        # Chatterbox shows shifted display labels (see CHATTERBOX_SPEED_DISPLAY) — every other
        # provider shows its real value as-is. self.config["speed"] always stores the REAL
        # value regardless of provider, so switching providers never loses the actual setting.
        self.speed_popup.removeAllItems()
        if self.config.get("provider") == "Sesame":
            # The same time-stretch technique that works for Chatterbox does not hold up on
            # Sesame's output — confirmed directly at 0.8x. Locked to 1.0x here rather than
            # left adjustable-but-broken; _requestSesameTTS ignores speed unconditionally too,
            # as a backstop, but the control itself should look locked, not just silently
            # do nothing when changed.
            self.speed_popup.addItemWithTitle_("1.0x")
            self.speed_popup.selectItemWithTitle_("1.0x")
            self.speed_popup.setEnabled_(False)
            return
        self.speed_popup.setEnabled_(True)
        real = self.config.get("speed", "0.8x")
        is_cb = self.config.get("provider") == "Chatterbox"
        for s in SPEEDS:
            self.speed_popup.addItemWithTitle_(CHATTERBOX_SPEED_DISPLAY.get(s, s) if is_cb else s)
        selected = CHATTERBOX_SPEED_DISPLAY.get(real, real) if is_cb else real
        self.speed_popup.selectItemWithTitle_(selected)

    def voiceChanged_(self, sender):
        i = self.voice_popup.indexOfSelectedItem()
        if 0 <= i < len(self.voice_ids):
            chosen = self.voice_ids[i]
            if chosen == CREATE_VOICE_SENTINEL:
                # Never persisted as a real selection — revert the popup to whatever was
                # already chosen (so the sentinel never visibly "sticks"), then open the
                # recording flow instead. Entered from the main screen, so Cancel/Save should
                # close back to the main screen too — see addVoiceFromManageClicked_ for the
                # other entry point, which sets this to come back to Manage Voices instead.
                self._rec_return_to = None
                self._revertVoiceMenuSelection()
                self._showStyleChoiceCard()
                return
            self._setVoiceId(chosen)
            self._invalidateUngeneratedChunks()

    @objc.python_method
    def _setVoiceId(self, voice_id, provider=None):
        # config["voice_id"] stays the single "currently active" value every generation/
        # history call site already reads — this ADDS a per-provider memory alongside it, so
        # switching providers restores whatever THAT provider's own last pick was instead of
        # always resetting to its first/default voice (which is what a single shared voice_id
        # meant in practice: it almost never matched the new provider's own id namespace).
        provider = provider or self.config.get("provider", "ElevenLabs")
        self.config["voice_id"] = voice_id
        per_provider = self.config.get("voice_ids_by_provider", {})
        per_provider[provider] = voice_id
        self.config["voice_ids_by_provider"] = per_provider
        save_config(self.config)

    @objc.python_method
    def _revertVoiceMenuSelection(self):
        saved = self.config.get("voice_id")
        idx = self.voice_ids.index(saved) if saved in self.voice_ids else 0
        self.voice_popup.selectItemAtIndex_(idx)

    @objc.python_method
    def _sesameVoiceCatalog(self):
        return list(SESAME_VOICES) + list(self.config.get("sesame_custom_voices", []))

    @objc.python_method
    def _invalidateUngeneratedChunks(self):
        # Changing voice/speed mid-read only takes effect going forward — the chunk currently
        # playing keeps playing as-is rather than cutting off mid-sentence. But everything
        # cached or duration-recorded so far was generated under the OLD setting: a cache hit
        # here would replay the old voice, and speed directly changes how long a chunk of text
        # takes to speak, so old duration estimates (and the already-prefetched next chunk,
        # generated before this change) are no longer valid either.
        if not self.all_chunks:
            return
        self.chunk_audio_cache = {}
        self._prefetch_frontier = self.chunk_index
        # Preserve the CURRENTLY PLAYING chunk's own word timings — despite this function's own
        # comment above ("the chunk currently playing keeps playing as-is"), wiping the whole
        # dict unconditionally also wiped that chunk's entry, silently killing word-highlighting
        # for the rest of it on any mid-playback voice/speed change (chunk_word_timings is read
        # live by _scheduleNextWordTimer/_syncWordHighlightNow every time a word timer fires).
        current_timings = self.chunk_word_timings.get(self.chunk_index)
        self.chunk_word_timings = {self.chunk_index: current_timings} if current_timings else {}
        self.next_chunk_audio = None
        self.chunk_durations = [None] * len(self.all_chunks)
        self.avg_chars_per_sec = None
        if self.player is not None:
            self._prefetchNextChunk()

    # ----- voices / usage -----
    def fetchVoices(self):
        provider = self.config.get("provider", "ElevenLabs")
        self._populateSpeedMenu()
        if provider == "ElevenLabs":
            self.setStatus("Loading voices...")
            threading.Thread(target=self._fetchElVoicesWorker, daemon=True).start()
        elif provider == "System":
            # speechVoices() only returns voices actually installed on this Mac — filtered to
            # English so the list stays a manageable size (there are 190+ across all
            # languages) and relevant to what this app is actually for. Also drops the
            # "com.apple.speech.synthesis.voice." bundle — the old novelty/sound-effect voices
            # (Zarvox, Trinoids, Bells, Organ, Boing, Bahh, etc.) that show up under perfectly
            # normal-looking names too (Albert, Fred, Kathy, Ralph) — not real reading voices,
            # just noise in the list for what this app is actually for.
            NOVELTY_PREFIX = "com.apple.speech.synthesis.voice."
            voices = [v for v in AVFoundation.AVSpeechSynthesisVoice.speechVoices()
                      if v.language().startswith("en") and not v.identifier().startswith(NOVELTY_PREFIX)]
            voices.sort(key=lambda v: v.name())
            self.voice_ids = [v.identifier() for v in voices]
            self._populateVoiceMenu([self._systemVoiceLabel(v) for v in voices])
            self.usage_label.setStringValue_("Uses your Mac's built-in voices — free, offline, no limit.")
        elif provider == "Chatterbox":
            self.voice_ids = [v["id"] for v in CHATTERBOX_VOICES]
            self._populateVoiceMenu([v["label"] for v in CHATTERBOX_VOICES])
            self.usage_label.setStringValue_("A free, offline neural voice — no account, no limit.")
        elif provider == "Sesame":
            catalog = self._sesameVoiceCatalog()
            self.voice_ids = [v["id"] for v in catalog] + [CREATE_VOICE_SENTINEL]
            self._populateVoiceMenu([v["label"] for v in catalog] + ["Create your own..."])
            self.usage_label.setStringValue_("Premium offline voices — private to this Mac.")
        else:
            # OpenAI (and Other) use a fixed voice list; no usage endpoint
            self.voice_ids = list(OPENAI_VOICES)
            self._populateVoiceMenu([v.capitalize() for v in OPENAI_VOICES])
            self.usage_label.setStringValue_("")

    @objc.python_method
    def _systemVoiceLabel(self, voice):
        # v.language() is a raw BCP-47 tag ("en-ZA") — not something a non-technical user
        # reads at a glance. Every voice reaching here already passed the "en" prefix filter
        # in fetchVoices, so the language half is always English; only the region varies.
        # GB->UK because that's the common name people actually use for it.
        region = voice.language().split("-")[-1]
        region = "UK" if region == "GB" else region
        quality = {
            AVFoundation.AVSpeechSynthesisVoiceQualityEnhanced: "Enhanced",
            AVFoundation.AVSpeechSynthesisVoiceQualityPremium: "Premium",
        }.get(voice.quality(), "Standard")
        return f"{voice.name()} (English {region}, {quality})"

    @objc.python_method
    def _historyVoiceLabel(self, provider, voice_id):
        # History entries store the raw voice id (whatever fetchVoices populated self.voice_ids
        # with at generation time), not a friendly label — every other place in the app that
        # shows a voice always shows v["label"]/voice.name(), never the raw id, so resolving it
        # here keeps that same idiom (a history row would otherwise read something like
        # "Sesame · custom_7f3a91bc · 2h ago" instead of the voice's actual name). Falls back to
        # the raw id if the voice can't be found (e.g. deleted since, or an ElevenLabs voice —
        # not worth a network call just to label a history row).
        if provider == "System":
            voice = AVFoundation.AVSpeechSynthesisVoice.voiceWithIdentifier_(voice_id)
            if voice is not None:
                return self._systemVoiceLabel(voice)
        elif provider == "Chatterbox":
            match = next((v for v in CHATTERBOX_VOICES if v["id"] == voice_id), None)
            if match is not None:
                return match["label"]
        elif provider == "Sesame":
            catalog = list(SESAME_VOICES) + list(self.config.get("sesame_custom_voices", []))
            match = next((v for v in catalog if v["id"] == voice_id), None)
            if match is not None:
                return match["label"]
        elif provider in ("OpenAI", "Other") and voice_id in OPENAI_VOICES:
            return voice_id.capitalize()
        return voice_id

    @objc.python_method
    def _request_json(self, url, headers):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            return json.load(resp)

    @objc.python_method
    def _fetchElVoicesWorker(self):
        key = self.config.get("api_key", "")
        try:
            data = self._request_json(f"{EL_API}/voices", {"xi-api-key": key})
        except urllib.error.HTTPError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", f"ElevenLabs rejected the request (HTTP {e.code}). Check your API key.", False)
            return
        except urllib.error.URLError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", f"Could not reach ElevenLabs: {e.reason}", False)
            return
        voices = data.get("voices", [])
        self.voice_ids = [v.get("voice_id") for v in voices]
        labels = []
        for v in voices:
            name = v.get("name", "Unknown")
            accent = (v.get("labels") or {}).get("accent")
            labels.append(f"{name} ({accent})" if accent else name)
        self.performSelectorOnMainThread_withObject_waitUntilDone_("populateVoicesMain:", labels, False)
        # usage
        try:
            data = self._request_json(f"{EL_API}/user", {"xi-api-key": key})
            sub = data.get("subscription", {})
            used, limit = sub.get("character_count"), sub.get("character_limit")
            if used is not None and limit is not None:
                text = f"{used:,} / {limit:,} characters used this period ({limit - used:,} left)"
                self.performSelectorOnMainThread_withObject_waitUntilDone_("updateUsageMain:", text, False)
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass

    def populateVoicesMain_(self, labels):
        self._populateVoiceMenu(list(labels))
        self.setStatus("")

    @objc.python_method
    def _populateVoiceMenu(self, labels):
        # Cached so showMainScreen() can restore the SAME labels into a freshly-rebuilt
        # voice_popup on any later screen round-trip without a real re-fetch (wasteful for
        # ElevenLabs specifically — a network call — and voice_ids/labels don't change just
        # because the user navigated to Settings and back). Confirmed via direct reproduction:
        # voice_ids stayed populated across a round-trip, but the freshly-rebuilt popup itself
        # showed zero items — nothing was ever re-populating it, only the initial fetchVoices()
        # call at launch ever had.
        self._voice_labels = list(labels)
        self.voice_popup.removeAllItems()
        for label in labels:
            self.voice_popup.addItemWithTitle_(label)
        provider = self.config.get("provider", "ElevenLabs")
        per_provider = self.config.get("voice_ids_by_provider", {})
        # Falls back to the flat voice_id the first time THIS provider is seen since this
        # per-provider memory shipped — a natural one-time migration (whatever was already
        # active becomes this provider's own remembered choice) rather than a hard reset to
        # its first/default voice, which is what happened before: a single shared voice_id
        # almost never matched a DIFFERENT provider's own id namespace, so switching providers
        # silently landed on idx 0 every time instead of what was actually last picked for it.
        saved = per_provider.get(provider, self.config.get("voice_id"))
        found = saved in self.voice_ids
        idx = self.voice_ids.index(saved) if found else 0
        if labels:
            self.voice_popup.selectItemAtIndex_(idx)
            # Only persist when the saved choice actually resolved — landing on idx 0 because
            # `saved` didn't match anything in the current voice_ids (a transient fetch race, a
            # renamed/removed voice, etc.) must NOT overwrite the real remembered preference with
            # that fallback. This was a real bug: any single lookup miss permanently replaced the
            # correct per-provider memory with voice_ids[0], since this ran unconditionally.
            if found:
                self._setVoiceId(self.voice_ids[idx], provider)

    def updateUsageMain_(self, text):
        self.usage_label.setStringValue_(str(text))

    # ----- playback (AVAudioPlayer: pause/resume + seek) -----
    # Long text is split into ~600-char chunks (see chunk_text) and generated one ahead of
    # playback: the first chunk is requested up front (this is the only wait the user sees),
    # then as soon as it starts playing, the next chunk is requested in the background so
    # it's normally ready before the current one finishes. This is what actually fixes a
    # 56,000-character paste either silently failing (providers reject requests that long
    # outright) or taking a minute-plus of dead silence before anything plays.
    #
    # The scrubber needs a position across the WHOLE document, but we deliberately never hold
    # more than ~2 chunks' audio at once (that's the whole point of chunking) — so most of a
    # long document's duration is only ever an ESTIMATE, extrapolated from the actual
    # chars-per-second of whatever chunks have really been generated so far, refined as more
    # come in. A "seek" (scrub, or ±15s crossing a chunk boundary) is really just "start a
    # fresh chunk at a given offset instead of at 0" — playPauseClicked_ and every skip/scrub
    # all funnel through _seekToVirtualTime for exactly that reason.
    def playPauseClicked_(self, sender):
        if self.player is not None:
            if self.player.isPlaying():
                self.player.pause()
                self._stopProgressTimer()
            else:
                self.player.play()
                self._startProgressTimer()
                # Recomputes from live currentTime(), so an arbitrary pause duration is handled
                # for free — no separate "how long were we paused" bookkeeping needed.
                self._syncWordHighlightNow()
            self._syncPlaybackUI()
            return
        text = str(self.text_view.string())
        if not text or not self._isConfigured():
            return
        # Must happen before the session_text comparison just below, not only right before
        # chunking — otherwise every replay of the exact same pasted text would compare its
        # raw form against a normalized session_text and never match, silently breaking the
        # "restart what's already cached" path every single time.
        text = normalize_paragraph_breaks(text)
        if self.all_chunks and self.session_text == text:
            # Playback ran to the end and was never a "real" stop — all_chunks and
            # chunk_audio_cache are still sitting there from that session, so this is just
            # restarting from 0 within it, not a new one. _seekToVirtualTime finds the cache
            # hit on its own and replays the exact same audio instead of regenerating it.
            self._seekToVirtualTime(0.0)
            return
        target_chars = chatterbox_chunk_target if self.config.get("provider") == "Chatterbox" else CHUNK_TARGET_CHARS
        chunks = chunk_text(text, target_chars)
        if not chunks:
            return
        # Text differs from whatever session_text left behind (or there was none) — any cache
        # still sitting around belongs to that other text and must not be reused for this one.
        self.chunk_audio_cache = {}
        self.chunk_word_timings = {}
        self.next_chunk_audio = None
        self.session_text = text
        self.all_chunks = chunks
        self.chunk_durations = [None] * len(chunks)
        self.avg_chars_per_sec = None
        self._prefetch_frontier = 0
        self._seekToVirtualTime(0.0)

    @objc.python_method
    def _requestTTS(self, text):
        text = sanitize_for_speech(text)
        provider = self.config.get("provider", "ElevenLabs")
        key = self.config.get("api_key", "")
        voice = self.config.get("voice_id", "")
        speed = float(self.config.get("speed", "0.8x").rstrip("x"))
        if provider == "System":
            return self._requestSystemTTS(text, voice, speed)
        if provider == "Chatterbox":
            return self._requestChatterboxTTS(text, voice, speed)
        if provider == "Sesame":
            return self._requestSesameTTS(text, voice, speed)
        if provider == "OpenAI":
            body = json.dumps({"model": "gpt-4o-mini-tts", "voice": voice, "input": text, "speed": speed}).encode()
            req = urllib.request.Request(f"{OPENAI_API}/audio/speech", data=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        else:  # ElevenLabs (and Other falls back to EL-compatible)
            body = json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                               "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed}}).encode()
            req = urllib.request.Request(f"{EL_API}/text-to-speech/{voice}", data=body, headers={
                "xi-api-key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
            return resp.read()

    @objc.python_method
    def _requestSystemTTS(self, text, voice_identifier, speed):
        # Renders via Apple's on-device AVSpeechSynthesizer instead of a network call, then
        # wraps the result as a WAV so it's just another _requestTTS backend to everything
        # downstream — chunking, prefetch, the scrubber, and the audio cache all stay exactly
        # the same regardless of which provider actually produced the bytes.
        synthesizer = AVFoundation.AVSpeechSynthesizer.alloc().init()
        utterance = AVFoundation.AVSpeechUtterance.speechUtteranceWithString_(text)
        voice = AVFoundation.AVSpeechSynthesisVoice.voiceWithIdentifier_(voice_identifier) if voice_identifier else None
        if voice is None:
            voice = AVFoundation.AVSpeechSynthesisVoice.voiceWithLanguage_(None)
        utterance.setVoice_(voice)
        base_rate = AVFoundation.AVSpeechUtteranceDefaultSpeechRate
        min_rate = AVFoundation.AVSpeechUtteranceMinimumSpeechRate
        max_rate = AVFoundation.AVSpeechUtteranceMaximumSpeechRate
        utterance.setRate_(max(min_rate, min(max_rate, base_rate * speed)))

        collected = {"pcm": bytearray(), "sample_rate": None, "channels": None, "done": False}
        # Word timing for live highlighting during playback (see _SpeechTimingDelegate) —
        # System voice's own real-time source; Chatterbox/Sesame get theirs from ASR word-
        # alignment instead (see speech_verify._align_words), since they have no live callback
        # to listen to. Recorded as the exact cumulative sample count already written to
        # collected["pcm"] at the moment each word's range callback fires — real audio-frame
        # position, not a wall-clock guess from a separate pass.
        word_timings = []

        def on_range(loc, length):
            channels = collected["channels"] or 1
            sample_rate = collected["sample_rate"]
            frames_so_far = len(collected["pcm"]) // (2 * channels)
            start_time = (frames_so_far / sample_rate) if sample_rate else 0.0
            word_timings.append({"start": start_time, "loc": loc, "length": length, "text": text[loc:loc + length]})

        timing_delegate = _SpeechTimingDelegate.alloc().init()
        timing_delegate.on_range = on_range
        synthesizer.setDelegate_(timing_delegate)

        def callback(buffer):
            if buffer is None or buffer.frameLength() == 0:
                collected["done"] = True
                return
            frame_length = buffer.frameLength()
            fmt = buffer.format()
            ch = fmt.channelCount()
            collected["sample_rate"] = fmt.sampleRate()
            collected["channels"] = ch
            # AVAudioPCMBuffer here is float32 (not int16), and PyObjC already bridges
            # floatChannelData() to something directly indexable as ptr[channel][sample] —
            # no ctypes/from_address needed, confirmed via a standalone spike beforehand.
            ptr = buffer.floatChannelData()
            samples = bytearray()
            for i in range(frame_length):
                for c in range(ch):
                    v = max(-1.0, min(1.0, ptr[c][i]))
                    samples += struct.pack("<h", int(v * 32767))
            collected["pcm"] += samples

        synthesizer.writeUtterance_toBufferCallback_(utterance, callback)
        # The callback is delivered via the calling thread's run loop (this runs on a
        # background thread already, via _chunkWorker) — a plain time.sleep() poll never
        # pumps it, so nothing would fire; this has to actually run the run loop.
        rl = NSRunLoop.currentRunLoop()
        deadline = time.time() + 30
        while not collected["done"] and time.time() < deadline:
            rl.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))
        # Side-channel, not a return-value change — _requestTTS's return type (just WAV bytes)
        # stays identical for every provider; _chunkWorker reads this immediately afterward, on
        # the same single persistent TTS worker thread that called this, so there's no
        # concurrent-access risk despite it being an instance attribute.
        self._last_word_timings = self._enforceMinWordWindow(word_timings)

        if not collected["pcm"] or not collected["sample_rate"]:
            raise RuntimeError("The system voice produced no audio.")

        buf = io.BytesIO()
        w = wave.open(buf, "wb")
        w.setnchannels(int(collected["channels"]))
        w.setsampwidth(2)
        w.setframerate(int(collected["sample_rate"]))
        w.writeframes(bytes(collected["pcm"]))
        w.close()
        return buf.getvalue()

    @objc.python_method
    def _enforceMinWordWindow(self, word_timings):
        # Shared by every provider's word-timing source (System's live callbacks, Chatterbox/
        # Sesame's ASR alignment) — two consecutive words landing on the same or near-same
        # start makes the earlier one mathematically unselectable (or visible for ~0 seconds)
        # no matter how the highlight is later scheduled against this list, regardless of
        # which mechanism produced the timing in the first place.
        if not word_timings:
            return word_timings
        for i in range(1, len(word_timings)):
            min_start = word_timings[i - 1]["start"] + self.HIGHLIGHT_MIN_WORD_WINDOW
            if word_timings[i]["start"] < min_start:
                word_timings[i]["start"] = min_start
        return word_timings

    @objc.python_method
    def _resourcePath(self, *parts):
        # In the frozen py2app bundle, py2app sets RESOURCEPATH to Contents/Resources and
        # "resources": ["chatterbox_assets"] in setup.py copies the whole directory tree there
        # unchanged; in dev mode (running main.py directly) there's no RESOURCEPATH, so this
        # falls back to the script's own directory, where chatterbox_assets/ also lives. Same
        # relative layout either way — validated against a real frozen build beforehand.
        base = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, *parts)

    @objc.python_method
    def _chatterboxEngine(self):
        # Loading the model is a few seconds — must happen once and be reused for every
        # chunk, not reloaded per request. Every chunk now funnels through the single
        # persistent _ttsWorkerLoop thread (see applicationDidFinishLaunching_ — spawning a
        # fresh thread per chunk used to exhaust MLX's own per-thread GPU stream pool on long
        # text), so this lock isn't guarding against real concurrent access anymore; it's
        # cheap to keep as a defensive guard in case that ever changes.
        if getattr(self, "_chatterbox_engine", None) is not None:
            return self._chatterbox_engine
        with self._chatterbox_lock:
            if self._chatterbox_engine is None:
                from mlx_audio.tts.utils import load_model
                # Pass the real local snapshot directory, not the "mlx-community/..." repo-id
                # string — load_model()/get_model_path() short-circuits immediately on a path
                # that already exists, touching zero huggingface_hub/network code. Confirmed
                # during the packaging spike that relying on HF_HUB_OFFLINE alone is NOT a
                # hard guarantee — one fallback path inside huggingface_hub still attempted a
                # real network connection despite the env var being set.
                hub_dir = self._resourcePath(
                    "chatterbox_assets", "hf_cache", "hub",
                    "models--mlx-community--chatterbox-turbo-4bit", "snapshots")
                snapshot_dir = os.path.join(hub_dir, os.listdir(hub_dir)[0])
                self._chatterbox_engine = load_model(snapshot_dir)
        return self._chatterbox_engine

    @objc.python_method
    def _generateChatterboxAudio(self, engine, text, ref_audio):
        import numpy as np
        # split_pattern=None and temperature=0.05 were both found by direct listening tests —
        # the library's own defaults (per-sentence splitting, temperature ~0.7-0.8) produced
        # random upward inflections and unstable pacing/duration on some voices/sentences.
        # Calling the model's own .generate() generator directly (not generate_audio(), the
        # CLI-oriented wrapper that writes files to disk) keeps this fully in-memory, matching
        # how every other provider here returns audio bytes without touching disk.
        #
        # The generation-length parameter for THIS model (mlx-community Chatterbox TURBO) is
        # named max_tokens, defaulting to 800 — NOT max_new_tokens, which is the older,
        # non-turbo chatterbox model's parameter name. Passing max_new_tokens= here was a real,
        # confirmed no-op the whole time it shipped: ChatterboxTurboTTS.generate() has no such
        # parameter, so it silently landed in **kwargs and was never read (that code path only
        # gets used when stream=True, which this call never sets) — every chunk was actually
        # generated under the untouched 800-token default regardless of length, confirmed
        # directly via two isolated test chunks of different lengths (499 vs 579 chars) that
        # both produced an identical "800/800" token count and an identical 32.2s of audio,
        # with the longer one cut off mid-sentence as a result. split_pattern=None (below)
        # means the WHOLE chunk goes through as one ungapped generation with no fallback
        # re-segmentation, so this cap applies to the chunk's entire content at once, not
        # per-sentence. 4 tokens/char follows the library's own internal comment on its
        # sentence-splitting heuristic (~8 speech tokens per text token, ~4 chars per text
        # token) rather than reusing the old model's 15-tokens/char figure, which was
        # calibrated for a different model's token semantics and never actually exercised here.
        # Headroom check: GPT2's positional embedding table caps the whole sequence (voice
        # conditioning + text + speech tokens) at 8196 positions; even this app's largest
        # possible chunk (CHUNK_MAX_CHARS=900) stays well under that with real margin to spare.
        max_tokens = max(800, len(text) * 4)
        #
        # Literal double/smart quotes are a confirmed, deterministic bug in this exact model
        # build (mlx-community Chatterbox Turbo, upstream issue #433) — any literal quote
        # character produces a ~1.2s non-speech "sigh" sound, unrelated to voice or content.
        # Free, independent fix, cheaper to prevent here than to catch via verification below.
        text = text.translate(str.maketrans("", "", "\"“”"))

        # Short inputs are a confirmed, unresolved weak spot for this model (single words/
        # short phrases reliably producing gibberish, upstream issue #97) — and independently
        # the hardest case for the verification below too, since Whisper's own transcription
        # is least reliable on very short audio. One extra attempt gives the retry loop more
        # chances on exactly the input shape most likely to need it.
        is_short = len(text.split()) < speech_verify.SHORT_INPUT_WORD_COUNT
        max_retries = CHATTERBOX_MAX_RETRIES + (1 if is_short else 0)

        best = None  # (audio, sample_rate, cer, word_timings) — lowest-CER attempt, for the fallback
        for attempt in range(max_retries + 1):
            results = list(engine.generate(
                text=text, ref_audio=ref_audio, split_pattern=None, temperature=0.05,
                max_tokens=max_tokens))
            audio = np.concatenate([np.array(r.audio) for r in results])
            sample_rate = results[0].sample_rate
            is_last = attempt == max_retries

            # Two independent, cheap-first signals kept side by side, not one replacing the
            # other — timing (the original heuristic, catches bloated/runaway generation) and
            # content (new — catches a normal-paced clip that just says the wrong thing,
            # which timing alone structurally can't see). Confirmed as the right shape by real
            # shipped forks of this exact model: the hardened one runs both checks in parallel
            # rather than dropping the duration check once ASR verification was added.
            timing_ok = True
            if len(text) >= CHATTERBOX_MIN_CHARS_FOR_CHECK:
                chars_per_sec = len(text) / (len(audio) / sample_rate)
                timing_ok = chars_per_sec >= CHATTERBOX_MIN_CHARS_PER_SEC

            result = speech_verify.verify(audio, sample_rate, text)
            if best is None or result.cer < best[2]:
                best = (audio, sample_rate, result.cer, result.word_timings)
            if (timing_ok and result.passed) or is_last:
                return best[0], best[1], best[3]
        return best[0], best[1], best[3]  # unreachable — loop always returns

    @objc.python_method
    def _requestChatterboxTTS(self, text, voice_identifier, speed):
        import numpy as np
        engine = self._chatterboxEngine()
        voice = next((v for v in CHATTERBOX_VOICES if v["id"] == voice_identifier), CHATTERBOX_VOICES[0])
        ref_audio = self._resourcePath("chatterbox_assets", "voices", voice["ref_audio"]) if voice["ref_audio"] else None

        audio, sample_rate, word_timings = self._generateChatterboxAudio(engine, text, ref_audio)

        # Chatterbox has no native speed parameter (unlike every other provider here) — see
        # time_stretch's own docstring for why this specific technique was picked.
        if speed != 1.0:
            from pitch_shift import time_stretch
            audio = time_stretch(audio, sample_rate, speed)
            # word_timings above was computed against the PRE-stretch audio's timeline —
            # time_stretch changes playback duration by dividing by speed (e.g. 1.25x plays
            # in 1/1.25 the time), so every captured start must be rescaled the same way or
            # the highlight would drift further out of sync with the actual audio as the
            # chunk goes on, worse the longer the chunk.
            if word_timings:
                word_timings = [{**w, "start": w["start"] / speed} for w in word_timings]

        self._last_word_timings = self._enforceMinWordWindow(word_timings)

        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        w = wave.open(buf, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
        w.close()
        return buf.getvalue()

    @objc.python_method
    def _sesameAssetsBase(self):
        # sesame_assets/ (the CSM model + its 5 stock voice clips, ~1.6GB) is too large to
        # bundle in the app itself, so it's downloaded on demand the first time a valid
        # Sesame key is entered (see sesame_download.py) — that download location takes
        # priority since it's where a real distributed build actually gets its assets from.
        # The bundled Resources path is kept as a fallback for dev convenience / in case a
        # future build ever bundles it directly. Returns None if neither has anything yet,
        # which the two callers below both already handle (live-repo-id / missing-file).
        if os.path.isdir(SESAME_ASSETS_DIR):
            return SESAME_ASSETS_DIR
        bundled = self._resourcePath("sesame_assets")
        return bundled if os.path.isdir(bundled) else None

    @objc.python_method
    def _sesameEngine(self):
        # Same lazy-load-with-lock shape as _chatterboxEngine.
        #
        # 8bit, not the full-precision csm-1b: confirmed directly, 8 isolated-process trials
        # each — fp had a 38% "runaway generation" rate (audio 2-4x too long, garbled) and
        # averaged 10.3s per generation; csm-1b-8bit had 0% anomalies over the same 8 trials
        # and averaged 5.1s. Research into CSM's own GitHub issues afterward confirmed the
        # runaway-generation failure mode is a known, maintainer-acknowledged base-model bug
        # (unreliable end-of-speech detection, not a bundled-model corruption on our end) —
        # nothing in that research explains WHY 8bit specifically tests more stable here (the
        # general quantization literature actually points the opposite direction at low bit
        # widths, though 8bit itself sits in the "no expected effect" range), so treat this as
        # an empirically-verified choice for this exact build, not a general "quantized is
        # always better" rule.
        if getattr(self, "_sesame_engine", None) is not None:
            return self._sesame_engine
        with self._sesame_lock:
            if self._sesame_engine is None:
                base = self._sesameAssetsBase()
                # Unlike Chatterbox, Sesame's own model code (mlx_audio/tts/models/sesame/
                # sesame.py) separately loads a LLaMA3 tokenizer by REPO ID
                # ("unsloth/Llama-3.2-1B"), not a local path — passing load_model() a local
                # snapshot dir (below) has no effect on that second, independent lookup, so it
                # always goes through transformers' AutoTokenizer.from_pretrained(), which
                # checks the standard HF cache directories before ever considering the network.
                # Pointing those directories at our downloaded/bundled sesame_assets (which
                # includes this tokenizer's own cache entry — see sesame_download.py) makes that
                # lookup resolve locally instead. Confirmed directly: without this, a user whose
                # personal ~/.cache/huggingface doesn't happen to already have this tokenizer
                # (i.e. everyone except this dev machine, which had it cached from earlier,
                # unrelated work) hits a real network call — which then fails outright in the
                # packaged app, since httpx's own SSL context creation can't find the system
                # certificate store there either. HF_HUB_OFFLINE is set too, as a hard backstop —
                # per _chatterboxEngine's own comment, the env var alone isn't a reliable
                # guarantee, but combined with an actual local cache hit here, there's no
                # fallback path left for it to need to guarantee anything about.
                #
                # MUST happen before the `import mlx_audio.tts.utils` below, not after: that
                # import pulls in huggingface_hub as a side effect, and huggingface_hub reads
                # these exact env vars into its OWN module-level constants at import time (see
                # its constants.py) — once imported, later os.environ changes have zero effect
                # for the rest of the process. Confirmed directly: this exact ordering mistake
                # (env vars set after the import) silently no-ops the whole fix below, while
                # looking identical to a working fix in a standalone test script that happened
                # to set them first.
                if base:
                    hf_cache_dir = os.path.join(base, "hf_cache")
                    os.environ["HF_HOME"] = hf_cache_dir
                    os.environ["HF_HUB_CACHE"] = os.path.join(hf_cache_dir, "hub")
                    os.environ["TRANSFORMERS_CACHE"] = os.path.join(hf_cache_dir, "hub")
                    os.environ["HF_HUB_OFFLINE"] = "1"
                from mlx_audio.tts.utils import load_model
                hub_dir = os.path.join(
                    base, "hf_cache", "hub", "models--mlx-community--csm-1b-8bit", "snapshots") if base else None
                if hub_dir and os.path.isdir(hub_dir) and os.listdir(hub_dir):
                    snapshot_dir = os.path.join(hub_dir, os.listdir(hub_dir)[0])
                else:
                    # No downloaded/bundled snapshot found — this machine's own dev-time HF
                    # cache (if any) or a live network fetch. Only ever reached in dev mode or
                    # if a licensed user somehow reaches Sesame generation without having gone
                    # through the download flow in _validateKeyWorker, which shouldn't happen.
                    snapshot_dir = "mlx-community/csm-1b-8bit"
                # model_type explicitly forced to "sesame", not inferred: mlx_audio normally
                # guesses the architecture from a hint embedded in the model's *name string*
                # (e.g. "csm" in "mlx-community/csm-1b-8bit" hints at the sesame architecture)
                # — a raw local snapshot path (a hash-named directory) carries no such hint, so
                # it falls through to the model's own config.json, whose "model_type" field is
                # "sam" (an upstream naming quirk, not something wrong with the download) and
                # isn't a registered architecture, raising "Model type sam not supported".
                # Confirmed directly: this only ever surfaced once a local snapshot path was
                # actually reachable for the first time (via the download feature below) — the
                # live-repo-id fallback string above always happened to carry the "csm" hint by
                # accident, masking this same bug in every case tested before now.
                self._sesame_engine = load_model(snapshot_dir, model_type="sesame")
        return self._sesame_engine

    @objc.python_method
    def _generateSesameAudio(self, engine, text, ref_audio, ref_text):
        import numpy as np
        # CSM's own EOS detection (does the model spontaneously emit an all-zero codebook
        # frame) has no repetition penalty or loop guard behind it — confirmed via CSM's own
        # GitHub issues (e.g. SesameAILabs/csm#122) to be a known, maintainer-acknowledged
        # base-model limitation, not something specific to this app's setup or fixable from
        # here. A lower temperature was tried first (the same fix that stabilized Chatterbox)
        # but made things WORSE in direct testing — plausible in hindsight, since CSM's
        # failure mode is specifically a repetition loop, and lower/more-deterministic
        # sampling tends to make loops MORE persistent once started, not less; that's a
        # different failure mode than whatever temperature was fixing for Chatterbox.
        # Left at the library default (temp=0.9) for that reason.
        #
        # max_audio_length_ms IS capped, though — not because it reduces how often a runaway
        # happens (it doesn't, confirmed directly), but because it makes a runaway fail much
        # faster on a SHORT chunk instead of running all the way to the library's flat
        # 90-second default. 200ms/char (~5 chars/sec) is a deliberately generous floor —
        # slower than even SESAME_MIN_CHARS_PER_SEC's own "barely acceptable" 11.5 chars/sec
        # — chosen after a first attempt at this (basing the multiplier off
        # SESAME_MIN_CHARS_PER_SEC directly) produced a cap LARGER than 90s for this app's
        # real ~600-char chunks, the opposite of the intent — confirmed directly when a real
        # first-chunk generation ran past two minutes before this got caught. min(90_000, ...)
        # guarantees this can never regress past the library's own original ceiling either way.
        max_ms = min(90_000, max(20_000, len(text) * 200))
        # See _generateChatterboxAudio's matching comment — same reasoning, same model family.
        is_short = len(text.split()) < speech_verify.SHORT_INPUT_WORD_COUNT
        max_retries = SESAME_MAX_RETRIES + (1 if is_short else 0)

        best = None  # (audio, sample_rate, cer, word_timings) — lowest-CER attempt, for the fallback
        for attempt in range(max_retries + 1):
            results = list(engine.generate(
                text=text, ref_audio=ref_audio, ref_text=ref_text, max_audio_length_ms=max_ms))
            is_last = attempt == max_retries
            # A genuinely empty result (the model emitted zero audio frames — confirmed directly
            # to happen even on ordinary short text, not just long runaway-prone chunks, since
            # this is CSM's own base-model instability, same root cause as the runaway case
            # below) used to reach np.concatenate([]) and crash with "need at least one array to
            # concatenate" instead of retrying — the length-gated check below only ever
            # protected long text, leaving short text with zero retry protection at all.
            if not results:
                if is_last:
                    if best is not None:
                        return best[0], best[1], best[3]
                    raise RuntimeError("Sesame produced no audio for this text after retrying.")
                continue
            audio = np.concatenate([np.array(r.audio) for r in results])
            sample_rate = results[0].sample_rate

            # Two independent, cheap-first signals kept side by side — see
            # _generateChatterboxAudio's matching comment for why neither replaces the other.
            timing_ok = True
            if len(text) >= SESAME_MIN_CHARS_FOR_CHECK:
                chars_per_sec = len(text) / (len(audio) / sample_rate)
                timing_ok = SESAME_MIN_CHARS_PER_SEC <= chars_per_sec <= SESAME_MAX_CHARS_PER_SEC

            result = speech_verify.verify(audio, sample_rate, text)
            if best is None or result.cer < best[2]:
                best = (audio, sample_rate, result.cer, result.word_timings)
            if (timing_ok and result.passed) or is_last:
                return best[0], best[1], best[3]
        return best[0], best[1], best[3]  # unreachable — loop always returns

    @objc.python_method
    def _requestSesameTTS(self, text, voice_identifier, speed):
        import numpy as np
        engine = self._sesameEngine()
        catalog = self._sesameVoiceCatalog()
        voice = next((v for v in catalog if v["id"] == voice_identifier), catalog[0])
        # Built-ins (Sadie/Manny/Ben/Alex/Jordan) are keyed by "ref_audio" and live wherever
        # _sesameAssetsBase() finds them (downloaded on-demand, see sesame_download.py, or
        # bundled — see that method's own comment); a user-created custom voice lives outside
        # either of those (see sesame_voices_path) and is keyed by "audio_file" instead — see
        # the data model in _maybeFinalizeSesameClone-equivalent commit path (useRecordingClicked_).
        if "ref_audio" in voice:
            base = self._sesameAssetsBase() or self._resourcePath("sesame_assets")
            ref_audio = os.path.join(base, "voices", voice["ref_audio"])
        else:
            ref_audio = sesame_voices_path(voice["audio_file"])

        audio, sample_rate, word_timings = self._generateSesameAudio(engine, text, ref_audio, voice["ref_text"])

        # Unlike Chatterbox, the same time-stretch treatment does not hold up on Sesame's
        # output — confirmed directly at 0.8x ("she didn't even know how to talk"). The speed
        # control is locked to 1.0x for this provider (see _populateSpeedMenu) as the real
        # fix; ignoring `speed` here too is a deliberate backstop in case a leftover non-1.0x
        # value from another provider is still sitting in config when this runs. No rescale
        # needed for word_timings below either, for the same reason.
        self._last_word_timings = self._enforceMinWordWindow(word_timings)

        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        w = wave.open(buf, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
        w.close()
        return buf.getvalue()

    # ----- Sesame voice recording -----
    @objc.python_method
    def _showStyleChoiceCard(self):
        # A reader "embodies" whatever's actually on the page — a script written with formal,
        # complete sentences reads like a narrator; one written with casual self-corrections
        # and filler words reads like a natural conversation — confirmed directly against real
        # generated output (Ben's formal reference vs. Sesame's own casual "conversational"
        # demo reference produced clearly different delivery styles from the same book text).
        # This screen exists so the user picks which of RECORD_SCRIPT_PRESETS' styles they
        # want BEFORE recording, not after — the style is baked into the reference clip, not
        # something adjustable later.
        cw = 340
        row_w = cw - 40
        row_h = 74

        title = make_label("Choose a reading style", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        sub = make_label(
            "The words on the page shape how your voice comes out — pick whichever fits.",
            11.5, 0.5, align=AppKit.NSTextAlignmentCenter)

        cursor = 16
        cancel_y, cancel_h = cursor, 24
        cursor += cancel_h + 12
        row_ys = []
        for _ in RECORD_SCRIPT_PRESETS:
            row_ys.append(cursor)
            cursor += row_h + 10
        cursor += 4
        sub_y, sub_h = cursor, 32
        cursor += sub_h + 4
        title_y, title_h = cursor, 20
        cursor += title_h + 16
        ch = cursor

        card = self._makeCard(cw, ch)
        title.setFrame_(NSMakeRect(0, title_y, cw, title_h))
        sub.setFrame_(NSMakeRect(20, sub_y, cw - 40, sub_h))

        cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
        # NOT "dismissOverlay:" — dismissOverlay is a zero-arg method (def dismissOverlay(self)),
        # so wiring a button straight to the "dismissOverlay:" selector (which passes sender)
        # never matched any real method on this object; the button silently did nothing when
        # clicked (confirmed directly: reported as "cancel doesn't even work... it does
        # nothing"). styleChoiceCancelClicked_ below is the real target/action pair.
        cancel_btn = text_button_brighten("Cancel", NSMakeRect(cw / 2 - 40, cancel_y, 80, cancel_h),
                                           "styleChoiceCancelClicked:", self, cancel_font, white(0.5), white(0.85))

        subviews = [title, sub, cancel_btn]
        for preset, y in zip(RECORD_SCRIPT_PRESETS, row_ys):
            row = HoverButton.alloc().initWithFrame_(NSMakeRect(20, y, row_w, row_h))
            row.configure(0.05, 0.11, 10.0)
            row.setTitle_("")
            row.setTarget_(self)
            row.setAction_("_styleChosenClicked:")
            row._style_id = preset["id"]

            label = make_label(preset["label"], 13.5, 0.95, AppKit.NSFontWeightSemibold)
            label.setFrame_(NSMakeRect(14, row_h - 30, row_w - 28, 18))
            desc = make_label(preset["description"], 11.5, 0.55)
            desc.cell().setWraps_(True)
            desc.setFrame_(NSMakeRect(14, 12, row_w - 28, 30))
            row.addSubview_(label)
            row.addSubview_(desc)
            subviews.append(row)

        for sub_view in subviews:
            card.addSubview_(sub_view)
        self._presentOverlay(card)

    def styleChoiceCancelClicked_(self, sender):
        # This is the FIRST step of the whole record-a-voice flow, so Cancel here genuinely
        # means "abort the flow" (unlike the capture card one step later, which has a real
        # "Back" — see recordingCaptureBackClicked_). Same return_to/dismiss fallback already
        # used by recordingCancelClicked_/useRecordingClicked_: lands back on Manage Voices if
        # that's where the flow was entered from, or the main screen otherwise.
        return_to, self._rec_return_to = self._rec_return_to, None
        if return_to is not None:
            return_to()
        else:
            self.dismissOverlay()

    def _styleChosenClicked_(self, sender):
        style_id = getattr(sender, "_style_id", None)
        self._rec_selected_script = next(
            (p for p in RECORD_SCRIPT_PRESETS if p["id"] == style_id), RECORD_SCRIPT_PRESETS[0])
        self._showRecordingCaptureCard()

    @objc.python_method
    def _showRecordingCaptureCard(self):
        self._rec_recording_active = False
        self._rec_buffer = None
        self._rec_write_pos = 0
        self._rec_preview_audio = None
        if self._rec_preview_player is not None:
            self._rec_preview_player.stop()
            self._rec_preview_player = None

        cw = 320
        box_w = cw - 40
        text_w = box_w - 24

        style = AppKit.NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(AppKit.NSTextAlignmentCenter)
        style.setLineSpacing_(5.0)
        script_attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(13),
            AppKit.NSForegroundColorAttributeName: white(0.85),
            AppKit.NSParagraphStyleAttributeName: style,
        }
        script_text = (self._rec_selected_script or RECORD_SCRIPT_PRESETS[0])["script"]
        script_attr_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(script_text, script_attrs)
        script_label = AppKit.NSTextField.alloc().init()
        script_label.setBezeled_(False)
        script_label.setDrawsBackground_(False)
        script_label.setEditable_(False)
        script_label.setSelectable_(False)
        script_label.setAttributedStringValue_(script_attr_str)
        # Measured via the label's own cell (cellSizeForBounds_), not
        # NSAttributedString.boundingRectWithSize_options_ — confirmed empirically the two can
        # disagree for real text: for the Narrator script specifically, boundingRect measured
        # 226pt while the cell's own layout actually needed 247pt (a full line short), silently
        # clipping the last line inside the label's own frame regardless of the surrounding
        # scroll math being correct. Conversational/Reading happened to measure identically
        # either way, which is why only Narrator ever showed the bug. cellSizeForBounds_
        # reflects what the field will actually render at this width, so it can't drift from
        # the real layout the way a separate Core Text estimate can.
        text_h = math.ceil(script_label.cell().cellSizeForBounds_(NSMakeRect(0, 0, text_w, 10000)).height)
        box_pad = 10
        content_h = text_h + box_pad * 2
        # Confirmed directly: the longer, style-specific scripts can produce a box tall enough
        # that the whole card no longer fits in the window (clipped at the bottom, cutting off
        # the record button and Cancel entirely) — capped here, with the box itself scrolling
        # internally for whatever doesn't fit, rather than the box (and everything below it)
        # just growing without limit. 170 comfortably fits even the longest current script's
        # first several lines before it needs to scroll, and still leaves room for
        # record/cancel/etc. within this app's minimum window size, not just its default one.
        MAX_BOX_H = 170.0
        box_h = min(content_h, MAX_BOX_H)
        needs_scroll = content_h > box_h

        # Built bottom-up from fixed, tight gaps so the card's total height is exactly what its
        # content needs — no leftover space "because the card used to be taller."
        cursor = 16  # bottom margin
        cancel_y, cancel_h = cursor, 24
        cursor += cancel_h + 12
        record_y, record_d = cursor, 52
        cursor += record_d + 12
        elapsed_y, elapsed_h = cursor, 16
        cursor += elapsed_h + 8
        meter_y, meter_h = cursor, 6
        cursor += meter_h + 8
        error_y, error_h = cursor, 16
        cursor += error_h + 12
        box_y = cursor
        cursor += box_h + 12
        title_y, title_h = cursor, 20
        cursor += title_h + 16  # top margin
        ch = cursor

        card = self._makeCard(cw, ch)

        title = make_label("Record your voice sample", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        title.setFrame_(NSMakeRect(0, title_y, cw, title_h))

        script_box = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(20, box_y, box_w, box_h))
        script_box.setWantsLayer_(True)
        script_box.layer().setBackgroundColor_(white(0.06).CGColor())
        script_box.layer().setBorderColor_(white(0.12).CGColor())
        script_box.layer().setBorderWidth_(1.0)
        script_box.layer().setCornerRadius_(10.0)
        script_box.layer().setMasksToBounds_(True)  # clip scrolled content to the rounded box

        scroll = AppKit.NSScrollView.alloc().initWithFrame_(script_box.bounds())
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(needs_scroll)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, content_h))
        script_label.setFrame_(NSMakeRect(12, box_pad, text_w, text_h))
        container.addSubview_(script_label)
        scroll.setDocumentView_(container)
        script_box.addSubview_(scroll)

        if needs_scroll:
            # Same top-of-content fix already used for the main dropdown's own scroll view —
            # NSScrollView's clip view defaults its visible origin to (0, 0), which in this
            # bottom-up coordinate layout is the BOTTOM of the text, not the top. A script
            # should always open showing its first line, not its last.
            clip = scroll.contentView()
            clip.scrollToPoint_(NSMakePoint(0, content_h - box_h))
            scroll.reflectScrolledClipView_(clip)

            # A scrollbar alone isn't a reliable "there's more" cue — confirmed directly, a
            # real user couldn't tell the text was cut off rather than just ending there, since
            # macOS's default overlay-style scroller stays invisible until actively scrolled.
            # Same edge-fade technique as the dropdown's own scroll view (text genuinely fades
            # toward transparent approaching the hidden edge, via a mask on the scroll view's
            # own layer — not a colored overlay, which would look like a patch sitting on top
            # rather than the text dissolving into the box's real background).
            #
            # This MUST track scroll position, not sit static at the viewport's bottom 28pt —
            # a static fade there also covers the true final words once the user actually
            # scrolls all the way down, since it has no idea the content ended (confirmed
            # directly: the user scrolled to the end and reported "there's nothing," because
            # the last line was sitting inside the permanently-transparent zone). Reuses the
            # dropdown's own scroll-tracked edge-fade math (see _installHorizontalEdgeFade /
            # the voice-menu dropdown above) — bottom_alpha goes to 1.0 (no fade, fully opaque)
            # exactly when origin_y reaches 0, the true end of the scrollable content.
            fade_h = 28.0
            scroll.setWantsLayer_(True)
            mask = Quartz.CAGradientLayer.layer()
            mask.setFrame_(scroll.bounds())
            mask.setStartPoint_(NSMakePoint(0.5, 0.0))
            mask.setEndPoint_(NSMakePoint(0.5, 1.0))
            # Confirmed empirically for this exact gradient orientation elsewhere in this file
            # (see the dropdown's own edge-fade comment): location 0.0 renders at the visual
            # TOP of the view, location 1.0 at the visual BOTTOM.
            mask.setLocations_([0.0, max(0.0, 1.0 - fade_h / box_h), 1.0])

            def update_script_fade(note=None):
                origin_y = clip.bounds().origin.y
                bottom_alpha = 1.0 - max(0.0, min(1.0, origin_y / fade_h))
                AppKit.CATransaction.begin()
                AppKit.CATransaction.setDisableActions_(True)
                mask.setColors_([
                    AppKit.NSColor.whiteColor().CGColor(),
                    AppKit.NSColor.whiteColor().CGColor(),
                    AppKit.NSColor.whiteColor().colorWithAlphaComponent_(bottom_alpha).CGColor(),
                ])
                AppKit.CATransaction.commit()

            scroll.layer().setMask_(mask)
            update_script_fade()
            clip.setPostsBoundsChangedNotifications_(True)
            self._rec_script_fade_observer = AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                AppKit.NSViewBoundsDidChangeNotification, clip, None, update_script_fade)

        error_label = ClickThroughTextField.alloc().init()
        error_label.setBezeled_(False)
        error_label.setDrawsBackground_(False)
        error_label.setEditable_(False)
        error_label.setSelectable_(False)
        error_label.setAlignment_(AppKit.NSTextAlignmentCenter)
        error_label.setFont_(AppKit.NSFont.systemFontOfSize_(11.5))
        error_label.setAlphaValue_(0.0)
        error_label.setFrame_(NSMakeRect(10, error_y, cw - 20, error_h))
        self.rec_error_label = error_label

        meter = LevelMeterView.alloc().initWithFrame_(NSMakeRect(20, meter_y, box_w, meter_h))
        meter.configure()
        self.rec_meter = meter

        elapsed_label = make_label("0:00", 11, 0.5, align=AppKit.NSTextAlignmentCenter)
        elapsed_label.setFrame_(NSMakeRect(0, elapsed_y, cw, elapsed_h))
        self.rec_elapsed_label = elapsed_label

        record_btn = RecordButton.alloc().initWithFrame_(NSMakeRect(cw / 2 - record_d / 2, record_y, record_d, record_d))
        record_btn.configure(self.recordToggleClicked_)
        self.rec_toggle_btn = record_btn

        cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
        # "Back", not "Cancel" — this card is the SECOND step of the flow (style choice came
        # first), and unlike the later confirm/naming card (which has a separate "Re-record"
        # button for stepping back, so its own Cancel can legitimately exit the whole flow),
        # this card has no other way to go back one step. Reusing recordingCancelClicked_ here
        # was exactly the bug: it exits the ENTIRE flow via _rec_return_to (e.g. straight to
        # Manage Voices), skipping back past the style-choice screen entirely instead of
        # landing on it. recordingCaptureBackClicked_ below does the same in-flight cleanup but
        # always steps back to style choice specifically.
        cancel_btn = text_button_brighten("Back", NSMakeRect(cw / 2 - 40, cancel_y, 80, cancel_h),
                                           "recordingCaptureBackClicked:", self, cancel_font, white(0.5), white(0.85))

        for sub in (title, script_box, meter, elapsed_label, error_label, record_btn, cancel_btn):
            card.addSubview_(sub)
        self._presentOverlay(card)

    def recordToggleClicked_(self, sender):
        if self._rec_recording_active:
            self._stopRecording()
        else:
            self._startRecording()

    @objc.python_method
    def _startRecording(self):
        import sounddevice as sd
        import numpy as np
        # Confirmed directly: text-to-speech playback kept going right through the mic capture
        # otherwise, bleeding straight into the recorded reference clip. Paused, not stopped —
        # this preserves where they were so they can pick playback back up afterward, rather
        # than resetting the whole session over what might just be a quick voice-creation detour.
        if self.player is not None and self.player.isPlaying():
            self.player.pause()
            self._stopProgressTimer()
            self._syncPlaybackUI()
        self._rec_buffer = np.zeros(int(RECORD_MAX_SECONDS * RECORD_SAMPLE_RATE), dtype=np.float32)
        self._rec_write_pos = 0

        def callback(indata, frames, time_info, status):
            remaining = len(self._rec_buffer) - self._rec_write_pos
            n = min(frames, remaining)
            if n > 0:
                self._rec_buffer[self._rec_write_pos:self._rec_write_pos + n] = indata[:n, 0]
                self._rec_write_pos += n
            level = float(np.sqrt(np.mean(np.square(indata[:n, 0])))) if n > 0 else 0.0
            payload = {"level": level, "elapsed": self._rec_write_pos / RECORD_SAMPLE_RATE}
            self.performSelectorOnMainThread_withObject_waitUntilDone_("recLevelMain:", payload, False)
            if self._rec_write_pos >= len(self._rec_buffer):
                raise sd.CallbackStop

        try:
            self._rec_stream = sd.InputStream(
                samplerate=RECORD_SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=int(RECORD_SAMPLE_RATE * 0.1), callback=callback,
                finished_callback=lambda: self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "recordingAutoStoppedMain:", None, False))
            self._rec_stream.start()
        except Exception as e:
            self._flashInlineError(self.rec_error_label, f"Couldn't access your microphone: {e}")
            return
        self._rec_recording_active = True
        self.rec_toggle_btn.setRecording_(True)

    def recordingAutoStoppedMain_(self, _):
        # The 10s hard cap fired CallbackStop from inside the audio callback itself, rather
        # than the user clicking Stop in time — same trimming/validation path either way.
        if self._rec_recording_active:
            self._stopRecording()

    @objc.python_method
    def _stopRecording(self):
        import numpy as np
        if self._rec_stream is not None:
            self._rec_stream.stop()
            self._rec_stream.close()
            self._rec_stream = None
        self._rec_recording_active = False
        audio = self._rec_buffer[:self._rec_write_pos].copy() if self._rec_buffer is not None else np.zeros(0, dtype=np.float32)
        ok, message = self._validateRecording(audio, RECORD_SAMPLE_RATE)
        if not ok:
            self.rec_toggle_btn.setRecording_(False)
            self.rec_meter.setLevel_(0.0)
            self.rec_elapsed_label.setStringValue_("0:00")
            self._flashInlineError(self.rec_error_label, message)
            return
        # Peak-normalize to roughly match the bundled reference clips' own level (Ben/Sadie
        # both sit around -6 to -7.5 dBFS peak) — confirmed directly that a real user's
        # recording can land 5-11dB quieter than that with a normal mic/room setup, and that
        # gap measurably degrades Sesame's voice cloning, up to fully incoherent output on
        # the quietest one tested. This is why it applies before the preview too, not just
        # before the final save — what you hear in preview should be what the model actually
        # gets.
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 1e-6:
            audio = audio * (0.5 / peak)
        # Let the button's own release animation (shrinking back down) actually play on
        # screen before swapping to the confirm card, instead of cutting it off instantly.
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.18, False, lambda t: self._showRecordingConfirmCard(audio, RECORD_SAMPLE_RATE))

    def recLevelMain_(self, payload):
        try:
            self.rec_meter.setLevel_(min(1.0, float(payload["level"]) * 6.0))
            self.rec_elapsed_label.setStringValue_(format_playback_time(float(payload["elapsed"])))
        except Exception:
            traceback.print_exc(file=sys.stderr)

    @objc.python_method
    def _validateRecording(self, audio, sample_rate):
        import numpy as np
        duration = len(audio) / sample_rate
        if duration < RECORD_MIN_SECONDS:
            return False, "Too short — please read the whole script."
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak < 0.02:
            return False, "We didn't pick up any sound — check the right microphone is selected and try again."
        clipped_fraction = float(np.mean(np.abs(audio) > 0.97))
        if clipped_fraction > 0.005:
            return False, "That sounded distorted — try sitting a bit further from the microphone and record again."
        return True, ""

    @objc.python_method
    def _showRecordingConfirmCard(self, audio, sample_rate):
        self._rec_preview_audio = (audio, sample_rate)
        cw, ch = 320, 260
        card = self._makeCard(cw, ch)

        title = make_label("Listen and name it", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        title.setFrame_(NSMakeRect(0, ch - 40, cw, 20))

        duration = len(audio) / sample_rate
        dur_label = make_label(format_playback_time(duration), 11, 0.5, align=AppKit.NSTextAlignmentCenter)
        dur_label.setFrame_(NSMakeRect(0, ch - 64, cw, 16))

        play_btn = icon_button("play.fill", 15, NSMakeRect(cw / 2 - 20, ch - 138, 40, 40),
                                "previewToggleClicked:", self, base=0.08, hover=0.16, corner=20.0, tint=0.95)
        self.rec_preview_play_btn = play_btn

        name_field = AppKit.NSTextField.alloc().initWithFrame_(NSMakeRect(20, ch - 180, cw - 40, 30))
        name_field.setBezeled_(True)
        name_field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
        name_field.setDrawsBackground_(True)
        name_field.setBackgroundColor_(white(0.08))
        name_field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        name_field.setDelegate_(self)
        attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(13),
                 AppKit.NSForegroundColorAttributeName: white(0.45)}
        name_field.setPlaceholderAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("Name this voice", attrs))
        self.rec_name_field = name_field

        rerecord_font = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)
        rerecord_btn = text_button("Re-record", NSMakeRect(20, 16, 120, 32), "rerecordClicked:", self,
                                    rerecord_font, 0.08, 0.16, 9.0, white(0.85))
        save_btn = cta_button("Save", NSMakeRect(150, 16, cw - 170, 32), "useRecordingClicked:", self)
        self.rec_save_btn = save_btn
        self._updateRecordSaveState()

        cancel_font = AppKit.NSFont.systemFontOfSize_(11)
        cancel_btn = text_button("Cancel", NSMakeRect(cw / 2 - 40, ch - 208, 80, 18), "recordingCancelClicked:", self,
                                  cancel_font, 0.0, 0.06, 5.0, white(0.35))

        for sub in (title, dur_label, play_btn, name_field, rerecord_btn, save_btn, cancel_btn):
            card.addSubview_(sub)
        self._presentOverlay(card)

    @objc.python_method
    def _updateRecordSaveState(self):
        enabled = bool(str(self.rec_name_field.stringValue()).strip())
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithWhite_alpha_(0.11 if enabled else 0.4, 1.0),
        }
        self.rec_save_btn.setAttributedTitle_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("Save", attrs))
        self.rec_save_btn.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithWhite_alpha_(0.95 if enabled else 0.3, 1.0).CGColor())

    def previewToggleClicked_(self, sender):
        import numpy as np
        if self._rec_preview_player is not None and self._rec_preview_player.isPlaying():
            self._rec_preview_player.stop()
            img = symbol_image("play.fill", 15)
            if img:
                self.rec_preview_play_btn.setImage_(img)
            return
        audio, sample_rate = self._rec_preview_audio
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        w = wave.open(buf, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
        w.close()
        player, err = AVFoundation.AVAudioPlayer.alloc().initWithData_error_(bytes(buf.getvalue()), None)
        if player is None:
            self.showError_("Could not play back the recording.")
            return
        self._rec_preview_player = player
        player.setVolume_(max(0.0, min(1.0, self.config.get("volume", 1.0))))
        player.play()
        img = symbol_image("pause.fill", 15)
        if img:
            self.rec_preview_play_btn.setImage_(img)

    def rerecordClicked_(self, sender):
        self._showRecordingCaptureCard()

    def recordingCancelClicked_(self, sender):
        # Cancel must ALWAYS get the user out, no matter what — clear every reference before
        # touching it, so a raised exception from stop()/close() can never leave a stale
        # reference behind (which would keep dismissOverlay's own stream guard blocking exit)
        # or skip the dismiss entirely.
        stream, self._rec_stream = self._rec_stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                traceback.print_exc(file=sys.stderr)
        player, self._rec_preview_player = self._rec_preview_player, None
        if player is not None:
            try:
                player.stop()
            except Exception:
                traceback.print_exc(file=sys.stderr)
        self._rec_recording_active = False
        return_to, self._rec_return_to = self._rec_return_to, None
        if return_to is not None:
            return_to()
        else:
            self.dismissOverlay()

    def recordingCaptureBackClicked_(self, sender):
        # Same in-flight cleanup as recordingCancelClicked_ (stop/close a live mic stream,
        # stop any preview player), but always steps back to style choice rather than exiting
        # the whole flow — deliberately does NOT touch/consume _rec_return_to, since that's
        # still needed later (either if the user backs out further from style choice, or once
        # they actually save the voice).
        stream, self._rec_stream = self._rec_stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                traceback.print_exc(file=sys.stderr)
        player, self._rec_preview_player = self._rec_preview_player, None
        if player is not None:
            try:
                player.stop()
            except Exception:
                traceback.print_exc(file=sys.stderr)
        self._rec_recording_active = False
        self._showStyleChoiceCard()

    def useRecordingClicked_(self, sender):
        name = str(self.rec_name_field.stringValue()).strip()
        if not name:
            return
        try:
            import uuid
            import numpy as np
            audio, sample_rate = self._rec_preview_audio
            voice_id = "custom_" + uuid.uuid4().hex[:8]
            audio_file = f"{voice_id}.wav"
            dest = sesame_voices_path(audio_file)
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            with wave.open(dest, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(sample_rate))
                w.writeframes(pcm)
            ref_text = (self._rec_selected_script or RECORD_SCRIPT_PRESETS[0])["script"]
            entry = {"id": voice_id, "label": name, "audio_file": audio_file, "ref_text": ref_text}
            self.config.setdefault("sesame_custom_voices", []).append(entry)
            self._setVoiceId(voice_id, "Sesame")  # saves the whole config, sesame_custom_voices included
            # Unlike a normal dropdown voice switch (voiceChanged_), this path never went
            # through that handler, so nothing invalidated the previous voice's cached audio —
            # confirmed directly: the UI correctly showed the new voice selected, but pressing
            # Play replayed the OLD voice's cached chunks anyway, since the cache-hit check in
            # playPauseClicked_ only compares the text, not which voice generated it.
            self._invalidateUngeneratedChunks()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            # Clear the guard BEFORE showError_ — showError_ itself calls dismissOverlay(),
            # which no-ops while this flag is set (see dismissOverlay's own comment), so
            # leaving it set here would silently swallow this exact error message: the
            # overlay stays open, hiding the error behind it on the now-unreachable status
            # label underneath.
            self._rec_recording_active = False
            self.showError_("Couldn't save that voice — please try again.")
            return
        # The voice is already saved on disk and in config at this point — everything below
        # is just cleanup/UI. Clear the reference before calling stop() (same reasoning as
        # recordingCancelClicked_): if a real preview player's stop() ever throws, this must
        # not skip dismissOverlay()/fetchVoices()/setStatus() and strand the user looking at a
        # stale confirm card for a voice that actually saved successfully.
        player, self._rec_preview_player = self._rec_preview_player, None
        if player is not None:
            try:
                player.stop()
            except Exception:
                traceback.print_exc(file=sys.stderr)
        self._rec_recording_active = False
        self.fetchVoices()
        return_to, self._rec_return_to = self._rec_return_to, None
        if return_to is not None:
            return_to()  # e.g. back to Manage Voices, refreshed with the new voice included
        else:
            self.dismissOverlay()
        self.setStatus(f'"{name}" saved — ready to use.')
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            2.5, False, lambda t: self.setStatus(""))

    @objc.python_method
    def _buildVoicesSection(self, content):
        # Inline in Settings now, not its own popup card — modeled on macOS's own
        # list-editing sheets (System Settings' Text Replacements, Login Items): plain rows
        # separated by hairlines rather than each name sitting in its own bordered box.
        customs = list(self.config.get("sesame_custom_voices", []))
        cb = content.bounds()
        box_w, box_h = cb.size.width, cb.size.height

        list_bottom, row_h = 60, 40
        list_h = box_h - list_bottom
        # CardView (not a plain NSView) so clicking blank space anywhere in the list — between
        # rows, below the last one — ends any active rename the same way clicking the card's
        # own background does; a plain NSView never becomes first responder on click, so an
        # active field editor would never resign and a rename would never commit that way.
        list_box = CardView.alloc().initWithFrame_(NSMakeRect(0, list_bottom, box_w, list_h))
        list_box.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        list_box.setWantsLayer_(True)
        list_box.layer().setBackgroundColor_(white(0.05).CGColor())
        list_box.layer().setBorderColor_(white(0.09).CGColor())
        list_box.layer().setBorderWidth_(1.0)
        list_box.layer().setCornerRadius_(10.0)
        list_box.layer().setMasksToBounds_(True)
        content.addSubview_(list_box)

        if not customs:
            empty = make_label("You haven't created any custom voices yet.", 12, 0.45, align=AppKit.NSTextAlignmentCenter)
            empty.setFrame_(NSMakeRect(10, list_h / 2 - 16, box_w - 20, 32))
            empty.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            list_box.addSubview_(empty)
        else:
            content_h = max(list_h, len(customs) * row_h)
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, list_h))
            scroll.setBorderType_(AppKit.NSNoBorder)
            scroll.setHasVerticalScroller_(True)
            scroll.setDrawsBackground_(False)
            scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            container = CardView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, content_h))
            cy = content_h
            self._manage_voice_fields = {}
            for i, entry in enumerate(customs):
                cy -= row_h
                if i > 0:
                    line = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, cy + row_h - 1, box_w, 1))
                    line.setWantsLayer_(True)
                    line.layer().setBackgroundColor_(white(0.09).CGColor())
                    container.addSubview_(line)
                # Plain-text at rest; a soft box fades in on hover and it only becomes truly
                # editable on double-click (see EditableNameField) — reads as a real list
                # label rather than a permanently-visible little form field.
                field = EditableNameField.alloc().initWithFrame_(NSMakeRect(12, cy + 6, box_w - 60, 26))
                field.configure()
                field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
                field.setTextColor_(white(0.9))
                field.setStringValue_(entry["label"])
                field.setDelegate_(self)
                self._manage_voice_fields[entry["id"]] = field
                trash = icon_button("trash", 13, NSMakeRect(box_w - 40, cy + 4, 30, 30),
                                     "manageVoiceDeleteClicked:", self, base=0.0, hover=0.14, corner=7.0, tint=0.5)
                # trash IS a custom HoverButton subclass (unlike the plain NSTextField above),
                # which PyObjC lets carry arbitrary Python attributes fine — same convention
                # already used for _showDropdown's rows (_on_click, _suppress_hover).
                trash._manage_voice_id = entry["id"]
                container.addSubview_(field)
                container.addSubview_(trash)
            scroll.setDocumentView_(container)
            clip = scroll.contentView()
            clip.scrollToPoint_(NSMakePoint(0, max(0.0, content_h - list_h)))
            scroll.reflectScrolledClipView_(clip)
            # The REAL bug behind the shrink-to-blank failure: content_h (used above to size
            # the initial container/positions) is max(list_h, len(customs)*row_h) — at build
            # time that's fine, but it means content_h can be INFLATED by whatever the viewport
            # happened to be at that moment, not the list's own true minimum size. Passing that
            # inflated number as natural_h made _installScrollReclamp treat it as a floor the
            # container could never shrink below — confirmed directly via debug logging: once
            # the window had been big, this stayed locked at that height forever, so shrinking
            # the window just scrolled the (still-oversized) container to reveal empty space
            # below its unmoved rows instead of actually shrinking to match. The list's real
            # minimum is len(customs)*row_h, full stop — that's what natural_h needs to be.
            self._installScrollReclamp(scroll, container, len(customs) * row_h)
            list_box.addSubview_(scroll)

        add_btn = icon_button("plus", 14, NSMakeRect(0, 16, 36, 32), "addVoiceFromManageClicked:", self,
                               base=0.08, hover=0.16, corner=9.0, tint=0.85)
        content.addSubview_(add_btn)

    def addVoiceFromManageClicked_(self, sender):
        # Entered from Settings' Voices section — Cancel and a successful Save should both
        # come back here (refreshed, in Save's case), not dump you out to the main text-input
        # screen.
        self._rec_return_to = lambda: self.showSettingsScreen("voices")
        self._showStyleChoiceCard()

    def manageVoiceDeleteClicked_(self, sender):
        voice_id = getattr(sender, "_manage_voice_id", None)
        entry = next((v for v in self.config.get("sesame_custom_voices", []) if v["id"] == voice_id), None)
        if entry is None:
            return
        self._pending_delete_voice_id = voice_id

        cw, ch = 300, 170
        card = self._makeCard(cw, ch)
        title = make_label("Delete this voice?", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        title.setFrame_(NSMakeRect(0, ch - 40, cw, 20))
        sub = make_label(f'"{entry["label"]}" will be permanently removed.', 12, 0.5, align=AppKit.NSTextAlignmentCenter)
        sub.setFrame_(NSMakeRect(20, ch - 70, cw - 40, 32))

        cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
        cancel_btn = text_button("Cancel", NSMakeRect(20, 20, (cw - 52) / 2, 34), "cancelDeleteVoice:", self,
                                  cancel_font, 0.08, 0.16, 9.0, white(0.85))
        delete_btn = cta_button("Delete", NSMakeRect(cw / 2 + 6, 20, (cw - 52) / 2, 34), "confirmDeleteVoiceClicked:", self)
        # cta_button defaults to a light bg/dark text "positive" look — overridden here to a
        # solid red fill with white text so a destructive, unrecoverable action reads as
        # visually distinct from every other confirm button in the app.
        delete_btn.layer().setBackgroundColor_(AppKit.NSColor.systemRedColor().colorWithAlphaComponent_(0.85).CGColor())
        delete_attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        delete_btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_("Delete", delete_attrs))

        for s in (title, sub, cancel_btn, delete_btn):
            card.addSubview_(s)
        self._presentOverlay(card)

    def cancelDeleteVoice_(self, sender):
        self._pending_delete_voice_id = None
        self.showSettingsScreen("voices")  # back to the list, not fully closed

    def confirmDeleteVoiceClicked_(self, sender):
        voice_id = self._pending_delete_voice_id
        self._pending_delete_voice_id = None
        customs = self.config.get("sesame_custom_voices", [])
        entry = next((v for v in customs if v["id"] == voice_id), None)
        if entry is not None:
            try:
                os.remove(sesame_voices_path(entry["audio_file"]))
            except OSError:
                pass
            self.config["sesame_custom_voices"] = [v for v in customs if v["id"] != voice_id]
            if self.config.get("voice_id") == voice_id:
                self._setVoiceId(SESAME_VOICES[0]["id"], "Sesame")  # fall back to the first built-in
            else:
                save_config(self.config)  # still need to persist the sesame_custom_voices removal above
        self.fetchVoices()
        self.showSettingsScreen("voices")  # refreshed list, still in the management screen
        self.setStatus("Voice deleted.")
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            2.0, False, lambda t: self.setStatus(""))

    def recordingsClicked_(self, sender):
        self.showRecordingsScreen(self.current_recordings_tab)

    def backToMainClicked_(self, sender):
        self.showMainScreen()

    # Shared vertical geometry for every full-screen destination (History, Settings) — the
    # title sits alone at top with real breathing room, Back sits alone at bottom-left, and
    # everything in between is "safe content" that can't collide with either. HEADER_H/FOOTER_H
    # are how much of the window's total height each end reserves.
    SCREEN_HEADER_H = 54.0
    SCREEN_FOOTER_H = 58.0

    @objc.python_method
    def _wrapIntoLines(self, items, gap, max_w):
        """items: [(width, payload), ...]. Groups into lines that fit within max_w, in order —
        a real flow-wrap (like text wrapping, or CSS flex-wrap), not just a single fixed row.
        Used so pill rows (Storage's mode/size buttons) drop to a new line instead of either
        clipping or requiring horizontal scroll when the window is narrower than they need.
        Verified in isolation against known inputs before wiring into real layout code."""
        lines = []
        current = []
        current_w = 0.0
        for item_w, payload in items:
            added_w = item_w if not current else item_w + gap
            if current and current_w + added_w > max_w:
                lines.append(current)
                current = []
                current_w = 0.0
                added_w = item_w
            current.append((item_w, payload))
            current_w += added_w
        if current:
            lines.append(current)
        return lines

    @objc.python_method
    def _installWidthRebuildTrigger(self, v, rebuild_fn):
        # Growing/shrinking a scroll's document view (see _installScrollReclamp) is enough for
        # height, and for simple left-aligned content it was enough for width too — but real
        # reflow (Storage's pills dropping to a new line, History/Voices' row dividers and
        # trash icons actually tracking the new width) needs the same construction logic this
        # screen already uses at build time, not an incremental patch bolted onto individual
        # elements after the fact. Rebuilds live, on every real width change during the drag
        # (not debounced to mouse-up) — explicitly wanted: the reflow should be visible while
        # actually resizing, not just appear once you let go.
        if self._width_rebuild_observer is not None:
            AppKit.NSNotificationCenter.defaultCenter().removeObserver_(self._width_rebuild_observer)
            self._width_rebuild_observer = None
        if self._width_rebuild_timer is not None:
            self._width_rebuild_timer.invalidate()
            self._width_rebuild_timer = None
        v.setPostsFrameChangedNotifications_(True)
        state = {"w": v.frame().size.width}

        def on_frame_change(note):
            new_w = v.frame().size.width
            if abs(new_w - state["w"]) < 1.0:
                return
            state["w"] = new_w
            rebuild_fn()

        self._width_rebuild_observer = AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AppKit.NSViewFrameDidChangeNotification, v, None, on_frame_change)

    @objc.python_method
    def _installScrollReclamp(self, scroll, container, natural_h, natural_w=None):
        # Two related bugs, both from the same root cause: NSScrollView's document view
        # (container) is a fixed size set once at build time, and neither its size nor the
        # scroll offset ever get re-synced against a LIVE window resize on their own.
        #
        # Bug 1 — short content sinks to the BOTTOM instead of staying pinned to the top:
        # when the viewport is taller than the content actually needs (natural_h), container
        # was left at exactly natural_h, so it doesn't fill the viewport — and since its own
        # origin is what's fixed (not its top edge), the empty leftover space appears ABOVE
        # it, not below. Confirmed directly: Data & Storage's controls rendered hugging the
        # bottom of the window instead of the top. Fixed by growing container to at least the
        # viewport's height whenever there's slack, and shifting its existing content down by
        # the same amount that growth adds below — so the content's position relative to
        # container's TOP edge never changes, only how much empty space trails below it.
        #
        # Bug 2 — an existing scroll offset can point at a stale, now-nonsensical position
        # once the viewport's own height changes (confirmed: the Voices list visibly "floated"
        # at an arbitrary spot after a resize). Reclamped back into valid range afterward.
        #
        # Runs on every clip bounds change (a resize, or an ordinary scroll) — the resize-sync
        # only ever ADDS height when there's new slack (never removes it, so a normal scroll
        # never fights this), and the reclamp only ever pulls an offset that's gone invalid
        # back in, never touching an already-valid one.
        if self._list_scroll_observer:
            nc0 = AppKit.NSNotificationCenter.defaultCenter()
            for token in self._list_scroll_observer:
                nc0.removeObserver_(token)
            self._list_scroll_observer = []
        clip = scroll.contentView()
        clip.setPostsBoundsChangedNotifications_(True)
        scroll.setPostsFrameChangedNotifications_(True)
        state = {"h": container.frame().size.height, "w": container.frame().size.width}

        def sync(note=None):
            visible_h = clip.bounds().size.height
            visible_w = clip.bounds().size.width
            new_h = max(natural_h, visible_h)
            new_w = max(natural_w, visible_w) if natural_w is not None else state["w"]
            if new_h != state["h"] or new_w != state["w"]:
                # `!=`, not `>` — the real bug. Growing was handled, but shrinking back down
                # after the window had been made bigger was not, so container stayed stuck at
                # its largest-ever size: shrinking the window afterward left the real content
                # (still positioned near what used to be the top of that oversized container)
                # scrolled miles out of the now-small viewport — confirmed directly, it showed
                # as a big blank area with just a scrollbar and nothing else visible.
                #
                # Width was a SEPARATE bug found afterward: container's width was frozen at
                # whatever it was at build time and never tracked the viewport at all (only
                # height did) — confirmed directly via screenshot, pill rows built at a wider
                # window got clipped at the right edge once the window was narrower. No
                # x-shift needed for width the way height needed a y-shift: this content is
                # left-aligned already, so growing/shrinking width just changes how much empty
                # space trails on the right, not where anything starts.
                delta_h = new_h - state["h"]
                f = container.frame()
                container.setFrame_(NSMakeRect(f.origin.x, f.origin.y, new_w, new_h))
                if delta_h:
                    for sub in list(container.subviews()):
                        sf = sub.frame()
                        sub.setFrameOrigin_(NSMakePoint(sf.origin.x, sf.origin.y + delta_h))
                state["h"] = new_h
                state["w"] = new_w
                # A resize invalidates whatever the scroll position meant relative to the old
                # size — always resnap to showing the top rather than trying to preserve a
                # stale relative offset, which is simple and correct for what this exists to
                # fix (content ending up sunk to the bottom, or scrolled to nothing at all).
                clip.scrollToPoint_(NSMakePoint(0, max(0.0, new_h - visible_h)))
                scroll.reflectScrolledClipView_(clip)
                return
            max_origin = max(0.0, state["h"] - visible_h)
            if clip.bounds().origin.y > max_origin:
                clip.scrollToPoint_(NSMakePoint(0, max_origin))
                scroll.reflectScrolledClipView_(clip)

        sync()
        nc = AppKit.NSNotificationCenter.defaultCenter()
        self._list_scroll_observer = [
            nc.addObserverForName_object_queue_usingBlock_(AppKit.NSViewBoundsDidChangeNotification, clip, None, sync),
            # ALSO listening on scroll's own frame-change, not just the clip's bounds-change —
            # this is the actual fix being tested: a resize driven by an autoresizing mask
            # (i.e. a real window drag, as opposed to a direct setBounds_/scrollToPoint_ call)
            # may not reliably post NSViewBoundsDidChangeNotification, which would explain
            # content never tracking a live resize at all (confirmed: it stayed exactly at its
            # original size/position, just floating wherever that lands in a since-grown
            # viewport, rather than growing or shrinking with it).
            nc.addObserverForName_object_queue_usingBlock_(AppKit.NSViewFrameDidChangeNotification, scroll, None, sync),
        ]

    @objc.python_method
    def _screenHeader(self, v, w, h, title_text, right_control=None):
        # Back moved to bottom-left — used to share the top row with the title, cramped up
        # against the traffic lights, and read as an afterthought rather than a real,
        # deliberate control. x=20 to match list_box/sidebar's own left margin exactly (was
        # 16, one pixel off from everything else on these screens — confirmed visually).
        # text_button, not cta_button — same reasoning as Continue on the welcome screen: a
        # bordered, dark fill with the pills' own real hover/press feedback, not a flat static
        # white block with none.
        back_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold)
        # No background box, no border — plain text with the same brighten-on-hover feedback
        # as the sidebar rows and the wordmark (BrightenOnHoverButton), left-aligned to match
        # "Storage"/"Voices" exactly rather than approximating it via a snug centered box.
        # y shifted +8 from the "obvious" 16, same empirically-measured correction and same
        # frame height as the wordmark — see its comment for why (centering vs. the old
        # top-alignment this position was tuned against, and why shrinking the label_frame
        # instead didn't land where predicted).
        back_btn = BrightenOnHoverButton.alloc().initWithFrame_(NSMakeRect(20, 24, 60, 26))
        back_btn.configureBrighten(
            "‹ Back", back_font, white(0.55), white(0.95),
            align=AppKit.NSTextAlignmentLeft, label_frame=NSMakeRect(0, 0, 60, 26))
        back_btn.setTarget_(self)
        back_btn.setAction_("backToMainClicked:")
        back_btn.setAutoresizingMask_(AppKit.NSViewMaxXMargin | AppKit.NSViewMaxYMargin)
        v.addSubview_(back_btn)
        # Empty title_text ("") means the destination doesn't need one — Settings' own sidebar
        # already says where you are (Storage/Voices/...), so a redundant "Settings" heading
        # above it was just consuming space no other screen needed. Skipped entirely rather
        # than added-but-invisible, so it doesn't reserve empty space for nothing.
        if title_text:
            # A little lower than the very top edge — the whole header band (title + wordmark)
            # reads more like one deliberate row this way instead of the title crowding the top.
            title = make_label(title_text, 16, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, h - 46, w, 22))
            title.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
            v.addSubview_(title)
        if right_control is not None:
            v.addSubview_(right_control)

    @objc.python_method
    def showRecordingsScreen(self, tab="recent"):
        # One screen, two tabs via a sliding pill selector — replaces the earlier version
        # where Saved was three clicks deep in Settings > File Location while Recent was one
        # click from the wordmark, a real inconsistency once both existed side by side. The
        # pill control (see SegmentedPillControl) is deliberately kept ALIVE across a tab
        # switch instead of going through a full rebuild like every other change on this
        # screen — see _recordingsTabChanged.
        self.current_recordings_tab = tab
        v = AppKit.NSView.alloc().initWithFrame_(self.root.bounds())
        b = v.bounds()
        W, H = b.size.width, b.size.height
        self._screenHeader(v, W, H, "Recordings")

        pill_w, pill_h = 176.0, 30.0
        seg = SegmentedPillControl.alloc().initWithFrame_(
            NSMakeRect((W - pill_w) / 2.0, H - 46.0 - 12.0 - pill_h, pill_w, pill_h))
        seg.configure(["Recent", "Saved"], self._recordingsTabChanged)
        seg.selected_index = 0 if tab == "recent" else 1
        seg._applySelectionColors()
        seg._layoutSegments(animated=False)
        # Fixed width, centered — only its X position needs to track a resize (autoresizing
        # handles that on its own); see SegmentedPillControl's own docstring for why it
        # doesn't need a resize-driven relayout hook the way the list below does.
        seg.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMinYMargin)
        v.addSubview_(seg)
        self._recordings_seg = seg

        cog = icon_button("gearshape", 13, NSMakeRect(W - 46, 20, 26, 26),
                           "recordingsCogClicked:", self, base=0.0, hover=0.12, corner=8.0, tint=0.55)
        cog.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
        v.addSubview_(cog)

        # 42 = the pill row's own height (30) + a 12pt gap above the list — same reserved-
        # band idiom as SCREEN_HEADER_H/SCREEN_FOOTER_H, scoped to this one screen since it's
        # the only one with a second header row.
        list_top, list_bottom = H - self.SCREEN_HEADER_H - 42.0, self.SCREEN_FOOTER_H
        list_h = max(1.0, list_top - list_bottom)
        list_box = CardView.alloc().initWithFrame_(NSMakeRect(20, list_bottom, W - 40, list_h))
        list_box.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        list_box.setWantsLayer_(True)
        list_box.layer().setBackgroundColor_(white(0.05).CGColor())
        list_box.layer().setBorderColor_(white(0.09).CGColor())
        list_box.layer().setBorderWidth_(1.0)
        list_box.layer().setCornerRadius_(10.0)
        list_box.layer().setMasksToBounds_(True)
        v.addSubview_(list_box)
        self._recordings_list_box = list_box

        self._buildRecordingsList(tab)

        self.current_screen = "recordings"
        self.swap_screen(v)
        self._installWidthRebuildTrigger(v, lambda: self.showRecordingsScreen(self.current_recordings_tab))

    @objc.python_method
    def _recordingsTabChanged(self, index):
        # Fires from the pill control AFTER it's already updated its own selected_index and
        # animated the slide — this only swaps what's listed below it, not the header/pill.
        tab = "recent" if index == 0 else "saved"
        self.current_recordings_tab = tab
        self._buildRecordingsList(tab)

    @objc.python_method
    def _buildRecordingsList(self, tab):
        list_box = self._recordings_list_box
        for sub in list(list_box.subviews()):
            sub.removeFromSuperview()
        box_w = list_box.bounds().size.width
        list_h = list_box.bounds().size.height
        if tab == "recent":
            self._buildRecentRows(list_box, box_w, list_h)
        else:
            self._buildSavedRows(list_box, box_w, list_h)

    def recordingsCogClicked_(self, sender):
        self.showSettingsScreen("storage" if self.current_recordings_tab == "recent" else "location")

    @objc.python_method
    def _buildRecentRows(self, list_box, box_w, list_h):
        entries = history.list_entries()
        row_h = 56
        if not entries:
            empty = make_label(
                "Nothing here yet — finished generations you play all the way through are "
                "saved automatically.", 12, 0.45, align=AppKit.NSTextAlignmentCenter)
            empty.setFrame_(NSMakeRect(10, list_h / 2 - 28, box_w - 20, 56))
            empty.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            list_box.addSubview_(empty)
            return
        content_h = max(list_h, len(entries) * row_h)
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, list_h))
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        container = CardView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, content_h))
        cy = content_h
        for i, entry in enumerate(entries):
            cy -= row_h
            if i > 0:
                line = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, cy + row_h - 1, box_w, 1))
                line.setWantsLayer_(True)
                line.layer().setBackgroundColor_(white(0.09).CGColor())
                container.addSubview_(line)

            # Whole-row clickable/hoverable background — ContextMenuButton (not plain
            # HoverButton) so a right-click offers "Save to Permanent Location", the
            # promote-a-cache-entry-to-real-storage action this row otherwise has no
            # way to trigger (left-click is already spoken for: loads the text).
            row = ContextMenuButton.alloc().initWithFrame_(NSMakeRect(0, cy, box_w, row_h))
            row.configure(0.0, 0.06, 8.0)
            row.setTitle_("")
            row.setTarget_(self)
            row.setAction_("_historyRowClicked:")
            row._history_entry_id = entry["id"]
            row.context_menu_items = [
                ("Save to Permanent Location", lambda eid=entry["id"]: self._historySaveClicked(eid)),
            ]
            container.addSubview_(row)

            # ClickThroughTextField so these labels never swallow clicks meant for the row
            # button underneath them (same idiom as placeholder_label).
            preview = ClickThroughTextField.alloc().init()
            preview.setBezeled_(False)
            preview.setDrawsBackground_(False)
            preview.setEditable_(False)
            preview.setSelectable_(False)
            preview.setFont_(AppKit.NSFont.systemFontOfSize_(13))
            preview.setTextColor_(white(0.85))
            preview.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            preview.setStringValue_(entry["text"])
            preview.setFrame_(NSMakeRect(12, cy + 28, box_w - 60, 18))
            container.addSubview_(preview)

            meta = ClickThroughTextField.alloc().init()
            meta.setBezeled_(False)
            meta.setDrawsBackground_(False)
            meta.setEditable_(False)
            meta.setSelectable_(False)
            meta.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            meta.setTextColor_(white(0.45))
            meta.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            meta.setStringValue_(
                f"{entry['provider']} · {self._historyVoiceLabel(entry['provider'], entry['voice'])} · "
                f"{format_relative_time(entry['created_at'])}")
            meta.setFrame_(NSMakeRect(12, cy + 8, box_w - 60, 14))
            container.addSubview_(meta)

            trash = icon_button("trash", 13, NSMakeRect(box_w - 40, cy + (row_h - 30) / 2.0, 30, 30),
                                 "_historyDeleteClicked:", self, base=0.0, hover=0.14, corner=7.0, tint=0.5)
            trash._history_entry_id = entry["id"]
            container.addSubview_(trash)
        scroll.setDocumentView_(container)
        clip = scroll.contentView()
        # NSScrollView's clip view defaults to showing the BOTTOM of non-flipped content —
        # scroll to the top (newest entries) explicitly, same fix used in Manage Voices.
        clip.scrollToPoint_(NSMakePoint(0, max(0.0, content_h - list_h)))
        scroll.reflectScrolledClipView_(clip)
        # natural_h must be the list's true minimum (len(entries)*row_h), NOT content_h — see
        # the Voices-section bug this exact reasoning was confirmed against via debug logging.
        self._installScrollReclamp(scroll, container, len(entries) * row_h)
        list_box.addSubview_(scroll)

    @objc.python_method
    def _buildSavedRows(self, list_box, box_w, list_h):
        location = self._saveLocation()
        entries = saved.list_saved(location)
        row_h = 56
        if not entries:
            empty = make_label(
                'Nothing saved yet — right-click a Recent recording and choose "Save to '
                'Permanent Location."', 12, 0.45, align=AppKit.NSTextAlignmentCenter)
            empty.cell().setWraps_(True)
            empty.setFrame_(NSMakeRect(20, list_h / 2 - 28, box_w - 40, 56))
            empty.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            list_box.addSubview_(empty)
            return
        content_h = max(list_h, len(entries) * row_h)
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, list_h))
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        container = CardView.alloc().initWithFrame_(NSMakeRect(0, 0, box_w, content_h))
        cy = content_h
        for i, entry in enumerate(entries):
            cy -= row_h
            if i > 0:
                line = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, cy + row_h - 1, box_w, 1))
                line.setWantsLayer_(True)
                line.layer().setBackgroundColor_(white(0.09).CGColor())
                container.addSubview_(line)

            row = ContextMenuButton.alloc().initWithFrame_(NSMakeRect(0, cy, box_w, row_h))
            row.configure(0.0, 0.06, 8.0)
            row.setTitle_("")
            row.setTarget_(self)
            row.setAction_("savedRowClicked:")
            row._saved_path = entry["path"]
            # "Copy Text" only offered when there IS text — a file dragged in from Finder
            # (no sidecar, see saved.list_saved) has none, and copying an empty string to
            # the clipboard would just silently clobber whatever the user had there.
            menu_items = []
            if entry["text"]:
                menu_items.append(("Copy Text", lambda text=entry["text"]: self._savedCopyText(text)))
            menu_items.append(("Reveal in Finder", lambda path=entry["path"]: self._savedReveal(path)))
            menu_items.append((None, None))
            menu_items.append(("Delete", lambda path=entry["path"], text=entry["text"], fn=entry["filename"]:
                                self._savedDeleteRequested(path, text, fn)))
            row.context_menu_items = menu_items
            container.addSubview_(row)

            preview = ClickThroughTextField.alloc().init()
            preview.setBezeled_(False)
            preview.setDrawsBackground_(False)
            preview.setEditable_(False)
            preview.setSelectable_(False)
            preview.setFont_(AppKit.NSFont.systemFontOfSize_(13))
            preview.setTextColor_(white(0.85))
            preview.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            # Falls back to the filename when there's no text (dragged in from Finder) —
            # an unlabeled blank row would otherwise be indistinguishable from any other.
            preview.setStringValue_(entry["text"] or entry["filename"])
            preview.setFrame_(NSMakeRect(12, cy + 28, box_w - 60, 18))
            container.addSubview_(preview)

            meta = ClickThroughTextField.alloc().init()
            meta.setBezeled_(False)
            meta.setDrawsBackground_(False)
            meta.setEditable_(False)
            meta.setSelectable_(False)
            meta.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            meta.setTextColor_(white(0.45))
            meta.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            if entry["provider"]:
                meta_text = (f"{entry['provider']} · {self._historyVoiceLabel(entry['provider'], entry['voice'])} · "
                             f"{format_relative_time(entry['modified_at'])} · {self._humanMB(entry['size_bytes'])}")
            else:
                meta_text = f"{format_relative_time(entry['modified_at'])} · {self._humanMB(entry['size_bytes'])}"
            meta.setStringValue_(meta_text)
            meta.setFrame_(NSMakeRect(12, cy + 8, box_w - 60, 14))
            container.addSubview_(meta)

            trash = icon_button("trash", 13, NSMakeRect(box_w - 40, cy + (row_h - 30) / 2.0, 30, 30),
                                 "savedDeleteClicked:", self, base=0.0, hover=0.14, corner=7.0, tint=0.5)
            trash._saved_path = entry["path"]
            container.addSubview_(trash)
        scroll.setDocumentView_(container)
        clip = scroll.contentView()
        clip.scrollToPoint_(NSMakePoint(0, max(0.0, content_h - list_h)))
        scroll.reflectScrolledClipView_(clip)
        self._installScrollReclamp(scroll, container, len(entries) * row_h)
        list_box.addSubview_(scroll)

    def savedRowClicked_(self, sender):
        path = getattr(sender, "_saved_path", None)
        if path is None:
            return
        entry = next((e for e in saved.list_saved(self._saveLocation()) if e["path"] == path), None)
        if entry is None:
            return
        self.showMainScreen()
        self._playSavedEntry(entry["path"], entry["text"])

    @objc.python_method
    def _playSavedEntry(self, path, text):
        try:
            with open(path, "rb") as f:
                wav_bytes = f.read()
        except OSError:
            self.showError_("That recording is no longer available.")
            return
        self.stopPlayback_(None)
        if text:
            self.text_view.setString_(text)
            self.updateCharCount()
        # Same one-chunk "session" treatment _playHistoryEntry uses — every chunk-aware
        # consumer (skip back/forward, the scrubber) keeps working unmodified. No
        # history.touch_entry equivalent here — Saved has no LRU/eviction concept to protect.
        self.playback_token = object()
        self.all_chunks = [text or path]
        self.chunk_durations = [None]
        self.chunk_index = 0
        self.next_chunk_audio = None
        self._prefetch_frontier = 0
        self.chunk_audio_cache = {}
        self.chunk_word_timings = {}
        self.avg_chars_per_sec = None
        self.session_text = None
        self._beginChunkPlayback(wav_bytes)

    @objc.python_method
    def _savedCopyText(self, text):
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
        self.setStatus("Text copied.")
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(2.0, False, lambda t: self.setStatus(""))

    @objc.python_method
    def _savedReveal(self, path):
        AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(path)])

    def savedDeleteClicked_(self, sender):
        path = getattr(sender, "_saved_path", None)
        if path is None:
            return
        entry = next((e for e in saved.list_saved(self._saveLocation()) if e["path"] == path), None)
        if entry is None:
            return
        self._savedDeleteRequested(path, entry["text"], entry["filename"])

    @objc.python_method
    def _savedDeleteRequested(self, path, text, filename):
        # Same confirm-before-delete treatment just added to History's own trash icon, applied
        # here from the start rather than needing the same bug report a second time — see
        # _historyDeleteClicked_'s comment for the full reasoning (recoverable via Trash, but
        # the app has to actually SAY so, not just silently do it).
        self._pending_delete_saved_path = path
        cw, ch = 300, 180
        card = self._makeCard(cw, ch)
        title = make_label("Move this recording to the Trash?", 15, 0.92,
                            AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        title.cell().setWraps_(True)
        title.setFrame_(NSMakeRect(20, ch - 56, cw - 40, 36))
        sub = make_label(
            "It leaves this list, but stays recoverable in your Mac's Trash until you empty it.",
            12, 0.5, align=AppKit.NSTextAlignmentCenter)
        sub.cell().setWraps_(True)
        sub.setFrame_(NSMakeRect(20, ch - 100, cw - 40, 40))

        cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
        cancel_btn = text_button("Cancel", NSMakeRect(20, 20, (cw - 52) / 2, 34), "cancelSavedDelete:", self,
                                  cancel_font, 0.08, 0.16, 9.0, white(0.85))
        trash_btn = cta_button("Move to Trash", NSMakeRect(cw / 2 + 6, 20, (cw - 52) / 2, 34),
                                "confirmSavedDeleteClicked:", self)
        trash_btn.layer().setBackgroundColor_(AppKit.NSColor.systemRedColor().colorWithAlphaComponent_(0.85).CGColor())
        trash_attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(11.5, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        trash_btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_("Move to Trash", trash_attrs))

        for s in (title, sub, cancel_btn, trash_btn):
            card.addSubview_(s)
        self._presentOverlay(card)

    def cancelSavedDelete_(self, sender):
        self._pending_delete_saved_path = None
        self.dismissOverlay()

    def confirmSavedDeleteClicked_(self, sender):
        path = self._pending_delete_saved_path
        self._pending_delete_saved_path = None
        self.dismissOverlay()
        if path is not None:
            saved.delete_saved(path)
        self.showRecordingsScreen("saved")

    def _historyRowClicked_(self, sender):
        entry_id = getattr(sender, "_history_entry_id", None)
        # Back to the main screen first so the user actually sees the real transport controls
        # (play/pause icon, scrubber) animate — those live on the main screen, not here.
        self.showMainScreen()
        self._playHistoryEntry(entry_id)

    def _historyDeleteClicked_(self, sender):
        # Used to delete immediately on click, no confirmation, no way for the user to tell
        # from the UI alone that it's actually recoverable (Trash, not gone for good — see
        # history.trash_file) — confirmed via a direct NSFileManager test that the trash
        # operation itself does succeed, but the app never SAID so, which reads identically to
        # a real permanent delete from the user's side. Same confirm-card treatment as
        # Manage Voices' delete and Storage's eviction — the message says explicitly that this
        # one specifically stays recoverable, since that's exactly what was missing.
        entry_id = getattr(sender, "_history_entry_id", None)
        entry = next((e for e in history.list_entries() if e["id"] == entry_id), None)
        if entry is None:
            return
        self._pending_delete_history_id = entry_id

        cw, ch = 300, 180
        card = self._makeCard(cw, ch)
        title = make_label("Move this recording to the Trash?", 15, 0.92,
                            AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        title.cell().setWraps_(True)
        title.setFrame_(NSMakeRect(20, ch - 56, cw - 40, 36))
        sub = make_label(
            "It leaves this list, but stays recoverable in your Mac's Trash until you empty it.",
            12, 0.5, align=AppKit.NSTextAlignmentCenter)
        sub.cell().setWraps_(True)
        sub.setFrame_(NSMakeRect(20, ch - 100, cw - 40, 40))

        cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
        cancel_btn = text_button("Cancel", NSMakeRect(20, 20, (cw - 52) / 2, 34), "cancelHistoryDelete:", self,
                                  cancel_font, 0.08, 0.16, 9.0, white(0.85))
        trash_btn = cta_button("Move to Trash", NSMakeRect(cw / 2 + 6, 20, (cw - 52) / 2, 34),
                                "confirmHistoryDeleteClicked:", self)
        trash_btn.layer().setBackgroundColor_(AppKit.NSColor.systemRedColor().colorWithAlphaComponent_(0.85).CGColor())
        trash_attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(11.5, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        trash_btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_("Move to Trash", trash_attrs))

        for s in (title, sub, cancel_btn, trash_btn):
            card.addSubview_(s)
        self._presentOverlay(card)

    def cancelHistoryDelete_(self, sender):
        self._pending_delete_history_id = None
        self.dismissOverlay()

    def confirmHistoryDeleteClicked_(self, sender):
        entry_id = self._pending_delete_history_id
        self._pending_delete_history_id = None
        self.dismissOverlay()
        if entry_id is not None:
            history.remove_entry(entry_id)  # moved to Trash, not gone for good — see history.trash_file
        self.showRecordingsScreen("recent")  # mutate then fully re-render, same shape as confirmDeleteVoiceClicked_

    @objc.python_method
    def _historySaveClicked(self, entry_id):
        # location is read here, on the main thread, and passed down as a plain arg — not
        # re-read from self.config inside the background worker, matching the existing
        # convention (see audioPlayerDidFinishPlaying_successfully_'s call into
        # _saveSessionToHistoryWorker) for keeping self.config access off background threads.
        location = self._saveLocation()
        threading.Thread(target=self._saveHistoryEntryPermanentlyWorker, args=(entry_id, location), daemon=True).start()

    @objc.python_method
    def _saveHistoryEntryPermanentlyWorker(self, entry_id, location):
        entry = next((e for e in history.list_entries() if e["id"] == entry_id), None)
        if entry is None:
            return
        wav_path = os.path.join(history.CACHE_DIR, entry["audio_file"])
        try:
            with open(wav_path, "rb") as f:
                wav_bytes = f.read()
            saved.save_generation(location, entry["text"], entry["provider"], entry["voice"], wav_bytes)
        except OSError:
            self.performSelectorOnMainThread_withObject_waitUntilDone_("historySaveFailedMain:", "", False)
            return
        self.performSelectorOnMainThread_withObject_waitUntilDone_("historySavedMain:", "", False)

    def historySavedMain_(self, sender):
        self.setStatus("Saved to permanent location.")
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(2.0, False, lambda t: self.setStatus(""))

    def historySaveFailedMain_(self, sender):
        self.showError_("Could not save this recording.")

    @objc.python_method
    def _playHistoryEntry(self, entry_id):
        entry = next((e for e in history.list_entries() if e["id"] == entry_id), None)
        if entry is None:
            return
        path = os.path.join(history.CACHE_DIR, entry["audio_file"])
        try:
            with open(path, "rb") as f:
                wav_bytes = f.read()
        except OSError:
            self.showError_("That recording is no longer available.")
            return
        # Same teardown the Stop button uses — guarantees no leftover chunked-session state
        # from whatever was playing before this.
        self.stopPlayback_(None)
        # Drop the original text back into the box too, not just the audio — same
        # setString_/updateCharCount pairing pasteClicked_ already uses to set text
        # programmatically (setString_ alone doesn't post textDidChange_, so this can't
        # collide with the one-chunk playback session set up just below).
        self.text_view.setString_(entry["text"])
        self.updateCharCount()
        history.touch_entry(entry_id)
        # A cached entry is a single, already-concatenated WAV blob — treat it as a one-chunk
        # "session" so every existing chunk-aware consumer (skip back/forward, the scrubber,
        # the natural-end-of-session check) keeps working unmodified.
        self.playback_token = object()
        self.all_chunks = [entry["text"]]
        self.chunk_durations = [None]
        self.chunk_index = 0
        self.next_chunk_audio = None
        self._prefetch_frontier = 0
        self.chunk_audio_cache = {}
        self.chunk_word_timings = {}
        self.avg_chars_per_sec = None
        # Deliberately None, NOT entry["text"] — audioPlayerDidFinishPlaying_successfully_ only
        # resaves to history when session_text is truthy at the natural end of playback; leaving
        # it set to the entry's real text would silently create a fresh duplicate entry every
        # time this same clip gets replayed to completion.
        self.session_text = None
        self._beginChunkPlayback(wav_bytes)

    # ----- settings -----
    SETTINGS_SECTIONS = [
        ("personalization", "Personalization"), ("storage", "Storage"),
        ("voices", "Voices"), ("location", "File Location"), ("support", "Support"),
    ]
    STORAGE_COUNT_OPTIONS = [5, 10, 20, 50, 100]
    STORAGE_SIZE_OPTIONS = [50 * 1024 * 1024, 100 * 1024 * 1024, 250 * 1024 * 1024, 500 * 1024 * 1024]

    # Word-highlight personalization (Settings > Personalization). Controls how the spoken
    # word gets emphasized during playback — this section builds the config UI only; the live
    # highlighting-during-playback engine itself (System-voice word timing, forced alignment
    # for cloned voices) is separate, larger, not-yet-started work.
    DEFAULT_HIGHLIGHT_COLOR = "#CC5500"  # burnt orange — the original proposal that started this feature
    # Floor below the smallest real word-to-word gap seen in practice (58ms) — guards only
    # against the pathological case of two words landing on the same/near-same captured start
    # (two independent async callback streams feeding word_timings), which would otherwise make
    # the earlier of the two mathematically unselectable, or visible for ~0 seconds, regardless
    # of how the highlight is scheduled. See _requestSystemTTS.
    HIGHLIGHT_MIN_WORD_WINDOW = 0.04
    # Moves each word's highlight moment slightly ahead of its actual captured audio start —
    # a natural reader's eyes move onto the next word before finishing hearing/saying the
    # current one (the well-documented "eye-voice span" in oral-reading research), so a
    # highlight synced exactly to audio onset reads as trailing behind where the eye already
    # wants to be. General reading eye-tracking and saccadic-latency research puts this kind of
    # effect more in the 100-200ms range than 15-30ms (saccadic reaction time alone — the delay
    # between a visual cue and the eye actually beginning to move to it — is consistently
    # measured around 140-240ms); tunable here rather than hard-guessed, since the "right" feel
    # is inherently subjective and worth testing directly against real playback.
    HIGHLIGHT_LEAD_TIME = 0.12
    # NSStrokeWidthAttributeName's magnitude is a percentage of the font's point size, not an
    # absolute stroke thickness — -3.0 (an earlier guess) turned out visually indistinguishable
    # from regular weight at 14pt. Compared side by side against true bold at several
    # magnitudes (rendered to a bitmap and inspected directly): -7.0 is the point where it
    # actually reads as bold and roughly matches true bold's visual weight, without going thick
    # enough to look blotchy.
    HIGHLIGHT_BOLD_STROKE_WIDTH = -7.0
    HIGHLIGHT_COLOR_PRESETS = [
        ("#CC5500", "Burnt Orange"), ("#D4A017", "Amber"), ("#2E8B8B", "Teal"),
        ("#4A90D9", "Sky Blue"), ("#8A63D2", "Violet"), ("#D9538A", "Rose"),
        ("#4CAF80", "Mint"), ("#B0B0B0", "Neutral Gray"),
    ]
    HIGHLIGHT_STYLE_OPTIONS = [
        ("highlight", "Highlight"), ("bold", "Bold"),
        ("underline", "Underline"), ("color", "Text Color"), ("none", "Off"),
    ]
    HIGHLIGHT_SHAPE_OPTIONS = [("rounded", "Rounded"), ("pill", "Pill")]
    HIGHLIGHT_THICKNESS_OPTIONS = [("single", "Single"), ("thick", "Thick")]
    HIGHLIGHT_ANIMATION_OPTIONS = [("snap", "Snap"), ("slide", "Slide")]
    HIGHLIGHT_PREVIEW_PREFIX = "The quick "
    HIGHLIGHT_PREVIEW_WORD = "brown"
    HIGHLIGHT_PREVIEW_SUFFIX = " fox jumps."

    def settingsClicked_(self, sender):
        self.showSettingsScreen("personalization")

    def backToMainFromSettingsClicked_(self, sender):
        self.showMainScreen()

    def settingsSectionClicked_(self, sender):
        self.showSettingsScreen(getattr(sender, "_settings_section", "personalization"))

    @objc.python_method
    def _humanMB(self, num_bytes):
        return f"{num_bytes / (1024 * 1024):.1f} MB"

    @objc.python_method
    def _storageAverageBytesPerEntry(self):
        entries = history.list_entries()
        if not entries:
            return None
        return sum(e["size_bytes"] for e in entries) / len(entries)

    @objc.python_method
    def _storagePreviewText(self):
        avg = self._storageAverageBytesPerEntry()
        mode, value = self._settings_storage_mode, self._settings_storage_value
        if avg is None:
            # Nothing generated yet to estimate from — say so plainly rather than showing a
            # fabricated number, which is exactly the kind of silently-wrong conversion that
            # caused the "will 50MB actually keep 50 recordings?" confusion in the first place.
            return "No recordings yet to estimate size from."
        if mode == "count":
            total = value * avg
            secs = history.estimate_seconds_for_bytes(total)
            return (f"Keeping the last {value} recordings ≈ {self._humanMB(total)}, "
                    f"about {secs / 60:.0f} min of audio (based on your current average size).")
        if value is None:
            return "No size limit — recordings are only capped by how many are kept elsewhere."
        approx_count = int(value / avg) if avg else 0
        return (f"A {self._humanMB(value)} limit keeps roughly {approx_count} recordings at your "
                f"current average size (~{self._humanMB(avg)} each) — not a fixed count.")

    @objc.python_method
    def showSettingsScreen(self, section="personalization"):
        # Full-screen destination, same swap_screen mechanism as History/the main screen — a
        # real, standalone place to browse settings, not something you get dumped into and
        # auto-bounced out of. Reached from the wordmark dropdown/app menu directly, or via
        # History's cog (landing straight on the "storage" section).
        idx = history.load_index()
        # _settings_storage_mode/_value are transient "what the user is currently configuring"
        # state — NOT written to history.py until storageConfirmClicked_ actually applies it.
        # Only (re)initialized from the real stored config the first time this screen is
        # entered in a session; clicking pills just updates this pending state and re-renders,
        # so exploring options never touches real data until Confirm.
        if getattr(self, "_settings_storage_mode", None) is None:
            if idx.get("max_bytes"):
                self._settings_storage_mode = "size"
                self._settings_storage_value = idx.get("max_bytes")
            else:
                self._settings_storage_mode = "count"
                self._settings_storage_value = idx.get("max_entries") or history.DEFAULT_MAX_ENTRIES

        v = AppKit.NSView.alloc().initWithFrame_(self.root.bounds())
        b = v.bounds()
        W, H = b.size.width, b.size.height
        self._screenHeader(v, W, H, "")  # no "Settings" title — the sidebar itself already says where you are

        # x=20 everywhere on this screen (sidebar, divider, list_box on History) — was 12 for
        # the sidebar specifically, a leftover inconsistency with everything else.
        # "Personalization" is the longest label now — measured ~97px, +6px inset each side.
        sidebar_w = 114
        content_y = self.SCREEN_FOOTER_H
        # A smaller top reservation than SCREEN_HEADER_H (54, sized to fit a real title) — with
        # no title here, the sidebar/content only need to clear the wordmark's own top-right
        # corner and a bit of breathing room, not a whole title row's worth of space.
        SETTINGS_HEADER_H = 24.0
        content_h = H - SETTINGS_HEADER_H - self.SCREEN_FOOTER_H
        sidebar = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(20, content_y, sidebar_w, content_h))
        sidebar.setAutoresizingMask_(AppKit.NSViewHeightSizable)
        row_h = 32
        for i, (key, label) in enumerate(self.SETTINGS_SECTIONS):
            # Text-only hover/selected feedback, no background pill/border — same
            # BrightenOnHoverButton treatment as the voice dropdown's own rows, so this reads
            # as one consistent "menu" style across the app instead of a second, different-
            # looking kind of list control.
            row = BrightenOnHoverButton.alloc().initWithFrame_(NSMakeRect(0, content_h - (i + 1) * row_h, sidebar_w, row_h))
            # Anchored to the TOP of sidebar (fixed distance from sidebar's own top edge), not
            # left at its build-time position — sidebar's OWN frame stretches on window resize
            # (HeightSizable above), and without this every row stayed frozen at its original
            # y, which read as "nothing moves" until the window got tall enough to reveal a
            # growing gap above them. Same fix applies throughout this screen and History.
            row.setAutoresizingMask_(AppKit.NSViewMinYMargin)
            sel = key == section
            row_font = AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightSemibold if sel else AppKit.NSFontWeightRegular)
            dim_color = white(0.92 if sel else 0.5)
            row.configureBrighten(label, row_font, dim_color, white(0.85), align=AppKit.NSTextAlignmentLeft,
                                   label_frame=NSMakeRect(6, 0, sidebar_w - 12, row_h))
            row.setTarget_(self)
            row.setAction_("settingsSectionClicked:")
            row._settings_section = key
            sidebar.addSubview_(row)
        v.addSubview_(sidebar)

        # Thin divider instead of bare whitespace — the empty gap alone read as "too much
        # space for no reason"; a deliberate line makes the same (now smaller) gap read as an
        # intentional boundary. Doesn't reach the very top/bottom of the content band: starts
        # a little above the first sidebar row, ends a little above the Back button.
        # Bottom was y=28 — BELOW Back's own top edge (16+34=50), so it ran straight through
        # Back's vertical band instead of stopping above it. content_y (58) sits a real 8pt
        # clear of Back's top; the divider's height is content_h+4 so its top lands a few
        # points above the sidebar's own top row.
        divider_x = 20 + sidebar_w + 8
        divider = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(divider_x, content_y, 1, content_h + 4))
        divider.setAutoresizingMask_(AppKit.NSViewHeightSizable)
        divider.setWantsLayer_(True)
        divider.layer().setBackgroundColor_(white(0.14).CGColor())
        v.addSubview_(divider)

        content_x = divider_x + 8
        content = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(content_x, content_y, W - content_x - 20, content_h))
        content.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        if section == "voices":
            self._buildVoicesSection(content)
        elif section == "personalization":
            self._buildPersonalizationSection(content)
        elif section == "location":
            self._buildLocationSection(content)
        elif section == "support":
            self._buildSupportSection(content)
        else:
            self._buildStorageSection(content)
        v.addSubview_(content)

        self.current_screen = "settings"
        self.swap_screen(v)
        self._installWidthRebuildTrigger(v, lambda: self.showSettingsScreen(section))

    @objc.python_method
    def _hexToColor(self, hex_str):
        h = hex_str.lstrip("#")
        r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
        return AppKit.NSColor.colorWithRed_green_blue_alpha_(r, g, b, 1.0)

    @objc.python_method
    def _buildPersonalizationSection(self, content):
        # Same scroll+reclamp+wrap treatment every other Settings section uses (see
        # _buildStorageSection's comment on why) — this section has the MOST pill rows of any
        # of them (style, optionally shape or thickness, color swatches, animation), so getting
        # the wrap/resize handling right from the start matters even more here than usual.
        cb = content.bounds()
        w = cb.size.width
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(cb)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content.addSubview_(scroll)

        style = self.config.get("highlight_style", "highlight")
        shape = self.config.get("highlight_shape", "pill")
        thickness = self.config.get("highlight_underline_thickness", "single")
        animation = self.config.get("highlight_animation", "slide")
        color_hex = self.config.get("highlight_color", self.DEFAULT_HIGHLIGHT_COLOR)
        # Color applies to every style except "none" (nothing to color) — highlight uses it as
        # the background, bold/underline/color all use it as the word's own text/underline
        # color (see _buildHighlightPreview). Animation is meaningless with no visual change
        # happening at all, so it's tied to the same "none" gate as color.
        show_shape = style == "highlight"
        show_thickness = style == "underline"
        show_color = style != "none"
        show_animation = style != "none"

        pill_font = AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
        label_h, PILL_H, LINE_GAP, ROW_GAP = 16.0, 26.0, 6.0, 18.0
        SWATCH_D = 30.0
        PREVIEW_H = 64.0

        def pill_lines_for(options, gap=6):
            items = [(max(32.0, 10.0 + len(label) * 7.0), (key, label)) for key, label in options]
            lines = self._wrapIntoLines(items, gap, w)
            rows_h = len(lines) * PILL_H + (len(lines) - 1) * LINE_GAP
            return lines, rows_h

        # Every row is a (label_text, lines, item_h, line_gap, render_kind, selected/extra) tuple,
        # built up in top-to-bottom display order, then laid out generically below in one pass —
        # replaces hand-rolling each row's cursor math individually, which stopped scaling once
        # Color/Animation also became conditional (on top of Shape/Thickness already being so).
        rows = [("Style", *pill_lines_for(self.HIGHLIGHT_STYLE_OPTIONS), PILL_H, LINE_GAP, "pills", style,
                 "personalizationStyleClicked:")]
        if show_shape:
            rows.append(("Shape", *pill_lines_for(self.HIGHLIGHT_SHAPE_OPTIONS), PILL_H, LINE_GAP, "pills", shape,
                         "personalizationShapeClicked:"))
        if show_thickness:
            rows.append(("Underline Thickness", *pill_lines_for(self.HIGHLIGHT_THICKNESS_OPTIONS), PILL_H, LINE_GAP,
                         "pills", thickness, "personalizationThicknessClicked:"))
        if show_color:
            swatch_items = [(SWATCH_D, (hexv, label)) for hexv, label in self.HIGHLIGHT_COLOR_PRESETS]
            swatch_lines = self._wrapIntoLines(swatch_items, 8, w)
            swatch_rows_h = len(swatch_lines) * SWATCH_D + (len(swatch_lines) - 1) * 8.0
            rows.append(("Color", swatch_lines, swatch_rows_h, SWATCH_D, 8.0, "swatches", color_hex, None))
        if show_animation:
            rows.append(("Word-to-Word Animation", *pill_lines_for(self.HIGHLIGHT_ANIMATION_OPTIONS), PILL_H, LINE_GAP,
                         "pills", animation, "personalizationAnimationClicked:"))

        NATURAL_H = 4 + PREVIEW_H
        for label_text, lines, rows_h, item_h, gap, kind, selected, action in rows:
            NATURAL_H += ROW_GAP + label_h + 8 + rows_h
        NATURAL_H += 4
        container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, NATURAL_H))

        cursor = NATURAL_H - 4
        cursor -= PREVIEW_H
        preview_y = cursor

        positions = []
        for label_text, lines, rows_h, item_h, gap, kind, selected, action in rows:
            cursor -= ROW_GAP
            cursor -= label_h
            label_y = cursor
            cursor -= 8
            first_line_y = cursor - item_h
            cursor -= rows_h
            positions.append((label_text, lines, item_h, gap, kind, selected, action, label_y, first_line_y))

        # ----- preview -----
        preview_bg = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, preview_y, w, PREVIEW_H))
        preview_bg.setWantsLayer_(True)
        preview_bg.layer().setBackgroundColor_(white(0.045).CGColor())
        preview_bg.layer().setBorderColor_(white(0.1).CGColor())
        preview_bg.layer().setBorderWidth_(1.0)
        preview_bg.layer().setCornerRadius_(10.0)
        container.addSubview_(preview_bg)
        self._buildHighlightPreview(preview_bg, style, shape, thickness, self._hexToColor(color_hex))

        # ----- rows -----
        for label_text, lines, item_h, gap, kind, selected, action, label_y, first_line_y in positions:
            label = make_label(label_text, 12, 0.55)
            label.setFrame_(NSMakeRect(0, label_y, 220, label_h))
            container.addSubview_(label)
            if kind == "pills":
                self._addPillLines(container, lines, first_line_y, item_h, gap, pill_font, selected, action)
            else:
                self._addColorSwatches(container, lines, first_line_y, item_h, gap, selected)

        scroll.setDocumentView_(container)
        clip = scroll.contentView()
        clip.scrollToPoint_(NSMakePoint(0, max(0.0, NATURAL_H - cb.size.height)))
        scroll.reflectScrolledClipView_(clip)
        self._installScrollReclamp(scroll, container, NATURAL_H)

    @objc.python_method
    def _addColorSwatches(self, container, lines, first_line_y, sw_d, gap, color_hex):
        pill_font = AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
        line_y = first_line_y
        for line in lines:
            px = 0.0
            for _, (hexv, label) in line:
                swatch = text_button("", NSMakeRect(px, line_y, sw_d, sw_d), "personalizationColorClicked:", self,
                                      pill_font, 0.0, 0.0, sw_d / 2.0, white(0.0))
                swatch.layer().setBackgroundColor_(self._hexToColor(hexv).CGColor())
                sel = hexv.upper() == color_hex.upper()
                swatch.layer().setBorderWidth_(2.0 if sel else 1.0)
                swatch.layer().setBorderColor_((white(0.95) if sel else white(0.2)).CGColor())
                swatch._base_alpha = None
                swatch._fill = lambda *a, **k: None  # plain color swatch — no separate hover fill on top of it
                swatch._highlight_color_hex = hexv
                swatch.setToolTip_(label)
                container.addSubview_(swatch)
                px += sw_d + gap
            line_y -= (sw_d + gap)

    @objc.python_method
    def _addPillLines(self, container, lines, first_line_y, pill_h, line_gap, font, selected_key, action):
        line_y = first_line_y
        for line in lines:
            px = 0.0
            for btn_w, (key, label_text) in line:
                btn = text_button(label_text, NSMakeRect(px, line_y, btn_w, pill_h), action, self,
                                   font, 0.04, 0.14, 9.0, white(0.55))
                btn.layer().setBorderWidth_(1.0)
                sel = key == selected_key
                btn.layer().setBackgroundColor_(white(0.16 if sel else 0.04).CGColor())
                btn.layer().setBorderColor_(white(0.3 if sel else 0.1).CGColor())
                attrs = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: white(0.95 if sel else 0.55)}
                btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(label_text, attrs))
                btn._base_alpha = 0.16 if sel else 0.04
                btn._pill_key = key
                container.addSubview_(btn)
                px += btn_w + 8
            line_y -= (pill_h + line_gap)

    @objc.python_method
    def _buildHighlightPreview(self, preview_bg, style, shape, thickness, nscolor):
        # Built as a single real NSTextView measured through NSLayoutManager — NOT three
        # separately-positioned NSTextField labels sized via NSAttributedString.size(), which
        # is what this used to be. That approximate approach kept drifting out of sync with
        # what real playback (_glyphRectForRange, also NSLayoutManager-based) actually renders,
        # no matter how the padding constants were tuned — confirmed directly: two rounds of
        # numeric tuning left the preview and live view visibly different every time. Sharing
        # the exact same measurement mechanism as the live highlight is what actually fixes it.
        pw = preview_bg.bounds().size.width
        ph = preview_bg.bounds().size.height
        font = AppKit.NSFont.systemFontOfSize_(14)
        sentence = self.HIGHLIGHT_PREVIEW_PREFIX + self.HIGHLIGHT_PREVIEW_WORD + self.HIGHLIGHT_PREVIEW_SUFFIX
        word_loc = len(self.HIGHLIGHT_PREVIEW_PREFIX)
        word_len = len(self.HIGHLIGHT_PREVIEW_WORD)
        word_range = AppKit.NSMakeRange(word_loc, word_len)

        tv = AppKit.NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, pw - 32, 22))
        tv.setString_(sentence)
        tv.setFont_(font)
        tv.setTextColor_(white(0.7))
        tv.setEditable_(False)
        tv.setSelectable_(False)
        tv.setDrawsBackground_(False)
        tv.setVerticallyResizable_(False)
        tv.setHorizontallyResizable_(False)
        tv.setTextContainerInset_(NSMakeSize(0, 0))
        tv.textContainer().setLineFragmentPadding_(0)
        tv.setWantsLayer_(True)

        storage = tv.textStorage()
        storage.beginEditing()
        # Every style except "highlight" (background does the coloring instead — the word's
        # own text stays a neutral white, same as a real highlighter marks text without
        # recoloring it) and "none" (nothing to color) applies the chosen color to the word's
        # text directly, not just "Text Color" specifically.
        storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, white(0.92), word_range)
        if style == "bold":
            # Faux bold (negative stroke width on the REGULAR font), not a true bold font — see
            # the matching comment in _applyTextStyleHighlight — keeps this preview's word the
            # same width real playback renders it at, instead of a wider true-bold substitute.
            storage.addAttribute_value_range_(
                AppKit.NSStrokeWidthAttributeName, self.HIGHLIGHT_BOLD_STROKE_WIDTH, word_range)
            storage.addAttribute_value_range_(AppKit.NSStrokeColorAttributeName, nscolor, word_range)
            storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, nscolor, word_range)
        elif style == "underline":
            underline_style = AppKit.NSUnderlineStyleThick if thickness == "thick" else AppKit.NSUnderlineStyleSingle
            storage.addAttribute_value_range_(AppKit.NSUnderlineStyleAttributeName, underline_style, word_range)
            storage.addAttribute_value_range_(AppKit.NSUnderlineColorAttributeName, nscolor, word_range)
            storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, nscolor, word_range)
        elif style == "color":
            storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, nscolor, word_range)
        storage.endEditing()

        lm = tv.layoutManager()
        tc = tv.textContainer()
        lm.ensureLayoutForTextContainer_(tc)
        used_rect = lm.usedRectForTextContainer_(tc)
        tv.setFrame_(NSMakeRect(max(16.0, (pw - used_rect.size.width) / 2.0),
                                 (ph - used_rect.size.height) / 2.0,
                                 used_rect.size.width, used_rect.size.height))

        if style == "highlight":
            glyph_range, _ = lm.glyphRangeForCharacterRange_actualCharacterRange_(word_range, None)
            word_rect = lm.boundingRectForGlyphRange_inTextContainer_(glyph_range, tc)
            # Same pad_h/pad_v as the real live-playback pill (_applyWordHighlight) — sharing
            # the constant, not just the same NUMBER independently chosen twice, is the point.
            pad_h, pad_v = 2.0, 2.0
            hl = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(
                tv.frame().origin.x + word_rect.origin.x - pad_h,
                tv.frame().origin.y + word_rect.origin.y - pad_v,
                word_rect.size.width + pad_h * 2, word_rect.size.height + pad_v * 2))
            hl.setWantsLayer_(True)
            hl.layer().setBackgroundColor_(nscolor.colorWithAlphaComponent_(0.85).CGColor())
            hl.layer().setCornerRadius_(4.0 if shape == "rounded" else hl.frame().size.height / 2.0)
            # Added BEFORE tv, not as a sublayer of tv's own layer — same reasoning as
            # _ensureHighlightOverlay: a layer-backed view's sublayers always render on top of
            # its own drawn content, so the pill would cover the glyphs instead of sitting
            # behind them. These are separate SIBLING views under preview_bg, not sublayers of
            # the same layer, so ordinary AppKit z-order (added-first = behind) is sufficient.
            preview_bg.addSubview_(hl)

        preview_bg.addSubview_(tv)

    def personalizationStyleClicked_(self, sender):
        self.config["highlight_style"] = getattr(sender, "_pill_key", "highlight")
        save_config(self.config)
        self.showSettingsScreen("personalization")

    def personalizationShapeClicked_(self, sender):
        self.config["highlight_shape"] = getattr(sender, "_pill_key", "pill")
        save_config(self.config)
        self.showSettingsScreen("personalization")

    def personalizationThicknessClicked_(self, sender):
        self.config["highlight_underline_thickness"] = getattr(sender, "_pill_key", "single")
        save_config(self.config)
        self.showSettingsScreen("personalization")

    def personalizationColorClicked_(self, sender):
        hexv = getattr(sender, "_highlight_color_hex", None)
        if hexv:
            self.config["highlight_color"] = hexv
            save_config(self.config)
        self.showSettingsScreen("personalization")

    def personalizationAnimationClicked_(self, sender):
        self.config["highlight_animation"] = getattr(sender, "_pill_key", "slide")
        save_config(self.config)
        self.showSettingsScreen("personalization")

    @objc.python_method
    def _buildStorageSection(self, content):
        cb = content.bounds()
        w = cb.size.width

        # Wrapped in its own scroll view instead of positioning everything straight into
        # content — at default/small window sizes this section's controls simply didn't fit
        # and had nowhere to go. The scroll view stretches with the window; container's HEIGHT
        # is whatever this section actually needs at the CURRENT width (recomputed fresh every
        # build, including width-change rebuilds — see _installWidthRebuildTrigger).
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(cb)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content.addSubview_(scroll)

        # Pill rows wrap onto additional lines instead of clipping or requiring horizontal
        # scroll when they don't fit the available width (horizontal scroll was tried and
        # explicitly rejected — wrapping is what a real user actually wants here). Computed
        # fresh at the CURRENT width every time this section is built, via the same
        # _wrapIntoLines helper — a real flow-wrap, not a guess.
        mode_font = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)
        mode_items = [(20.0 + len(label) * 6.6, (key, label)) for key, label in
                      (("count", "Number of recordings"), ("size", "Total file size"))]
        mode_lines = self._wrapIntoLines(mode_items, 8, w)

        pill_font = AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
        if self._settings_storage_mode == "count":
            value_options = [(n, str(n)) for n in self.STORAGE_COUNT_OPTIONS]
        else:
            value_options = [(n, self._humanMB(n)) for n in self.STORAGE_SIZE_OPTIONS]
        value_items = [(max(32.0, 10.0 + len(text) * 7.0), (value, text)) for value, text in value_options]
        value_lines = self._wrapIntoLines(value_items, 6, w)

        MODE_BTN_H, VALUE_BTN_H, LINE_GAP = 30.0, 26.0, 6.0
        mode_rows_h = len(mode_lines) * MODE_BTN_H + (len(mode_lines) - 1) * LINE_GAP
        value_rows_h = len(value_lines) * VALUE_BTN_H + (len(value_lines) - 1) * LINE_GAP

        # Built bottom-up from a single cursor with explicit gaps (same idiom used throughout
        # this file, e.g. _showRecordingCaptureCard), now accounting for however many lines
        # each pill row actually needs at this width instead of assuming exactly one.
        mode_label_h, value_label_h, preview_h, confirm_h = 20.0, 18.0, 50.0, 34.0
        NATURAL_H = (4 + mode_label_h + 8 + mode_rows_h + 20 + value_label_h + 8 + value_rows_h
                     + 20 + preview_h + 16 + confirm_h + 4)
        container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, NATURAL_H))

        cursor = NATURAL_H - 4
        cursor -= mode_label_h
        mode_label_y = cursor
        cursor -= 8
        first_mode_line_y = cursor - MODE_BTN_H
        cursor -= mode_rows_h
        cursor -= 20
        cursor -= value_label_h
        value_label_y = cursor
        cursor -= 8
        first_value_line_y = cursor - VALUE_BTN_H
        cursor -= value_rows_h
        cursor -= 20
        cursor -= preview_h
        preview_y = cursor
        cursor -= 16
        cursor -= confirm_h
        confirm_y = cursor

        mode_label = make_label("Manage the cache by", 12, 0.55)
        mode_label.setFrame_(NSMakeRect(0, mode_label_y, 260, mode_label_h))
        container.addSubview_(mode_label)

        line_y = first_mode_line_y
        for line in mode_lines:
            px = 0.0
            for btn_w, (key, label_text) in line:
                btn = text_button(label_text, NSMakeRect(px, line_y, btn_w, MODE_BTN_H), "storageModeClicked:", self,
                                   mode_font, 0.04, 0.14, 9.0, white(0.55))
                btn.layer().setBorderWidth_(1.0)
                sel = key == self._settings_storage_mode
                btn.layer().setBackgroundColor_(white(0.16 if sel else 0.04).CGColor())
                btn.layer().setBorderColor_(white(0.3 if sel else 0.1).CGColor())
                attrs = {AppKit.NSFontAttributeName: mode_font, AppKit.NSForegroundColorAttributeName: white(0.95 if sel else 0.55)}
                btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(label_text, attrs))
                btn._base_alpha = 0.16 if sel else 0.04
                btn._mode_key = key
                container.addSubview_(btn)
                px += btn_w + 8
            line_y -= (MODE_BTN_H + LINE_GAP)

        # Two independent caps used to be able to silently override each other (a size cap
        # smaller than what a count cap implied would win with zero explanation) — mode is now
        # exclusive, and applying one always clears the other (see _applyStorageLimit), so this
        # value row only ever needs to show pills for whichever mode is currently selected.
        value_label = make_label(
            "Keep last" if self._settings_storage_mode == "count" else "Limit total size to", 12, 0.55)
        value_label.setFrame_(NSMakeRect(0, value_label_y, 260, value_label_h))
        container.addSubview_(value_label)

        action = "storageValueClicked:"
        line_y = first_value_line_y
        for line in value_lines:
            px = 0.0
            for btn_w, (value, text) in line:
                btn = text_button(text, NSMakeRect(px, line_y, btn_w, VALUE_BTN_H), action, self,
                                   pill_font, 0.04, 0.14, 8.0, white(0.55))
                btn.layer().setBorderWidth_(1.0)
                sel = value == self._settings_storage_value
                btn.layer().setBackgroundColor_(white(0.16 if sel else 0.04).CGColor())
                btn.layer().setBorderColor_(white(0.3 if sel else 0.1).CGColor())
                attrs = {AppKit.NSFontAttributeName: pill_font, AppKit.NSForegroundColorAttributeName: white(0.95 if sel else 0.55)}
                btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs))
                btn._base_alpha = 0.16 if sel else 0.04
                btn._limit_value = value
                container.addSubview_(btn)
                px += btn_w + 6
            line_y -= (VALUE_BTN_H + LINE_GAP)

        preview = make_label(self._storagePreviewText(), 12, 0.6)
        preview.cell().setWraps_(True)
        preview.setFrame_(NSMakeRect(0, preview_y, w, preview_h))
        container.addSubview_(preview)

        # Same treatment as Back/Continue — pill base color, hover dialed back to 0.10 to
        # compensate for this being a much larger filled area than a pill.
        confirm_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold)
        confirm_btn = text_button("Confirm", NSMakeRect(0, confirm_y, 110, confirm_h), "storageConfirmClicked:", self,
                                   confirm_font, 0.04, 0.10, 9.0, white(0.95))
        confirm_btn.layer().setBorderWidth_(1.0)
        confirm_btn.layer().setBorderColor_(white(0.1).CGColor())
        container.addSubview_(confirm_btn)

        scroll.setDocumentView_(container)
        clip = scroll.contentView()
        clip.scrollToPoint_(NSMakePoint(0, max(0.0, NATURAL_H - cb.size.height)))
        scroll.reflectScrolledClipView_(clip)
        # No natural_w here anymore — wrapping means container's width should just always
        # track the viewport directly (nothing left-aligned inside it needs a wider floor the
        # way un-wrapped pills used to), which is also exactly what a width-change rebuild
        # produces at NATURAL_H's new value on its own.
        self._installScrollReclamp(scroll, container, NATURAL_H)

    def storageModeClicked_(self, sender):
        mode = getattr(sender, "_mode_key", "count")
        if mode != self._settings_storage_mode:
            self._settings_storage_mode = mode
            idx = history.load_index()
            if mode == "size":
                self._settings_storage_value = idx.get("max_bytes") or self.STORAGE_SIZE_OPTIONS[0]
            else:
                self._settings_storage_value = idx.get("max_entries") or history.DEFAULT_MAX_ENTRIES
        self.showSettingsScreen("storage")

    def storageValueClicked_(self, sender):
        self._settings_storage_value = getattr(sender, "_limit_value", None)
        self.showSettingsScreen("storage")

    def storageConfirmClicked_(self, sender):
        mode, value = self._settings_storage_mode, self._settings_storage_value
        evict = history.preview_eviction(
            max_entries=(value if mode == "count" else 10 ** 9),
            max_bytes=(value if mode == "size" else None))
        if evict > 0:
            self._showStorageConfirmDeleteCard(mode, value, evict)
        else:
            self._applyStorageLimit(mode, value)

    @objc.python_method
    def _applyStorageLimit(self, mode, value):
        # Mode is mutually exclusive at the STORED-DATA level, not just in the UI — applying
        # one always explicitly resets the other, so they can never both silently constrain
        # the cache at once the way the two-independent-caps version used to.
        if mode == "count":
            history.set_limits(max_entries=value, max_bytes=None)
        else:
            history.set_limits(max_entries=10 ** 9, max_bytes=value)
        self.showSettingsScreen("storage")
        self.setStatus("Storage setting applied.")
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            2.0, False, lambda t: self.setStatus(""))

    @objc.python_method
    def _showStorageConfirmDeleteCard(self, mode, value, evict_count):
        self._pending_storage_mode = mode
        self._pending_storage_value = value
        plural = "recording" if evict_count == 1 else "recordings"

        cw, ch = 300, 170
        card = self._makeCard(cw, ch)
        title = make_label(f"Move {evict_count} {plural} to the Trash?", 15, 0.92,
                            AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        title.setFrame_(NSMakeRect(0, ch - 40, cw, 20))
        sub = make_label(
            "This setting keeps fewer recordings than you currently have cached — the rest go "
            "to the Trash, where you can still recover them.", 12, 0.5, align=AppKit.NSTextAlignmentCenter)
        sub.cell().setWraps_(True)
        sub.setFrame_(NSMakeRect(20, ch - 78, cw - 40, 40))

        cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
        cancel_btn = text_button("Cancel", NSMakeRect(20, 20, (cw - 52) / 2, 34), "cancelStorageConfirm:", self,
                                  cancel_font, 0.08, 0.16, 9.0, white(0.85))
        move_btn = cta_button("Move to Trash", NSMakeRect(cw / 2 + 6, 20, (cw - 52) / 2, 34),
                               "confirmStorageConfirmClicked:", self)
        move_btn.layer().setBackgroundColor_(AppKit.NSColor.systemRedColor().colorWithAlphaComponent_(0.85).CGColor())
        move_attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(11.5, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        move_btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_("Move to Trash", move_attrs))

        for s in (title, sub, cancel_btn, move_btn):
            card.addSubview_(s)
        self._presentOverlay(card)

    def cancelStorageConfirm_(self, sender):
        self._pending_storage_mode = None
        self._pending_storage_value = None
        self.dismissOverlay()

    def confirmStorageConfirmClicked_(self, sender):
        mode, value = self._pending_storage_mode, self._pending_storage_value
        self._pending_storage_mode = None
        self._pending_storage_value = None
        self.dismissOverlay()
        if mode is not None:
            self._applyStorageLimit(mode, value)

    @objc.python_method
    def _saveLocation(self):
        return self.config.get("save_location") or saved.DEFAULT_SAVE_DIR

    @objc.python_method
    def _buildLocationSection(self, content):
        # Same scroll+reclamp treatment as every other Settings section (see _buildStorageSection's
        # comment) — the resize bugs fixed earlier today were never section-specific, so every
        # new section gets this from the start rather than needing its own fix-cycle later.
        cb = content.bounds()
        w = cb.size.width
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(cb)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content.addSubview_(scroll)

        location = self._saveLocation()
        entries = saved.list_saved(location)
        count = len(entries)
        total_bytes = sum(e["size_bytes"] for e in entries)
        is_default = (location == saved.DEFAULT_SAVE_DIR)

        # Content width here is much narrower than the full window (sidebar + divider eat a
        # fixed ~150pt), and this row has up to 3 buttons — same overflow risk the Storage
        # section's pill rows had, so it gets the same real flow-wrap fix instead of assuming
        # a fixed row width like the button row's first (buggy) draft did.
        # "View Saved Recordings" used to live here — now redundant, since Saved is a tab on
        # the Recordings screen itself (one click from the wordmark), not buried in Settings.
        btn_font = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)
        btn_specs = [("Choose Folder…", "locationChooseFolderClicked:"), ("Reveal in Finder", "locationRevealClicked:")]
        if not is_default:
            btn_specs.append(("Reset to Default", "locationResetClicked:"))
        btn_items = [(20.0 + len(label) * 6.6, (label, action)) for label, action in btn_specs]
        btn_lines = self._wrapIntoLines(btn_items, 8, w)

        BTN_H, LINE_GAP = 30.0, 8.0
        btn_rows_h = len(btn_lines) * BTN_H + (len(btn_lines) - 1) * LINE_GAP
        title_h, desc_h, path_h, stats_h = 20.0, 34.0, 40.0, 18.0
        NATURAL_H = (4 + title_h + 8 + desc_h + 16 + path_h + 10 + btn_rows_h + 16 + stats_h + 4)
        container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, NATURAL_H))

        cursor = NATURAL_H - 4
        cursor -= title_h
        title_y = cursor
        cursor -= 8
        cursor -= desc_h
        desc_y = cursor
        cursor -= 16
        cursor -= path_h
        path_y = cursor
        cursor -= 10
        first_btn_line_y = cursor - BTN_H
        cursor -= btn_rows_h
        cursor -= 16
        cursor -= stats_h
        stats_y = cursor

        title_label = make_label("File Location", 15, 0.75, AppKit.NSFontWeightSemibold)
        title_label.setFrame_(NSMakeRect(0, title_y, 260, title_h))
        container.addSubview_(title_label)

        desc = make_label(
            "Recordings you explicitly save (not the automatic Recents cache) are written here, "
            "as real files you can find in Finder.", 12.5, 0.5)
        desc.cell().setWraps_(True)
        desc.setFrame_(NSMakeRect(0, desc_y, w, desc_h))
        container.addSubview_(desc)

        path_bg = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, path_y, w, path_h))
        path_bg.setWantsLayer_(True)
        path_bg.layer().setBackgroundColor_(white(0.06).CGColor())
        path_bg.layer().setBorderColor_(white(0.1).CGColor())
        path_bg.layer().setBorderWidth_(1.0)
        path_bg.layer().setCornerRadius_(8.0)
        path_label = make_label(location, 11.5, 0.6)
        path_label.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingMiddle)
        path_label.setFrame_(NSMakeRect(10, 0, w - 20, path_h))
        path_bg.addSubview_(path_label)
        container.addSubview_(path_bg)

        line_y = first_btn_line_y
        for line in btn_lines:
            px = 0.0
            for btn_w, (label, action) in line:
                btn = text_button(label, NSMakeRect(px, line_y, btn_w, BTN_H), action, self,
                                   btn_font, 0.04, 0.14, 9.0, white(0.55))
                btn.layer().setBorderWidth_(1.0)
                btn.layer().setBorderColor_(white(0.1).CGColor())
                container.addSubview_(btn)
                px += btn_w + 8
            line_y -= (BTN_H + LINE_GAP)

        if count == 0:
            stats_text = "No saved recordings yet."
        else:
            plural = "recording" if count == 1 else "recordings"
            stats_text = f"{count} saved {plural} · {self._humanMB(total_bytes)}"
        stats = make_label(stats_text, 12, 0.45)
        stats.setFrame_(NSMakeRect(0, stats_y, w, stats_h))
        container.addSubview_(stats)

        scroll.setDocumentView_(container)
        clip = scroll.contentView()
        clip.scrollToPoint_(NSMakePoint(0, max(0.0, NATURAL_H - cb.size.height)))
        scroll.reflectScrolledClipView_(clip)
        self._installScrollReclamp(scroll, container, NATURAL_H)

    @objc.python_method
    def _buildSupportSection(self, content):
        cb = content.bounds()
        w = cb.size.width
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(cb)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content.addSubview_(scroll)

        title_h, desc_h, btn_h = 20.0, 50.0, 30.0
        NATURAL_H = 4 + title_h + 8 + desc_h + 16 + btn_h + 4
        container = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, NATURAL_H))

        cursor = NATURAL_H - 4
        cursor -= title_h
        title_y = cursor
        cursor -= 8
        cursor -= desc_h
        desc_y = cursor
        cursor -= 16
        btn_y = cursor - btn_h

        title_label = make_label("Support", 15, 0.75, AppKit.NSFontWeightSemibold)
        title_label.setFrame_(NSMakeRect(0, title_y, 260, title_h))
        container.addSubview_(title_label)

        desc = make_label(
            "Running into a problem? Send a diagnostic report — this session's provider/voice "
            "usage and timing, your answers below, and basic system info. You'll see exactly "
            "what's being sent before it goes anywhere.", 12.5, 0.5)
        desc.cell().setWraps_(True)
        desc.setFrame_(NSMakeRect(0, desc_y, w, desc_h))
        container.addSubview_(desc)

        btn_font = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)
        btn = text_button("Report a Problem…", NSMakeRect(0, btn_y, 170, btn_h),
                           "reportProblemClicked:", self, btn_font, 0.04, 0.14, 9.0, white(0.55))
        btn.layer().setBorderWidth_(1.0)
        btn.layer().setBorderColor_(white(0.1).CGColor())
        container.addSubview_(btn)

        scroll.setDocumentView_(container)
        clip = scroll.contentView()
        clip.scrollToPoint_(NSMakePoint(0, max(0.0, NATURAL_H - cb.size.height)))
        scroll.reflectScrolledClipView_(clip)
        self._installScrollReclamp(scroll, container, NATURAL_H)

    def locationChooseFolderClicked_(self, sender):
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setCanCreateDirectories_(True)
        panel.setPrompt_("Choose")
        current = self._saveLocation()
        if os.path.isdir(current):
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(current))
        if panel.runModal() == AppKit.NSModalResponseOK:
            urls = panel.URLs()
            if urls:
                self.config["save_location"] = urls[0].path()
                save_config(self.config)
                self.showSettingsScreen("location")

    def locationRevealClicked_(self, sender):
        location = self._saveLocation()
        saved.ensure_dir(location)
        AppKit.NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(None, location)

    def locationResetClicked_(self, sender):
        self.config.pop("save_location", None)
        save_config(self.config)
        self.showSettingsScreen("location")

    def _ttsWorkerLoop(self):
        # Runs forever on the ONE persistent thread MLX ever sees — see the comment where this
        # is started in applicationDidFinishLaunching_. Must never actually exit or die: an
        # uncaught exception here would silently kill generation for the rest of the app's
        # life with no error shown, a far worse failure mode than today's crash, so this
        # catches everything rather than letting anything propagate out of the loop.
        while True:
            job = self._tts_job_queue.get()
            try:
                if callable(job):
                    # A zero-arg job (e.g. warming the model at launch — see
                    # applicationDidFinishLaunching_) rather than a chunk-generation tuple.
                    # Must go through this same queue/thread, not a separate one: MLX's
                    # per-thread stream identity is exactly what the persistent-worker fix
                    # above exists to keep singular, and loading the model touches MLX just
                    # as much as generating with it does.
                    job()
                else:
                    text, token, role, index, offset, should_play = job
                    # A superseded job (Stop, or a new Play/seek issued before this one was
                    # even pulled off the queue) is cheap to skip before spending real
                    # generation time on audio nobody will ever see — chunkResultMain_ still
                    # re-checks the token itself once a result comes back, so this is a pure
                    # efficiency win, not a correctness requirement.
                    if token is self.playback_token:
                        self._chunkWorker(text, token, role, index, offset, should_play)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
            finally:
                self._tts_job_queue.task_done()

    @objc.python_method
    def _logReportEntry(self, text, started_at, error):
        # Deliberately no raw text, only its length — see bug_report.py's own header for why.
        self.session_report_log.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "provider": self.config.get("provider", "?"),
            "voice": self.config.get("voice_id", "?"),
            "chars": len(text),
            "duration": time.monotonic() - started_at,
            "error": error,
        })
        # A long session (a whole book chapter) could otherwise grow this without bound —
        # nothing needs more than the last couple hours of context to debug a single report.
        del self.session_report_log[:-200]

    @objc.python_method
    def _chunkWorker(self, text, token, role, index, offset, should_play=True):
        started_at = time.monotonic()
        try:
            audio = self._requestTTS(text)
            # Set only for System voice (see _requestSystemTTS); None for every other
            # provider. Read immediately and cleared right away so a later non-System chunk
            # can never accidentally inherit a previous System chunk's stale timings.
            word_timings = self._last_word_timings
            self._last_word_timings = None
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": audio,
                      "word_timings": word_timings, "should_play": should_play, "error": None}
            self._logReportEntry(text, started_at, None)
        except urllib.error.HTTPError as e:
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": None,
                      "should_play": should_play, "error": f"TTS request failed (HTTP {e.code})."}
            self._logReportEntry(text, started_at, result["error"])
        except urllib.error.URLError as e:
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": None,
                      "should_play": should_play, "error": f"Could not reach provider: {e.reason}"}
            self._logReportEntry(text, started_at, result["error"])
        except Exception as e:
            # Covers the System voice path (AVSpeechSynthesizer, no urllib involved) — without
            # this, an unexpected failure there would just kill the background thread silently,
            # leaving the UI stuck showing "Generating..." forever with no way out but Stop.
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": None,
                      "should_play": should_play, "error": f"Couldn't generate speech: {e}"}
            self._logReportEntry(text, started_at, result["error"])
        self.performSelectorOnMainThread_withObject_waitUntilDone_("chunkResultMain:", result, False)

    def chunkResultMain_(self, result):
        # A Stop (or a new Play/seek started before this background request returned)
        # invalidates the token this result was generated under — drop it rather than let a
        # stale chunk from a previous session clobber whatever's happening now.
        if result["token"] is not self.playback_token:
            return
        if result["error"]:
            # We can't continue the read reliably without this chunk — surface the error and
            # end the session, rather than skip it (silently dropping part of what's being
            # read) or crash later on missing audio. If something is actively playing right
            # now (this was a background prefetch failure), don't clear self.player here —
            # AVAudioPlayer isn't guaranteed to keep playing once we drop the only reference
            # to it, so that would risk cutting the current chunk off mid-sentence. Instead,
            # just stop the pipeline from advancing past it; audioPlayerDidFinishPlaying_
            # successfully_ tears the session down once the current chunk finishes on its own.
            if self.player is not None and self.player.isPlaying():
                self.all_chunks = self.all_chunks[:self.chunk_index + 1]
                self.next_chunk_audio = None
                self.waiting_for_next = False
                self.playback_token = None
            else:
                self._resetPlaybackState()
                self._stopProgressTimer()
                self._syncPlaybackUI()
            self.showError_(result["error"])
            return
        # Stored here (once, regardless of which branch below handles playback) rather than
        # threaded through _beginChunkPlayback as an extra parameter — a chunk's word timing is
        # a property of the chunk itself, same as chunk_audio_cache, and every branch below
        # already keys off result["index"]/self.chunk_index into that same dict.
        self.chunk_word_timings[result["index"]] = result.get("word_timings")
        if result["role"] == "seek":
            self.chunk_index = result["index"]
            self._beginChunkPlayback(
                result["audio"], start_offset=result["offset"], should_play=result.get("should_play", True))
        elif self.waiting_for_next:
            self.waiting_for_next = False
            self._beginChunkPlayback(result["audio"])
        else:
            self.chunk_audio_cache[result["index"]] = result["audio"]
            # next_chunk_audio must only ever hold the IMMEDIATE next chunk specifically —
            # audioPlayerDidFinishPlaying_successfully_ hands it straight to _beginChunkPlayback
            # without checking its index. With deeper lookahead (see PREFETCH_LOOKAHEAD_CHUNKS),
            # a result landing here can now be chunk_index+2 or +3, not just +1 — setting this
            # unconditionally would silently overwrite it with the wrong chunk's audio.
            if result["index"] == self.chunk_index + 1:
                self.next_chunk_audio = result["audio"]
            # Keep the chain going — a chunk finishing generation is exactly the moment the
            # worker thread would otherwise sit idle until the next chunk starts playing.
            self._extendPrefetchFrontier()

    @objc.python_method
    def _padWithTrailingSilence(self, audio_bytes, pad_ms=300):
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as w:
                nchannels, sampwidth, framerate = w.getnchannels(), w.getsampwidth(), w.getframerate()
                frames = w.readframes(w.getnframes())
        except (wave.Error, EOFError):
            return audio_bytes
        silence_frames = int(framerate * pad_ms / 1000)
        silence = b"\x00" * (silence_frames * nchannels * sampwidth)
        buf = io.BytesIO()
        out = wave.open(buf, "wb")
        out.setnchannels(nchannels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        out.writeframes(frames + silence)
        out.close()
        return buf.getvalue()

    @objc.python_method
    def _beginChunkPlayback(self, audio_bytes, start_offset=0.0, should_play=True):
        # AVAudioPlayer has been observed clipping the very last ~100-200ms of short clips —
        # a buffer/output-flush timing quirk in CoreAudio itself, confirmed NOT a generation
        # bug (Whisper word-timestamps showed the saved WAV's actual audio content is already
        # complete). Padding trailing silence onto the buffer we hand the player means any tail
        # clipping eats silence instead of the last syllable. Only the player's copy is padded —
        # chunk_audio_cache below keeps the original, unpadded bytes so Save/History content
        # stays exact.
        playback_bytes = self._padWithTrailingSilence(audio_bytes)
        player, err = AVFoundation.AVAudioPlayer.alloc().initWithData_error_(bytes(playback_bytes), None)
        if player is None:
            self.showError_("Could not decode the generated audio.")
            self._resetPlaybackState()
            self._syncPlaybackUI()
            return
        self._recordChunkDuration(self.chunk_index, player.duration())
        self.chunk_audio_cache[self.chunk_index] = audio_bytes
        if start_offset > 0.0:
            player.setCurrentTime_(min(start_offset, max(0.0, player.duration() - 0.05)))
        self.player = player
        self.player.setDelegate_(self)
        self.player.setVolume_(max(0.0, min(1.0, self.config.get("volume", 1.0))))
        # should_play=False lands a seek at the new position without resuming — a scrub/skip
        # performed while playback was already stopped/paused shouldn't itself start it (the
        # only callers that ever pass False are the seek paths below; every other caller here —
        # first Play, History/Saved replay, natural chunk-to-chunk advance — always wants to
        # play, hence the True default).
        if should_play:
            self.player.play()
        self.setStatus("")
        self._syncPlaybackUI()
        if should_play:
            self._startProgressTimer()
            # Chunk-level follow as the immediate landing scroll — refined further, word by
            # word, inside _applyWordHighlight below whenever this chunk has usable word
            # timing (see _scrollToKeepPlaybackVisible's own docstring).
            self._scrollToKeepPlaybackVisible(self._approxChunkStartOffset(self.chunk_index))
        self._syncWordHighlightNow()
        if self.config.get("provider", "ElevenLabs") == "ElevenLabs":
            threading.Thread(target=self._fetchElVoicesWorker, daemon=True).start()  # refresh usage
        self._prefetchNextChunk()

    @objc.python_method
    def _prefetchNextChunk(self):
        next_index = self.chunk_index + 1
        if next_index < len(self.all_chunks):
            cached = self.chunk_audio_cache.get(next_index)
            if cached is not None:
                self.next_chunk_audio = cached
        self._extendPrefetchFrontier()

    @objc.python_method
    def _extendPrefetchFrontier(self):
        # Keeps up to PREFETCH_LOOKAHEAD_CHUNKS chunks ahead of the current one queued for
        # generation, not just the immediate next one — see that constant's own comment for
        # why a single-chunk buffer stopped being enough once verification retries could make
        # a chunk take longer to generate than it takes to PLAY. Called both from here (when a
        # chunk starts playing) and from chunkResultMain_ (whenever a prefetch result lands),
        # so the worker thread keeps working ahead instead of sitting idle between those two
        # events — which is exactly the idle time that's now needed as buffer margin.
        # should_play is irrelevant for "prefetch" jobs (chunkResultMain_'s prefetch branch
        # just caches the audio, never calls _beginChunkPlayback) — True only to match the
        # tuple shape every job on this queue is unpacked with.
        target = min(self.chunk_index + PREFETCH_LOOKAHEAD_CHUNKS, len(self.all_chunks) - 1)
        while self._prefetch_frontier < target:
            next_index = self._prefetch_frontier + 1
            if next_index not in self.chunk_audio_cache:
                self._tts_job_queue.put(
                    (self.all_chunks[next_index], self.playback_token, "prefetch", next_index, 0.0, True))
            self._prefetch_frontier = next_index

    @objc.python_method
    def _resetPlaybackState(self):
        self.player = None
        self.playback_token = None
        self.all_chunks = []
        self.chunk_durations = []
        self.avg_chars_per_sec = None
        self.chunk_index = 0
        self.next_chunk_audio = None
        self._prefetch_frontier = 0
        self.chunk_audio_cache = {}
        self.chunk_word_timings = {}
        self.session_text = None
        self.waiting_for_next = False

    @objc.python_method
    def _concatenateSessionAudio(self):
        """Combines every chunk's cached WAV bytes into one WAV blob — only meaningful right
        at the natural end of playback, when every chunk (0..len(all_chunks)-1) is guaranteed
        to already be sitting in chunk_audio_cache from having played through in order."""
        frames = bytearray()
        params = None
        for i in range(len(self.all_chunks)):
            chunk_bytes = self.chunk_audio_cache.get(i)
            if chunk_bytes is None:
                return None
            with wave.open(io.BytesIO(chunk_bytes), "rb") as w:
                if params is None:
                    params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                frames += w.readframes(w.getnframes())
        if params is None:
            return None
        nchannels, sampwidth, framerate = params
        buf = io.BytesIO()
        out = wave.open(buf, "wb")
        out.setnchannels(nchannels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        out.writeframes(bytes(frames))
        out.close()
        return buf.getvalue()

    @objc.python_method
    def _saveSessionToHistoryWorker(self, text, provider, voice, speed, wav_bytes):
        try:
            history.add_entry(text=text, provider=provider, voice=voice, speed=speed, wav_bytes=wav_bytes)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    def audioPlayerDidFinishPlaying_successfully_(self, player, flag):
        if player is not self.player:
            return  # a stale delegate callback from a player Stop already replaced/cleared
        next_index = self.chunk_index + 1
        if next_index >= len(self.all_chunks):
            # Reached the actual end of the document — the one trigger (besides an explicit
            # Save, not built yet) for entering the visible "recent generations" cache. A
            # take that gets stopped partway through (to tweak voice/speed/text) never gets
            # here, so it never surfaces — exactly the hidden-vs-visible split from the
            # design discussion. Combining+writing happens off the main thread since a long
            # chapter's audio is a real amount of bytes to concatenate and write to disk.
            if self.session_text:
                combined = self._concatenateSessionAudio()
                if combined is not None:
                    threading.Thread(
                        target=self._saveSessionToHistoryWorker,
                        args=(self.session_text, self.config.get("provider", ""),
                              self.config.get("voice_id", ""), self.config.get("speed", ""), combined),
                        daemon=True,
                    ).start()
            # Deliberately NOT _resetPlaybackState()
            # here — that would also wipe all_chunks/chunk_durations/chunk_audio_cache, so
            # pressing Play again would re-chunk and regenerate from scratch instead of just
            # replaying what's already sitting in cache. Only the "currently playing" bits
            # reset; playPauseClicked_ checks session_text to reuse the rest.
            self.player = None
            self.playback_token = None
            self.chunk_index = 0
            self.next_chunk_audio = None
            self.waiting_for_next = False
            self._stopProgressTimer()
            self._resetScrubberUI()
            self._syncPlaybackUI()
            return
        self.chunk_index = next_index
        if self.next_chunk_audio is not None:
            audio = self.next_chunk_audio
            self.next_chunk_audio = None
            self._beginChunkPlayback(audio)
        else:
            # Prefetch hasn't finished yet (slow connection) — chunkResultMain_ will start
            # playback itself the moment it lands, since waiting_for_next is set.
            self.waiting_for_next = True
            self.setStatus("Generating...")
            self._syncPlaybackUI()

    def stopPlayback_(self, sender):
        # showWelcomeScreen calls this unconditionally on every entry, including a true first
        # launch — before the main screen has ever been built once, so there's nothing here
        # yet to stop or reset. Same reasoning as the guard in _syncPlaybackUI/_resetScrubberUI.
        if not hasattr(self, "status_label"):
            return
        if self.player is not None:
            self.player.stop()
        self._resetPlaybackState()
        self._stopProgressTimer()
        self._resetScrubberUI()
        self.setStatus("")
        self._syncPlaybackUI()

    def skipBack_(self, sender):
        if self.player is None or not self.all_chunks:
            return
        current = self._cumulativeDurationBefore(self.chunk_index) + self.player.currentTime()
        self._seekToVirtualTime(current - 15.0, should_play=self.player.isPlaying())

    def skipForward_(self, sender):
        if self.player is None or not self.all_chunks:
            return
        current = self._cumulativeDurationBefore(self.chunk_index) + self.player.currentTime()
        self._seekToVirtualTime(current + 15.0, should_play=self.player.isPlaying())

    # ----- whole-document virtual timeline (scrubber + duration-aware skip) -----
    @objc.python_method
    def _estimateChunkDuration(self, index):
        known = self.chunk_durations[index]
        if known is not None:
            return known
        rate = self.avg_chars_per_sec or 15.0  # ~15 chars/sec is a reasonable blind guess pre-generation
        return len(self.all_chunks[index]) / rate

    @objc.python_method
    def _cumulativeDurationBefore(self, index):
        return sum(self._estimateChunkDuration(i) for i in range(index))

    @objc.python_method
    def _totalEstimatedDuration(self):
        return sum(self._estimateChunkDuration(i) for i in range(len(self.all_chunks)))

    @objc.python_method
    def _recordChunkDuration(self, index, duration):
        # Refines the running speech-rate estimate from every chunk whose REAL duration is
        # now known, so the estimate for not-yet-generated chunks keeps improving as playback
        # progresses (or as scrubbing generates chunks out of order).
        self.chunk_durations[index] = duration
        known_chars = sum(len(self.all_chunks[i]) for i, d in enumerate(self.chunk_durations) if d is not None)
        known_secs = sum(d for d in self.chunk_durations if d is not None)
        if known_secs > 0:
            self.avg_chars_per_sec = known_chars / known_secs

    @objc.python_method
    def _seekToVirtualTime(self, target_seconds, should_play=True):
        if not self.all_chunks:
            return
        # should_play is the CALLER's decision, not auto-detected here — this function is also
        # the shared landing spot for a brand-new Play click and for restarting a just-finished
        # session (playPauseClicked_'s two direct calls below), where self.player is None
        # precisely BECAUSE there's no prior playback to check, not because anything was
        # paused. Auto-detecting "was playing" from self.player.isPlaying() here would read
        # that None as False and silently load the audio without ever starting it — confirmed
        # directly: it turned a first Play click into a no-op, and the very next Pause click
        # (hitting self.player.isPlaying()==False) started it instead of pausing it. Only the
        # actual scrub/skip callers (which require an existing player already) compute "was it
        # playing" themselves and pass it in.
        total = self._totalEstimatedDuration()
        target_seconds = max(0.0, min(target_seconds, max(total - 0.1, 0.0)))
        cumulative = 0.0
        target_index = len(self.all_chunks) - 1
        offset = 0.0
        for i in range(len(self.all_chunks)):
            dur = self._estimateChunkDuration(i)
            if cumulative + dur > target_seconds or i == len(self.all_chunks) - 1:
                target_index = i
                offset = max(0.0, target_seconds - cumulative)
                break
            cumulative += dur

        self.playback_token = object()
        if self.player is not None:
            self.player.stop()
        self.player = None
        self.chunk_index = target_index
        self.next_chunk_audio = None
        self.waiting_for_next = False
        self._stopProgressTimer()

        cached = self.chunk_audio_cache.get(target_index)
        if cached is not None:
            # Already generated this one earlier in the session (either played before, or
            # prefetched-but-skipped-past) — reuse the exact same audio instead of a fresh
            # regeneration, which also means the offset lines up exactly with what was heard
            # before rather than drifting by however much a fresh TTS call's timing differs.
            self._beginChunkPlayback(cached, start_offset=offset, should_play=should_play)
            return

        self.setStatus("Generating...")
        self._tts_job_queue.put(
            (self.all_chunks[target_index], self.playback_token, "seek", target_index, offset, should_play))

    @objc.python_method
    def _startProgressTimer(self):
        self._stopProgressTimer()
        # NSRunLoopCommonModes, not the plain scheduled-timer default mode — same reasoning as
        # the shimmer/pulse animation timers in widgets.py: a default-mode-only timer freezes
        # for the whole duration of a live window-resize drag, so the scrubber/elapsed-time
        # display would visibly stall if the user resizes mid-playback.
        # 0.03s just for smooth scrubber/elapsed-time motion — word-highlight timing is no
        # longer driven by this poll at all (see _scheduleNextWordTimer), so this interval no
        # longer needs to out-run individual word durations.
        self.progress_timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(
            0.03, True, lambda t: self._updateScrubberUI())
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(self.progress_timer, AppKit.NSRunLoopCommonModes)

    @objc.python_method
    def _stopProgressTimer(self):
        # Centralized cancellation point for the word-highlight timer chain too — every real
        # stop/pause/seek/error path in the app already calls this, so hooking it here covers
        # all of them for free instead of duplicating an invalidate call at each call site.
        self._invalidateHighlightTimer()
        if self.progress_timer is not None:
            self.progress_timer.invalidate()
            self.progress_timer = None

    @objc.python_method
    def _updateScrubberUI(self):
        if self.is_scrubbing or self.player is None or not self.all_chunks:
            return
        current = self._cumulativeDurationBefore(self.chunk_index) + self.player.currentTime()
        total = self._totalEstimatedDuration()
        self.scrubber.setFraction((current / total) if total > 0 else 0.0)
        self.elapsed_label.setStringValue_(format_playback_time(current))
        self.remaining_label.setStringValue_(format_playback_time(max(0.0, total - current)))

    @objc.python_method
    def _resetScrubberUI(self):
        # showWelcomeScreen calls stopPlayback_ unconditionally on every entry, including a
        # true first launch — before the main screen (and therefore the scrubber) has ever
        # been built once. Same guard shape as _syncPlaybackUI for the same reason.
        if not hasattr(self, "scrubber"):
            return
        self.scrubber.setFraction(0.0)
        self.elapsed_label.setStringValue_("0:00")
        self.remaining_label.setStringValue_("0:00")
        self._clearWordHighlight()

    # ----- live word-highlight during playback (System voice only — see _requestSystemTTS for
    # how word_timings gets captured; #23 will extend this to cloned voices via forced
    # alignment) -----
    #
    # Event-driven, not polled: each word transition is armed as its own one-shot NSTimer, timed
    # to that word's exact captured start (recomputed fresh from player.currentTime() every time
    # a timer is armed, not scheduled once up front) — exactly one timer is ever pending. This
    # replaces an earlier polling design (checking "what word should be current" on a fixed
    # 0.03s tick) that measurably still skipped words shorter than the poll interval — sampling
    # can't structurally guarantee catching every word regardless of how fast it polls, since
    # real word durations can be shorter than any practical interval. Scheduling each transition
    # as its own timer removes that failure mode: the only remaining way to miss a word is the
    # main thread being preempted past that word's own start AND the next word's start together.

    @objc.python_method
    def _invalidateHighlightTimer(self):
        timer = getattr(self, "_highlight_timer", None)
        if timer is not None:
            timer.invalidate()
        self._highlight_timer = None

    @objc.python_method
    def _syncWordHighlightNow(self):
        # The one "landing" entry point — called whenever playback lands somewhere new (chunk
        # start, seek, resume, a mid-session screen rebuild), never on a periodic tick. Resolves
        # the correct current word from live player.currentTime() and arms the next transition.
        self._invalidateHighlightTimer()
        try:
            style = self.config.get("highlight_style", "highlight")
            if style == "none" or self.player is None:
                self._clearWordHighlight()
                return
            # Data-driven, not provider-gated: System always has timings from its live
            # callback; Chatterbox/Sesame have them whenever ASR alignment found a usable
            # mapping (see speech_verify._align_words) and otherwise fall through to "no
            # timings" below exactly like a cloud provider that's never had any highlight data.
            timings = self.chunk_word_timings.get(self.chunk_index)
            if not timings:
                self._clearWordHighlight()
                return
            current_time = self.player.currentTime()
            idx = -1
            for i, t in enumerate(timings):
                if t["start"] <= current_time:
                    idx = i
                else:
                    break
            # Unconditional, not gated on chunk_index having changed — this only runs at
            # discrete landing events now, so every call is itself a fresh anchor point. A
            # same-chunk backward seek used to leave a stale, too-far-ahead search cursor,
            # making _findWordGlobalRange's forward-only search miss and fall back to a
            # from-scratch full_text.find() — highlighting the word's FIRST occurrence in the
            # whole document instead of the one near the seek target.
            self._highlight_search_cursor = self._approxChunkStartOffset(self.chunk_index)
            self._highlight_chunk_index = self.chunk_index
            if idx == -1:
                # Landed before the first word's own captured start (e.g. right at chunk start —
                # real speech has a few ms of lead-in before the very first callback fires, so
                # current_time==0.0 can be earlier than timings[0]["start"]). Nothing should be
                # showing yet, but this must NOT just give up: _highlight_word_index stays -1 so
                # the fall-through to _scheduleNextWordTimer below correctly arms word 0
                # (next_idx = _highlight_word_index + 1 == 0). Returning here instead — as a
                # simple "nothing to show yet" bail-out — was a real bug: nothing else ever
                # re-invokes this function until the next landing event, so it silently killed
                # highlighting for the entire rest of the chunk.
                self._highlight_word_index = -1
                self._hideHighlightOverlay()
                self._revertPrevTextAttributes()
            else:
                self._highlight_word_index = idx
                word = timings[idx]
                global_range = self._findWordGlobalRange(word["text"])
                if global_range is not None:
                    self._applyWordHighlight(global_range, style)
                else:
                    # Word simply never found (even the punctuation-tolerant fallback in
                    # _findWordGlobalRange missed) — should be rare after that fix, but a silent
                    # skip here has no other visible symptom, so this is worth a real trace.
                    print(f"Word highlight: no match for {word['text']!r}", file=sys.stderr, flush=True)
        except Exception:
            # Moved here from the old polling tick's wrapper — an uncaught exception inside an
            # NSTimer block callback is silently swallowed with no printed trace, which is
            # exactly what hid the real bug that broke every highlight update until this was
            # added. Kept as a permanent safety net, not just debug scaffolding.
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        # Only chain forward if audio is actually playing — NSTimer measures real wall-clock
        # time, not player playback time, so a scheduled timer fires on schedule regardless of
        # whether the player itself is advancing. Landing here paused (should_play=False from a
        # scrub/skip performed while stopped) with this called unconditionally meant the
        # highlight kept animating through the text on its own even though the audio never
        # made a sound — confirmed by the user directly. The word already resolved and shown
        # above is correct for a paused landing; it just must not advance any further on its
        # own.
        if self.player is not None and self.player.isPlaying():
            self._scheduleNextWordTimer()

    @objc.python_method
    def _scheduleNextWordTimer(self):
        self._invalidateHighlightTimer()
        try:
            style = self.config.get("highlight_style", "highlight")
            if style == "none" or self.player is None:
                return
            timings = self.chunk_word_timings.get(self.chunk_index)
            if not timings or self.chunk_index != self._highlight_chunk_index:
                return  # nothing valid to chain from; the next _syncWordHighlightNow call resyncs
            next_idx = self._highlight_word_index + 1
            if next_idx >= len(timings):
                return  # last word of this chunk is already showing; chunk-transition resyncs fresh
            # Clamped to the PREVIOUS word's own start (never earlier) — the lead is meant to
            # move a transition slightly ahead of its own word's audio, not spill backward into
            # the still-playing previous word, which for two closely-spaced words could
            # otherwise flip their displayed order.
            floor = timings[next_idx - 1]["start"] if next_idx > 0 else 0.0
            target_time = max(floor, timings[next_idx]["start"] - self.HIGHLIGHT_LEAD_TIME)
            current_time = self.player.currentTime()
            # player.rate() is never changed anywhere in this app (speed is baked into the
            # rendered WAV at synthesis time instead) — dividing by it here is a correctness
            # safety net, not something currently exercised.
            rate = self.player.rate() or 1.0
            delay = max(0.0, (target_time - current_time) / rate)
            expected_token = self.playback_token
            expected_chunk = self.chunk_index
            timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(
                delay, False,
                lambda t: self._fireWordHighlightTimer(next_idx, expected_token, expected_chunk))
            # NSRunLoopCommonModes, same reasoning as _startProgressTimer — a default-mode-only
            # timer freezes during a live window resize, which here would silently push a word's
            # highlight moment past its real start time.
            AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(timer, AppKit.NSRunLoopCommonModes)
            self._highlight_timer = timer
        except Exception:
            # Same silent-swallow risk as _syncWordHighlightNow — this can be called from a
            # timer callback (via _fireWordHighlightTimer's reschedule), where an uncaught
            # exception would otherwise vanish with no trace.
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    @objc.python_method
    def _fireWordHighlightTimer(self, idx, expected_token, expected_chunk):
        self._highlight_timer = None
        # Defensive redundancy, not the primary guard — _stopProgressTimer already invalidates
        # any pending timer at every real stop/pause/seek point, so this should never actually
        # fire stale. Kept anyway, using the same playback_token idiom already used elsewhere.
        if self.playback_token is not expected_token or self.chunk_index != expected_chunk or self.player is None:
            return
        timings = self.chunk_word_timings.get(self.chunk_index)
        if not timings or idx >= len(timings):
            return
        self._highlight_word_index = idx
        try:
            word = timings[idx]
            global_range = self._findWordGlobalRange(word["text"])
            if global_range is not None:
                self._applyWordHighlight(global_range, self.config.get("highlight_style", "highlight"))
            else:
                print(f"Word highlight: no match for {word['text']!r}", file=sys.stderr, flush=True)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        # Unconditional reschedule, even after an exception — one bad word shouldn't permanently
        # kill highlighting for the rest of the chunk.
        self._scheduleNextWordTimer()

    @objc.python_method
    def _approxChunkStartOffset(self, index):
        # +1 per boundary for the single space chunk_text() joins pieces with — approximate
        # (original whitespace between sentences may have been different), used only as a
        # forward-search starting point, never as the final answer.
        return sum(len(self.all_chunks[i]) + 1 for i in range(index))

    @objc.python_method
    def _findWordGlobalRange(self, word_text):
        full_text = self.text_view.string()
        idx = full_text.find(word_text, self._highlight_search_cursor)
        if idx == -1:
            # Cursor drifted past the real occurrence (chunk-boundary approximation was off
            # more than expected) — fall back to a fresh search from the very start rather
            # than silently leaving the highlight stuck on the previous word.
            idx = full_text.find(word_text)
        if idx != -1:
            self._highlight_search_cursor = idx + len(word_text)
            return (idx, len(word_text))
        # sanitize_for_speech (text_prep.py) rewrites some punctuation before the text ever
        # reaches the synthesizer — a colon/semicolon becomes a comma, "(" becomes ", ", ")"
        # becomes "," — so a captured word like "there," never appears verbatim in the
        # DISPLAYED text, which still has the original "there:". That made the exact match
        # above fail and silently skip the word entirely. Falls back to the word's alphanumeric
        # core (stripping whatever punctuation the synthesizer's copy ended up with) matched at
        # a real word boundary in the original text, so the word still gets found and
        # highlighted — just without whatever trailing/leading punctuation differed.
        core = word_text.strip(string.punctuation)
        if not core:
            return None
        pattern = re.compile(r"\b" + re.escape(core) + r"\b")
        match = pattern.search(full_text, self._highlight_search_cursor)
        if match is None:
            match = pattern.search(full_text)
        if match is None:
            return None
        self._highlight_search_cursor = match.end()
        return (match.start(), len(core))

    @objc.python_method
    def _glyphRectForRange(self, loc, length):
        lm = self.text_view.layoutManager()
        tc = self.text_view.textContainer()
        char_range = AppKit.NSMakeRange(loc, length)
        # PyObjC returns a (result, out_param) TUPLE here, not a bare NSRange — the real ObjC
        # signature's second argument is an NSRangePointer (NSRange*) out-param, and PyObjC
        # surfaces "actualCharacterRange" as a second return value rather than accepting None
        # and returning just the primary range. Confirmed directly: passing the whole tuple
        # into boundingRectForGlyphRange_inTextContainer_ raised "depythonifying 'unsigned
        # long long', got 'Foundation.NSRange'" — it silently killed every highlight update
        # since the exception was swallowed inside the progress timer's block callback.
        glyph_range, _actual_char_range = lm.glyphRangeForCharacterRange_actualCharacterRange_(char_range, None)
        # A word that happens to fall exactly at a line-wrap point (most commonly one with a
        # hyphen, which text layout treats as a valid break opportunity — no artificial
        # hyphenation needed) can have its glyph range split across two lines. Confirmed via a
        # real screenshot: highlighting "word-skipping" split across a wrap turned into two
        # entire lines covered by one pill. boundingRectForGlyphRange_inTextContainer_ on the
        # FULL range is the cause — for a range that continues onto another line, Cocoa's own
        # bounding rect for the first line's portion extends to the line's right margin (same
        # behavior as a multi-line text SELECTION highlight, which is exactly what this looks
        # like), not to where the word's own glyphs actually end.
        # lineFragmentRectForGlyphAtIndex_effectiveRange_'s effectiveRange is the glyph range
        # that occupies the ENTIRE first line — intersecting glyph_range against it narrows to
        # just the glyphs of THIS word that actually sit on that line, and getting the bounding
        # rect for THAT narrowed range (not the line's own full-width fragment rect) gives a
        # tight rect around just those glyphs, e.g. just "word-", not the whole line.
        _first_line_rect, line_glyph_range = lm.lineFragmentRectForGlyphAtIndex_effectiveRange_(
            glyph_range.location, None)
        line_end = line_glyph_range.location + line_glyph_range.length
        clipped_end = min(glyph_range.location + glyph_range.length, line_end)
        clipped_range = AppKit.NSMakeRange(glyph_range.location, clipped_end - glyph_range.location)
        rect = lm.boundingRectForGlyphRange_inTextContainer_(clipped_range, tc)
        origin = self.text_view.textContainerOrigin()
        return NSMakeRect(rect.origin.x + origin.x, rect.origin.y + origin.y, rect.size.width, rect.size.height)

    @objc.python_method
    def _scrollToKeepPlaybackVisible(self, char_index):
        """Keeps whatever's currently being read scrolled into view, proactively — before it
        reaches the bottom edge, not only once it's already scrolled past it (confirmed
        needed via real testing: a document longer than one screen otherwise silently falls
        behind and has to be scrolled manually). Driven from a single character position: the
        highlighted word whenever word-level timing is available (System always; Chatterbox/
        Sesame whenever ASR alignment found a usable mapping for that chunk), or a chunk's
        start otherwise. _glyphRectForRange and NSClipView's documentVisibleRect are already both expressed in
        text_view's own (flipped) coordinate space, so no conversion is needed between them
        here — unlike _applyWordHighlight's overlay rect, which has to convert into the clip
        view's space because the overlay layer is a sibling of text_view, not a child of it."""
        storage = self.text_view.textStorage()
        length = storage.length()
        if length == 0:
            return
        char_index = max(0, min(char_index, length - 1))
        rect = self._glyphRectForRange(char_index, 1)
        clip = self.scroll_view.contentView()
        visible = clip.documentVisibleRect()
        # A quarter-screen margin off the bottom edge is the "before it reaches the last
        # lines" lead — scrolling starts while the current line still has room to breathe,
        # not right as it's about to clip off the bottom.
        margin = visible.size.height * 0.25
        already_visible = (rect.origin.y >= visible.origin.y and
                            rect.origin.y + rect.size.height <= visible.origin.y + visible.size.height - margin)
        if already_visible:
            return
        # Lands the target line roughly a third of the way down the viewport rather than
        # glued to the very top edge — reads more like a natural reading position than a
        # jarring "line pinned to the top" snap.
        new_y = max(0.0, rect.origin.y - visible.size.height * 0.3)
        max_y = max(0.0, self.text_view.frame().size.height - visible.size.height)
        new_y = min(new_y, max_y)
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(0.35)
        clip.animator().setBoundsOrigin_(NSMakePoint(visible.origin.x, new_y))
        AppKit.NSAnimationContext.endGrouping()

    @objc.python_method
    def _ensureHighlightOverlay(self):
        if self._highlight_overlay is None:
            layer = Quartz.CALayer.layer()
            layer.setHidden_(True)
            # Parented to the scroll view's clip view, NOT text_view's own layer — a layer-backed
            # NSView always composites its sublayers on top of its own drawn content, so a
            # sublayer of text_view's layer can never sit behind its glyphs no matter what
            # zPosition it's given. The clip view is text_view's real superview and sits behind
            # it in the actual view hierarchy, so inserting a sibling sublayer below text_view's
            # own layer there puts the pill genuinely behind the letters — text_view already
            # draws no background (setDrawsBackground_(False)), which is what makes it visible.
            self.scroll_view.contentView().layer().insertSublayer_below_(layer, self.text_view.layer())
            self._highlight_overlay = layer
        return self._highlight_overlay

    @objc.python_method
    def _applyWordHighlight(self, global_range, style):
        loc, length = global_range
        rect = self._glyphRectForRange(loc, length)
        self._scrollToKeepPlaybackVisible(loc)
        if style == "highlight":
            self._revertPrevTextAttributes()
            overlay = self._ensureHighlightOverlay()
            shape = self.config.get("highlight_shape", "pill")
            color_hex = self.config.get("highlight_color", self.DEFAULT_HIGHLIGHT_COLOR)
            nscolor = self._hexToColor(color_hex)
            # A small amount of horizontal padding — zero (an earlier attempt) was overcorrected,
            # sitting air-tight against the word with no breathing room; the ORIGINAL 4.0 was
            # the opposite problem (visibly bleeding past a word's own trailing punctuation into
            # the following space). 2.0 is a middle ground: a few pixels of margin without
            # reaching into the next word.
            pad_h, pad_v = 2.0, 2.0
            # The overlay is no longer a descendant of text_view (see _ensureHighlightOverlay),
            # so its rect needs converting into the clip view's coordinate space.
            conv_rect = self.text_view.convertRect_toView_(rect, self.scroll_view.contentView())
            new_frame = NSMakeRect(conv_rect.origin.x - pad_h, conv_rect.origin.y - pad_v,
                                    conv_rect.size.width + pad_h * 2, conv_rect.size.height + pad_v * 2)
            animated = self.config.get("highlight_animation", "slide") == "slide"
            AppKit.CATransaction.begin()
            if animated:
                AppKit.CATransaction.setAnimationDuration_(0.18)
            else:
                AppKit.CATransaction.setDisableActions_(True)
            overlay.setBackgroundColor_(nscolor.colorWithAlphaComponent_(0.55).CGColor())
            overlay.setCornerRadius_(4.0 if shape == "rounded" else new_frame.size.height / 2.0)
            overlay.setFrame_(new_frame)
            overlay.setHidden_(False)
            AppKit.CATransaction.commit()
        else:
            self._hideHighlightOverlay()
            self._applyTextStyleHighlight(loc, length, style)

    @objc.python_method
    def _revertRangeAttributes(self, storage, loc, length):
        # Just the attribute changes — no beginEditing/endEditing of its own. Callers wrap
        # this in their own edit transaction so a combined revert-then-apply (see
        # _applyTextStyleHighlight) happens as ONE atomic edit instead of two separate ones.
        if loc + length > storage.length():
            return
        rng = AppKit.NSMakeRange(loc, length)
        # self._body_font (cached once at construction), NOT self.text_view.font() — the latter
        # reflects the font at the current selection/insertion point, which sits wherever it was
        # last left (e.g. position 0) and never moves during hands-off playback. Once the word
        # under that point got bolded, .font() started returning bold for every subsequent call
        # too — so "reverting" a word was actually re-applying bold instead of clearing it,
        # which is why words stayed bold permanently once highlighted.
        storage.addAttribute_value_range_(AppKit.NSFontAttributeName, self._body_font, rng)
        storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, AppKit.NSColor.textColor(), rng)
        storage.removeAttribute_range_(AppKit.NSUnderlineStyleAttributeName, rng)
        storage.removeAttribute_range_(AppKit.NSUnderlineColorAttributeName, rng)
        storage.removeAttribute_range_(AppKit.NSStrokeWidthAttributeName, rng)
        storage.removeAttribute_range_(AppKit.NSStrokeColorAttributeName, rng)

    @objc.python_method
    def _applyTextStyleHighlight(self, loc, length, style):
        thickness = self.config.get("highlight_underline_thickness", "single")
        color_hex = self.config.get("highlight_color", self.DEFAULT_HIGHLIGHT_COLOR)
        nscolor = self._hexToColor(color_hex)
        storage = self.text_view.textStorage()
        if loc + length > storage.length():
            return
        prev = self._highlight_prev_range
        self._highlight_prev_range = None
        # Revert the PREVIOUS word and apply the NEW word's styling as one atomic edit rather
        # than two separate beginEditing/endEditing transactions — avoids a second edit landing
        # in between and observing a half-updated state, since this runs as often as every 30ms.
        storage.beginEditing()
        if prev is not None:
            self._revertRangeAttributes(storage, prev[0], prev[1])
        rng = AppKit.NSMakeRange(loc, length)
        if style == "bold":
            # A true bold font (NSFont.boldSystemFontOfSize_) has WIDER glyph advances than
            # regular weight for the same characters — confirmed directly (74.7pt vs 80.7pt for
            # the same 11-letter word at 14pt) — so swapping fonts as each word gets highlighted
            # shifted that word, and reflowed everything after it on the line, every single
            # time. A negative NSStrokeWidthAttributeName is Cocoa's own "faux bold" technique:
            # it fills AND strokes the glyph outline using the SAME (regular) font's metrics, so
            # nothing reflows — confirmed the rendered width is pixel-identical to plain text.
            storage.addAttribute_value_range_(AppKit.NSStrokeWidthAttributeName, self.HIGHLIGHT_BOLD_STROKE_WIDTH, rng)
            storage.addAttribute_value_range_(AppKit.NSStrokeColorAttributeName, nscolor, rng)
            storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, nscolor, rng)
        elif style == "underline":
            underline_style = AppKit.NSUnderlineStyleThick if thickness == "thick" else AppKit.NSUnderlineStyleSingle
            storage.addAttribute_value_range_(AppKit.NSUnderlineStyleAttributeName, underline_style, rng)
            storage.addAttribute_value_range_(AppKit.NSUnderlineColorAttributeName, nscolor, rng)
            storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, nscolor, rng)
        elif style == "color":
            storage.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, nscolor, rng)
        storage.endEditing()
        self._highlight_prev_range = (loc, length)

    @objc.python_method
    def _revertPrevTextAttributes(self):
        # Standalone revert (its own begin/end transaction) — used by the "highlight" style's
        # CALayer-overlay path and by _clearWordHighlight, neither of which pairs it with a
        # simultaneous new-range apply the way _applyTextStyleHighlight does, so there's no
        # second edit landing right behind it to race against.
        if self._highlight_prev_range is None:
            return
        loc, length = self._highlight_prev_range
        self._highlight_prev_range = None
        storage = self.text_view.textStorage()
        storage.beginEditing()
        self._revertRangeAttributes(storage, loc, length)
        storage.endEditing()
        self.text_view.setNeedsDisplay_(True)

    @objc.python_method
    def _hideHighlightOverlay(self):
        if self._highlight_overlay is not None:
            self._highlight_overlay.setHidden_(True)

    @objc.python_method
    def _clearWordHighlight(self):
        self._invalidateHighlightTimer()
        self._highlight_word_index = -1
        self._highlight_chunk_index = None
        self._highlight_search_cursor = 0
        self._hideHighlightOverlay()
        self._revertPrevTextAttributes()

    @objc.python_method
    def _scrubberDragged(self, fraction):
        # Freeze the highlight for the duration of the drag — audio keeps playing in the
        # background during a scrub (this never touches self.player), so left running the timer
        # chain would keep advancing the highlight against the real position while the scrubber
        # thumb shows the dragged one. _scrubberReleased -> _seekToVirtualTime -> a landing in
        # _beginChunkPlayback resyncs once the real seek actually lands.
        self._invalidateHighlightTimer()
        self.is_scrubbing = True
        total = self._totalEstimatedDuration() if self.all_chunks else 0.0
        current = fraction * total
        self.scrubber.setFraction(fraction)
        self.elapsed_label.setStringValue_(format_playback_time(current))
        self.remaining_label.setStringValue_(format_playback_time(max(0.0, total - current)))

    @objc.python_method
    def _scrubberReleased(self, fraction):
        self.is_scrubbing = False
        if not self.all_chunks:
            return
        # A scrub/click on the timeline while playback was already stopped or paused should
        # land at the new position without starting it; only scrubbing WHILE actively playing
        # should keep playing after.
        should_play = self.player is not None and self.player.isPlaying()
        total = self._totalEstimatedDuration()
        self._seekToVirtualTime(fraction * total, should_play=should_play)

    @objc.python_method
    def _syncPlaybackUI(self):
        if not hasattr(self, "play_btn"):
            return
        playing = self.player is not None and self.player.isPlaying()
        active = self.player is not None
        img = symbol_image("pause.fill" if playing else "play.fill", 13)
        if img:
            self.play_btn.setImage_(img)
        for btn in (self.back_btn, self.fwd_btn):
            btn.setEnabled_(active)
            btn.setAlphaValue_(1.0 if active else 0.35)
        self.stop_btn.setEnabled_(active)
        self.stop_btn.setAlphaValue_(1.0 if active else 0.4)

    # ----- in-window overlays (blur backdrop, fade in/out, Esc/backdrop dismiss) -----
    @objc.python_method
    def _presentOverlay(self, card):
        try:
            self.dismissOverlay()
            backdrop = BackdropView.alloc().initWithFrame_(self.root.bounds())
            backdrop.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
            backdrop.setBlendingMode_(AppKit.NSVisualEffectBlendingModeWithinWindow)  # blurs the app UI behind it
            backdrop.setState_(AppKit.NSVisualEffectStateActive)
            backdrop.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            backdrop.dismiss_callback = self.dismissOverlay
            dim = AppKit.NSView.alloc().initWithFrame_(backdrop.bounds())
            dim.setWantsLayer_(True)
            dim.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.07, 0.45).CGColor())
            dim.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            backdrop.addSubview_(dim)

            b = self.root.bounds()
            cf = card.frame()
            card.setFrameOrigin_(NSMakePoint((b.size.width - cf.size.width) / 2.0, (b.size.height - cf.size.height) / 2.0))
            card.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
            # card is a SIBLING of backdrop (both direct children of self.root), not nested
            # inside it — a shadow on a layer that's a descendant of an NSVisualEffectView
            # using BehindWindow blending wasn't rendering reliably (invisible over a bright
            # background behind the app). As a normal sibling, its shadow composites the usual
            # way. Clicking the card still can't reach the backdrop underneath it (topmost
            # view wins hit-testing), so dismiss-on-backdrop-click still works the same.

            backdrop.setAlphaValue_(0.0)
            self.root.addSubview_(backdrop)
            card.setAlphaValue_(0.0)
            self.root.addSubview_(card)
            self.overlay = backdrop
            self.overlay_card = card
            # fade + slight pop
            card.setWantsLayer_(True)
            fix_anchor(card)
            card.layer().setTransform_(Quartz.CATransform3DMakeScale(0.94, 0.94, 1.0))
            # self.root is itself an NSVisualEffectView (the window's own background blur) —
            # adding a layer-backed subview to an NSVisualEffectView silently zeroes that
            # subview's shadowOpacity the moment it's added (confirmed by direct testing: read
            # back right after addSubview_ and it was 0 despite being set to a nonzero value
            # in _makeCard). It's a one-time reset, not continuously re-asserted, so setting it
            # again here — after the subview has already been added — sticks correctly.
            card.layer().setShadowOpacity_(SHADOW_OPACITY)

            def fade_in(ctx):
                ctx.setDuration_(0.25)
                backdrop.animator().setAlphaValue_(1.0)
                card.animator().setAlphaValue_(1.0)
            AppKit.NSAnimationContext.runAnimationGroup_(fade_in)
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setAnimationDuration_(0.28)
            card.layer().setTransform_(Quartz.CATransform3DIdentity)
            AppKit.CATransaction.commit()

            if self.esc_monitor is None:
                def handler(event):
                    if event.keyCode() == 53:  # Esc
                        self.dismissOverlay()
                        return None
                    return event
                self.esc_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    AppKit.NSEventMaskKeyDown, handler)
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def dismissOverlay(self):
        if self._rec_stream is not None:
            # Actively capturing from the mic right now — an accidental backdrop click or Esc
            # press must not silently abandon the hardware stream and a mid-take recording.
            # Once recording stops (Stop clicked, or the 10s cap fires), this is None again,
            # so normal dismissal is allowed even during the confirm/preview/naming stage —
            # losing an unsaved preview there is a much smaller cost than being unable to back
            # out of the flow at all, which is exactly what blocking it there felt like.
            return
        if self._rec_preview_player is not None:
            # Covers backdrop-click/Esc dismissing the recording confirm card directly,
            # bypassing recordingCancelClicked_'s own explicit cleanup — a playing preview
            # shouldn't keep going after the card that shows it has closed.
            try:
                self._rec_preview_player.stop()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            self._rec_preview_player = None
        if self._rec_script_fade_observer is not None:
            AppKit.NSNotificationCenter.defaultCenter().removeObserver_(self._rec_script_fade_observer)
            self._rec_script_fade_observer = None
        if self.overlay is None:
            return
        overlay = self.overlay
        card = self.overlay_card
        self.overlay = None
        self.overlay_card = None
        if self.esc_monitor is not None:
            AppKit.NSEvent.removeMonitor_(self.esc_monitor)
            self.esc_monitor = None

        def fade_out(ctx):
            ctx.setDuration_(0.22)
            overlay.animator().setAlphaValue_(0.0)
            if card is not None:
                card.animator().setAlphaValue_(0.0)

        def done(ctx=None):
            overlay.removeFromSuperview()
            if card is not None:
                card.removeFromSuperview()
        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(fade_out, done)

    @objc.python_method
    def _makeCard(self, w, h):
        card = CardView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        card.setWantsLayer_(True)
        card.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.17, 0.75).CGColor())
        card.layer().setBorderColor_(white(0.14).CGColor())
        card.layer().setBorderWidth_(1.0)
        card.layer().setCornerRadius_(14.0)
        # this is a plain NSView sitting inside the window, not its own NSWindow — it doesn't
        # get a window shadow for free, so the drop shadow has to be drawn explicitly.
        # masksToBounds is deliberately False: True would clip the shadow away since the
        # shadow is drawn outside the layer's own bounds. (_presentOverlay re-asserts
        # shadowOpacity again after adding this view to the window — see the comment there.)
        card.layer().setMasksToBounds_(False)
        card.layer().setShadowColor_(AppKit.NSColor.blackColor().CGColor())
        card.layer().setShadowOpacity_(SHADOW_OPACITY)
        card.layer().setShadowRadius_(34.0)
        card.layer().setShadowOffset_(NSMakeSize(0, -14))
        return card

    # ----- report a problem -----
    REPORT_FIELDS = [
        ("activity", "What were you trying to do?", "e.g. read a poem, an article, a book chapter"),
        ("problem", "What went wrong?", "e.g. audio sounded garbled, took too long, app froze"),
        ("when", "When did this happen?", "e.g. just now, about 10 minutes ago"),
        ("notes", "Anything else? (optional)", ""),
        ("contact", "Name or email, so we can follow up (optional)", ""),
    ]

    def reportProblemClicked_(self, sender):
        self._report_fields = {}
        self._showReportCard("form")

    @objc.python_method
    def _showReportCard(self, stage, error_message=None):
        if stage == "form":
            row_h, label_h, gap = 46.0, 14.0, 6.0
            title_h = 24.0
            rows_h = len(self.REPORT_FIELDS) * row_h
            btn_h = 34.0
            card_w = 320.0
            NATURAL_H = 20 + title_h + 10 + rows_h + 14 + btn_h + 20
            card = self._makeCard(card_w, NATURAL_H)
            title = make_label("Report a Problem", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, NATURAL_H - 20 - title_h, card_w, title_h))
            card.addSubview_(title)

            cursor = NATURAL_H - 20 - title_h - 10
            saved = getattr(self, "_report_fields", {}) or {}
            field_refs = {}
            for key, label_text, placeholder in self.REPORT_FIELDS:
                label = make_label(label_text, 11, 0.5)
                label.setFrame_(NSMakeRect(20, cursor - label_h, card_w - 40, label_h))
                card.addSubview_(label)
                field = AppKit.NSTextField.alloc().initWithFrame_(NSMakeRect(20, cursor - label_h - gap - 24, card_w - 40, 24))
                field.setFont_(AppKit.NSFont.systemFontOfSize_(12.5))
                if placeholder:
                    field.setPlaceholderString_(placeholder)
                if key in saved:
                    field.setStringValue_(saved[key])
                card.addSubview_(field)
                field_refs[key] = field
                cursor -= row_h
            self._report_field_refs = field_refs

            cancel_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
            cancel = text_button("Cancel", NSMakeRect(20, 20, (card_w - 52) / 2, btn_h), "reportCancelClicked:", self,
                                 cancel_font, 0.08, 0.16, 9.0, white(0.85))
            cont = cta_button("Continue", NSMakeRect(20 + (card_w - 52) / 2 + 12, 20, (card_w - 52) / 2, btn_h),
                              "reportContinueClicked:", self)
            card.addSubview_(cancel)
            card.addSubview_(cont)
        elif stage == "review":
            card_w = 320.0
            title_h, notice_h, box_h, btn_h = 24.0, 34.0, 160.0, 34.0
            NATURAL_H = 20 + title_h + 10 + notice_h + 10 + box_h + 14 + btn_h + 20
            card = self._makeCard(card_w, NATURAL_H)
            title = make_label("Review Your Report", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, NATURAL_H - 20 - title_h, card_w, title_h))
            notice = make_label(
                "This is exactly what will be sent — nothing goes out until you tap Send Report.",
                11, 0.5, align=AppKit.NSTextAlignmentCenter)
            notice.cell().setWraps_(True)
            notice.setFrame_(NSMakeRect(20, NATURAL_H - 20 - title_h - 10 - notice_h, card_w - 40, notice_h))

            box_y = NATURAL_H - 20 - title_h - 10 - notice_h - 10 - box_h
            box = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(20, box_y, card_w - 40, box_h))
            box.setWantsLayer_(True)
            box.layer().setBackgroundColor_(white(0.05).CGColor())
            box.layer().setBorderColor_(white(0.08).CGColor())
            box.layer().setBorderWidth_(1.0)
            box.layer().setCornerRadius_(9.0)
            nscroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(6, 6, card_w - 40 - 12, box_h - 12))
            nscroll.setBorderType_(AppKit.NSNoBorder)
            nscroll.setHasVerticalScroller_(True)
            nscroll.setDrawsBackground_(False)
            body_tv = AppKit.NSTextView.alloc().initWithFrame_(nscroll.bounds())
            body_tv.setEditable_(False)
            body_tv.setDrawsBackground_(False)
            body_tv.setFont_(AppKit.NSFont.systemFontOfSize_(10.5))
            body_tv.setTextColor_(white(0.7))
            title_text, body_text = self._report_draft
            body_tv.setString_(f"{title_text}\n\n{body_text}")
            nscroll.setDocumentView_(body_tv)
            box.addSubview_(nscroll)

            back_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
            back = text_button("Back", NSMakeRect(20, 20, (card_w - 52) / 2, btn_h), "reportBackClicked:", self,
                               back_font, 0.08, 0.16, 9.0, white(0.85))
            send = cta_button("Send Report", NSMakeRect(20 + (card_w - 52) / 2 + 12, 20, (card_w - 52) / 2, btn_h),
                              "reportSendClicked:", self)
            for s in (title, notice, box, back, send):
                card.addSubview_(s)
        elif stage == "sending":
            card = self._makeCard(280, 120)
            spinner = AppKit.NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(280 / 2 - 13, 66, 26, 26))
            spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
            spinner.startAnimation_(None)
            lbl = make_label("Sending report...", 13, 0.75, align=AppKit.NSTextAlignmentCenter)
            lbl.setFrame_(NSMakeRect(0, 32, 280, 18))
            card.addSubview_(spinner)
            card.addSubview_(lbl)
        elif stage == "sent":
            card = self._makeCard(280, 170)
            icon = AppKit.NSImageView.alloc().initWithFrame_(NSMakeRect(280 / 2 - 17, 116, 34, 34))
            img = symbol_image("checkmark.circle.fill", 26)
            if img:
                icon.setImage_(img)
                icon.setContentTintColor_(white(0.9))
            title = make_label("Report Sent", 14, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, 90, 280, 18))
            sub = make_label("Thank you — this really helps.", 12, 0.5, align=AppKit.NSTextAlignmentCenter)
            sub.setFrame_(NSMakeRect(0, 70, 280, 16))
            ok_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
            ok = text_button("Done", NSMakeRect(20, 20, 240, 34), "reportCancelClicked:", self, ok_font, 0.08, 0.16, 9.0, white(0.85))
            for s in (icon, title, sub, ok):
                card.addSubview_(s)
        else:  # failed
            card = self._makeCard(300, 190)
            icon = AppKit.NSImageView.alloc().initWithFrame_(NSMakeRect(300 / 2 - 17, 136, 34, 34))
            img = symbol_image("exclamationmark.triangle.fill", 26)
            if img:
                icon.setImage_(img)
                icon.setContentTintColor_(white(0.9))
            title = make_label("Couldn't Send Report", 14, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, 110, 300, 18))
            sub = make_label(error_message or "Something went wrong.", 11.5, 0.5, align=AppKit.NSTextAlignmentCenter)
            sub.cell().setWraps_(True)
            sub.setFrame_(NSMakeRect(20, 80, 260, 30))
            back_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
            back = text_button("Back", NSMakeRect(20, 40, 126, 34), "reportBackClicked:", self,
                               back_font, 0.08, 0.16, 9.0, white(0.85))
            retry = cta_button("Try Again", NSMakeRect(154, 40, 126, 34), "reportSendClicked:", self)
            for s in (icon, title, sub, back, retry):
                card.addSubview_(s)
        self._presentOverlay(card)

    def reportContinueClicked_(self, sender):
        self._report_fields = {key: field.stringValue() for key, field in self._report_field_refs.items()}
        # Auto-filled from live app state rather than asked as a guided question — the tester
        # already told us this by picking a voice, no need to make them retype it.
        self._report_fields["provider_voice"] = f"{self.config.get('provider', '?')} / {self.config.get('voice_id', '?')}"
        self._report_draft = bug_report.build_report(
            self._report_fields, self.session_report_log, APP_VERSION, APP_BUILD)
        self._showReportCard("review")

    def reportBackClicked_(self, sender):
        self._showReportCard("form")

    def reportCancelClicked_(self, sender):
        self.dismissOverlay()

    def reportSendClicked_(self, sender):
        self._showReportCard("sending")
        threading.Thread(target=self._reportSendWorker, daemon=True).start()

    @objc.python_method
    def _reportSendWorker(self):
        title, body = self._report_draft
        try:
            bug_report.submit_report(title, body)
        except bug_report.ReportError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_("reportFailedMain:", str(e), False)
            return
        self.performSelectorOnMainThread_withObject_waitUntilDone_("reportSentMain:", "", False)

    def reportSentMain_(self, _):
        self._showReportCard("sent")

    def reportFailedMain_(self, message):
        self._showReportCard("failed", error_message=str(message))

    # ----- about -----
    def showAbout_(self, sender):
        card = self._makeCard(300, 330)
        cw = 300

        icon_bg = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(cw / 2 - 26, 258, 52, 52))
        icon_bg.setWantsLayer_(True)
        icon_bg.layer().setBackgroundColor_(white(0.08).CGColor())
        icon_bg.layer().setCornerRadius_(12.0)
        icon = AppKit.NSImageView.alloc().initWithFrame_(icon_bg.bounds())
        img = symbol_image("waveform", 22)
        if img:
            icon.setImage_(img)
            icon.setContentTintColor_(white(0.85))
        icon_bg.addSubview_(icon)

        name = make_label(APP_NAME, 17, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        name.setFrame_(NSMakeRect(0, 228, cw, 22))
        tagline = make_label("TEXT TO SPEECH", 10, 0.35, AppKit.NSFontWeightMedium, AppKit.NSTextAlignmentCenter)
        tagline.setFrame_(NSMakeRect(0, 212, cw, 13))
        version = make_label(f"Version {APP_VERSION} (build {APP_BUILD})", 11.5, 0.5, align=AppKit.NSTextAlignmentCenter)
        version.setFrame_(NSMakeRect(0, 188, cw, 15))
        byline = make_label("Designed & built by Gilberto Rodriguez", 12, 0.75, align=AppKit.NSTextAlignmentCenter)
        byline.setFrame_(NSMakeRect(10, 164, cw - 20, 16))

        link_font = AppKit.NSFont.systemFontOfSize_(11.5)
        link = text_button("github.com/Redcupss", NSMakeRect(cw / 2 - 80, 138, 160, 20), "openGitHub:", self,
                           link_font, 0.0, 0.08, 5.0, white(0.65))

        update_font = AppKit.NSFont.systemFontOfSize_weight_(11.5, AppKit.NSFontWeightMedium)
        update_btn = text_button("Check for Updates", NSMakeRect(cw / 2 - 75, 96, 150, 30), "checkForUpdatesClicked:", self,
                                 update_font, 0.08, 0.16, 8.0, white(0.85))

        legal = make_label(
            "© 2026 Gilberto Rodriguez. All rights reserved.\nSpeech audio is generated by your connected provider.",
            10, 0.3, align=AppKit.NSTextAlignmentCenter)
        legal.setFrame_(NSMakeRect(14, 24, cw - 28, 40))

        for sub in (icon_bg, name, tagline, version, byline, link, update_btn, legal):
            card.addSubview_(sub)
        self._presentOverlay(card)

    def openGitHub_(self, sender):
        AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(GITHUB_URL))

    # ----- updates -----
    def checkForUpdatesClicked_(self, sender):
        self._showUpdateCard("checking")
        threading.Thread(target=self._checkUpdateWorker, args=(False,), daemon=True).start()

    @objc.python_method
    def _checkUpdateWorker(self, silent):
        try:
            data = self._request_json(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                {"Accept": "application/vnd.github+json"})
            tag = (data.get("tag_name") or "").lstrip("v")
            assets = data.get("assets") or []
            info = {
                "tag": tag,
                "notes": data.get("body") or "",
                "asset_url": assets[0]["browser_download_url"] if assets else None,
                "asset_size": assets[0].get("size", 0) if assets else 0,
            }
        except Exception:
            if not silent:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("updateCheckFailedMain:", "", False)
            return
        self.update_info = info
        newer = self._isNewer(info["tag"], APP_VERSION)
        if silent:
            if newer and info["tag"] != self.config.get("skipped_version"):
                self.performSelectorOnMainThread_withObject_waitUntilDone_("showUpdateAvailableMain:", "", False)
        else:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showUpdateAvailableMain:" if newer else "showUpToDateMain:", "", False)

    @objc.python_method
    def _isNewer(self, remote, local):
        def parts(s):
            return [int(p) for p in s.split(".") if p.isdigit()]
        try:
            return parts(remote) > parts(local)
        except ValueError:
            return False

    def updateCheckFailedMain_(self, _):
        self._showUpdateCard("uptodate", failed=True)

    def showUpdateAvailableMain_(self, _):
        self._showUpdateCard("available")

    def showUpToDateMain_(self, _):
        self._showUpdateCard("uptodate")

    @objc.python_method
    def _showUpdateCard(self, stage, failed=False):
        if stage == "checking":
            card = self._makeCard(300, 120)
            spinner = AppKit.NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(300 / 2 - 13, 66, 26, 26))
            spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
            spinner.startAnimation_(None)
            lbl = make_label("Checking for updates...", 13, 0.75, align=AppKit.NSTextAlignmentCenter)
            lbl.setFrame_(NSMakeRect(0, 32, 300, 18))
            card.addSubview_(spinner)
            card.addSubview_(lbl)
        elif stage == "available":
            info = self.update_info or {}
            card = self._makeCard(300, 300)
            title = make_label("Update Available", 15, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, 262, 300, 20))
            sub = make_label(f"{APP_NAME} {info.get('tag', '?')} — you have {APP_VERSION}", 12, 0.5,
                             align=AppKit.NSTextAlignmentCenter)
            sub.setFrame_(NSMakeRect(0, 242, 300, 16))

            notes_box = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(20, 116, 260, 116))
            notes_box.setWantsLayer_(True)
            notes_box.layer().setBackgroundColor_(white(0.05).CGColor())
            notes_box.layer().setBorderColor_(white(0.08).CGColor())
            notes_box.layer().setBorderWidth_(1.0)
            notes_box.layer().setCornerRadius_(9.0)
            header = make_label("WHAT'S NEW", 10, 0.4, AppKit.NSFontWeightSemibold)
            header.setFrame_(NSMakeRect(12, 94, 236, 13))
            nscroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(6, 8, 248, 84))
            nscroll.setBorderType_(AppKit.NSNoBorder)
            nscroll.setHasVerticalScroller_(True)
            nscroll.setDrawsBackground_(False)
            notes_tv = AppKit.NSTextView.alloc().initWithFrame_(nscroll.bounds())
            notes_tv.setEditable_(False)
            notes_tv.setDrawsBackground_(False)
            notes_tv.setFont_(AppKit.NSFont.systemFontOfSize_(11.5))
            notes_tv.setTextColor_(white(0.75))
            notes_tv.setString_(info.get("notes") or "General improvements and fixes.")
            nscroll.setDocumentView_(notes_tv)
            notes_box.addSubview_(header)
            notes_box.addSubview_(nscroll)

            later_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
            later = text_button("Later", NSMakeRect(20, 72, 126, 34), "dismissUpdate:", self,
                                later_font, 0.08, 0.16, 9.0, white(0.85))
            update_now = cta_button("Update Now", NSMakeRect(154, 72, 126, 34), "startUpdateDownload:", self)
            skip_font = AppKit.NSFont.systemFontOfSize_(11)
            skip = text_button("Skip this version", NSMakeRect(300 / 2 - 60, 40, 120, 20), "skipVersion:", self,
                               skip_font, 0.0, 0.06, 5.0, white(0.35))
            for s in (title, sub, notes_box, later, update_now, skip):
                card.addSubview_(s)
        elif stage == "downloading":
            card = self._makeCard(300, 130)
            info = self.update_info or {}
            lbl = make_label(f"Downloading {APP_NAME} {info.get('tag', '')}...", 13, 0.75, align=AppKit.NSTextAlignmentCenter)
            lbl.setFrame_(NSMakeRect(0, 88, 300, 18))
            bar = AppKit.NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(22, 58, 256, 6))
            bar.setStyle_(AppKit.NSProgressIndicatorStyleBar)
            bar.setIndeterminate_(False)
            bar.setMinValue_(0)
            bar.setMaxValue_(100)
            self.progress_bar = bar
            self.progress_label = make_label("0%", 11, 0.4, align=AppKit.NSTextAlignmentCenter)
            self.progress_label.setFrame_(NSMakeRect(0, 34, 300, 14))
            card.addSubview_(lbl)
            card.addSubview_(bar)
            card.addSubview_(self.progress_label)
        elif stage == "ready":
            card = self._makeCard(300, 190)
            icon = AppKit.NSImageView.alloc().initWithFrame_(NSMakeRect(300 / 2 - 17, 136, 34, 34))
            img = symbol_image("checkmark.circle.fill", 26)
            if img:
                icon.setImage_(img)
                icon.setContentTintColor_(white(0.9))
            title = make_label("Ready to Install", 14, 0.92, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, 110, 300, 18))
            sub = make_label(f"{APP_NAME} will relaunch to finish updating.", 12, 0.5, align=AppKit.NSTextAlignmentCenter)
            sub.setFrame_(NSMakeRect(0, 90, 300, 16))
            btn = cta_button("Relaunch Now", NSMakeRect(20, 40, 260, 34), "installAndRelaunch:", self)
            for s in (icon, title, sub, btn):
                card.addSubview_(s)
        else:  # uptodate / failed
            card = self._makeCard(300, 190)
            icon = AppKit.NSImageView.alloc().initWithFrame_(NSMakeRect(300 / 2 - 17, 136, 34, 34))
            img = symbol_image("checkmark.circle.fill" if not failed else "wifi.slash", 26)
            if img:
                icon.setImage_(img)
                icon.setContentTintColor_(white(0.9))
            title = make_label("You're up to date" if not failed else "Couldn't check for updates", 14, 0.92,
                               AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
            title.setFrame_(NSMakeRect(0, 110, 300, 18))
            sub = make_label(
                f"{APP_NAME} {APP_VERSION} is the latest version." if not failed else "Check your connection and try again.",
                12, 0.5, align=AppKit.NSTextAlignmentCenter)
            sub.setFrame_(NSMakeRect(0, 90, 300, 16))
            ok_font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightMedium)
            ok = text_button("OK", NSMakeRect(20, 40, 260, 34), "dismissUpdate:", self, ok_font, 0.08, 0.16, 9.0, white(0.85))
            for s in (icon, title, sub, ok):
                card.addSubview_(s)
        self._presentOverlay(card)

    def dismissUpdate_(self, sender):
        self.dismissOverlay()

    def skipVersion_(self, sender):
        if self.update_info:
            self.config["skipped_version"] = self.update_info["tag"]
            save_config(self.config)
        self.dismissOverlay()

    def startUpdateDownload_(self, sender):
        if not self.update_info or not self.update_info.get("asset_url"):
            self.dismissOverlay()
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                NSURL.URLWithString_(f"https://github.com/{GITHUB_REPO}/releases/latest"))
            return
        self._showUpdateCard("downloading")
        threading.Thread(target=self._downloadWorker, daemon=True).start()

    @objc.python_method
    def _downloadWorker(self):
        info = self.update_info
        dest = os.path.expanduser(f"~/Downloads/{APP_NAME}-{info['tag']}.zip")
        try:
            req = urllib.request.Request(info["asset_url"])
            with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
                total = int(resp.headers.get("Content-Length") or info.get("asset_size") or 0)
                got = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            pct = round(got * 100.0 / total)
                            mb = total / 1048576.0
                            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                                "updateProgressMain:", f"{pct}|{pct}% of {mb:.1f} MB", False)
        except Exception:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", "Update download failed. Try again from the About window.", False)
            self.performSelectorOnMainThread_withObject_waitUntilDone_("dismissUpdateMain:", "", False)
            return
        self.downloaded_update_path = dest
        self.performSelectorOnMainThread_withObject_waitUntilDone_("downloadFinishedMain:", "", False)

    def updateProgressMain_(self, packed):
        pct, label = str(packed).split("|", 1)
        self.progress_bar.setDoubleValue_(float(pct))
        self.progress_label.setStringValue_(label)

    def dismissUpdateMain_(self, _):
        self.dismissOverlay()

    def downloadFinishedMain_(self, _):
        self._showUpdateCard("ready")

    def installAndRelaunch_(self, sender):
        zip_path = getattr(self, "downloaded_update_path", "")
        if not zip_path or not os.path.exists(zip_path):
            self.showError_("Update file not found.")
            return
        self.dismissOverlay()
        threading.Thread(target=self._installUpdateWorker, args=(zip_path,), daemon=True).start()

    @objc.python_method
    def _installUpdateWorker(self, zip_path):
        tmp_dir = tempfile.mkdtemp()
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            subprocess.run(["ditto", "-x", "-k", zip_path, extract_dir], check=True)
        except subprocess.CalledProcessError:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", "Could not unpack the update.", False)
            return

        extracted_app = None
        for name in os.listdir(extract_dir):
            if name.endswith(".app"):
                extracted_app = os.path.join(extract_dir, name)
                break
        if not extracted_app:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", "The update package did not contain an app.", False)
            return

        current_app_path = str(AppKit.NSBundle.mainBundle().bundlePath())
        if not current_app_path or not current_app_path.endswith(".app"):
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", "Could not determine the app location; update aborted.", False)
            return

        script_path = os.path.join(tmp_dir, "install_update.sh")
        script = (
            "#!/bin/sh\n"
            "sleep 1\n"
            f"rm -rf {shlex.quote(current_app_path)}\n"
            f"mv {shlex.quote(extracted_app)} {shlex.quote(current_app_path)}\n"
            f"rm -f {shlex.quote(zip_path)}\n"
            f"open {shlex.quote(current_app_path)}\n"
            f"rm -f {shlex.quote(script_path)}\n"
        )
        with open(script_path, "w") as f:
            f.write(script)
        os.chmod(script_path, 0o755)

        subprocess.Popen(["/bin/sh", script_path], start_new_session=True)
        self.performSelectorOnMainThread_withObject_waitUntilDone_("terminateApp:", None, False)

    def terminateApp_(self, sender):
        AppKit.NSApp.terminate_(None)


if __name__ == "__main__":
    app = AppKit.NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    app.run()
