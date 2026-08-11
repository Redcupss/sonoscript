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
- `native_host.py` is a dependency-free script whose only job is reading that token file and
  handing it back over the native-messaging channel.
- Verified directly: unauthorized requests get rejected (403), authorized ones succeed and
  trigger real generation; the native host correctly implements the messaging protocol and
  returns the live, current token.

**Known gap, not yet built**: SonoScript has to already be running. Nothing launches it
automatically yet if it's closed.

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
