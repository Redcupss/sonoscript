// SonoScript in-page playback toolbar — modeled on Edge's built-in Read Aloud bar, extended
// with a real scrubber and a voice picker. Loaded as a plain content-script file (like
// Readability.js), then invoked via window.__sonoscriptInitToolbar(token, wsUrl) from
// background.js — same two-step "load file, then call a function" pattern used for extraction.
//
// Everything lives inside a Shadow DOM so the host page's own CSS can never bleed in (a real,
// common failure mode for injected UI — this page's own styles reset margins/fonts/colors on
// everything, which would otherwise make the toolbar look broken depending on the site).
//
// The scrubber's hit-tolerance/grab-vs-jump/animated-size behavior deliberately mirrors
// widgets.py's ScrubberView (the app's own scrubber) constant-for-constant — see the comments
// below at each matching constant.
//
// Glass effect: rendered by liquidGL.js (vendored, see that file's own header for the exact
// patches applied), a real WebGL glass/refraction library — NOT a homegrown CSS backdrop-filter
// + SVG displacement filter, which is what shipped here originally. That approach went through
// five separate rounds of real, verified bug fixes (linearRGB vs sRGB color space, canvas raster
// resampling at devicePixelRatio, a mix-blend-mode math error, filter-region margins too small
// for the displacement scale, a vertical displacement range too large for the bar's own short
// height) and STILL never produced real edge-confined refraction that tracked correctly with
// page scroll — it fundamentally couldn't, because backdrop-filter samples the backdrop AT THE
// TIME each frame paints, with no way to distinguish "the page under me scrolled" from "content
// changed," so scrolling produced a mirror-like artifact instead of true tracking refraction.
// liquidGL takes its own live snapshot of the page and re-projects it through an actual lens
// shader, which handles scroll correctly by construction. See liquidGL.js's own header for what
// was patched to make it work from inside a closed Shadow DOM (its target option only ever
// accepted a CSS selector string, which document-level queries can't resolve through "closed"
// mode) and to support being torn down cleanly when the toolbar closes and reopens.

