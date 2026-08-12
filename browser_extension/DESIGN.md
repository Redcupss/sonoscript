# SonoScript Browser Extension — Technical Design

## Status

Working MVP, verified end to end on Chrome and Edge: select text on a page, right-click "Read
selection aloud," SonoScript reads it. First real usage surfaced two real gaps in the plan
(below), both being addressed before this goes further.

## Security architecture (built, verified)

- `browser_bridge.py`'s local listener binds to 127.0.0.1 only and requires a per-launch random
  token on every request.
- The token is minted by SonoScript on startup and written to a file only the current Mac user
  can read (`~/Library/Application Support/SonoScript/bridge_token`, `chmod 600`).
- The *only* way the extension can obtain that token is through Chrome/Edge Native Messaging —
  a channel ordinary web-page JavaScript is structurally incapable of reaching (not merely
  permission-gated; the API doesn't exist for page scripts at all). This closes off any
  malicious website trying to trigger playback or otherwise reach the local listener.
- `native_host.py` is a dependency-free script that reads the token file and hands it back over
  the native-messaging channel — and, if SonoScript isn't already running, launches it first and
  waits.
- Verified directly: unauthorized requests get rejected (403), authorized ones succeed and
  trigger real generation; the native host correctly implements the messaging protocol and
  returns the live, current token.

**Auto-launch, shipped — three real bugs found by actually testing it in real Chrome rather than
trusting the design, each one only surfacing once the previous fix was live.**

SonoScript no longer has to already be running. `native_host.py` checks whether the local
listener is actually accepting connections on its known port — not whether the token *file*
exists, which is never deleted on quit and would otherwise make a stale file from a previous
session look identical to a currently-running instance.

*Bug 1 — the launched app still stole focus.* The first version launched via `open -g -a
SonoScript --args --browser-launch`, relying on `main.py` checking `sys.argv` for
`--browser-launch` to know to suppress its own window. `open -g` only stops macOS from
activating/focusing the newly launched app; it can't reach into the app to suppress its own
window, so the whole scheme depended on `--args` reliably reaching `sys.argv` through Launch
Services and a frozen py2app bundle — untested, and it turned out not to work reliably in
practice. First fix: launch the real binary directly instead of going through `open` at all —
`native_host.py` resolves the app's actual installed path the same way Finder/Launch Services
would (`osascript -e 'POSIX path of (path to application "SonoScript")'` — confirmed directly
this ISN'T always `/Applications/SonoScript.app`; on the actual dev machine it's one directory
deeper), then `subprocess.Popen([binary_path, "--browser-launch"], ...)`. A plain fork+exec
delivers argv with zero ambiguity, and doesn't get the "activate/focus me" treatment a
Launch-Services-mediated launch can carry. This traded one real bug for another — see Bug 3.

*Bug 2 — the toolbar never appeared and the text never reached the app, even though the app
itself visibly launched.* The first version had `native_host.py` block for up to 20s, polling
until the freshly-launched app came up before ever replying to Chrome. Confirmed directly (real
user testing, not just reading Chrome's docs) that this doesn't work: native-messaging
connections have a real practical timeout well under 20s, and — separately — an MV3 extension
service worker can be suspended as idle while a single `sendNativeMessage` call just sits there
waiting, silently losing the whole request. Fixed by moving the wait to `background.js` instead:
`native_host.py` now always responds within a fraction of a second — a real token, a real error,
or `{launching: true}` — and `getTokenWithRetry()` retries every ~1s for up to ~20s as a
continuously-running async chain kicked off directly from the still-open
`contextMenus.onClicked` listener (which now `await`s it, rather than firing it and returning
immediately) rather than a `setTimeout` scheduled after that listener has already returned, which
risks the worker being suspended before it ever fires. Every individual native-messaging round
trip is short either way; only the *overall* wait is long, and it now lives somewhere actually
built to survive that.

*Bug 3 — a Gatekeeper block and a py2app launch-error dialog, only after Bug 1's fix shipped.*
Real testing surfaced macOS blocking a temp-extracted `llvmlite` `.dylib` ("Not Opened — Apple
could not verify...") immediately followed by a py2app launch-error dialog — on a launch that
never happens through a normal Finder/`open` path. Root cause: `subprocess.Popen` launching the
binary directly (Bug 1's fix) bypasses Launch Services entirely, so the child process inherits
`native_host.py`'s own environment — which is itself whatever restricted/unusual environment
Chrome hands to its own native-messaging host child process — rather than the clean, standard
user-session environment `open`/Launch Services normally sets up. `llvmlite` extracts a fresh
temp shared library on every launch; a different or restricted `TMPDIR` in that inherited
environment is the likely reason Gatekeeper flagged it here and not on a normal launch. Fixed by
going back to `open -g` for the actual launch (correct environment restored), while replacing the
unreliable `--args` argv signaling with a marker file instead: `native_host.py` writes
`~/Library/Application Support/SonoScript/pending_browser_launch` immediately before launching,
and `main.py` deletes (consumes) it at startup to learn "this launch came from the browser
extension, stay invisible." A file `native_host.py` directly controls the writing of has none of
argv's forwarding uncertainty through Launch Services and a frozen py2app bundle — this sidesteps
the Bug 1 problem and the Bug 3 problem at the same time, with `open -g` never having been the
actual source of either.

A separate launch marker file (mtime-based staleness, ~25s) stops the several rapid retries above
from each independently deciding "nothing's running yet" and triggering their own duplicate
launch.

Verified directly: real app-binary resolution against the actual installed app, the
already-running fast path against the real running app (real token, milliseconds), the
launching→retry→success and launching→timeout paths with simulated delayed startup, duplicate-launch
prevention across rapid repeated calls, the JS retry loop's four branches (immediate success,
retry-through-launching, real error stops immediately, `lastError` stops immediately) against a
mocked `chrome.runtime`, and — critically, after discovering mid-debug that testing had been
running against a stale, unrebuilt `.app` bundle the whole time (py2app bundles `main.py` at
build time; source edits don't reach an already-installed app until rebuilt) — the full real
chain against a freshly rebuilt and reinstalled app: marker write → `open -g` → app launch →
marker consumed by `main.py` → bridge starts → port live in ~3s → token returned in 2ms.

*Bug 4 — the window still appeared on a genuine cold-launch, despite the above all checking out.*
Real testing surfaced the window showing again even with the marker mechanism itself confirmed
working in isolation. Root cause: both `native_host.py` and `main.py` resolved the marker file's
path via `os.path.expanduser("~/...")`, which depends on the `HOME` environment variable —
and `native_host.py` is spawned by Chrome with a different, more restricted environment than
`main.py` gets from a normal Launch-Services launch (already the confirmed source of Bug 3's
Gatekeeper failure). If `HOME` ever differed between the two rather than just being unset (where
`expanduser`'s own fallback to the OS user database would still cover it), the two processes
could silently compute two different paths and never find each other, with no error raised on
either side. Fixed by having both sides resolve the real home directory via
`pwd.getpwuid(os.getuid()).pw_dir` instead — the OS user database directly, bypassing environment
variables entirely. Verified end-to-end afterward with the same live-process test used for Bug
3's fix: marker written → `open -g` → app launch → marker consumed → zero windows → frontmost
app unchanged → bridge port live.

## Content-detection strategy (revised after first real use)

Original plan (manual text selection) works mechanically but fails the actual usability goal:
real pages interleave photos/captions/ads/related-article links between paragraphs, so cleanly
dragging over "just the article" in one action is often impossible — and needing to think about
page layout at all runs against the entire point of the extension (see the Forbes screenshot
that surfaced this).

Revised plan:

1. **Default: automatic whole-page extraction** — built, using Mozilla's actual Readability.js
   (bundled unmodified, Apache-2.0) rather than a hand-rolled reimplementation, the same library
   behind Firefox's own Reader View. New "Read this page aloud" context-menu item (Chrome's
   `"page"` context, works without a selection); "Read selection aloud" stays as the manual
   override. Verified directly against the real Forbes article that surfaced the original
   complaint (twice — once with a static HTML fetch, then again against the actual live,
   fully-rendered page after the first pass missed something a static fetch structurally
   couldn't see). Three real issues found and fixed, not just assumed away:
   - A photo caption leaked through: the site wraps it in a real `<figure>` tag with
     meaningless auto-generated CSS class names instead of a `<figcaption>` or any descriptive
     naming (common on modern React/Next.js-built sites). Fixed by stripping all `<figure>`
     elements before parsing.
   - A "More From Forbes" related-articles block leaked through: confirmed directly it's nested
     INSIDE the same container as the real article body, not a separate sidebar — no DOM
     structure distinguishes them. Since this kind of block always sits at the true end of an
     article, truncating the extracted text at a short list of recognizable markers ("More From
     Forbes," "Related Articles," etc.) is safe and effective.
   - A cookie/subscription-gate notice leaked through mid-article in the user's real test, but
     did not reproduce in a fresh test session — likely conditional on cookie/paywall-metering
     state. Since it can appear mid-article (truncation isn't safe there), stripped via an exact
     match on its known text instead of guessing at a DOM rule for something that may not always
     even be present.
   - Readability picked the wrong section entirely on a hub/landing-style page (Forbes' "50 Over
     50" rankings page): the page's actual intro was ~2 sentences, while a page-footer
     "Methodology" section was one dense, eleven-sentence paragraph — by a wide margin the single
     densest block of prose anywhere on the page (category tiles and the "Related Coverage"
     module carry no paragraph text at all, just headline links). Readability scores candidate
     containers by text density, so it selected Methodology (and the adjacent, similarly
     text-heavy Credits block) over the real, much shorter intro.

     Took three real rounds to actually fix, each verified (or disproven) against the real page
     rather than assumed:
     1. First attempt: strip a matched heading and every DOM sibling that follows it. Shipped,
        then disproven by real testing — the wrong content still got read.
     2. Second attempt, after a code review caught the first version could also over-delete real
        content on a different DOM shape: bound that same sibling-walk to stop at the next
        heading element. Still shipped without checking the *actual* Forbes DOM — confirmed
        wrong again in real testing, still reading Credits.
     3. Only after getting the real saved page HTML (not a screenshot) was the actual cause
        clear: this is a React/CSS-Modules site, and it renders a section's heading and its real
        content as SEPARATE SIBLING elements under one shared wrapper —
        `<section><div class="Title..."><h2>credits</h2></div><div class="RowContainer...">
        ...the real rows...</div></section>` — not a heading directly followed by its own
        content. Walking the heading's own siblings only ever found the empty title wrapper; the
        real content one level over was never touched. Fixed by removing the nearest ancestor
        `<section>` instead (HTML5's own semantic section boundary, independent of any specific
        site's component/class structure) — falls back to the immediate parent when no
        `<section>` ancestor exists. Verified end-to-end this time: ran the actual vendored
        Readability.js against the real saved page's HTML (via jsdom) and confirmed the output is
        the real intro paragraph, not Credits or Methodology, before shipping.

     Known tradeoff, accepted deliberately: a genuine article with a real subsection literally
     headed by just "Methodology" or "Credits" would lose that whole section — worse than not
     touching it, but strictly better than the failure mode this replaces: confidently reading
     back page-footer boilerplate as if it were the article.
   
   Honest limitation of how this was tested: a plain static HTML fetch (via `curl`) cannot see
   content injected by the page's own JavaScript after load — both the related-articles block
   and the subscription notice are exactly that kind of dynamically-inserted content, which is
   why the first test pass missed them. Re-verified against the real, live-rendered page instead
   once this became clear.
2. **Site-specific rules for major webmail providers**, Gmail first, Outlook next — triggered by
   a right-click that works on blank page space, not only on a text selection (Chrome's
   context-menu `"page"` context, not just `"selection"`). Each rule knows how to find that
   provider's currently-open email's subject/sender/body while skipping the sidebar, other
   message previews in the list, and footer/signature boilerplate.
   - Real precedent this works: NaturalReader, Checker Plus for Gmail, Audeus, and Email Text
     Extractor are all currently-shipping extensions doing DOM-based, provider-specific email
     content extraction successfully today.
   - Correction to an earlier assumption: Gmail's `gmail_signature` CSS class is added by
     Gmail's own compose window to a signature the *user* inserts when writing a new message —
     it is not present in mail *received* from others generally, so it can't be used as a
     universal signature-skipping trick. Footer/signature detection needs a real heuristic
     (position near the bottom + distinct formatting + recognizable patterns like a phone number,
     address, or "unsubscribe"), not this one shortcut.
3. **Manual selection stays available** as an explicit override for anything the automatic rules
   get wrong, or any site without a dedicated rule yet.
4. **Longer-term, provider-agnostic alternative**: a vision/language-model-based classifier that
   recognizes "this is the real content vs. navigation/signature/ads" contextually, with no
   hand-written rule needed per site. Real tradeoff: needs network access and a real per-use
   cost (a subscription or capped-usage model would be needed), and is a bigger lift to build.
   Deferred, not abandoned.
5. **Alternative avenue considered for email specifically**: official APIs exist (Gmail API,
   Microsoft Graph for Outlook) that hand back clean, structured message data with zero
   page-scraping — more robust long-term, since Google/Microsoft won't quietly break their own
   API the way a page redesign could break a hand-written rule. Real cost: requires OAuth
   consent from the user, and for anything beyond personal use, a real app-review/verification
   process from Google/Microsoft for mail-read access. Deferred in favor of the simpler
   page-reading approach for now; worth revisiting if the DOM-based rules prove too fragile to
   maintain.

## UI direction (revised after first real use)

Original plan (extension hands text to SonoScript, SonoScript's own window takes over and comes
to the front) works, but breaks the "never have to touch the actual app" goal.

Revised target, modeled directly on Edge's own built-in Read Aloud: a small floating control bar
injected into the page itself (play/pause/skip, voice options), with the currently-spoken
word/phrase highlighted live on the page — SonoScript runs invisibly in the background purely as
the audio-generation engine, never surfacing its own window. Requires upgrading from the current
one-shot "send text, get an acknowledgment" HTTP call to a real bidirectional streaming
connection (audio out, word-timing back) — a local WebSocket connection was already the
architecture the original research recommended for exactly this reason; the first working
version intentionally started simpler and defers this until now.

The same token-based authorization requirement applies to this new WebSocket channel — this is
an extension of the existing security work, not a reason to relax it. One real difference from
the POST /read path, found the hard way when the toolbar shipped and simply didn't connect:
toolbar.js runs as a content script INJECTED INTO THE PAGE, so its WebSocket carries the Origin
of whatever page it's on (e.g. `https://www.nytimes.com`), never `chrome-extension://...` —
that origin only applies to requests made from the extension's own background service worker
(which is why background.js's own `fetch()` to POST /read correctly sends it). An Origin check
on /control would reject every real connection from every real page, so /control relies on the
token alone; the POST /read path keeps its Origin check since it genuinely runs from the
background context.

**Status: v1 shipped.** Floating bar (play/pause/skip/scrubber/voice picker) built, backed by a
hand-rolled WebSocket server in browser_bridge.py (stdlib only — RFC 6455 handshake + framing,
same minimal-dependency approach as everything else in this file, not a third-party library).
Live on-page word/phrase highlighting (matching a spoken word back to its exact position in the
LIVE page DOM, not just inside the toolbar) is deliberately not in v1 — a harder problem than the
transport (Readability's extracted text doesn't map 1:1 onto the live page's scattered text
nodes) — and is the next real piece of this section's original scope once picked back up.

**Two more real bugs found from live multi-page browsing, not single-page testing.** Both only
surfaced once a user actually navigated across several pages in one session — the original v1
testing was all single-page.

*Orphaned playback, no way to stop it.* Navigating away from a page destroys its toolbar and the
toolbar's control WebSocket along with it — but nothing told SonoScript to stop, since the
toolbar's own close (×) button was the only thing that ever sent `{cmd: "stop"}`. Confirmed
directly: playback kept running indefinitely with zero control surface reachable anywhere.
`browser_bridge.py`'s connection tracking already used a `set()` for control connections
specifically because multiple simultaneously-open toolbars (e.g. across tabs) are meant to share
one playback session, not race to own it — so the fix has to trigger only when the *last*
connection drops, not any single one. `remove_control_connection` now checks, immediately after
discarding a connection, whether the set is empty; if so, it fires the same `{cmd: "stop"}` the
close button already sends, covering every other way the connection can end (closed tab,
navigation, reload) where that explicit send never gets the chance to run.

*Toolbar missing on a later read, despite the read itself succeeding.* Confirmed in the same
session: a subsequent "Read this page aloud" on a different page started audio (the POST /read
succeeded) with no toolbar appearing at all, and no visible error anywhere a user would see one —
`showToolbar`'s failure path only ever logged to the invisible service-worker devtools console.
The likely cause: `chrome.scripting.executeScript` can transiently fail right after a navigation,
before the target frame is actually ready to accept injection — a real, known Chrome timing gap,
not treated here as fatal on the first attempt the way a genuinely restricted page (`chrome://`,
the Web Store) still is. `showToolbar` now retries its injection up to 3 times (400ms apart)
before giving up.

### Toolbar visual treatment: real "Liquid Glass" — shipped

**Status: shipped**, via [liquidGL](https://github.com/naughtyduk/liquidGL) (MIT, vendored and
patched at `browser_extension/liquidGL.js`) — a real WebGL library, not a CSS approximation. It
takes its own live snapshot of the page, uploads it as a texture, and renders the toolbar as an
actual refracting lens: a GPU fragment shader samples that texture through a displacement offset
(concentrated at the edges via a rounded-rect signed-distance field, flat/undistorted in the
center) and splits the R/B channels for chromatic aberration.

**This replaced an earlier CSS `backdrop-filter` + SVG-displacement-filter approach entirely**,
after five separate rounds of real bug fixes (color space, canvas resampling, a mix-blend-mode
math error, filter-region margins, displacement range) still never produced correct edge-confined
refraction that tracked scroll properly. Root cause, not fixable within that approach:
`backdrop-filter` samples the backdrop live at paint time with no way to distinguish "the page
scrolled" from "the content changed," so scrolling produced a mirror-like artifact instead of
true tracking refraction. liquidGL's own live-snapshot-and-reproject model handles scroll
correctly by construction, which a filter sampling the compositor's current frame never can.

**Patches made to liquidGL itself** (all documented in liquidGL.js's own header):
- `target` accepts a real `Element`, not just a CSS selector string — needed because the toolbar
  lives inside a closed Shadow DOM, which `document.querySelectorAll()` can't reach through.
- `destroy()` / `removeLens()` — added because this library was built for glass elements that
  live for a page's whole lifetime; the toolbar opens and closes repeatedly on the same page, and
  without an explicit teardown a closed toolbar's lens stayed registered in the shared renderer
  forever, still being measured and rendered every frame against a detached element.
- `cornerNormal()`'s refraction-direction fallback was `normalize(p)` — the direction from the
  *element's own center*, not from the nearest edge. On a roughly square element that's a
  reasonable approximation; on this toolbar (~12x wider than tall) it made refraction visibly
  converge toward the bar's centroid like a circular lens bulge instead of tracking the actual
  rounded-rect outline, and made the bevel/corner blend jump between two disagreeing direction
  fields, which read as a jagged rainbow seam right at the corner. Fixed by deriving a proper
  nearest-straight-edge direction from the same real pixel-space coordinates already available.
- `bevelWidth` is a fraction of `min(width, height)` — confirmed in the shader math. This bar is
  short and wide, so bevelWidth needs to be tuned much smaller than the library's own demo
  defaults (which assume a squarer element) or the bevel band reaches the bar's own vertical
  center, reading as "the effect pushes into the middle" instead of a thin edge band.
- A second, independent blur (`ourFrost`, a SonoScript-only `lens.options` key liquidGL's own
  code never defined but doesn't reject either) was added directly in the fragment shader,
  sampled at the *undistorted* texture position and reused for every subsequent texture read
  (base color, the refracted sample, both aberration channels). That's what actually puts it
  *before* refraction/aberration in the chain — a CSS layer applied after liquidGL's WebGL render
  is finished can only ever blur the already-bent result, softening the aberration fringing and
  specular highlight along with the scene; sampling a blurred source before the displacement math
  runs keeps the bend itself crisp and only the underlying content soft. (liquidGL's own `frost`
  option is unrelated and untouched — a 16-tap scatter blur sampled *after* the refracted
  position, which reads more like a soft glow than true frost at higher values.)

**SonoScript's own additions, outside liquidGL entirely:**
- **Adaptive text/icon color** (`initAdaptiveTheme` in toolbar.js): fixed light text was
  illegible over light page backgrounds. mix-blend-mode: difference (liquidGL's own README
  suggests this for exactly this problem) doesn't actually work here — the toolbar's own
  `position: fixed` establishes a stacking context, and mix-blend-mode can't blend across that
  boundary to reach the real WebGL canvas rendered behind it. What's shipped instead: liquidGL
  exposes its own canvas as `lens.renderer.canvas`; a few times a second, the toolbar's own
  on-screen rect is cropped out of that real canvas via `drawImage`, averaged for luminance, and
  used to pick between a fixed dark-gray and a fixed near-white (never anything continuously
  blended between them — an earlier linear-inversion version produced gray text on a gray
  background at exactly the point it mattered most). Drives a single `--sono-fg` CSS custom
  property on `.bar`, which every text/icon/scrubber rule reads from.
- **Gray tint** (`.sono-tint`): a flat, adjustable-opacity gray layer on top of the rendered
  glass, for a "less pure mirror, more frosted card" look liquidGL has no option for at all.

**Not yet done, worth knowing:** the main app's own UI doesn't have this treatment yet — nothing
about the current approach is browser-extension-specific if it's ever picked up there. Also see
the "Liquid Glass presets" note in the roadmap below — named presets (Liquid Glass → Opaque →
Solid) plus a master fade slider are planned but not started.

## Next steps

See `SonoScript Board.md` in the Obsidian vault for live day-to-day tracking. In rough order:

1. Generic Readability-style whole-page extraction as the new default interaction.
2. Gmail-specific right-click-without-selection rule.
3. In-page floating control bar + live highlighting + WebSocket audio/timing streaming,
   replacing the current "app comes to the front" behavior.
4. Auto-launch SonoScript if it isn't already running.
5. Packaging (works without Developer Mode) — needed before this is shareable with anyone else.
