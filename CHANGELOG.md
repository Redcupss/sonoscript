# Changelog

All notable changes to SonoScript are tracked here, newest first. Versioning
follows [Semantic Versioning](https://semver.org) (MAJOR.MINOR.PATCH); the
build number increments once per release regardless of version bump.

## [1.13.1] (build 49) — 2026-08-11

### Fixed
- **Auto-launch: a Gatekeeper block and a launch-error dialog on cold start.** The previous fix
  for the auto-launched app stealing focus (launching the app binary directly via `subprocess`
  instead of through `open`) traded one bug for another: bypassing Launch Services meant the
  launched app inherited the native-messaging host's own restricted process environment instead
  of a normal user-session one, which could cause a Gatekeeper block on a temp-extracted shared
  library the app JIT-loads on every launch. Fixed by going back to `open -g` for the launch
  itself (restoring a normal environment) while replacing the unreliable `--args` argv signaling
  with a marker file the native host writes and the app consumes on startup — same "stay silent,
  don't steal focus" behavior, without the environment problem.
- **Orphaned playback with no way to stop it.** Navigating away from a page destroyed its
  toolbar (and the toolbar's only control connection) without stopping playback, leaving audio
  running indefinitely with no control surface reachable anywhere. The local bridge now stops
  playback automatically when the last connected toolbar disconnects — scoped correctly so it
  never fires while another toolbar (e.g. in a different tab) is still legitimately watching the
  same session.
- **Playback toolbar sometimes missing after a successful read request.** A subsequent read could
  start audio with no toolbar appearing and no visible error. The toolbar's in-page injection now
  retries a few times before giving up, covering a transient timing gap right after a page
  navigation.
- **Wrong content read aloud on rankings/hub-style pages.** On pages that are mostly link/card
  grids rather than a normal article (e.g. an awards-list landing page), automatic whole-page
  extraction could pick a page-footer methodology/credits section instead of the real page intro,
  since that boilerplate was often the single densest block of text on the page. Sections headed
  by "Methodology" or "Credits" are now excluded before content detection runs.

## [1.13.0] (build 48) — 2026-08-11

### Added
- **Browser extension (Chrome/Edge, not Safari yet)**: read any web page aloud using
  SonoScript's own voice engine instead of the browser's built-in TTS. A local-only listener
  hands the extension a fresh per-launch token over Chrome/Edge Native Messaging — the only way
  to obtain it, verified end to end — rather than any open network-reachable auth. Automatic
  whole-page content detection strips ads/navigation/photo captions the way Firefox's own
  Reader View does (via Readability.js), with a manual "read selection" override for anything it
  gets wrong. If SonoScript isn't already running when a read is requested, the native host now
  launches it automatically — checked against the local listener actually accepting connections,
  not just a leftover token file from a previous session, which would otherwise look identical to
  a currently-running instance. The launch is silent (no window, no focus stolen from whatever
  the user is doing in the browser) and the wait for it to finish starting up happens as a
  bounded retry loop in the extension itself rather than a single long-blocking native-messaging
  call, which real testing showed Chrome doesn't reliably allow. Full technical design, including
  two real bugs this went through before landing, in `browser_extension/DESIGN.md`.
- **In-page floating playback toolbar** — play/pause/skip/scrubber/voice picker, modeled on
  Edge's own Read Aloud bar, with SonoScript's own window no longer jumping to the front on
  every read.
- **Real "Liquid Glass" material on the toolbar**, via [liquidGL](https://github.com/naughtyduk/liquidGL)
  (a real WebGL library, not a CSS approximation — an earlier `backdrop-filter` + SVG-filter
  attempt was fully replaced after several rounds of fixes still couldn't handle scroll
  correctly, a limitation in backdrop-filter itself, not a bug). Vendored and patched at
  `browser_extension/liquidGL.js`: closed-Shadow-DOM element targeting, clean teardown on
  repeated toolbar open/close, a bevelWidth fix for this bar's extreme aspect ratio, a
  refraction-direction bug that made the effect bulge like a circle instead of tracking the
  bar's actual rounded-rect shape, and a second blur patched directly into the fragment shader
  so it applies before refraction/aberration rather than after. Toolbar text/icon color now
  reads real luminance off the rendered glass (sampling liquidGL's own canvas) and picks between
  a fixed dark-gray and near-white accordingly, so it stays legible over both light and dark
  page content.

## [1.12.5] (build 47) — 2026-08-08

### Changed
- **Automatic spelling correction turned on** in the main text box (was deliberately off since
  the box is mostly pasted text, and silently rewriting pasted words seemed worse than an
  unflagged typo). Reversed by request. Worth knowing: this also applies to pasted text, not
  just typed text, so an unusual word or name in something pasted in could get silently
  "corrected" into something else — same tradeoff macOS's own text boxes make everywhere else.

### Investigating
- **Spell-check's red underline isn't appearing at all**, even though continuous spell-checking
  is (and was already) explicitly enabled in code. Not yet root-caused — suspected but unconfirmed
  interaction with this app's layer-backed text rendering (used throughout for its dark theme),
  a known but obscure category of AppKit bug. Left open rather than guessed at.

## [1.12.4] (build 46) — 2026-08-08

### Fixed
- **Voice recording silently captured no audio.** The built app's `Info.plist` never declared
  `NSMicrophoneUsageDescription`, so macOS's TCC privacy framework never registered SonoScript
  as a microphone-requesting app at all — no permission prompt ever appeared, and the app never
  showed up under Settings > Privacy & Security > Microphone. `sounddevice`'s `InputStream`
  still opened without raising an exception, but macOS silently withheld the actual hardware
  feed from an unauthorized process, so every recording came back as pure silence and failed
  validation's "we didn't pick up any sound" check instead of surfacing a real permission error.
  Added the missing usage-description key so the OS properly prompts for (and can grant) mic
  access on first use.

## [1.12.3] (build 45) — 2026-08-05

### Changed
- **Re-recorded Manny's Chatterbox reference clip** (8.25s → 15.28s), aimed at the model's actual
  10-15 second usable conditioning window rather than the old clip's underlength one. Mixed
  evidence so far: a controlled 8-attempt batch on hard content scored 0/8 (worse than the old
  clip's 2/8 baseline), but casual fresh-generation testing since then has been clean. Shipping
  as a trial rather than a proven fix — Chatterbox's own per-attempt variance is wide enough that
  neither result is conclusive alone. Old clip kept as `manny_v1_backup.wav` for an easy revert;
  if this doesn't hold up, the next step is trying a less-quantized Chatterbox variant instead of
  another re-recording.

Also chased a reported generation stall — live-reproduced two fresh (non-cached) generations end
to end with full log/UI instrumentation and both completed and played back normally. Could not
force a repro. Likely the same no-CPU/no-error hang noted as unresolved in 1.12.2, now just more
visible if the new Manny clip happens to need more retries.

## [1.12.2] (build 44) — 2026-08-04

Investigated a Chatterbox/Sesame reliability report using real generated audio and Whisper
transcription for ground truth rather than guessing at the cause. Four real, distinct issues
found; three fully fixed, one substantially mitigated but honestly not eliminated.

### Fixed
- **"4:37 a.m." became "4:37 a dot m."** `text_prep.py`'s filename-detection regex (built to
  turn "example.file" into "example dot file") was matching "a.m."/"p.m." too — a single letter,
  a dot, another single letter is exactly the same shape as a short file extension. Now excludes
  these two specific abbreviations by name rather than trying to generalize the regex further.
- **A silent gap after the last word of a chunk could pass verification undetected.** The
  internal-gap check added in 1.11.5 only ever compared consecutive *recognized words* against
  each other — it had no way to notice dead air sitting *after* the last word, before the
  chunk's own audio actually ends. Confirmed directly against a real generated clip with a
  multi-second silent gap after its last recognized word, and it's exactly this blind spot,
  since there's no following word for the old check to compare against. Now also compares the
  last recognized word's end time against the audio's own trimmed length.
- **Chatterbox was silently capped at ~32 seconds of audio per chunk, no matter what.** The real
  bug behind the truncation: `main.py` was calling `engine.generate(..., max_new_tokens=)`
  — but that's the parameter name for the older, non-Turbo Chatterbox model. The Turbo model
  this app actually loads uses `max_tokens` instead, defaults to 800, and has no
  `max_new_tokens` parameter at all — so the app's own length-scaling logic (added in 1.11.4
  specifically to prevent this exact failure mode) was silently swallowed into an unused keyword
  argument the whole time, doing nothing. Confirmed directly: two different-length chunks both
  produced an identical "800/800" token count and identical 32.2-second audio, with the longer
  one cut off mid-sentence as a direct result. Now passes the correct parameter name, scaled to
  the model's own real token semantics rather than the other model's.

### Changed
- **Chatterbox's retry budget raised from 3 attempts to 5** (`CHATTERBOX_MAX_RETRIES` 2→4,
  matching Sesame's existing budget). Measured directly, not assumed: an isolated 8-trial batch
  on hard content passed only 2 times — a ~75% per-attempt failure rate — and
  strikingly bimodal, not a smooth quality gradient: every failure scored a complete,
  unrelated-to-the-source mismatch, every pass scored well under the threshold. That matches
  this model family's own documented "autoregressive collapse" pattern rather than ordinary
  noise. At the old 3-attempt budget, a ~75% failure rate means roughly 42% of the time *every*
  attempt fails and a garbled chunk plays anyway; 5 attempts brings that down to roughly 24% —
  a real, measured improvement, not a fix. Honest caveat, same shape as Sesame's own: this
  specific voice, on specifically hard content, can still exhaust every attempt. Retries make it
  less likely, not impossible — a genuine model reliability ceiling, not an app bug, alongside
  the already-documented foreign-word-pronunciation limitation.

Also investigated a real, separate symptom — the app going fully unresponsive (0% CPU, no
error, no crash) partway through a long document — but could not reproduce it across 20+
controlled test runs after the fixes above landed, including the exact sequence that first
surfaced it. Left honestly unresolved rather than claimed fixed; it may have been a downstream
consequence of the truncation bug interacting with something not yet understood, or a rarer
edge case this round of testing didn't happen to hit.

## [1.12.1] (build 43) — 2026-08-04

Two real, reported bugs, both about the same thing: opening Settings mid-session and coming
back. Root-caused with a live diagnostic trace (temporary print statements through the actual
generation pipeline, not guesses) before touching any code, then verified fixed the same way.

### Fixed
- **Opening Settings while a chunk was generating made it look like generation had silently
  stopped.** It hadn't — traced directly and confirmed the background worker thread, job queue,
  and generation pipeline are completely unaffected by which screen is showing. The real bug:
  `showMainScreen()` rebuilds a brand-new status label every time you return from Settings (same
  as it already does for the text view), and nothing re-announces "Generating..." on that new
  label if a job was already in flight before you left — so the label just comes back blank,
  even though the exact same job finishes normally moments later. Now tracks the last status
  text set and re-applies it after the rebuild.
- **Returning from Settings during playback made the read-along highlight jump around
  erratically.** `showMainScreen()` was clearing its saved timer reference with a bare
  reassignment instead of actually cancelling the real, still-scheduled `NSTimer` behind it — an
  `NSTimer` on the run loop stays alive independent of any Python reference to it. That orphaned
  timer kept firing in the background, on its own schedule, right alongside the fresh one
  `showMainScreen()` arms afterward — two independent chains fighting over the same shared
  state. Now properly invalidates the old timer before rebuilding.

## [1.12.0] (build 42) — 2026-08-04

### Added
- **Read-along word highlighting for Chatterbox and Sesame.** Previously only System voice
  could highlight the word currently being spoken as it played — Chatterbox and Sesame had no
  equivalent, since they generate audio up front rather than firing a live word-boundary
  callback the way System's AVSpeechSynthesizer does. Built on top of 1.11.4's content
  verification: every generated clip already gets transcribed back locally with word-level
  timestamps (originally just to check what was said); those timestamps are now mapped back
  onto the actual source text via word-level edit-distance alignment (`jiwer`, already a
  dependency for the CER check), and fed into the exact same event-driven highlight-timer
  system System voice already used, unchanged. Chatterbox's speed control (time-stretch, since
  it has no native rate parameter) rescales the timing to match; Sesame needs no rescale, since
  its speed is locked to 1.0x for unrelated reasons. Auto-scroll gets the same benefit
  automatically, since it was already driven off the same highlighted-word position. Falls
  back cleanly to the previous chunk-level behavior (no per-word highlight, chunk-level
  auto-scroll only) whenever the alignment isn't usable for a given chunk — a badly garbled
  attempt, or a rare case where normalization changes a word count — rather than showing a
  highlight that's more often wrong than right. Verified directly against real Chatterbox
  playback: the highlight tracked forward through real words in the correct order as the audio
  actually played, not just "doesn't crash."

## [1.11.5] (build 41) — 2026-08-04

Follow-up to 1.11.4's verification system, after a real regression report: a long,
multi-chunk Chatterbox story stalled repeatedly and went silent for the rest of the
recording. Diagnosed with real timing data and word-level audio analysis, not guesses —
two distinct, real issues, both addressed here.

### Fixed
- **Playback could fall behind generation and stall.** The app only ever prepared one chunk
  ahead, and only started once the current chunk began playing — fine when generation was
  fast, but the verification+retry system added in 1.11.4 can now legitimately take longer
  to resolve a hard chunk than that chunk takes to play. Prefetching now keeps up to 3 chunks
  queued ahead, and a chunk finishing generation immediately continues the chain instead of
  waiting for the next playback-start event — reclaiming idle worker time instead of wasting
  it. Confirmed via a precise timeline simulation using real measured generation times from
  the reported story: this meaningfully helps documents with a mix of easy and hard chunks,
  which is the common case. Honest caveat: for a chunk that's consistently, severely hard
  (confirmed separately to be a genuine model limitation, not random variance), no amount of
  earlier prefetching fully closes the gap — that needs the underlying generation problem
  solved, not just better scheduling.
- **Verification could miss a clip with the right words but a dead zone in the middle.**
  Confirmed directly: a real generated clip had a 25-second silent gap between two correctly
  recognized words. A transcript alone doesn't carry timing, so this could pass or
  under-penalize checks that only compare the words. Word-level timestamps are now checked
  for any internal gap longer than 4 seconds — well past any natural spoken pause — and
  treated as a failure worth retrying, the same as a straightforwardly wrong transcript.

## [1.11.4] (build 40) — 2026-08-04

The biggest reliability push so far. Neither Sesame nor Chatterbox can be trusted to
reliably say the right thing — both now get their output checked, not just timed.

### Added
- **Content verification for Sesame and Chatterbox** (`speech_verify.py`): every generated
  clip is transcribed back locally (bundled Whisper model, no network call) and compared
  against what it was supposed to say. A clip that doesn't match closely enough is
  regenerated automatically (varying attempts, same pass bar every time — an asymmetric
  "retries held to a stricter standard" bar was a real bug two other projects wrapping this
  same Chatterbox model shipped and had to fix, deliberately avoided here). If nothing passes
  within the retry cap, the least-bad attempt is kept and used rather than silently failing or
  blocking generation — this app already refuses to guess a fallback the user didn't ask for,
  and this is the same principle applied here. Design is based directly on two real,
  independently-maintained forks of the exact Chatterbox model this app uses, which already
  solved this same problem in production — several choices here exist specifically to avoid
  bugs those forks shipped and later fixed (stale retry seeds, pre-generating candidates
  before checking any of them, letting untrimmed silence trick the checker into inventing
  fake text). Applies only to the two on-device engines — cloud providers already handle
  their own quality control, and System voice can't hallucinate in the first place.
- Short text (under 5 words) gets one extra retry attempt automatically — a confirmed,
  unresolved weak spot for Chatterbox specifically (single words/short phrases reliably
  producing gibberish), and independently the hardest case for the new verification too.

### Fixed
- **Chatterbox silently truncating ordinary chunks.** Found directly while testing the
  verification retry loop: the library's own fixed default (1000 tokens) cut off a real,
  normal ~670-character chunk — well within this app's everyday chunk-size range — after only
  its first sentence, identically on every retry, since it wasn't random instability but a
  hard cap being hit. Now scales with text length, the same fix already applied to Sesame's
  own length cap.
- A literal quotation mark in the text made Chatterbox produce a random ~1.2 second
  non-speech "sigh" sound (a confirmed, unfixed upstream bug in this exact model build) —
  now stripped before generation, independent of the verification step above.

## [1.11.3] (build 39) — 2026-08-03

### Added
- **"Report a Problem"** (Settings > Support): a tester can send a diagnostic report in-app,
  no email client involved. Walks through a few guided questions, shows a review screen with
  the exact composed report before anything is sent, then submits it as a GitHub issue in a
  dedicated private repo — never the public source repo. Includes this session's
  provider/voice usage and generation timing, but deliberately never the raw text someone was
  reading, only its length. Verified end-to-end against the real API, not just read through:
  a real test submission created and was confirmed as an actual issue before shipping.

## [1.11.2] (build 38) — 2026-08-03

### Fixed
- **Poetry and other unpunctuated line breaks garbling or hallucinating Sesame output**, and
  taking far longer than expected to generate. Root cause: `normalize_paragraph_breaks` only
  ever recognized a blank-line gap as a paragraph break — a poem's line breaks (or a title
  glued directly onto its paragraph with no blank line between them) reached the model as
  literal, un-mapped `\n` characters. Confirmed directly: real generation on a test poem
  fragmented into 5-6 separate internal segments and took 12-19s, versus one clean pass in
  3-8s once fixed — reproduced twice, including once with a corrected reference-voice
  transcript. Fixed by treating a bare line break as a soft pause (comma) when the next line
  starts a new capitalized thought, while leaving a plain word-wrapped sentence (next line
  starts lowercase — a document's own line-wrap artifact, not a real break) untouched, exactly
  as before.
- **Sesame's generation quality check had no upper bound.** It already retried a too-slow
  (runaway) generation, but a too-fast, truncated/garbled one passed silently on the first
  attempt — confirmed directly: one generation at 49.4 chars/sec (more than double the
  established 15.2-20.5 chars/sec good range) sailed through with a Whisper transcription that
  didn't match the input at all. Added a matching upper bound so both failure directions retry.

### Added
- **Auto-scroll during playback.** The document now scrolls to keep the current reading
  position in view as playback advances — proactively, before it reaches the bottom of the
  visible area, rather than only after. Word-level precision for System voice; chunk-level for
  Chatterbox/Sesame, which don't have word timing yet.

## [1.11.1] (build 37) — 2026-08-03

### Fixed
- **Sesame playback cutting off the last syllable of a clip.** Confirmed via word-level Whisper
  transcription that the underlying generated/saved audio was already complete — the loss was
  happening in playback, not generation. Root cause is a known AVAudioPlayer/CoreAudio quirk
  where the very tail of a short buffer can get clipped during output flush, independent of
  what's actually in the file. Fixed by padding 300ms of trailing silence onto the buffer handed
  to the player (not the saved file on disk — History/Saved content is untouched) immediately
  before playback begins.
- **"SonoScript is damaged and can't be opened"** on first launch for anyone downloading via a
  browser. Root cause: the build script copies additional files (the `mlx` package, some
  dist-info) into the app bundle *after* py2app's own automatic ad-hoc code-signing pass, which
  silently invalidated the signature's sealed-resource manifest — confirmed directly via
  `spctl -a -vv` reporting "a sealed resource is missing or invalid." A broken signature is
  treated more harshly by Gatekeeper than no signature at all, producing the "damaged" dialog
  (no Settings override) instead of the milder "unidentified developer" dialog. Fixed by
  re-signing the app as the very last build step, after every post-py2app bundle change.

## [1.11.0] (build 36) — 2026-08-01

### Added
- **Live word-highlighting during playback**, System voice for now: the word currently being
  spoken is tracked and highlighted in real time as it plays. Word timing is captured during
  the same offline render that already produces the WAV bytes — not a separate muted
  synthesizer pass, which was the original plan but was directly measured to drift out of
  sync with the real audible pass by up to ~400ms per word. Each word's own highlight is
  scheduled individually against its exact captured start time (event-driven), rather than
  polled on a fixed interval, so a word's highlight moment can never fall through the cracks
  between two polls no matter how short that word is. A word whose text gets rewritten by the
  TTS sanitizer before reaching the synthesizer (a colon or parenthesis becomes a comma) still
  matches and highlights correctly via a punctuation-tolerant fallback. The highlight fires
  120ms ahead of the word's actual audio onset — closer to how a reader's eyes move ahead of
  what's currently being spoken than an exact-sync timestamp would feel.
- **Personalization settings**: choose the highlight style (Highlight, Bold, Underline, Text
  Color, or Off), shape (Rounded or Pill), color, and word-to-word animation (Snap or Slide),
  with a live preview.
- **Volume control** on the playback bar.
- **Permanent save-location**: promote any Recordings entry to a permanent folder via
  right-click, with the folder itself chosen in Settings > File Location. Deleting a
  recording now asks for confirmation first, and each entry's metadata sidecar is hidden from
  Finder (dot-prefixed) so the folder reads as a clean list of recordings.
- **Recordings screen**: History and Saved are now one screen with an animated sliding pill
  tab selector, instead of two separate screens.

### Fixed
- A highlighted word could stay visually bold permanently instead of clearing once the next
  word started — traced to `NSTextView.font()` reflecting the font at the current
  selection/insertion point rather than being a stable reference to the view's base font;
  once any word was bolded, every later "revert to normal" call was silently re-applying bold
  instead of clearing it.
- The highlight pill rendered in front of the text instead of behind it. A layer-backed
  view's sublayers always composite on top of that view's own drawn content, so the overlay
  had to move to a real ancestor elsewhere in the view hierarchy instead of being parented to
  the text view's own layer.
- A word landing exactly at a line-wrap point (most often one with a hyphen, which text
  layout treats as a valid break point) could highlight two entire lines at once instead of
  just that word.
- Bold highlighting visibly reflowed surrounding text as the highlighted word changed — a
  true bold font has measurably wider glyphs than regular weight at the same size. Replaced
  with a stroke-width-based faux bold that keeps the exact same layout width.
- Per-provider voice memory (remembering the last voice picked for System vs. Chatterbox vs.
  Sesame independently) could silently reset to that provider's first voice: any lookup miss
  was being persisted as if it were the real remembered choice, permanently overwriting it.
- Using the position scrubber started playback even if it had been stopped beforehand —
  scrubbing/skipping now only resumes playback if it was already playing.

### Changed
- The Italic highlight style was removed; visual quality wasn't good enough to keep.

## [1.10.0] (build 35) — 2026-07-30

### Added
- **Sesame voice cloning is fully usable now**, completing the tier gated behind the license
  key in 1.9.0: real cloned speech generation for the three built-in voices (Sadie, Manny,
  Ben — Ben is the developer's own reference clip, labeled that way everywhere rather than a
  real name); a recording flow ("Create your own..." in the voice menu) that walks through
  reading a fixed script with a live level meter, validates the take (too short, silent,
  clipped), and lets you listen back and name it before saving; and a dedicated Manage Voices
  screen (from the wordmark menu whenever Sesame is active) to rename or delete any custom
  voice, with a two-step confirm before deleting.
- **"Generating..." now shows real animated progress** — a looping shimmer sweep across the
  status text and along the text card's border — instead of a static message with no
  indication anything is happening.
- Backend for a "recent generations" cache: a completed generation is saved automatically
  once playback reaches its natural end (an interrupted take never surfaces). No browsing UI
  yet — that shipped in 1.11.0 as the Recordings screen.

### Changed
- Voice/menu dropdowns (System's voice list, ElevenLabs' library, any long list): the
  selected row now gets its own persistent highlight, visible even at rest — previously the
  only difference was slightly brighter text, easy to miss and identical to a hovered row.
  Reopening a dropdown also now centers on the current selection instead of always
  scrolling back to the top.
- Sesame's main-screen label changed from "Cloned voices" to "Premium offline voices"; the
  welcome screen's key-entry placeholder changed from "Sesame license key" to "SonoScript
  license key".
- **Kokoro removed entirely.** It was fully replaced by Chatterbox back in 1.8.0, but its
  ~121MB model/dictionary bundle and Kokoro-only dependencies were still being packaged with
  every build. The one-time config migration (any install still holding an old "Kokoro"
  provider value switches to Chatterbox automatically) stays in place.
- Closing the app window no longer quits the app — only Quit does, matching standard Mac
  behavior, and letting an in-progress background save actually finish instead of being cut
  off.

### Fixed
- The status label could stay red after an error even once the error condition had cleared.
- Saving a recorded voice could fail silently — the error message was rendering behind the
  still-open recording card. A successful save now also shows a brief confirmation instead
  of just closing without any feedback.
- The recording flow blocked closing the whole flow (backdrop-click/Esc) even after the
  microphone had already stopped recording — now only blocks while a real mic stream is
  actually open.
- Two bugs in the recording flow's own Cancel/Save cleanup could leave the flow stuck open
  if closing the mic stream or the preview player raised an error.
- Renaming a voice in Manage Voices could fail silently — the code tried to attach data
  directly to a native macOS text field, which isn't allowed for a plain (non-custom)
  control in this app's framework, the same class of bug already documented elsewhere in
  this codebase.
- Long documents (many chunks) could crash mid-read with "no Stream(gpu, 2) in current
  thread" — every chunk used to spawn its own new thread into the local voice engine,
  eventually exceeding a per-thread resource limit; now one persistent worker thread
  handles every chunk instead.

## [1.9.0] (build 34) — 2026-07-30

### Added
- First piece of the **Sesame** voice-cloning tier: a new "Sesame" option on the welcome
  screen, gated by an offline, no-account license key (Ed25519 signature, verified entirely
  on-device — no network call, matching this app's offline philosophy everywhere else). A
  valid key unlocks the tier; the actual voice-cloning UI (recording flow, voice library,
  "create your own") isn't built yet, so Sesame currently shows a clear "coming in a future
  update" placeholder rather than a live feature.

## [1.8.2] (build 33) — 2026-07-30

### Fixed
- Chatterbox could occasionally produce a "runaway" generation 2-3x longer than normal for a
  given chunk of text — garbled, breathy, static-sounding audio, made worse once it then went
  through the speed/time-stretch step. Confirmed via repeated direct trials to be a real,
  intermittent instability in the model itself (it reproduced even with Nova, which has no
  reference clip at all), not something tied to one specific voice. Every chunk's generated
  duration is now sanity-checked against its text length, and a chunk that comes back
  implausibly slow is automatically regenerated (up to 2 extra attempts) before being used.

## [1.8.1] (build 32) — 2026-07-30

### Changed
- Speed control now works for Chatterbox too, despite the model having no native speed
  parameter of its own (unlike Kokoro/ElevenLabs/OpenAI) — extends the existing
  Praat/parselmouth pitch-shift integration to also drive duration via its `DurationTier`,
  so no new dependency was needed. Chatterbox's most natural pace sits slightly slower than
  the other providers', so its speed menu is relabeled to match: what's actually 0.8x plays
  as the default and shows as "1.0x," with every other option shifted the same way
  (1.0x real shows as "1.25x," and so on). Every other provider's speed menu is unaffected.

## [1.8.0] (build 31) — 2026-07-30

### Changed
- Kokoro replaced with **Chatterbox Turbo** (MLX) as the bundled, free, offline default
  voice engine — same "no account, no downloads" experience, but noticeably more natural
  and correctly handles heteronyms (e.g. "wind" the weather vs. "wind" a clock) that Kokoro
  got wrong. Three voices for now: Nova (the model's own built-in voice), Sadie, and Manny
  — more planned. Existing installs migrate automatically on next launch; no user action
  needed.
- Speed control was temporarily unavailable for Chatterbox specifically in this release —
  the model has no native speed parameter (unlike Kokoro/ElevenLabs/OpenAI). Fixed in 1.8.1,
  immediately after.
- Settings landed only after direct listening tests, same process used for Puck's pitch
  fix: sentence-level splitting and default sampling both produced random upward
  inflections and unstable pacing on some voices/sentences — fixed by generating each
  chunk as one continuous pass (no per-sentence split) at a low, steady sampling
  temperature.

## [1.7.4] (build 30) — 2026-07-29

### Fixed
- Pasted text copied from certain PDFs (screenwriting software and some
  word processors export this way) could contain real IPA/phonetic
  characters standing in for ordinary Latin letters — visually identical
  glyphs, but a different Unicode character, from a broken font-subset
  cmap table in the PDF itself. Kokoro's espeak-ng backend doesn't
  recognize these as belonging to any alphabet and fell back to reading
  the character's own hex code point digit-by-digit: "ɑ" (U+0251, Latin
  Alpha, substituting for a plain "a") came out mid-sentence as "letter
  two five one" (hex 0251). Reproduced directly against the bundled
  espeak backend, then confirmed the fix (mapping the character back to
  "a" before phonemizing) produces byte-identical phonemes to clean text.
  A small table of these confusable letters (currently ɑ, ɡ, ı) is now
  normalized back to plain ASCII in sanitize_for_speech, ahead of every
  provider.

## [1.7.3] (build 29) — 2026-07-29

### Added
- The welcome screen's provider pills (System/Kokoro/ElevenLabs/OpenAI/
  Other) now live in a fixed-width strip that scrolls horizontally
  instead of forcing the whole window to grow to fit every pill. Adding
  "Other" had pushed the row past a comfortable width, so the window's
  centered layout column grew to match — which read as "the window got
  wider," even though the window's own size never changed in code; it
  had just started overflowing it. Same edge-fade affordance as the
  scrollable dropdowns (see 1.7.2), rotated 90 degrees: pills dissolve
  toward whichever side has more scrolled off, fully opaque at the true
  left/right end of the list.
- Re-opening this screen with an off-strip provider already selected
  (e.g. "Other") scrolls it into view automatically instead of leaving it
  hidden with no indication it's there.

## [1.7.2] (build 28) — 2026-07-29

### Added
- Dropdown rows now fade out approaching whichever edge still has more
  hidden content (same idea as many chat-app scroll views), gone entirely at
  the actual top/bottom of the list — shrinking smoothly to nothing as you
  approach the true edge rather than snapping off. Only shows up on
  dropdowns long enough to scroll (the 28-voice Kokoro list, long
  ElevenLabs voice lists).
- Implemented as a real alpha mask on the scroll view's own layer, not a
  colored gradient drawn on top of the rows. First attempt used a colored
  overlay (even color-matched to a real screenshot sample, RGB
  0.29/0.29/0.29) but the panel's background is a live translucent blur
  (NSVisualEffectView), not a flat color — any painted overlay still read
  as a visible patch sitting on top rather than rows genuinely dissolving
  into the real backdrop behind them. A mask reveals whatever's actually
  there instead.
- Dropdown internal top/bottom padding tightened (10pt -> 6pt, then -> 3pt)
  so a scrolled-to-the-edge row's fading text sits close to the panel's
  actual border instead of leaving a gap of blank padding between the fade
  and the edge — reinforcing "there's more this way" rather than
  undercutting it.
- Dropdown visible height now snaps down to a whole number of rows instead
  of an arbitrary fixed cutoff, so the row nearest a scroll boundary is
  always complete. Previously the cutoff could land mid-row, leaving a
  sliced sliver of a name at the edge that was also deep in the steepest
  part of the fade — reading as an empty gap rather than a name. A
  complete row that simply fades via opacity reads correctly at both ends.
- Fade zone deepened (48pt -> 72pt) so more of the dropdown is visibly
  involved in the dissolve, closer to the strength of a typical chat-app
  scroll shadow rather than a single-row hint.

### Fixed (during development, before landing)
- The gradient's two edges were mapped backwards twice over: first the
  in-strip direction (an early colored-overlay attempt), then — after
  switching to the mask approach — location 0.0 vs 1.0 on the mask itself,
  since NSScrollView's layer has a flipped coordinate space, opposite of
  a plain content layer's usual convention. Confirmed by testing rather
  than re-deriving blind a second time.
- The initial (scrolled-to-top) mask state didn't visually apply until
  triggered by an actual scroll gesture — CALayer property changes are
  implicitly animated by default, so the very first color update landed
  in a transaction that didn't commit until later. Now wrapped in an
  explicit disabled-actions transaction, same pattern already used in
  ScrubberView for instant (non-animated) updates.

## [1.7.1] (build 27) — 2026-07-29

### Fixed
- Any dropdown long enough to scroll (the 28-voice Kokoro list, long
  ElevenLabs voice lists) opened pre-scrolled to its last few rows instead
  of the top. Rows are laid out first-item-at-top/last-item-at-bottom, but
  NSScrollView's clip view defaults its visible origin to (0, 0) — exactly
  the bottom in that layout. Now explicitly scrolled to the top when the
  dropdown opens.

## [1.7.0] (build 26) — 2026-07-29

### Added
- Speed range widened to 0.5x–1.5x (from 0.7x–1.2x), with fewer options in
  the picker (0.5x, 0.8x, 1.0x, 1.25x, 1.5x instead of every 0.1x step) so
  the list stays short. Verified against each provider's actual API limits
  first — ElevenLabs' REST API and OpenAI's TTS API both support 0.25–4.0
  (an earlier assumption of a stricter 0.7–1.2 ElevenLabs cap turned out to
  be their separate Agents Platform's limit, not the REST API this app
  actually calls), and Kokoro supports 0.5–2.0 — so no per-provider
  clamping was needed, just a wider list.
- Graduated Kokoro chunk sizing: the first chunk now starts smaller than
  before and grows over the next couple of chunks up to the normal size,
  instead of every chunk being the same reduced size for the whole
  document. Local inference has enough of a speed cushion that a chunk can
  grow by more than 2x the previous one's size and still finish generating
  well within its predecessor's playback time, so this keeps the fast-
  first-sound benefit without fragmenting a long document into far more
  chunks than it needs.
- Per-voice pitch defaults: Puck now gets a built-in -1.5 semitone pitch
  correction automatically. Getting there took several real attempts, not
  a single clean implementation — worth recording since the failed
  attempts are exactly what a search for "how do I pitch shift speech in
  Python" turns up first:
  - A hand-rolled STFT phase vocoder got the math objectively right
    (verified against a sine wave — within ~2% of the target frequency)
    but sounded like "multiple layers slightly detuned, creating width" on
    real speech — the well-known phase-vocoder "chorus" artifact, caused
    by each frequency bin's phase drifting independently of the others.
  - Adding phase locking (Laroche & Dolson's technique — lock each bin's
    phase to its nearest spectral peak instead of letting every bin
    propagate independently) measurably reduced it, confirmed by ear, but
    didn't eliminate it.
  - Comparing against Spotify's pedalboard (Rubber Band library) — a
    single self-contained wheel, no dependency cascade — sounded better
    but still not natural, and highlighted a real gap: neither approach
    was touching formants (the vocal-tract resonances that make a voice
    sound like itself), which a naive shift drags along with the pitch —
    the classic reason a pitched voice reads as artificial.
  - Added formant preservation via cepstral-envelope correction (extract
    the original's spectral envelope, re-impose it on the shifted audio)
    on top of both the phase-locked vocoder and pedalboard. Better, but
    -2 semitones with either one still sounded robotic — this turned out
    to be an inherent ceiling of frame-based (STFT) approaches for speech
    specifically, not a bug to keep tuning away: even current (2026)
    research on WORLD-vocoder pitch shifting for speech notes the same
    formant-artifact ceiling and is exploring diffusion-based restoration
    to get past it.
  - Switched to TD-PSOLA instead — pitch-synchronous processing (aligned
    to the actual detected pitch periods, not a fixed analysis-frame
    grid), via the `psola` package (Praat/parselmouth underneath — the
    actual tool phoneticians use for this). Also correctly leaves
    unvoiced sounds (consonants, breath) untouched rather than pitch-
    shifting them too, since Praat's own pitch tracker already marks them
    as unvoiced. This is the one that actually sounded natural, confirmed
    by ear at -1/-1.5/-2 semitones.

### Fixed
- Packaging psola/parselmouth surfaced two new py2app gaps beyond the
  phonemizer ones from 1.6.0: `parselmouth` is a single-file compiled
  extension, not a package, and py2app's modulegraph mishandled that
  shape — alongside correctly placing the real thing in `lib-dynload/`,
  it ALSO wrote a broken duplicate at `lib/python3.12/parselmouth.py`
  containing the raw compiled binary, which shadowed the working one and
  crashed on import ("source code string cannot contain null bytes").
  `setup.py` now deletes that duplicate automatically after every build.
  Separately, `psola` depends on `soundfile` (removed from this project
  back in 1.6.0 since Kokoro itself doesn't need it) and its cffi-
  generated `_soundfile` companion module, both now bundled again.

## [1.6.1] (build 25) — 2026-07-28

### Added
- Text preprocessing pass, applied before any provider gets the text (so
  it covers System/Kokoro/ElevenLabs/OpenAI alike): filenames with
  extensions ("example.file", "report.pdf") no longer read the period as
  a sentence-ending pause — a period with no surrounding whitespace is
  reworded to "dot" instead. A small pronunciation-override dictionary
  fixes specific words that were coming out wrong or with a pause around
  an internal capital letter ("GitHub" -> "git hub", "SonoScript" -> "Sono
  Script") — picked from several rendered candidates by ear, not guessed.

### Known limitation
- "SonoScript"'s fix isn't complete: testing multiple phrasings showed the
  odd rise it had was actually sentence-position prosody (same word
  mid-sentence sounds fine; landing as the very last word before a period
  can still occasionally rise). No exposed parameter controls this — same
  root cause as Puck's sentence-initial pitch rise noted on the roadmap.

## [1.6.0] (build 24) — 2026-07-28

### Added
- A "Kokoro" voice provider — a free, fully offline neural voice bundled
  directly in the app (no download step), noticeably more natural-sounding
  than System's built-in voices. Adds ~270MB to the install size (28
  American/British English voices + the ONNX model + a bundled espeak-ng
  for phonemization) in exchange for zero setup and no network dependency,
  same as System. Uses a smaller chunk size than the other providers
  specifically for Kokoro (~180 chars vs. 600) — local inference runs at
  roughly half realtime, so the normal chunk size would mean 10+ seconds of
  dead air before the first chunk starts; every chunk after the first
  generates in the background well within its predecessor's playback time,
  so this only affects the very first wait, not overall responsiveness.

### Fixed
- Worked around a real bug in kokoro-onnx 0.5.0 (current latest) that made
  every generation call fail outright for this model's export layout: the
  library builds its "speed" input as int32 when the model requires
  float32, and separately leaves the raw model output un-flattened, which
  silently produces a near-empty result instead of raising. Patched just
  the one broken internal method rather than reimplementing the rest of
  its (correct) tokenizing/batching/trimming pipeline.
- Packaging the app with Kokoro included surfaced a chain of dependencies
  py2app's own dependency scan didn't catch: phonemizer's package
  `__init__` unconditionally imports all four of its backends (espeak,
  mbrola, festival, segments) just to build one lookup table, even though
  this app only ever uses espeak. The unused two backends (festival,
  segments) pull in a large, otherwise pointless dependency tree of their
  own — bypassed by pre-registering stub modules for them before
  kokoro-onnx is ever imported, so their dependencies never need bundling
  at all. Separately, a couple of packages read their own version via
  `importlib.metadata` at import time, which only works if their
  `.dist-info` folder is present alongside them — py2app's "packages"
  option doesn't copy that by default, so `setup.py` now copies each
  bundled package's `.dist-info` in as a build step.

## [1.5.0] (build 23) — 2026-07-28

### Added
- A free "System" voice provider using the Mac's built-in text-to-speech
  (`AVSpeechSynthesizer`), pre-selected by default — no API key, no account,
  no network required, so the app is fully usable the moment it's installed.
  Renders through the same chunked-generation/prefetch/scrubber/cache
  pipeline as every other provider, just with on-device synthesis standing
  in for the network request.
- System voice names are now readable ("Samantha (English US, Enhanced)")
  instead of a raw language tag ("Samantha (en-US)"), and include the
  voice's quality tier (Standard/Enhanced/Premium) so it's obvious upfront
  which voices sound natural versus dated.
- The System voice list drops the old novelty/sound-effect voices (Zarvox,
  Trinoids, Bells, Organ, Boing, Bahh, and others hiding behind ordinary
  names like Albert, Fred, Kathy, Ralph) — not real reading voices, just
  clutter for what this app is for.

### Fixed
- Pressing Play again right after a read finished on its own regenerated
  the audio from scratch instead of just replaying it — reaching the end
  was wiping the same cache that scrubbing-back already knew how to reuse.
  Now only the "currently playing" state resets; the generated chunks stick
  around until the text actually changes.
- The welcome screen's provider pills (System/ElevenLabs/OpenAI/Other) were
  clipped a couple points at each end of the row — adding the System pill
  pushed the row past a hardcoded 340pt column width. The column now sizes
  itself to whatever the pills actually need, so this doesn't recur when a
  Kokoro pill is added later either.
- Two crashes on a genuine first launch (empty config, never seen the main
  screen before): the welcome screen unconditionally calls the playback
  cleanup path on entry, which referenced `self.scrubber` and then
  `self.status_label` — both created only inside the main screen, which a
  true first launch has never built yet. Never surfaced before now because
  every prior test config already had a provider configured, so the app
  always skipped straight past the welcome screen.

## [1.4.4] (build 22) — 2026-07-28

### Fixed
- Scrubber fill (played portion) was still reading as plain white even at a
  requested alpha of 0.87 — this app's actual rendering is substantially
  brighter than the raw alpha value suggests (measured empirically: alpha
  0.65 renders at ~193/255, not the ~166/255 naive math would predict).
  Recalibrated against real measured pixel values instead of guessing again
  — now a clearly solid gray (alpha 0.40, measuring ~156/255) rather than
  something indistinguishable from white.

## [1.4.3] (build 21) — 2026-07-28

### Fixed
- The scrubber thumb no longer clips when it grows past the view's own
  bounds at either end of the track (hover/press right at an extreme). The
  actual cause: an `NSView`'s auto-created backing layer defaults
  `masksToBounds` to YES, unlike a bare `CALayer` — the same gotcha the
  About/Update card's shadow hit earlier this project, just not applied
  here yet. Explicitly disabled it.

### Changed
- Track (unplayed) brightens slightly on hover again, but capped well below
  the fill's brightness so it can never approach/match it — still reads
  clearly as "less prominent than played," just not perfectly static.
- Fill (played) dialed back from a near-white 0.92 to 0.87 — a genuinely
  slight brighten over the original, not a jump to plain white.

## [1.4.2] (build 20) — 2026-07-28

### Changed
- Scrubber thumb now sits flush with the actual ends of the track when
  pushed all the way to either side — the previous edge padding (added to
  stop the pressed/grown size from clipping) left a visible gap at rest.
  Travel is now inset by the RESTING size instead, so it's flush by
  default; the thumb harmlessly overhangs the view's own bounds by a couple
  points when grown at an extreme, since sublayers aren't clipped there and
  there's clearance before the time labels either side.
- Track (unplayed, right of the thumb) is darker and no longer brightens on
  hover; fill (played, left of the thumb) is a touch brighter. The contrast
  between the two stays constant regardless of hover/press state, so it
  always reads clearly as "already heard" vs. "coming up" — only the thumb
  itself grows/brightens now.
- Larger invisible grab area around the thumb for easier clicking/dragging.
- The play button's circle is a few points smaller.

## [1.4.1] (build 19) — 2026-07-28

### Changed
- Scrubber thumb interaction reworked: clicking directly on the thumb now
  grabs it in place and drags relative to where it already is, instead of
  snapping it to the exact pixel clicked (which was jarring if you clicked
  even slightly off-center). Clicking elsewhere on the track still jumps
  straight there, same as before.
- The thumb now has three distinct, animated size states — idle (smaller
  than before), hover (grows a little, and the remaining/unplayed portion
  of the track brightens), and pressed (grows further to show it's being
  held) — instead of a single instant idle/pressed snap. The thumb's travel
  range now reserves extra margin at both ends sized to its LARGEST
  (pressed) state plus some padding, so growing while already at either
  extreme of the track no longer clips it in half.

## [1.4.0] (build 18) — 2026-07-28

### Added
- The speed picker, voice picker, and app (wordmark) menu now fade in and
  out — quick (0.12s in, 0.1s out), plain opacity only, no blur-in or
  backdrop dimming like the About/Update cards use, since these are quick
  contextual menus rather than modal overlays. Replacing an already-open
  menu with a different one (e.g. clicking the voice picker while the speed
  picker is open) still swaps instantly, with no fade — only an actual
  dismissal (selecting a row, clicking outside, Escape) fades.

## [1.3.2] (build 17) — 2026-07-28

### Fixed
- Cmd-Z / Cmd-Shift-Z (undo/redo) now actually work in the text box. The text
  view's `allowsUndo` defaults to NO for a plain (non-field-editor)
  `NSTextView` and was never explicitly enabled, so undo was silently
  disabled from the start regardless of menu wiring or undo-manager
  resolution — it wasn't recording any edits to undo in the first place.

## [1.3.1] (build 16) — 2026-07-28

### Added
- Generated chunk audio is now cached for the rest of the playback session:
  scrubbing back to something you've already heard replays the exact same
  audio (same bytes, same real duration — no drift from a fresh regeneration
  landing slightly differently) instead of re-requesting it, and doesn't
  spend a fresh API call on content already generated. Changing voice or
  speed mid-read correctly invalidates the cache and duration estimates
  going forward, rather than serving stale audio in the old voice.
- Pressed/held visual feedback on the scrubber thumb (it grows and
  brightens while dragging), matching the hover/press feedback used
  elsewhere in the app.

### Fixed
- The scrubber thumb no longer gets clipped in half at either end of the
  track — its travel range wasn't inset by its own radius, so at 0% or 100%
  half the circle extended past the view's edge and got cut off.

## [1.3.0] (build 15) — 2026-07-28

### Added
- A real scrubber for the whole document, not just the currently loaded chunk
  — click-to-jump and drag-to-scrub, with elapsed/remaining time labels. Total
  duration is estimated from the actual chars-per-second of whatever chunks
  have been generated so far, refined as more come in, since generating every
  chunk up front to know an exact total would defeat the point of chunking.
  Jumping to a point that hasn't been generated yet fetches just that one
  chunk on demand rather than the whole document.
- Skip forward/back (±15s) now cross chunk boundaries using the same virtual
  timeline as the scrubber, instead of clamping at the edge of whichever
  chunk happens to be currently loaded.

### Fixed
- Dragging the scrubber no longer drags the whole app window. The window is
  movable-by-background for the empty-space-drag convenience elsewhere in the
  app, but a plain `NSView` (unlike `NSButton`/`NSControl`) defaults to
  allowing that even when it has its own mouse handling — needed an explicit
  `mouseDownCanMoveWindow` override.

## [1.2.0] (build 14) — 2026-07-28

### Added
- Chunked, pipelined speech generation: long text is split into ~600-character
  chunks on sentence boundaries and generated one chunk ahead of playback, so
  audio starts within a couple seconds regardless of document length instead
  of waiting for (or outright failing on) one giant request. This fixes both
  a very long paste silently failing outright (providers reject requests past
  their per-request character limit) and a moderately long one taking a
  minute or more of dead silence before anything played.

### Fixed
- A background chunk failing mid-read no longer risks cutting off the chunk
  that's still actively playing — the currently playing audio now finishes
  naturally and the read stops cleanly afterward, rather than the failure
  handler dropping the only reference to the in-flight AVAudioPlayer.

## [1.1.0] (build 13) — 2026-07-28

### Added
- Per-provider API key management: switching providers remembers each
  provider's key independently, and the field pre-fills on load/switch so
  accidentally opening "Set API Key" never loses or requires re-entering a key.
- Cancel button + Escape shortcut on the welcome/API-key screen, to back out
  of "Set API Key" without losing the existing configuration.
- First-launch welcome intro: an animated splash (logo + "Welcome to
  SonoScript" + tagline) that holds briefly, then shrinks and fades while the
  compact hero and API setup controls slide/fade into place.
- Drag-to-move on the About and Update overlay cards — clicking any empty
  space on the card moves the whole app window.

### Changed
- Removed the circular disc behind the animated waveform logo (welcome hero
  and splash) so the bars float directly on the background.
- Inline API-key error messages now appear inside the key field itself
  (replacing the rejected key, with a fade in/out) instead of a separate
  label below Continue.
- All vertical spacing on the welcome screen (icon → title → tagline →
  caption → provider pills → key field → Continue → Cancel) and in the splash
  is now uniform.

### Fixed
- Dropdown menus no longer show square-corner artifacts and render with
  correctly balanced blur/transparency.
- Dropdown row hover highlighting no longer fires spuriously while scrolling.
- About/Update overlay card drop shadows render correctly against an
  NSVisualEffectView background (shadow was being silently zeroed out).
- Waveform logo animation motion is smoother and more even top-to-bottom.

## [1.0.0] (build 12)

- Initial release: text-to-speech via ElevenLabs/OpenAI, playback controls,
  voice/speed selection, self-update mechanism with checksum verification.
