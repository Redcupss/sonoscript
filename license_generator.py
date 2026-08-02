# SonoScript License Generator — a small standalone companion app, not part of the shipped
# product. Wraps tools/sign_license.py's own signing logic in a GUI so minting a Sesame
# license doesn't require the terminal. Reuses SonoScript's own UI components (widgets.py/
# ui_helpers.py) directly so it looks and feels like the same app family, without pulling in
# any of SonoScript's TTS/audio dependencies — this file only ever imports AppKit plumbing,
# the two lightweight UI helper modules, and the signing function itself.
#
# Needs the private signing key (~/.sonoscript/signing_key.bin, created automatically on
# first use — see tools/sign_license.py's own docstring) to actually mint anything, so this
# only ever runs on the machine that owns that key. Never distribute this app itself.

import os
import sys
from datetime import datetime, timezone

import AppKit
import objc
from Foundation import NSMakeRect, NSObject

# Both relative to THIS file's own location, not the current working directory — so this
# still works if it's ever launched by double-clicking or from a different folder, not just
# "run from the repo root."
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "tools"))
from sign_license import sign_license  # noqa: E402

from ui_helpers import white, make_label  # noqa: E402
from widgets import HoverButton, CardView, cta_button  # noqa: E402

APP_NAME = "SonoScript"


def _field_box(frame):
    box = AppKit.NSView.alloc().initWithFrame_(frame)
    box.setWantsLayer_(True)
    box.layer().setBackgroundColor_(white(0.06).CGColor())
    box.layer().setBorderColor_(white(0.12).CGColor())
    box.layer().setBorderWidth_(1.0)
    box.layer().setCornerRadius_(10.0)
    return box


