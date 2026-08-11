# Roadmap

Where SonoScript is headed. Shipped work moves to [CHANGELOG.md](CHANGELOG.md);
this file tracks what's in progress, planned, or just an idea worth
considering later. Updated as priorities change — not a promise, a snapshot.

## In progress

- **Browser extension (Chrome/Edge, not Safari)**: read web pages aloud using SonoScript's own
  voice engine instead of a browser's built-in TTS, with a security model (local-only listener,
  per-launch token, Chrome/Edge Native Messaging as the only way to obtain it) verified working
  end to end. Automatic whole-page content detection and the in-page floating playback bar
  (play/pause/skip/scrubber/voice picker, modeled on Edge's own Read Aloud, SonoScript's own
  window no longer coming to the front) have both shipped. Still open: live word/phrase
  highlighting on the actual page as it's spoken (the toolbar knows the current word; matching
  it back to its exact spot in the live page DOM is the unbuilt part). Full technical design and
  current status: `browser_extension/DESIGN.md`.

## Planned

- **macOS media keys (system play/pause/skip)**: neither the app nor the browser toolbar
  responds to the Mac's dedicated media keys or Control Center's Now Playing widget right now —
  SonoScript doesn't hook into macOS's system media-key handling at all yet. This is real, new
  OS-integration work, not a bug fix: needs `MPRemoteCommandCenter` (to actually receive the key
  presses) and almost certainly `MPNowPlayingInfoCenter` (macOS generally only routes media keys
  to whichever app is currently registered as "Now Playing," so publishing metadata there isn't
  just a nice-to-have, it's likely required for the key presses to reach the app at all). Once
  wired up in the app, the browser toolbar's existing play/pause/skip commands (already built
  for the toolbar itself, see the browser extension section above) are the natural place to
  route the resulting key-press events through, so both surfaces stay in sync automatically
  rather than needing a second, separate implementation.
- **Extend word-highlight to cloned voices** (Chatterbox, Sesame) via forced
  alignment. The live word-highlighting engine (1.11.0) only works for
  System voice right now, since it relies on `AVSpeechSynthesizer`'s own
  word-boundary callbacks during synthesis — Chatterbox and Sesame have no
  equivalent, so this needs a real forced-alignment step (aligning the
  generated audio back to the source text) instead. Since Chatterbox is the
  default provider, the highlight feature currently has no effect for most
  actual use until this lands.
- **Drag-and-drop import** into the Recordings screen's Saved tab, from
  Finder. Deprioritized when the rest of the permanent save-location
  feature (1.11.0) shipped; the promote-from-History and File Location
  pieces landed without it.
- **Check `PulsingLabel`** for the same resize-frame-lock bug that was
  found and fixed in `ShimmerBorderView` — never actually investigated,
  just flagged as worth checking given the two widgets are similar.

## Ideas / maybe

Unscoped, not committed to — things worth considering as the app matures:

- **Save/resume position per document** — if you paste a long document,
  quit, and relaunch, pick back up where you left off instead of starting
  at 0:00.
- **Reading history / recent pastes** — a short list of recently-read
  texts so a long document doesn't have to be re-pasted after it's cleared.
- **Export to audio file** — render a whole document to an M4A/MP3 instead
  of only ever playing it live, for listening outside the app.
- **Keyboard shortcuts reference** — a small cheat-sheet (Cmd+Z undo is
  already fixed; spacebar play/pause, arrow-key skip, etc. could use a
  visible reference for a non-technical user).
- **Reading speed auto-ramp** — start a touch slower and speed up after a
  few seconds, the way some audiobook apps ease in.
- **Menu bar / background playback indicator** — since this is meant to run
  unattended for long documents, a menu bar item showing play/pause and
  progress without needing the main window focused.
- **Front-end pitch control** — per-voice pitch defaults (1.7.0) are a
  fixed backend value per voice, not a UI slider the way speed is. Could
  add one that adjusts on top of a voice's baseline, same relationship the
  speed dropdown already has to speed. No blocker now that pitch_shift.py
  exists — genuinely just an unbuilt UI element.
