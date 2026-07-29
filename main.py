# SonoScript — main.py
# UI rewrite matching the approved mockup (see handoff/HANDOFF-NOTES.md + screenshots).

import io
import json
import os
import shlex
import ssl
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

from chunking import chunk_text, CHUNK_TARGET_CHARS, kokoro_chunk_target
from config import load_config, save_config
from text_prep import sanitize_for_speech
from voice_defaults import voice_pitch_semitones
from ui_helpers import white, fix_anchor, build_waveform_bars, make_label, symbol_image, format_playback_time
from widgets import (
    ClickThroughTextField, ScrubberView, HoverButton, icon_button, text_button, cta_button,
    FlatPopUpButton, ControlRow, FocusTextView, BackdropView, CardView, DropdownPanel,
)

APP_NAME = "SonoScript"
APP_VERSION = "1.7.1"
APP_BUILD = "27"
GITHUB_REPO = "Redcupss/sonoscript"
GITHUB_URL = "https://github.com/Redcupss"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
PLACEHOLDER_TEXT = "Paste text here (⌘V)..."
SHADOW_OPACITY = 0.6  # About/Update card drop shadow

EL_API = "https://api.elevenlabs.io/v1"
OPENAI_API = "https://api.openai.com/v1"
OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
PROVIDERS = ["System", "Kokoro", "ElevenLabs", "OpenAI", "Other"]
KEYLESS_PROVIDERS = ("System", "Kokoro")  # no API key needed — bundled/on-device
SPEEDS = ["0.5x", "0.8x", "1.0x", "1.25x", "1.5x"]


# ---------- app ----------

