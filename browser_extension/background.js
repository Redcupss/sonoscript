// SonoScript Read Aloud — background service worker.
//
// Two ways to trigger a read: select text and use "Read selection aloud" (an explicit manual
// override, useful for anything automatic detection gets wrong), or right-click anywhere on the
// page with nothing selected and use "Read this page aloud," which extracts just the real
// content — skipping ads, navigation, and photo captions — the same way Firefox's own Reader
// View does, via Mozilla's Readability.js (bundled here, unmodified, Apache-2.0 licensed).

const NATIVE_HOST = "com.sonoscript.bridge";
const BRIDGE_URL = "http://127.0.0.1:51823/read";
const CONTROL_WS_URL = "ws://127.0.0.1:51823/control";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "sonoscript-read-selection",
    title: "Read selection aloud (SonoScript)",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "sonoscript-read-page",
    title: "Read this page aloud (SonoScript)",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  // Actually awaited (not fire-and-forget) — readAloud can now retry for up to ~20s while
  // SonoScript cold-starts (see getTokenWithRetry's own comment for why that moved here), and
  // this listener's own returned promise is the clearest signal to Chrome's MV3 service-worker
  // lifecycle that there's still real work in flight for the whole time that takes, not just
  // for the single event tick that kicked it off.
  if (info.menuItemId === "sonoscript-read-selection" && info.selectionText) {
    await readAloud(info.selectionText, tab.id);
    return;
  }
  if (info.menuItemId === "sonoscript-read-page") {
    const text = await extractPageContent(tab.id);
    if (!text) {
      console.error(
        "SonoScript: couldn't find readable content on this page — try selecting the " +
        "specific text you want instead."
      );
      return;
    }
    await readAloud(text, tab.id);
  }
});