- **Real "Liquid Glass" material for the main app's own UI** — shipped on the browser toolbar via
  [liquidGL](https://github.com/naughtyduk/liquidGL) (a real WebGL library, vendored and patched
  at `browser_extension/liquidGL.js`), not a CSS/SVG-filter approximation — an earlier CSS
  `backdrop-filter` + SVG-displacement approach was fully replaced after it turned out unable to
  handle scroll correctly by construction. See `browser_extension/DESIGN.md`'s "Toolbar visual
  treatment" section for the full history and the patches made to liquidGL along the way. Same
  approach should carry over cleanly to the main app's own overlay cards if it's ever picked up
  there — nothing about it is browser-extension-specific.
- **Liquid Glass presets + master fade slider**: named presets ("Liquid Glass" → "Opaque" →
  "Solid") for the toolbar's glass effect, plus a single master slider that fades continuously
  between adjacent presets, instead of requiring the full ~9-parameter tuning panel (still in
  `toolbar.js`, currently disabled via `SONOSCRIPT_GLASS_TUNING = false`) every time the overall
  "how much glass" feel needs adjusting. Not started — open question worth resolving first: does
  "Solid" mean dialing every parameter toward an opaque-looking limit while the WebGL lens keeps
  running, or swapping to a plain CSS background for that state (cheaper, but can't fade smoothly
  back out via the master slider without a visible re-init)?
- **Puck's sentence-initial pitch rise** — separate from the flat -1.5
  semitone correction shipped in 1.7.0 (which lowers the whole voice
  evenly). The rise is a *dynamic* prosody pattern baked into specific
  words at specific positions, not a static offset — pitch_shift.py has no
  way to selectively target that, and there's no known parameter for it
  either (see CHANGELOG.md 1.6.1's SonoScript investigation, same class of
  problem). Still unaddressed.

## Bigger directions (exploratory, not scoped)

Two larger ideas that would each be real, separate efforts rather than incremental features —
not committed to, worth researching further before any real scoping:

- **Camera-based document capture**: point a phone camera at printed text (a label, a letter, a
  menu), extract the text via OCR, optionally translate it, then read it aloud — aimed at low
  vision and non-native-English readers, not a blind-accessibility tool. This would be a new
  product surface (most plausibly iOS, a from-scratch app) rather than a SonoScript-the-Mac-app
  feature. Two real findings so far: (1) the Swift port of the same on-device speech framework
  SonoScript already uses on Mac (MLX) exists and supports iOS, but Chatterbox specifically isn't
  one of its supported models yet — voice quality parity with the Mac app isn't a given without
  real work; (2) multi-column layouts and messy source text are handled much better by a modern
  vision-capable AI model reading the page directly than by older rule-based OCR heuristics, at
  the cost of needing network access and a real per-call cost (metering/subscription needed to
  keep this bounded).
- **PDF support within the browser extension** (see "In progress" above) — deliberately deferred
  until the plain-page experience is solid. Real, currently-unfilled niche: no existing tool
  combines genuinely good voices with reliable word-position tracking on PDFs specifically
  (confirmed: even paid competitors with better voices than browsers' built-in ones have
  long-standing, unresolved PDF tracking bugs). Chrome's built-in PDF viewer exposes almost no
  scripting access to extensions, so extracting the text client-side is genuinely constrained to
  an undocumented internal API or bundling PDF.js. A third option sidesteps the browser side
  entirely: hand the PDF's bytes to the existing Python bridge and extract text server-side with
  PDFKit/Quartz — already a bundled dependency (main.py already imports Quartz;
  pyobjc-framework-Quartz is already in requirements.txt) — rather than adding any new
  dependency or fighting Chrome's extension sandboxing for something Python can already do.

## Shipped

See [CHANGELOG.md](CHANGELOG.md) for everything already released.