(function () {
  const HOST_ID = "sonoscript-toolbar-host";

  // A live tuning panel for liquidGL's own options, wired directly to the running lens instance —
  // refraction/aberration/bevelDepth/bevelWidth/frost/ourFrost/magnify are all read fresh every
  // frame straight from `lens.options` (confirmed directly in liquidGL.js's render loop), so
  // mutating them from a slider's input event takes effect immediately, no re-init needed.
  // shadow/specular need their own setter calls since those touch one-time DOM/listener setup
  // rather than a per-frame GL uniform — same distinction the library's own demo GUI makes.
  // Turned off now that real values (below, in the window.liquidGL() call) came from actually
  // turning knobs against the real toolbar instead of another guess-and-redeploy cycle — left
  // in place, not deleted, since more tuning work is planned (see the presets/master-slider note
  // in memory — project_liquid_glass_presets_roadmap or similar).
  const SONOSCRIPT_GLASS_TUNING = true;

  function buildTuningPanel(shadow, lens, overlay) {
    const panel = document.createElement("div");
    panel.style.cssText = [
      "position:fixed", "top:16px", "right:16px", "z-index:2147483647",
      "background:rgba(20,20,23,0.92)", "color:#f2f2f4", "border-radius:10px",
      "padding:12px 14px", "font:12px/1.4 -apple-system,sans-serif", "width:230px",
      "max-height:calc(100vh - 32px)", "overflow-y:auto",
      "box-shadow:0 8px 24px rgba(0,0,0,0.4)", "pointer-events:auto",
    ].join(";");

    const fields = [
      { key: "refraction", label: "Refraction", min: 0, max: 0.01, step: 0.0001 },
      { key: "aberration", label: "Aberration", min: 0, max: 1, step: 0.01 },
      { key: "bevelDepth", label: "Bevel depth", min: 0, max: 0.2, step: 0.001 },
      { key: "bevelWidth", label: "Bevel width", min: 0, max: 0.5, step: 0.001 },
      { key: "frost", label: "Frost (liquidGL)", min: 0, max: 10, step: 0.1 },
      // Genuinely a lens.options field now, read the same way as the ones above it — see the
      // sampleOurBlur() patch in liquidGL.js's fragment shader for why: it blurs the SOURCE the
      // refraction/aberration math reads from (so it comes BEFORE them in the chain), rather than
      // blurring the finished composite the way a CSS backdrop-filter after the fact would have
      // to. `ourFrost` was never one of liquidGL's own option names, but its constructor assigns
      // lens.options straight from what we pass at init with no whitelist, so this key survives
      // and stays live-mutable exactly like refraction/aberration do.
      { key: "ourFrost", label: "Frost blur (ours, pre-refraction)", min: 0, max: 10, step: 0.1 },
      { key: "magnify", label: "Magnify", min: 1, max: 5, step: 0.1 },
    ];

    // Independent from either frost field above — a flat gray tint applied to the already-
    // rendered glass (see the CSS comment on .sono-tint), for the "less pure mirror, more frosted
    // card" look liquidGL has no option for at all. Read/written directly off the tint element,
    // not lens.options — this one really is a plain CSS layer, not a shader parameter.
    const overlayFields = [
      { key: "tintOpacity", label: "Gray tint opacity", min: 0, max: 1, step: 0.01, value: 0.08 },
    ];

    let html = `<div style="font-weight:600; margin-bottom:8px;">Glass tuning (temp)</div>`;
    for (const f of fields) {
      const val = lens.options[f.key] != null ? lens.options[f.key] : f.min;
      html += `
        <label style="display:block; margin-bottom:6px;">
          <div style="display:flex; justify-content:space-between;">
            <span>${f.label}</span><span data-readout="${f.key}">${val}</span>
          </div>
          <input type="range" data-key="${f.key}" min="${f.min}" max="${f.max}" step="${f.step}" value="${val}" style="width:100%;" />
        </label>
      `;
    }
    html += `
      <label style="display:flex; align-items:center; gap:6px; margin:8px 0 4px;">
        <input type="checkbox" data-key="shadow" ${lens.options.shadow ? "checked" : ""} /> Shadow
      </label>
      <label style="display:flex; align-items:center; gap:6px; margin-bottom:8px;">
        <input type="checkbox" data-key="specular" ${lens.options.specular ? "checked" : ""} /> Specular
      </label>
      <div style="font-weight:600; margin:10px 0 8px; border-top:1px solid rgba(255,255,255,0.14); padding-top:8px;">
        SonoScript overlay
      </div>
    `;
    for (const f of overlayFields) {
      html += `
        <label style="display:block; margin-bottom:6px;">
          <div style="display:flex; justify-content:space-between;">
            <span>${f.label}</span><span data-readout="${f.key}">${f.value}</span>
          </div>
          <input type="range" data-overlay-key="${f.key}" min="${f.min}" max="${f.max}" step="${f.step}" value="${f.value}" style="width:100%;" />
        </label>
      `;
    }
    html += `
      <button type="button" data-action="copy" style="width:100%; padding:6px; border-radius:6px; border:none; background:#f6f6f7; color:#18181b; cursor:pointer; font:inherit;">Copy config</button>
      <div data-status style="margin-top:6px; min-height:14px; color:#9d9da3;"></div>
    `;
    panel.innerHTML = html;
    shadow.appendChild(panel);

    // Apply the tint's starting value to the actual element — the slider above only seeds its
    // own displayed value/position from the same number, it doesn't independently set anything,
    // so without this the tint would stay invisible until the user first touches the slider.
    overlay.tintEl.style.background = `rgba(128,128,128,${overlayFields[0].value})`;

    panel.querySelectorAll('input[type="range"][data-key]').forEach((input) => {
      input.addEventListener("input", () => {
        const key = input.dataset.key;
        const value = parseFloat(input.value);
        lens.options[key] = value;
        panel.querySelector(`[data-readout="${key}"]`).textContent = value;
      });
    });
    panel.querySelectorAll('input[type="range"][data-overlay-key]').forEach((input) => {
      input.addEventListener("input", () => {
        const key = input.dataset.overlayKey;
        const value = parseFloat(input.value);
        panel.querySelector(`[data-readout="${key}"]`).textContent = value;
        if (key === "tintOpacity") {
          overlay.tintEl.style.background = `rgba(128,128,128,${value})`;
        }
      });
    });
    panel.querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.addEventListener("change", () => {
        const key = input.dataset.key;
        lens.options[key] = input.checked;
        // specular is read fresh every frame like the sliders above, same as the render loop
        // check confirmed for them — only shadow needs an explicit call, since it's a one-time
        // CSS box-shadow toggle rather than a per-frame GL uniform.
        if (key === "shadow" && typeof lens.setShadow === "function") lens.setShadow(input.checked);
      });
    });
    panel.querySelector('[data-action="copy"]').addEventListener("click", () => {
      const config = {};
      for (const f of fields) config[f.key] = lens.options[f.key];
      config.shadow = lens.options.shadow;
      config.specular = lens.options.specular;
      for (const f of overlayFields) {
        config[f.key] = parseFloat(panel.querySelector(`input[data-overlay-key="${f.key}"]`).value);
      }
      const text = JSON.stringify(config, null, 2);
      const status = panel.querySelector("[data-status]");
      navigator.clipboard
        .writeText(text)
        .then(() => {
          status.textContent = "Copied — paste it back to me.";
        })
        .catch(() => {
          status.textContent = text; // clipboard API can be blocked in some contexts — show it inline as a fallback
        });
    });
  }

  // Reads real pixels back from liquidGL's own WebGL canvas under the bar's own on-screen rect
  // and drives a single --sono-fg CSS custom property (declared on .bar, inherited by every
  // descendant — text color AND the scrubber's background all read from it, see the CSS below)
  // from the measured luminance there. liquidGL exposes its shared renderer (and the real
  // <canvas> it draws into) as lens.renderer — confirmed directly in liquidGL.js's own
  // _renderLens()/render() methods, which is also where the rect-to-canvas-pixel math below
  // comes from (dpr scaling; canvas is read top-down via drawImage here, unlike gl.readPixels'
  // bottom-up framebuffer convention, so no Y-flip is needed the way liquidGL's own internals
  // need one).
  //
  // First version did shade = 255 - luminance, a continuous linear inversion — confirmed broken
  // directly against a real screenshot over dev.to's light-gray page background: a mid-toned,
  // visually busy area (mixed text/icons/borders) averaged out to roughly mid-gray luminance, and
  // 255 - ~130 is ALSO roughly mid-gray, so the text landed almost exactly on top of its own
  // backdrop's tone — near-zero contrast right where it matters most. A SECOND version blended
  // continuously between two fixed endpoints across a narrow luminance band instead of the full
  // 0-255 range — better, but the same failure mode is still reachable in principle for a
  // backdrop that itself sits mid-band. The color is always exactly DARK or exactly LIGHT now,
  // never anything between — a plain threshold with a few luminance units of hysteresis around it
  // purely to stop rapid A/B flicker for a value oscillating right at the boundary as the page
  // scrolls, not to blend a third in-between shade. It still reads as a smooth "shade" rather than
  // a jarring snap because button.ctrl/.time/select.voice already carry `transition: color 0.3s
  // ease` (and the scrubber pieces their own background-color transitions) — the softness comes
  // from CSS interpolating between the two fixed endpoints over time, not from a third JS-computed
  // color sitting between them. DARK is also softened from the first version's implicit black —
  // explicit feedback that full #000 read as too harsh; it's a soft charcoal instead.
  function initAdaptiveTheme(bar, glassLens) {
    const DARK = [38, 38, 38];
    const LIGHT = [245, 245, 247];
    const THRESHOLD = 130;
    const HYSTERESIS = 6; // +/- luminance units around THRESHOLD before actually flipping state
    let isDark = false; // "backdrop is bright enough that we're currently showing DARK text"
    const SAMPLE_MS = 200;
    const SWATCH = 8; // sample size is irrelevant beyond a few px — we only want an average
    let mirrorCtx = null;
    let lastSample = 0;
    let rafId = requestAnimationFrame(tick);
    let taintedCanvasStopped = false;

    function tick(ts) {
      rafId = requestAnimationFrame(tick);
      if (taintedCanvasStopped || ts - lastSample < SAMPLE_MS) return;
      lastSample = ts;

      const glCanvas = glassLens && glassLens.renderer && glassLens.renderer.canvas;
      if (!glCanvas || !glCanvas.width) return;

      const rect = bar.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const sx = Math.max(0, Math.round(rect.left * dpr));
      const sy = Math.max(0, Math.round(rect.top * dpr));
      const sw = Math.min(glCanvas.width - sx, Math.round(rect.width * dpr));
      const sh = Math.min(glCanvas.height - sy, Math.round(rect.height * dpr));
      if (sw <= 0 || sh <= 0) return;

      if (!mirrorCtx) {
        const c = document.createElement("canvas");
        c.width = SWATCH;
        c.height = SWATCH;
        mirrorCtx = c.getContext("2d", { willReadFrequently: true });
      }

      try {
        mirrorCtx.clearRect(0, 0, SWATCH, SWATCH);
        mirrorCtx.drawImage(glCanvas, sx, sy, sw, sh, 0, 0, SWATCH, SWATCH);
        const data = mirrorCtx.getImageData(0, 0, SWATCH, SWATCH).data;
        let total = 0;
        let count = 0;
        for (let i = 0; i < data.length; i += 4) {
          if (data[i + 3] === 0) continue; // no real frame rendered here yet — skip, don't drag the average toward black
          total += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
          count++;
        }
        if (count === 0) return;
        const luminance = total / count;
        // Bright backdrop -> DARK text, dark backdrop -> LIGHT text. Hysteresis only shifts WHEN
        // the flip happens, not what it flips between — DARK requires clearing THRESHOLD +
        // HYSTERESIS, switching back to LIGHT requires dropping below THRESHOLD - HYSTERESIS, so a
        // value hovering right at 130 doesn't rapidly toggle back and forth every tick.
        if (!isDark && luminance > THRESHOLD + HYSTERESIS) isDark = true;
        else if (isDark && luminance < THRESHOLD - HYSTERESIS) isDark = false;
        const [r, g, b] = isDark ? DARK : LIGHT;
        bar.style.setProperty("--sono-fg", `${r},${g},${b}`);
      } catch (err) {
        // getImageData throws a SecurityError if the canvas has gone "tainted" — liquidGL's own
        // README flags this as possible if any snapshotted page image lacks permissive CORS
        // headers. Not something to recover from mid-session: stop sampling for good so this
        // doesn't throw on every tick, and just leave --sono-fg at whatever it last was (the CSS
        // default, most likely, if this trips on the very first tick).
        taintedCanvasStopped = true;
      }
    }

    return () => cancelAnimationFrame(rafId);
  }

  function fmtTime(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  const ICONS = {
    play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M11 6v5.2L4.5 7 11 2.8V8h.2A8 8 0 1 1 3.3 13h2.03A6 6 0 1 0 11 6.6z"/></svg>',
    forward: '<svg viewBox="0 0 24 24"><path d="M13 6v5.2L19.5 7 13 2.8V8h-.2A8 8 0 1 0 20.7 13h-2.03A6 6 0 1 1 13 6.6z"/></svg>',
    close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
  };

  const CSS = `
    :host { all: initial; }
    .bar {
      position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%);
      z-index: 2147483647;
      display: grid;
      grid-template-columns: auto auto auto auto auto auto auto auto auto auto;
      column-gap: 10px;
      /* No row-gap here: CSS Grid inserts it unconditionally between row 1 and row 2 even while
         row 2 (.generating-label) is collapsed to max-height:0, which would leave the bar ~2px
         taller than it was before the generating label existed even at idle. The label's own
         margin-top supplies that same 2px of separation, but only while actually visible. */
      align-items: center;
      /* No background/blur here — liquidGL owns the entire glass appearance (translucency,
         refraction, blur) once it takes over the element. Before that first frame renders, this
         is fully transparent — see the header comment for why: a homegrown CSS approach here was
         a five-round dead end and got fully replaced, not tuned further. */
      color: #f2f2f4;
      /* Default before the first real luminance sample lands (see initAdaptiveTheme) — every
         adaptive text/icon/scrubber rule below reads this instead of a hardcoded color, so one
         JS-driven update here re-themes the whole bar at once. */
      --sono-fg: 245,245,247;
      border-radius: 14px;
      padding: 8px 14px;
      font: 13px/1.3 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      transition: opacity 0.15s ease;
      overflow: hidden;
    }
    .bar.disconnected { opacity: 0.55; }
    /* ----- SonoScript's own tint overlay, layered ON TOP of liquidGL's rendered glass -----
       A flat gray tint applied to the ALREADY-COMPOSITED glass image, for the "less pure mirror,
       more frosted card" look liquidGL has no option for at all (confirmed against its README's
       full parameter table — nothing like a tint or backing-card opacity exists there). It can't
       be fed into liquidGL's own snapshot/shader pipeline instead — confirmed directly in
       liquidGL.js's Painter.background(), its hand-rolled DOM rasterizer only ever does a flat
       fillStyle-then-fill() rect from the computed background color. This used to also carry a
       real backdrop-filter BLUR (.sono-frost, a sibling of this element) — that's gone now: the
       blur moved into liquidGL's own fragment shader instead (the ourFrost field in the panel
       above, plumbed through in liquidGL.js), specifically so it can apply BEFORE the shader's
       refraction/aberration math reads the texture rather than after the WebGL render is already
       done, which a CSS layer sitting on top of the finished canvas could never do regardless of
       DOM order. */
    .sono-tint {
      position: absolute; inset: 0;
      border-radius: inherit;
      pointer-events: none;
      z-index: 0;
      /* Baked in directly rather than left at 0 and relying on buildTuningPanel to apply it —
         that only runs when SONOSCRIPT_GLASS_TUNING is true, and it's false by default now, so a
         CSS-only default here is what actually ships when the panel is off. Keep this in sync
         with overlayFields' tintOpacity value above if the panel gets re-enabled later. */
      background: rgba(128, 128, 128, 0.08);
    }
    /* DOM order alone (prepended first) turned out NOT to be enough to keep the real controls
       crisp when this was still paired with a blur layer — confirmed directly with a live A/B
       test (frost blur 0 vs 6: buttons were visibly soft at 6). CSS Grid items apparently don't
       fall into the plain "non-positioned, painted in DOM order" stacking layer the way ordinary
       block children would, so an out-of-flow absolutely-positioned child could end up compositing
       ABOVE them despite coming first in markup. Forcing it explicitly here — every real control
       gets its own stacking context above the tint, so nothing here can ever paint over them. */
    .bar > *:not(.sono-tint) {
      position: relative;
      z-index: 1;
    }
    /* liquidGL sets pointer-events:none on the bar itself (its target element) — that's how it
       stays purely a visual/refraction layer instead of intercepting clicks meant for the page
       underneath. Since pointer-events is inherited, every actually-interactive child needs it
       set back to auto explicitly, or the whole toolbar goes click-dead the moment the glass
       effect attaches. Confirmed directly against the library's own demo, which does the same
       thing for its nav links. */
    button.ctrl, select.voice, .scrubber-wrap {
      pointer-events: auto;
    }
    /* ----- legibility: text/icon FILL COLOR itself shifts between white and black based on -----
       ----- what's actually rendered behind the glass at that spot (not a shadow/outline trick) -----
       Two earlier attempts didn't do this for real. mix-blend-mode: difference (liquidGL's own
       README recommendation) never actually reached the real backdrop: .bar is position:fixed
       with a z-index, so it establishes its own stacking context, and the actual glass (liquidGL's
       WebGL canvas) lives OUTSIDE .bar entirely — mix-blend-mode only blends against content
       painted within the SAME stacking context, so it had nothing real to react to. A drop-shadow
       outline came next, which was legible but read as an unwanted shadow effect, not a color
       shift. Neither was what was actually asked for: the glyph's own color should read the real
       luminance behind it and move from white to black accordingly.
       That's what initAdaptiveTheme() (below, wired up in initToolbar) actually does — it reads
       real pixels back from liquidGL's own WebGL canvas (liquidGL exposes it as
       lens.renderer.canvas) under the bar's own on-screen rect, averages luminance, and sets the
       --sono-fg custom property declared on .bar above — every rule here reads from that, so
       #f2f2f4/etc above are only the pre-JS/fallback values for the instant before the first
       sample lands. button.play-pause is excluded: it sits on its own opaque white pill, not the
       glass, and already has fixed guaranteed contrast. */
    button.ctrl {
      all: unset; display: flex; align-items: center; justify-content: center;
      width: 30px; height: 30px; border-radius: 999px; cursor: pointer;
      color: rgb(var(--sono-fg)); flex: none;
      transition: background-color 0.12s ease, color 0.3s ease;
      pointer-events: auto;
    }
    /* Hover reads as a clearly SOLID state change, not a faint transparent tint. */
    button.ctrl:hover { background: rgba(255,255,255,0.22); }
    button.ctrl:active { background: rgba(255,255,255,0.30); }
    button.ctrl svg { width: 16px; height: 16px; fill: currentColor; }
    button.play-pause {
      width: 34px; height: 34px; background: #f6f6f7; color: #18181b;
    }
    button.play-pause:hover { background: #fff; }
    button.play-pause svg { width: 16px; height: 16px; }
    .time {
      font-variant-numeric: tabular-nums; color: rgb(var(--sono-fg)); min-width: 34px; text-align: center;
      transition: color 0.3s ease;
    }
    /* ----- scrubber: mirrors widgets.py's ScrubberView constants and interaction model ----- */
    .scrubber-wrap { position: relative; width: 180px; height: 20px; display: flex; align-items: center; cursor: pointer; }
    .scrubber-track {
      position: absolute; left: 0; right: 0; height: 4px; border-radius: 999px;
      /* Reads --sono-fg same as the text/icons — this was missed entirely in the first adaptive
         pass (only text/icon .color was wired up), confirmed directly: over a light backdrop the
         scrubber stayed a fixed white-based translucent fill and didn't react at all. */
      background: rgba(var(--sono-fg), 0.16);
      transition: background-color 0.15s ease; /* ScrubberView: track brightens on hover/drag */
    }
    .scrubber-wrap.hovering .scrubber-track, .scrubber-wrap.dragging .scrubber-track {
      background: rgba(var(--sono-fg), 0.26);
    }
    .scrubber-fill { position: absolute; left: 0; height: 4px; border-radius: 999px; background: rgba(var(--sono-fg), 0.62); width: 0; }
    .scrubber-thumb {
      position: absolute; border-radius: 999px; background: rgba(var(--sono-fg), 0.95);
      /* IDLE_SIZE = 10 — same constant as ScrubberView */
      width: 10px; height: 10px; left: 0; top: 50%;
      transform: translate(-50%, -50%);
      transition: width 0.15s ease, height 0.15s ease, background-color 0.15s ease; /* animated, not a snap */
    }
    /* HOVER_SIZE = 13 */
    .scrubber-wrap.hovering .scrubber-thumb { width: 13px; height: 13px; }
    /* PRESSED_SIZE = 16, goes fully opaque (not literally #fff anymore) while dragging */
    .scrubber-wrap.dragging .scrubber-thumb { width: 16px; height: 16px; background: rgb(var(--sono-fg)); }
    select.voice {
      all: unset; max-width: 120px; color: rgb(var(--sono-fg)); background: rgba(255,255,255,0.10);
      border-radius: 8px; padding: 5px 8px; cursor: pointer; font: inherit;
      text-overflow: ellipsis; white-space: nowrap; overflow: hidden;
      transition: background-color 0.12s ease, color 0.3s ease;
      pointer-events: auto; /* all:unset above resets this too — must come after */
    }
    select.voice:hover { background: rgba(255,255,255,0.18); }
    .sep { width: 1px; height: 18px; background: rgba(255,255,255,0.14); flex: none; }
    /* Explicit grid row 1 for every control, so row 2 (the generating label below) can sit in
       just the scrubber's own column instead of spanning the whole bar. */
    .back { grid-row: 1; grid-column: 1; }
    .play-pause { grid-row: 1; grid-column: 2; }
    .forward { grid-row: 1; grid-column: 3; }
    .elapsed { grid-row: 1; grid-column: 4; }
    .scrubber-wrap { grid-row: 1; grid-column: 5; }
    .remaining { grid-row: 1; grid-column: 6; }
    .sep-1 { grid-row: 1; grid-column: 7; }
    .voice { grid-row: 1; grid-column: 8; }
    .sep-2 { grid-row: 1; grid-column: 9; }
    .close { grid-row: 1; grid-column: 10; }
    /* Small, under the scrubber specifically (same grid column, row 2) — matches
       status_label/PulsingLabel's own look: quiet gray text with a soft brightness band
       sweeping across it left to right on a loop, signaling "actively working" the same way
       the app's own "Generating..." text does, since there's no real percentage to show.
       max-height (not just opacity) is what's animated: a fixed 12px height here reserved
       that space in row 2 permanently, even fully transparent and not generating, making the
       whole bar taller all the time for no reason — collapsing to 0 when idle keeps the bar as
       compact as it was before this label existed, and it only grows while actually needed. */
    .generating-label {
      grid-row: 2; grid-column: 5;
      font-size: 10px; text-align: center; line-height: 12px; max-height: 0; margin-top: 0;
      overflow: hidden;
      opacity: 0; transition: opacity 0.2s ease, max-height 0.2s ease, margin-top 0.2s ease;
      color: transparent;
      background-image: linear-gradient(
        90deg,
        rgba(184,184,189,0.9) 30%, rgba(255,255,255,0.55) 47%,
        rgba(255,255,255,0.95) 50%, rgba(255,255,255,0.55) 53%, rgba(184,184,189,0.9) 70%
      );
      background-size: 300% 100%;
      -webkit-background-clip: text;
      background-clip: text;
      animation: sono-shimmer 2.2s linear infinite;
    }
    .generating-label.visible { opacity: 1; max-height: 12px; margin-top: 2px; }
    @keyframes sono-shimmer {
      from { background-position: 150% 0; }
      to { background-position: -150% 0; }
    }
  `;

  function buildBar(shadow) {
    const style = document.createElement("style");
    style.textContent = CSS;
    shadow.appendChild(style);

    const bar = document.createElement("div");
    bar.className = "bar disconnected";
    bar.innerHTML = `
      <div class="sono-tint"></div>
      <button class="ctrl back" title="Back 15s">${ICONS.back}</button>
      <button class="ctrl play-pause" title="Play/Pause">${ICONS.play}</button>
      <button class="ctrl forward" title="Forward 15s">${ICONS.forward}</button>
      <span class="time elapsed">0:00</span>
      <div class="scrubber-wrap">
        <div class="scrubber-track"></div>
        <div class="scrubber-fill"></div>
        <div class="scrubber-thumb"></div>
      </div>
      <span class="time remaining">0:00</span>
      <div class="sep sep-1"></div>
      <select class="voice" title="Voice"><option>Voice</option></select>
      <div class="sep sep-2"></div>
      <button class="ctrl close" title="Hide">${ICONS.close}</button>
      <span class="generating-label">Generating&hellip;</span>
    `;
    shadow.appendChild(bar);
    return bar;
  }

  // Grow-from-a-dot entrance: measure the bar's real final size first (while invisible), then
  // pin it to a small circle and transition width/height/border-radius to that measured size —
  // a FLIP-style animation, not a hardcoded width, so it stays correct regardless of how long the
  // voice label or button set ends up being. Opacity is deliberately NOT handled here anymore:
  // liquidGL's own lens constructor unconditionally forces the target's opacity to 0 the moment
  // it attaches, then fades it back in itself once its first real WebGL frame is ready (reveal:
  // "fade") — that's a genuinely different problem than this animation solves (avoiding a flash
  // of an un-textured glass pane before the snapshot exists) and fighting over the same inline
  // `opacity` property between two independent systems was a real, avoidable bug risk, not a
  // hypothetical one.
  function playEntrance(bar) {
    const rect = bar.getBoundingClientRect();
    const finalW = rect.width;
    const finalH = rect.height;
    const seed = Math.min(finalH, 46);

    bar.style.transition = "none";
    bar.style.width = seed + "px";
    bar.style.height = seed + "px";
    bar.style.borderRadius = "999px";
    // Force layout so the browser registers the seed state before the transition starts —
    // otherwise both the seed and final styles can get coalesced into one frame with no
    // visible animation at all.
    void bar.offsetHeight;

    requestAnimationFrame(() => {
      bar.style.transition =
        "width 0.45s cubic-bezier(0.32, 1.2, 0.4, 1), height 0.45s cubic-bezier(0.32, 1.2, 0.4, 1), " +
        "border-radius 0.45s cubic-bezier(0.32, 1.2, 0.4, 1)";
      bar.style.width = finalW + "px";
      bar.style.height = finalH + "px";
      bar.style.borderRadius = "14px";
    });

    const clearInlineSize = () => {
      // Once settled, drop the inline width/height/border-radius pins so the bar goes back to
      // being sized naturally by its own content/CSS (e.g. if the voice list repopulates with
      // a longer label later, the bar should be free to grow for it).
      bar.style.transition = "";
      bar.style.width = "";
      bar.style.height = "";
      bar.style.borderRadius = "";
      bar.removeEventListener("transitionend", clearInlineSize);
    };
    bar.addEventListener("transitionend", clearInlineSize);
  }

  function initToolbar(token, wsUrl) {
    const existingHost = document.getElementById(HOST_ID);
    if (existingHost) {
      if (existingHost._sonoscriptCleanup) existingHost._sonoscriptCleanup();
      existingHost.remove();
    }

    const host = document.createElement("div");
    host.id = HOST_ID;
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: "closed" });
    const bar = buildBar(shadow);
    playEntrance(bar);

    const els = {
      back: bar.querySelector(".back"),
      playPause: bar.querySelector(".play-pause"),
      forward: bar.querySelector(".forward"),
      elapsed: bar.querySelector(".elapsed"),
      remaining: bar.querySelector(".remaining"),
      scrubberWrap: bar.querySelector(".scrubber-wrap"),
      scrubberFill: bar.querySelector(".scrubber-fill"),
      scrubberThumb: bar.querySelector(".scrubber-thumb"),
      voice: bar.querySelector(".voice"),
      close: bar.querySelector(".close"),
      generatingLabel: bar.querySelector(".generating-label"),
    };

    // liquidGL.js is injected as a separate content-script file immediately before this one
    // (see background.js's showToolbar) specifically so window.liquidGL exists by the time this
    // runs. The typeof check is defensive, not expected in practice: same-origin content-script
    // injection isn't subject to the page's own CSP the way a page-context <script> tag would be,
    // so there's no realistic "page blocked it" failure mode here — this just avoids a hard crash
    // if injection order or the underlying file were ever wrong instead of failing silently.
    let glassLens = null;
    let stopAdaptiveText = null;
    if (typeof window.liquidGL === "function") {
      // Values below came from actually dialing in the tuning panel against the real toolbar,
      // not a guess — bevelWidth in particular: it's a fraction of min(width, height) (confirmed
      // directly in liquidGL.js's own shader code), and this bar is ~628px wide but only ~50-64px
      // tall, so a "normal"-looking bevelWidth for a squarer element ate 30-55% of the bar's
      // total height. 0.335 here looks right specifically because bevelDepth is also nearly zero
      // (0.001) — the two trade off against each other, so don't tune one without the other.
      glassLens = window.liquidGL({
        target: bar, // an Element, not a selector string — see liquidGL.js's own patch notes
        snapshot: "body",
        refraction: 0,
        aberration: 0.19,
        bevelDepth: 0.001,
        bevelWidth: 0.335,
        frost: 0,
        ourFrost: 1.2, // not a real liquidGL option — see the sampleOurBlur() patch in liquidGL.js
        shadow: true,
        specular: true,
        tilt: false,
        reveal: "fade",
      });
      if (SONOSCRIPT_GLASS_TUNING) {
        buildTuningPanel(shadow, glassLens, {
          tintEl: bar.querySelector(".sono-tint"),
        });
      }
      stopAdaptiveText = initAdaptiveTheme(bar, glassLens);
    } else {
      console.error("SonoScript: liquidGL failed to load — toolbar will render without the glass effect.");
    }

    let ws = null;
    let reconnectAttempts = 0;
    let reconnectTimer = null;
    let closedByUser = false;
    let lastTotal = 0; // most recent known duration, from the last "state" broadcast — used to
                        // keep elapsed/remaining live during a drag without waiting on the server
    let isPlaying = false; // tracked explicitly — comparing els.playPause.innerHTML against the
                            // ICONS.play/pause source strings doesn't work: browsers don't
                            // round-trip self-closing tags through the innerHTML getter the same
                            // way they were set (e.g. `<path d="..."/>` comes back out as
                            // `<path d="..."></path>`), so that comparison was always false and
                            // the button always sent "play", never "pause".

    // Mirrors widgets.py's ScrubberView constants exactly (IDLE_SIZE/HOVER_SIZE/PRESSED_SIZE/
    // HIT_SLOP) — same three thumb sizes, same invisible grab tolerance, same "travel inset by
    // half the resting size so the thumb reaches the true track ends at rest" positioning.
    const IDLE_SIZE = 10, HOVER_SIZE = 13, PRESSED_SIZE = 16, HIT_SLOP = 8;
    const scrubber = { dragging: false, hovering: false, fraction: 0, dragOffsetPx: 0 };

    function send(cmd) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
    }

    function travelPx() {
      return Math.max(1, els.scrubberWrap.getBoundingClientRect().width - IDLE_SIZE);
    }
    function thumbCenterPx(fraction) {
      return IDLE_SIZE / 2 + travelPx() * Math.max(0, Math.min(1, fraction));
    }
    function fractionForCenterPx(px) {
      return Math.max(0, Math.min(1, (px - IDLE_SIZE / 2) / travelPx()));
    }
    function setFraction(fraction) {
      const centerPx = thumbCenterPx(fraction);
      els.scrubberFill.style.width = centerPx + "px";
      els.scrubberThumb.style.left = centerPx + "px";
    }

    function applyState(msg) {
      isPlaying = !!msg.playing;
      els.playPause.innerHTML = isPlaying ? ICONS.pause : ICONS.play;
      lastTotal = msg.total || 0;
      if (!scrubber.dragging) {
        setFraction(msg.fraction || 0);
        els.elapsed.textContent = fmtTime(msg.elapsed);
        els.remaining.textContent = fmtTime(Math.max(0, lastTotal - (msg.elapsed || 0)));
      }
      if (msg.voice_id && els.voice.value !== msg.voice_id) {
        els.voice.value = msg.voice_id;
      }
      els.generatingLabel.classList.toggle("visible", !!msg.generating);
    }

    function applyVoices(msg) {
      els.voice.innerHTML = "";
      for (const v of msg.voices || []) {
        const opt = document.createElement("option");
        opt.value = v.id;
        opt.textContent = v.label;
        els.voice.appendChild(opt);
      }
      if (msg.current_voice_id) els.voice.value = msg.current_voice_id;
    }

    function connect() {
      ws = new WebSocket(`${wsUrl}?token=${encodeURIComponent(token)}`);
      ws.onopen = () => {
        reconnectAttempts = 0;
        bar.classList.remove("disconnected");
        send({ cmd: "get_state" });
        send({ cmd: "get_voices" });
      };
      ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch (e) {
          return;
        }
        if (msg.type === "state") applyState(msg);
        else if (msg.type === "voices") applyVoices(msg);
      };
      ws.onclose = () => {
        bar.classList.add("disconnected");
        if (closedByUser) return;
        // SonoScript itself may be mid-restart, or the toolbar opened before the app finished
        // launching — back off rather than hammering a closed port, but keep trying: this tab
        // has no other way to know when the app comes back.
        reconnectAttempts++;
        const delay = Math.min(1000 * reconnectAttempts, 8000);
        reconnectTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch (e) {}
      };
    }

    els.playPause.addEventListener("click", () => {
      send({ cmd: isPlaying ? "pause" : "play" });
    });
    els.back.addEventListener("click", () => send({ cmd: "skip_back" }));
    els.forward.addEventListener("click", () => send({ cmd: "skip_forward" }));
    els.voice.addEventListener("change", () => send({ cmd: "set_voice", voice_id: els.voice.value }));

    // ----- scrubber interaction — mirrors ScrubberView's mouseDown_/mouseDragged_/mouseUp_
    // (grab-vs-jump, live vs commit-on-release); sizes/HIT_SLOP/inset-travel math live above. -----
    function currentThumbDiameter() {
      if (scrubber.dragging) return PRESSED_SIZE;
      if (scrubber.hovering) return HOVER_SIZE;
      return IDLE_SIZE;
    }
    function setScrubberClasses() {
      els.scrubberWrap.classList.toggle("hovering", scrubber.hovering);
      els.scrubberWrap.classList.toggle("dragging", scrubber.dragging);
    }
    function updateTimeLabelsLive() {
      // Live during a click-jump or drag (on_scrub's job in ScrubberView) — the real seek
      // command only goes out on release, but the labels shouldn't wait for a round trip.
      els.elapsed.textContent = fmtTime(scrubber.fraction * lastTotal);
      els.remaining.textContent = fmtTime(Math.max(0, (1 - scrubber.fraction) * lastTotal));
    }

    els.scrubberWrap.addEventListener("mouseenter", () => {
      scrubber.hovering = true;
      setScrubberClasses();
    });
    els.scrubberWrap.addEventListener("mouseleave", () => {
      scrubber.hovering = false;
      setScrubberClasses();
    });

    els.scrubberWrap.addEventListener("mousedown", (ev) => {
      const rect = els.scrubberWrap.getBoundingClientRect();
      const clickX = ev.clientX - rect.left;
      const centerX = thumbCenterPx(scrubber.fraction);
      const hitRadius = Math.max(currentThumbDiameter(), 16) / 2 + HIT_SLOP;
      if (Math.abs(clickX - centerX) <= hitRadius) {
        // Grabbed the thumb where it already is — track the offset so the drag below moves it
        // relative to the cursor instead of snapping it to be centered under the cursor.
        scrubber.dragOffsetPx = clickX - centerX;
      } else {
        // Clicked elsewhere on the track — jump straight there, same as ScrubberView.
        scrubber.dragOffsetPx = 0;
        scrubber.fraction = fractionForCenterPx(clickX);
        setFraction(scrubber.fraction);
        updateTimeLabelsLive();
      }
      scrubber.dragging = true;
      setScrubberClasses();

      const onMove = (e) => {
        const r = els.scrubberWrap.getBoundingClientRect();
        scrubber.fraction = fractionForCenterPx(e.clientX - r.left - scrubber.dragOffsetPx);
        setFraction(scrubber.fraction);
        updateTimeLabelsLive();
      };
      const onUp = () => {
        scrubber.dragging = false;
        setScrubberClasses();
        send({ cmd: "seek", fraction: scrubber.fraction });
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });

    els.close.addEventListener("click", () => {
      // Closing the toolbar is the only stop control the user has for a browser-triggered
      // read — leaving the audio running with no visible way to stop it once the bar is gone
      // would be a dead end, so this actually stops playback, not just hides the controls.
      send({ cmd: "stop" });
      closedByUser = true;
      cleanup();
      host.remove();
    });

    function cleanup() {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      // Without this, closing and reopening the toolbar on the same page (a normal thing to do,
      // unlike a typical page's one-shot glass card) would leave the old lens registered in
      // liquidGL's shared renderer forever — still being measured and rendered every frame
      // against a bar element that's no longer in the DOM. See liquidGL.js's own patch notes for
      // why this method had to be added there in the first place.
      if (glassLens && typeof glassLens.destroy === "function") glassLens.destroy();
      if (stopAdaptiveText) stopAdaptiveText();
      if (ws) {
        try {
          ws.close();
        } catch (e) {}
      }
    }
    host._sonoscriptCleanup = cleanup;

    connect();
  }

  window.__sonoscriptInitToolbar = initToolbar;
})();
