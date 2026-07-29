# Changelog

All notable changes to SonoScript are tracked here, newest first. Versioning
follows [Semantic Versioning](https://semver.org) (MAJOR.MINOR.PATCH); the
build number increments once per release regardless of version bump.

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
  hidden content (same idea as Claude's own chat scroll), gone entirely at
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
  involved in the dissolve, closer to the strength of Claude's own chat
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