async function extractPageContent(tabId) {
  // Loaded as a real file first (not inlined into the function below) so Readability.js's own
  // ~2800 lines don't have to be duplicated into every call — this just defines the Readability
  // constructor in the page's isolated-world scope for the tab, ready for the next call to use.
  await chrome.scripting.executeScript({ target: { tabId }, files: ["Readability.js"] });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      // Readability mutates whatever document it's given (strips elements as it scores them) —
      // operating on a clone means the actual visible page is never touched.
      const clone = document.cloneNode(true);
      // Confirmed directly against a real page (a Forbes article) that Readability alone still
      // let a photo caption through: the site wraps it in a real <figure> tag, but with
      // meaningless auto-generated CSS class names instead of a <figcaption> or any descriptive
      // naming (common on modern React/Next.js-built sites) — Readability's heuristics have
      // nothing to recognize it by. Stripping every <figure> outright before parsing removes it
      // cleanly: images can't be read aloud anyway, and a caption/credit is supplementary, not
      // core content. Verified this doesn't remove real article text on the same test page.
      clone.querySelectorAll("figure").forEach((el) => el.remove());

      // Confirmed directly against a real page (Forbes' "50 Over 50" rankings hub) that
      // Readability can pick an entirely wrong section as "the article" on hub/landing-style
      // pages: the real content there is mostly name/category cards with almost no body prose,
      // while a page-footer "Methodology" section was one dense, eleven-sentence paragraph — by
      // a wide margin the single densest block of text anywhere on the page (the real intro was
      // two sentences; "Related Coverage" and the category tiles carry no paragraph text at
      // all). Readability's scoring selects for exactly that density, so it grabbed Methodology
      // (and the adjacent Credits block, similarly text-heavy in places) over the real intro.
      // Stripping any section headed by these exact labels before Readability ever scores the
      // page removes the false-positive candidate outright — same technique the <figure>
      // stripping above uses for a different false positive. Scoped to each heading's own
      // immediate parent (nextSibling only ever walks within one parent's child list), so this
      // can't reach into and remove unrelated content elsewhere on the page — and bounded to stop
      // at the very next heading element, so it only ever removes the ONE matched section, not
      // every section that happens to follow it. Confirmed via review this boundary check is
      // required, not optional: on a flat layout where sections are direct sibling headings under
      // one wrapping container (a common CMS pattern — Introduction/Methodology/Results/
      // Discussion as sibling <h2>s, not each wrapped in its own per-section <div>), walking to
      // the end of the parent's children with no stop condition would delete every real section
      // after Methodology too, and Readability would still return non-null/non-empty text (the
      // untouched Introduction survives), so the failure would be silent — an article read back
      // missing its entire second half with no error surfaced anywhere.
      const BOILERPLATE_SECTION_HEADINGS = ["methodology", "credits"];
      const HEADING_TAGS = new Set(["H1", "H2", "H3", "H4", "H5", "H6"]);
      clone.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((heading) => {
        const label = heading.textContent.trim().toLowerCase();
        if (!BOILERPLATE_SECTION_HEADINGS.includes(label)) return;
        let node = heading;
        while (node) {
          if (node !== heading && node.nodeType === Node.ELEMENT_NODE && HEADING_TAGS.has(node.tagName)) {
            break; // the next section has started — stop before touching it
          }
          const next = node.nextSibling;
          node.remove();
          node = next;
        }
      });

      const article = new Readability(clone).parse();
      if (!article || !article.textContent || !article.textContent.trim()) {
        return null;
      }
      let text = (article.title ? article.title + ". " : "") + article.textContent;

      // Related-content/recirculation modules ("More From Forbes" and equivalents on other
      // sites) are commonly embedded INSIDE the same container as the real article body —
      // confirmed directly: Forbes nests it in the exact same "article-body" div as the real
      // text, so no DOM-structural signal tells Readability the two apart. These always sit at
      // the true end of the article, so truncating there is safe and effective — the same
      // technique real scraping/reader-mode tools use for this exact, common situation.
      const TRAILING_JUNK_MARKERS = [
        "More From Forbes", "Related Articles", "You May Also Like", "Read Also",
        "Recommended For You",
      ];
      // Measured against the ORIGINAL length, captured once before the loop — re-measuring
      // against `text.length` after each truncation would keep shrinking the yardstick, so a
      // second marker's position (still the same real distance from the true start) could drift
      // past the 70% mark purely because an earlier truncation already shortened the string,
      // wrongly chopping real content that was never actually near the end of the real article.
      const originalLength = text.length;
      for (const marker of TRAILING_JUNK_MARKERS) {
        // lastIndexOf (not indexOf) targets the occurrence nearest the true end, and the 70%
        // position check refuses to truncate at all unless it's actually out there — a real
        // article that happens to use one of these exact phrases as an early subheading (not
        // impossible) must not get most of its real content chopped off.
        const idx = text.lastIndexOf(marker);
        if (idx !== -1 && idx > originalLength * 0.7) text = text.slice(0, idx);
      }

      // A cookie/subscription-gate notice ("Continue Reading with a Forbes Subscription...")
      // showed up mid-article in a real user test but did NOT reproduce in a fresh test
      // session — likely conditional on cookie/paywall-metering state, not something present
      // on every load. Since it can appear mid-article (unlike the trailing markers above,
      // truncating isn't safe here — it would cut off real content that follows), strip the
      // exact known phrase outright instead of guessing at its DOM wrapper.
      text = text.replace(
        /Continue Reading with a Forbes Subscription.*?Cookie Preferences link in our footer\./s,
        ""
      );

      // Same class of problem, second real site: an NYT article's ad-slot + "you have been
      // granted free access" gate notice landed mid-article, between the headline and the
      // real body. Different exact wording than the Forbes case above — this is genuinely the
      // second time a site-specific dynamically-injected message has slipped through, which is
      // worth treating as a pattern rather than an isolated one-off: a real content-extraction
      // library (e.g. Trafilatura, running server-side over the existing Python bridge instead
      // of client-side here) would handle this class of site-specific junk generically instead
      // of needing a new regex per site as they're discovered.
      text = text.replace(
        /Advertisement\s*SKIP ADVERTISEMENT.*?free to read/is,
        ""
      );

      return text;
    },
  });
  return result;
}

// Wraps sendNativeMessage in a Promise so getTokenWithRetry (below) can await it in a plain
// loop. Never rejects — chrome.runtime.lastError is folded into the resolved value, same as any
// other "native_host.py said no" case, so the retry loop only has one shape of failure to
// handle instead of two.
function sendNativeMessageAsync(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, message, (response) => {
      if (chrome.runtime.lastError) {
        resolve({ error: chrome.runtime.lastError.message });
        return;
      }
      resolve(response || {});
    });
  });
}