def _text_field(box, placeholder, width):
    field = AppKit.NSTextField.alloc().initWithFrame_(NSMakeRect(14, 0, width - 28, 20))
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
    field.setTextColor_(white(0.92))
    field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
    field.setPlaceholderString_(placeholder)
    field.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
    box.addSubview_(field)
    # Same "measure the field's own natural height, then center exactly that" trick used for
    # the real app's key field — an arbitrary fixed frame height doesn't vertically center a
    # field's actual glyph position.
    field.sizeToFit()
    fitted = field.frame()
    field.setFrame_(NSMakeRect(14, (box.frame().size.height - fitted.size.height) / 2.0,
                                width - 28, fitted.size.height))
    return field


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        W, H = 380, 520
        style = (
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskFullSizeContentView
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, AppKit.NSBackingStoreBuffered, False)
        self.window.setTitle_("License Generator")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        self.window.setAppearance_(AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        self.window.center()
        self.window.setMovableByWindowBackground_(True)

        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        self.window.setContentView_(effect)

        tint = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.0, 0.45).CGColor())
        tint.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        effect.addSubview_(tint)

        # ----- header -----
        wordmark = make_label(APP_NAME, 12.5, 0.5, AppKit.NSFontWeightSemibold, AppKit.NSTextAlignmentCenter)
        wordmark.setFrame_(NSMakeRect(0, H - 56, W, 16))
        effect.addSubview_(wordmark)

        title = make_label("License Generator", 21, 0.95, AppKit.NSFontWeightBold, AppKit.NSTextAlignmentCenter)
        title.setFrame_(NSMakeRect(0, H - 92, W, 28))
        effect.addSubview_(title)

        subtitle = make_label("Mint a Sesame license key", 12, 0.5, align=AppKit.NSTextAlignmentCenter)
        subtitle.setFrame_(NSMakeRect(0, H - 114, W, 16))
        effect.addSubview_(subtitle)

        # ----- form -----
        field_w = W - 48
        name_label = make_label("Name", 11.5, 0.5, AppKit.NSFontWeightMedium)
        name_label.setFrame_(NSMakeRect(24, H - 150, field_w, 16))
        effect.addSubview_(name_label)

        name_box = _field_box(NSMakeRect(24, H - 190, field_w, 36))
        effect.addSubview_(name_box)
        self.name_field = _text_field(name_box, "e.g. a friend's name", field_w)
        self.name_field.setTarget_(self)
        self.name_field.setAction_("generateClicked:")  # Enter submits

        expires_label = make_label("Expires (optional)", 11.5, 0.5, AppKit.NSFontWeightMedium)
        expires_label.setFrame_(NSMakeRect(24, H - 220, field_w, 16))
        effect.addSubview_(expires_label)

        expires_box = _field_box(NSMakeRect(24, H - 260, field_w, 36))
        effect.addSubview_(expires_box)
        self.expires_field = _text_field(expires_box, "YYYY-MM-DD — blank = lifetime", field_w)
        self.expires_field.setTarget_(self)
        self.expires_field.setAction_("generateClicked:")

        self.error_label = make_label("", 11.5, 0.85, align=AppKit.NSTextAlignmentCenter)
        self.error_label.setTextColor_(AppKit.NSColor.systemRedColor())
        self.error_label.setFrame_(NSMakeRect(24, H - 282, field_w, 16))
        effect.addSubview_(self.error_label)

        self.generate_btn = cta_button("Generate", NSMakeRect(24, H - 328, field_w, 38),
                                        "generateClicked:", self)
        effect.addSubview_(self.generate_btn)

        # ----- result card (hidden until a key is generated) -----
        self.result_card = CardView.alloc().initWithFrame_(NSMakeRect(24, 24, field_w, H - 372))
        self.result_card.setWantsLayer_(True)
        self.result_card.layer().setBackgroundColor_(white(0.045).CGColor())
        self.result_card.layer().setBorderColor_(white(0.1).CGColor())
        self.result_card.layer().setBorderWidth_(1.0)
        self.result_card.layer().setCornerRadius_(12.0)
        self.result_card.setHidden_(True)
        effect.addSubview_(self.result_card)

        result_label = make_label("KEY", 10, 0.4, AppKit.NSFontWeightSemibold)
        result_label.setFrame_(NSMakeRect(16, self.result_card.frame().size.height - 26, 100, 14))
        self.result_card.addSubview_(result_label)

        key_scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            NSMakeRect(16, 44, field_w - 32, self.result_card.frame().size.height - 74))
        key_scroll.setBorderType_(AppKit.NSNoBorder)
        key_scroll.setHasVerticalScroller_(True)
        key_scroll.setDrawsBackground_(False)
        key_scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self.key_text = AppKit.NSTextView.alloc().initWithFrame_(key_scroll.bounds())
        self.key_text.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(12, AppKit.NSFontWeightRegular))
        self.key_text.setTextColor_(white(0.9))
        self.key_text.setDrawsBackground_(False)
        self.key_text.setEditable_(False)
        self.key_text.setSelectable_(True)  # the whole point — easy manual copy too
        self.key_text.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        key_scroll.setDocumentView_(self.key_text)
        self.result_card.addSubview_(key_scroll)

        self.copy_btn = HoverButton.alloc().initWithFrame_(NSMakeRect(16, 12, field_w - 32, 28))
        self.copy_btn.configure(0.08, 0.16, 8.0)
        self.copy_btn.setTarget_(self)
        self.copy_btn.setAction_("copyClicked:")
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: white(0.85),
        }
        self.copy_btn.setAttributedTitle_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("Copy to Clipboard", attrs))
        self.result_card.addSubview_(self.copy_btn)

        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self.window.makeFirstResponder_(self.name_field)

    @objc.python_method
    def _flashError(self, message):
        self.error_label.setStringValue_(message)
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            2.5, False, lambda t: self.error_label.setStringValue_(""))

    def generateClicked_(self, sender):
        name = str(self.name_field.stringValue()).strip()
        if not name:
            self._flashError("Enter a name first.")
            self.window.makeFirstResponder_(self.name_field)
            return

        expires_raw = str(self.expires_field.stringValue()).strip()
        expires = None
        if expires_raw:
            try:
                expires = datetime.fromisoformat(expires_raw).replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                self._flashError("Expiry date must look like 2026-12-31.")
                self.window.makeFirstResponder_(self.expires_field)
                return

        try:
            key = sign_license(name, expires)
        except Exception as e:
            self._flashError(f"Couldn't generate a key: {e}")
            return

        self.key_text.setString_(key)
        self.result_card.setHidden_(False)

    def copyClicked_(self, sender):
        key = str(self.key_text.string())
        if not key:
            return
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(key, AppKit.NSPasteboardTypeString)
        original = self.copy_btn.attributedTitle()
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.systemGreenColor(),
        }
        self.copy_btn.setAttributedTitle_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("Copied!", attrs))

        def _restore(t):
            self.copy_btn.setAttributedTitle_(original)
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.5, False, _restore)

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return True


if __name__ == "__main__":
    app = AppKit.NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    app.run()