class AppDelegate(NSObject):
    # ----- lifecycle -----
    def applicationDidFinishLaunching_(self, notification):
        self.config = load_config()
        self.voice_ids = []
        self.player = None
        self._kokoro_engine = None  # lazy-loaded once, reused for every chunk — see _kokoroEngine
        self._kokoro_lock = threading.Lock()
        # Chunked playback state — see playPauseClicked_/_beginChunkPlayback for the pipeline.
        # playback_token identifies one Play session; background chunk results carrying a
        # stale token (from a Stop or a new Play superseding it) are dropped on arrival.
        self.playback_token = None
        self.all_chunks = []
        self.chunk_durations = []  # parallel to all_chunks; None until that chunk's real audio duration is known
        self.avg_chars_per_sec = None  # running speech-rate estimate, refined as real durations come in
        self.chunk_index = 0
        self.next_chunk_audio = None
        self.chunk_audio_cache = {}  # index -> already-generated audio bytes, so scrubbing back
                                      # to a chunk you've already heard replays it exactly
                                      # (same bytes, same real duration) instead of a fresh,
                                      # not-necessarily-identical regeneration, and doesn't
                                      # spend a fresh request on content you already paid for.
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
        else:
            self.showWelcomeScreen(show_intro=True)

        # silent update check on launch
        threading.Thread(target=self._checkUpdateWorker, args=(True,), daemon=True).start()

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    # ----- menu bar -----
    def build_main_menu(self):
        main_menu = AppKit.NSMenu.alloc().init()
        app_item = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(app_item)
        app_menu = AppKit.NSMenu.alloc().init()
        for title, action, key in [
            (f"About {APP_NAME}", "showAbout:", ""),
            ("Check for Updates", "checkForUpdatesClicked:", ""),
            (None, None, None),
            ("Set API Key", "resetApiKey:", ""),
            (None, None, None),
            (f"Quit {APP_NAME}", "terminate:", "q"),
        ]:
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
        self.window.center()
        # 360 wide: fits the control row without overlap. 434 tall: fits the welcome screen's
        # fixed-size centered container (386pt, including the Cancel button's row) with margin —
        # below that the container's flexible-margin autoresizing can't distribute negative
        # space and it snaps into a corner.
        self.window.setMinSize_(NSMakeSize(360, 458))
        self.window.setMovableByWindowBackground_(True)

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
        self.wordmark = text_button(
            APP_NAME, NSMakeRect(rect.size.width - 96, h - 32, 84, 26),
            "wordmarkClicked:", self, font, 0.0, 0.12, 7.0, white(0.55),
        )
        self.wordmark.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
        self.root.addSubview_(self.wordmark)

        self.screen_view = None
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
            {"title": "Set API Key", "on_click": lambda: self.resetApiKey_(None)},
        ]
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

    @objc.python_method
    def _showDropdown(self, anchor, rows, width=None, align="right", direction="up"):
        """rows: list of {title, selected, on_click} dicts, or None for a separator line."""
        self._closeDropdownImmediate()
        row_h, sep_h, pad, gap, max_h = 30, 10, 10, 14, 260
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
        content_h = pad * 2 + sum(sep_h if r is None else row_h for r in rows)
        height = min(content_h, max_h)
        needs_scroll = content_h > max_h

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
                    r._fill(r._base_alpha, animated=False)

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
        for r in rows:
            if r is None:
                cy -= sep_h
                line = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(12, cy + sep_h / 2.0, w - 24, 1))
                line.setWantsLayer_(True)
                line.layer().setBackgroundColor_(white(0.1).CGColor())
                container.addSubview_(line)
                continue
            cy -= row_h
            row = HoverButton.alloc().initWithFrame_(NSMakeRect(0, cy, w, row_h))
            row.configure(0.0, 0.09, 0.0)
            row.setTitle_("")
            lbl = make_label(r["title"], 13, 0.95 if r.get("selected") else 0.82)
            lbl.setFrame_(NSMakeRect(16, (row_h - 18) / 2.0, w - 32, 18))
            lbl.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
            row.addSubview_(lbl)
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
            scroll.contentView().scrollToPoint_(NSMakePoint(0, content_h - height))
            scroll.reflectScrolledClipView_(scroll.contentView())

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
        # Provider pills are measured before cw is fixed: a wider pill row (adding System, and
        # later Kokoro) must widen the whole centered column, rather than spill past a
        # hardcoded width and get clipped at both edges by the container.
        pill_h, gap, h_pad = 26, 10, 18
        pill_font = AppKit.NSFont.systemFontOfSize_weight_(11, AppKit.NSFontWeightMedium)
        pill_widths = []
        for name in PROVIDERS:
            text_w = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                name, {AppKit.NSFontAttributeName: pill_font}).size().width
            pill_widths.append(text_w + h_pad * 2)
        pills_total = sum(pill_widths) + gap * (len(PROVIDERS) - 1)

        cw = max(340, pills_total + 20)
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
        # Font/widths already measured above, before cw was sized to fit them.
        self.pill_buttons = []
        px = mx - pills_total / 2.0
        for name, w_ in zip(PROVIDERS, pill_widths):
            btn = text_button(name, NSMakeRect(px, my - 75, w_, pill_h), "providerClicked:", self,
                              pill_font, 0.04, 0.14, 13.0, white(0.55))
            btn.layer().setBorderWidth_(1.0)
            self.pill_buttons.append(btn)
            container.addSubview_(btn)
            px += w_ + gap
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
        self.continue_btn = cta_button("Continue", NSMakeRect(mx - field_w / 2.0, my - 175, field_w, 38), "saveApiKey:", self)
        self._updateContinueState()

        extras = [icon_bg, title, tagline, caption, self.key_field_box, self.continue_btn]
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
        return {"OpenAI": "OpenAI API key (sk-...)", "Other": "API key"}.get(self.provider, "ElevenLabs API key")

    @objc.python_method
    def _captionText(self):
        if self.provider == "System":
            return "Uses your Mac's built-in text-to-speech.\nFree, offline, no account needed."
        if self.provider == "Kokoro":
            return "A higher-quality offline voice, built into the app.\nFree, no account needed, no downloads required."
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
        # System/Kokoro need no key at all — swap the secure field for a plain explanatory
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
        # System/Kokoro need no key at all — an api_key is only required for the other providers.
        return self.config.get("provider") in KEYLESS_PROVIDERS or bool(self.config.get("api_key"))

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

    @objc.python_method
    def _updateContinueState(self):
        enabled = self.provider in KEYLESS_PROVIDERS or bool(str(self.key_field.stringValue()).strip())
        # Deliberately NOT calling self.continue_btn.setEnabled_(enabled) — NSButton applies
        # its own automatic dimming to a disabled control's content on top of whatever
        # attributed-title color is set, which was compounding with white(0.45) below and
        # rendering darker than the actual placeholder text it was supposed to match.
        # saveApiKey_ already no-ops on an empty key, so disabling isn't needed for correctness.
        # match the key field's own dark card styling exactly rather than the bright CTA
        # white — a full-white flash the moment you type read as jarring. Enabled/disabled is
        # now conveyed by text brightness only, never by the button itself turning white.
        # white(0.06)/white(0.12), not colorWithWhite_alpha_(...,1.0) — the key field's card
        # uses translucent white-tint fills (letting the blur show through), and an opaque
        # flat gray at the same numbers renders visibly different (solid vs. translucent).
        self.continue_btn.layer().setBackgroundColor_(white(0.06).CGColor())
        self.continue_btn.layer().setBorderColor_(white(0.12).CGColor())
        self.continue_btn.layer().setBorderWidth_(1.0)
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
        threading.Thread(target=self._validateKeyWorker, args=(key, self.provider), daemon=True).start()

    @objc.python_method
    def _validateKeyWorker(self, key, provider):
        try:
            if provider == "ElevenLabs":
                self._request_json(f"{EL_API}/user", {"xi-api-key": key})
            elif provider == "OpenAI":
                self._request_json(f"{OPENAI_API}/models", {"Authorization": f"Bearer {key}"})
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
            self.key_field.setStringValue_("")  # clear the rejected key
            self.key_field.setPlaceholderString_("")  # don't let it show through the error text
            self._updateContinueState()
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

    # ----- main screen -----
    def showMainScreen(self):
        self._teardownWelcomeEscMonitor()
        v = AppKit.NSView.alloc().initWithFrame_(self.root.bounds())
        b = v.bounds()
        W = b.size.width

        self.usage_label = make_label("", 11, 0.5)
        self.usage_label.setFrame_(NSMakeRect(20, b.size.height - 58, W - 40, 16))
        self.usage_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)

        # text card (flexible height); controls live below it. 156 instead of 128 leaves room
        # for the scrubber row (124-148) between the card and the transport controls (70-112).
        card_bottom = 156
        self.card = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(20, card_bottom, W - 40, b.size.height - 58 - 8 - card_bottom))
        self.card.setWantsLayer_(True)
        self.card.layer().setBackgroundColor_(white(0.06).CGColor())
        self.card.layer().setBorderColor_(white(0.10).CGColor())
        self.card.layer().setBorderWidth_(1.0)
        self.card.layer().setCornerRadius_(14.0)
        self.card.layer().setMasksToBounds_(True)
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
        self.text_view.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self.text_view.setRichText_(False)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextContainerInset_(NSMakeSize(10, 10))
        self.text_view.setVerticallyResizable_(True)
        self.text_view.setHorizontallyResizable_(False)
        self.text_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        # allowsUndo defaults to NO for a plain (non-field-editor) NSTextView — without this,
        # Cmd-Z/Cmd-Shift-Z are silent no-ops no matter how the undo manager itself resolves.
        self.text_view.setAllowsUndo_(True)
        self.text_view.setDelegate_(self)
        self.text_view.focus_callback = self._cardFocusChanged
        scroll.setDocumentView_(self.text_view)

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
        for s in SPEEDS:
            self.speed_popup.addItemWithTitle_(s)
        self.speed_popup.selectItemWithTitle_(self.config.get("speed", "1.0x"))
        self.speed_popup.setTarget_(self)
        self.speed_popup.setAction_("speedChanged:")
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

        self.status_label = make_label("", 10, 0.5, align=AppKit.NSTextAlignmentCenter)
        self.status_label.setFrame_(NSMakeRect(20, 52, W - 40, 13))
        self.status_label.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.status_label.setAlphaValue_(0.0)

        voice_lbl = make_label("Voice", 13, 0.85)
        voice_lbl.setFrame_(NSMakeRect(20, 20, 44, 20))
        self.voice_popup = FlatPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(72, 14, W - 92, 36), False)
        self.voice_popup.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.voice_popup.setTarget_(self)
        self.voice_popup.setAction_("voiceChanged:")

        for sub in (self.usage_label, self.card, row, self.elapsed_label, self.remaining_label,
                    self.scrubber, self.status_label, voice_lbl, self.voice_popup):
            v.addSubview_(sub)
        self.current_screen = "main"
        self.swap_screen(v)
        self._syncPlaybackUI()
        self.updateCharCount()

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
        self.status_label.setStringValue_(text)
        self.status_label.animator().setAlphaValue_(1.0 if text else 0.0)

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
        self.config["speed"] = str(self.speed_popup.titleOfSelectedItem())
        save_config(self.config)
        self._invalidateUngeneratedChunks()

    def voiceChanged_(self, sender):
        i = self.voice_popup.indexOfSelectedItem()
        if 0 <= i < len(self.voice_ids):
            self.config["voice_id"] = self.voice_ids[i]
            save_config(self.config)
            self._invalidateUngeneratedChunks()

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
        self.next_chunk_audio = None
        self.chunk_durations = [None] * len(self.all_chunks)
        self.avg_chars_per_sec = None
        if self.player is not None:
            self._prefetchNextChunk()

    # ----- voices / usage -----
    def fetchVoices(self):
        provider = self.config.get("provider", "ElevenLabs")
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
        elif provider == "Kokoro":
            voices_dir = self._resourcePath("kokoro_assets", "voices")
            ids = [os.path.splitext(f)[0] for f in os.listdir(voices_dir) if f.endswith(".bin")]
            ids.sort(key=lambda vid: vid.split("_", 1)[1])  # by person's name, not raw id
            self.voice_ids = ids
            self._populateVoiceMenu([self._kokoroVoiceLabel(v) for v in ids])
            self.usage_label.setStringValue_("A free, offline neural voice — no account, no limit.")
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
        self.voice_popup.removeAllItems()
        for label in labels:
            self.voice_popup.addItemWithTitle_(label)
        saved = self.config.get("voice_id")
        idx = self.voice_ids.index(saved) if saved in self.voice_ids else 0
        if labels:
            self.voice_popup.selectItemAtIndex_(idx)
            self.config["voice_id"] = self.voice_ids[idx]
            save_config(self.config)

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
            self._syncPlaybackUI()
            return
        text = str(self.text_view.string())
        if not text or not self._isConfigured():
            return
        if self.all_chunks and self.session_text == text:
            # Playback ran to the end and was never a "real" stop — all_chunks and
            # chunk_audio_cache are still sitting there from that session, so this is just
            # restarting from 0 within it, not a new one. _seekToVirtualTime finds the cache
            # hit on its own and replays the exact same audio instead of regenerating it.
            self._seekToVirtualTime(0.0)
            return
        target_chars = kokoro_chunk_target if self.config.get("provider") == "Kokoro" else CHUNK_TARGET_CHARS
        chunks = chunk_text(text, target_chars)
        if not chunks:
            return
        # Text differs from whatever session_text left behind (or there was none) — any cache
        # still sitting around belongs to that other text and must not be reused for this one.
        self.chunk_audio_cache = {}
        self.next_chunk_audio = None
        self.session_text = text
        self.all_chunks = chunks
        self.chunk_durations = [None] * len(chunks)
        self.avg_chars_per_sec = None
        self._seekToVirtualTime(0.0)

    @objc.python_method
    def _requestTTS(self, text):
        text = sanitize_for_speech(text)
        provider = self.config.get("provider", "ElevenLabs")
        key = self.config.get("api_key", "")
        voice = self.config.get("voice_id", "")
        speed = float(self.config.get("speed", "1.0x").rstrip("x"))
        if provider == "System":
            return self._requestSystemTTS(text, voice, speed)
        if provider == "Kokoro":
            return self._requestKokoroTTS(text, voice, speed)
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
    def _resourcePath(self, *parts):
        # In the frozen py2app bundle, py2app sets RESOURCEPATH to Contents/Resources and
        # "resources": ["kokoro_assets"] in setup.py copies the whole directory tree there
        # unchanged; in dev mode (running main.py directly) there's no RESOURCEPATH, so this
        # falls back to the script's own directory, where kokoro_assets/ also lives. Same
        # relative layout either way — validated against a real frozen build beforehand.
        base = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, *parts)

    @objc.python_method
    def _stubUnusedPhonemizerBackends(self):
        # phonemizer's package __init__ unconditionally imports ALL FOUR of its backends
        # (espeak, espeak-mbrola, festival, segments) just to build one name->class lookup
        # dict — even though we only ever use espeak (see _requestKokoroTTS/_kokoroEngine).
        # The festival and segments backends drag in a large, otherwise-unnecessary
        # dependency tree (segments -> csvw -> rdflib and jsonschema, festival -> its own CLI
        # tooling) purely to be importable, never to be used — Kokoro only ever passes
        # lang="en-us"/"en-gb", which resolves to the espeak backend. Pre-registering fake
        # modules for just those two makes Python's import machinery skip the real (and,
        # for this app, pointless) imports entirely, so none of that tree needs bundling.
        import sys
        import types
        if "phonemizer.backend.segments" not in sys.modules:
            mod = types.ModuleType("phonemizer.backend.segments")

            class SegmentsBackend:
                @staticmethod
                def name():
                    return "segments"

                @staticmethod
                def is_available():
                    return False

            mod.SegmentsBackend = SegmentsBackend
            sys.modules["phonemizer.backend.segments"] = mod
        if "phonemizer.backend.festival.festival" not in sys.modules:
            pkg = types.ModuleType("phonemizer.backend.festival")
            mod = types.ModuleType("phonemizer.backend.festival.festival")

            class FestivalBackend:
                @staticmethod
                def name():
                    return "festival"

                @staticmethod
                def is_available():
                    return False

            mod.FestivalBackend = FestivalBackend
            pkg.festival = mod
            sys.modules["phonemizer.backend.festival"] = pkg
            sys.modules["phonemizer.backend.festival.festival"] = mod

    @objc.python_method
    def _kokoroEngine(self):
        # Loading the model takes ~1.25s (measured) — must happen once and be reused for
        # every chunk, not reloaded per request. _chunkWorker runs on background threads
        # (current chunk + prefetch can both be in flight), so the lazy init itself needs a
        # lock; the loaded engine's own .create() calls are safe to share across threads.
        if getattr(self, "_kokoro_engine", None) is not None:
            return self._kokoro_engine
        with self._kokoro_lock:
            if self._kokoro_engine is None:
                import numpy as np
                self._stubUnusedPhonemizerBackends()
                from kokoro_onnx import Kokoro, EspeakConfig
                model_path = self._resourcePath("kokoro_assets", "model.onnx")
                espeak_config = EspeakConfig(
                    lib_path=self._resourcePath("kokoro_assets", "espeak_bundle", "libespeak-ng.dylib"),
                    data_path=self._resourcePath("kokoro_assets", "espeak_bundle", "espeak-ng-data"),
                )
                # Kokoro's constructor unconditionally np.load()s voices_path even though we
                # pass each voice's real style vector straight to create() ourselves — it just
                # needs something loadable, never actually read for content. Validated in the
                # py2app spike beforehand.
                dummy_voices = os.path.join(tempfile.gettempdir(), "sonoscript_kokoro_dummy_voices.npz")
                if not os.path.exists(dummy_voices):
                    np.savez(dummy_voices, dummy=np.zeros(1, dtype=np.float32))
                engine = Kokoro(model_path, dummy_voices, espeak_config=espeak_config)

                # kokoro-onnx 0.5.0 (current latest) has two real bugs in _create_audio for
                # this model's input layout (the "input_ids" branch, used when the export names
                # its token input "input_ids" rather than "tokens"): it builds the "speed"
                # input as int32 when the model requires float32 (every call raises
                # InvalidArgument), and the raw session output is (1, N), not (N,) — left
                # un-flattened, downstream trimming/duration math silently treats it as a
                # 1-sample array instead of raising, producing a near-empty result. Patching
                # just this one method with both fixed — everything else (tokenizing, phoneme-
                # length batching, trimming, concatenation) stays exactly as create() does it.
                def _fixed_create_audio(kokoro_self, phonemes, voice, speed):
                    tokens = np.array(kokoro_self.tokenizer.tokenize(phonemes), dtype=np.int64)
                    style = np.array(voice[len(tokens)], dtype=np.float32)
                    inputs = {
                        "input_ids": [[0, *tokens.tolist(), 0]],
                        "style": style,
                        "speed": np.array([speed], dtype=np.float32),
                    }
                    audio = kokoro_self.sess.run(None, inputs)[0].reshape(-1)
                    return audio, kokoro_onnx_config.SAMPLE_RATE
                import types
                from kokoro_onnx import config as kokoro_onnx_config
                engine._create_audio = types.MethodType(_fixed_create_audio, engine)

                self._kokoro_engine = engine
        return self._kokoro_engine

    @objc.python_method
    def _requestKokoroTTS(self, text, voice_identifier, speed):
        import numpy as np
        kokoro = self._kokoroEngine()
        voice_path = self._resourcePath("kokoro_assets", "voices", f"{voice_identifier}.bin")
        voice = np.fromfile(voice_path, dtype=np.float32).reshape(-1, 1, 256)
        # British voices (bf_/bm_) need en-gb phonemization or American pronunciation rules
        # get applied to a British voice — American voices (af_/am_) use en-us.
        lang = "en-gb" if voice_identifier.startswith("b") else "en-us"
        audio, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)

        semitones = voice_pitch_semitones(voice_identifier)
        if semitones:
            from pitch_shift import pitch_shift
            audio = pitch_shift(audio, sample_rate, semitones)

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
    def _kokoroVoiceLabel(self, voice_id):
        # Identifiers follow Kokoro's own convention: first letter is accent (a=American,
        # b=British), second is gender (f/m), rest is the voice's name (af_bella -> Bella).
        prefix, _, name = voice_id.partition("_")
        accent = "American" if prefix[0] == "a" else "British" if prefix[0] == "b" else prefix[0].upper()
        gender = "Female" if prefix[1] == "f" else "Male" if prefix[1] == "m" else prefix[1].upper()
        return f"{name.capitalize()} ({accent}, {gender})"

    @objc.python_method
    def _chunkWorker(self, text, token, role, index, offset):
        try:
            audio = self._requestTTS(text)
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": audio, "error": None}
        except urllib.error.HTTPError as e:
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": None,
                      "error": f"TTS request failed (HTTP {e.code})."}
        except urllib.error.URLError as e:
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": None,
                      "error": f"Could not reach provider: {e.reason}"}
        except Exception as e:
            # Covers the System voice path (AVSpeechSynthesizer, no urllib involved) — without
            # this, an unexpected failure there would just kill the background thread silently,
            # leaving the UI stuck showing "Generating..." forever with no way out but Stop.
            result = {"token": token, "role": role, "index": index, "offset": offset, "audio": None,
                      "error": f"Couldn't generate speech: {e}"}
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
        if result["role"] == "seek":
            self.chunk_index = result["index"]
            self._beginChunkPlayback(result["audio"], start_offset=result["offset"])
        elif self.waiting_for_next:
            self.waiting_for_next = False
            self._beginChunkPlayback(result["audio"])
        else:
            self.next_chunk_audio = result["audio"]
            self.chunk_audio_cache[result["index"]] = result["audio"]

    @objc.python_method
    def _beginChunkPlayback(self, audio_bytes, start_offset=0.0):
        player, err = AVFoundation.AVAudioPlayer.alloc().initWithData_error_(bytes(audio_bytes), None)
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
        self.player.play()
        self.setStatus("")
        self._syncPlaybackUI()
        self._startProgressTimer()
        if self.config.get("provider", "ElevenLabs") == "ElevenLabs":
            threading.Thread(target=self._fetchElVoicesWorker, daemon=True).start()  # refresh usage
        self._prefetchNextChunk()

    @objc.python_method
    def _prefetchNextChunk(self):
        next_index = self.chunk_index + 1
        if next_index >= len(self.all_chunks):
            return
        cached = self.chunk_audio_cache.get(next_index)
        if cached is not None:
            self.next_chunk_audio = cached
            return
        threading.Thread(
            target=self._chunkWorker,
            args=(self.all_chunks[next_index], self.playback_token, "prefetch", next_index, 0.0),
            daemon=True,
        ).start()

    @objc.python_method
    def _resetPlaybackState(self):
        self.player = None
        self.playback_token = None
        self.all_chunks = []
        self.chunk_durations = []
        self.avg_chars_per_sec = None
        self.chunk_index = 0
        self.next_chunk_audio = None
        self.chunk_audio_cache = {}
        self.session_text = None
        self.waiting_for_next = False

    def audioPlayerDidFinishPlaying_successfully_(self, player, flag):
        if player is not self.player:
            return  # a stale delegate callback from a player Stop already replaced/cleared
        next_index = self.chunk_index + 1
        if next_index >= len(self.all_chunks):
            # Reached the actual end of the document. Deliberately NOT _resetPlaybackState()
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
        self._seekToVirtualTime(current - 15.0)

    def skipForward_(self, sender):
        if self.player is None or not self.all_chunks:
            return
        current = self._cumulativeDurationBefore(self.chunk_index) + self.player.currentTime()
        self._seekToVirtualTime(current + 15.0)

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
    def _seekToVirtualTime(self, target_seconds):
        if not self.all_chunks:
            return
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
            self._beginChunkPlayback(cached, start_offset=offset)
            return

        self.setStatus("Generating...")
        threading.Thread(
            target=self._chunkWorker,
            args=(self.all_chunks[target_index], self.playback_token, "seek", target_index, offset),
            daemon=True,
        ).start()

    @objc.python_method
    def _startProgressTimer(self):
        self._stopProgressTimer()
        self.progress_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.25, True, lambda t: self._updateScrubberUI())

    @objc.python_method
    def _stopProgressTimer(self):
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

    @objc.python_method
    def _scrubberDragged(self, fraction):
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
        total = self._totalEstimatedDuration()
        self._seekToVirtualTime(fraction * total)

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