// native_host.py used to block for up to 20s itself, cold-launching SonoScript and polling
// until it came up before ever replying — confirmed directly (via real user testing, not just
// reasoning about it) that this doesn't actually work: Chrome's native-messaging connections
// have a real practical timeout well under 20s, and an MV3 service worker can be suspended as
// idle while a single sendNativeMessage call just sits there waiting, losing the whole request
// silently — matching exactly what broke (no toolbar, no text ever reaching the app, despite
// the app itself visibly launching).
//
// Fixed by moving the "wait for cold start" loop to THIS side instead: native_host.py now
// always responds within a fraction of a second (either a real token, a real error, or
// {launching: true}), and retrying lives here as a continuously-running async call chain kicked
// off directly from the contextMenus.onClicked listener that's still actively processing this
// same user click — not a dangling setTimeout scheduled after that handler has already
// returned, which risks the service worker being suspended before it ever fires. Each retry is
// its own short, ordinary native-messaging round trip, so no single call is ever the thing that
// times out.
async function getTokenWithRetry() {
  const MAX_ATTEMPTS = 20;
  const RETRY_DELAY_MS = 1000;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const response = await sendNativeMessageAsync({ action: "get_token" });
    if (response.token) return response.token;
    if (response.error && !response.launching) {
      // A real, non-transient failure (host not registered, SonoScript genuinely broken) —
      // retrying the exact same call isn't going to change that outcome.
      console.error("SonoScript native host error:", response.error);
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
  }
  console.error("SonoScript: took too long to start.");
  return null;
}

async function readAloud(text, tabId) {
  const token = await getTokenWithRetry();
  if (!token) {
    console.error(
      "SonoScript: couldn't reach the native host — is it registered, and is SonoScript " +
      "running?"
    );
    return;
  }
  try {
    const res = await fetch(BRIDGE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-SonoScript-Token": token,
      },
      body: JSON.stringify({ text }),
    });
    // fetch() only REJECTS on a network-level failure (caught below) — it resolves normally
    // for any HTTP status, including a rejection from the bridge itself (e.g. 403 on a stale
    // token, 413 on oversized text), which would otherwise fail completely silently here.
    if (!res.ok) {
      console.error(`SonoScript: the local app rejected this request (HTTP ${res.status}).`);
      return;
    }
    if (tabId !== undefined) showToolbar(tabId, token);
  } catch (err) {
    console.error("SonoScript: request to the local app failed — is it running?", err);
  }
}

async function showToolbar(tabId, token) {
  // Same two-step "load the file, then call a function it defines" pattern extractPageContent
  // uses for Readability.js — keeps toolbar.js's own ~250 lines out of every call instead of
  // re-injecting them inline each time.
  //
  // Retries a couple of times before giving up: confirmed in real testing that playback can
  // start (the /read POST above already succeeded) with no toolbar ever appearing and nothing
  // visible to the user explaining why — the only trace was a console.error in the service
  // worker's own devtools, which nobody looks at during normal use. executeScript can genuinely
  // fail transiently right after a navigation (the target frame isn't yet in a state that
  // accepts injection) — a real, known Chrome timing gap, not something worth treating as fatal
  // on the first attempt the way a permanently-restricted page (chrome://, the Web Store) is.
  const MAX_ATTEMPTS = 3;
  const RETRY_DELAY_MS = 400;
  let lastErr = null;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["liquidGL.js", "toolbar.js"] });
      await chrome.scripting.executeScript({
        target: { tabId },
        func: (t, wsUrl) => window.__sonoscriptInitToolbar(t, wsUrl),
        args: [token, CONTROL_WS_URL],
      });
      return;
    } catch (err) {
      lastErr = err;
      if (attempt < MAX_ATTEMPTS - 1) {
        await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
      }
    }
  }
  // Not fatal even after every retry — playback already started via the /read POST above, this
  // only affects the in-page controls. Genuinely un-injectable pages (chrome://, the Web Store,
  // etc.) will always end up here, same limitation extractPageContent has.
  console.error("SonoScript: couldn't show the playback toolbar on this page.", lastErr);
}
