import AppKit
import Quartz
import objc
from Foundation import NSMakeRect, NSMakePoint, NSPointInRect

from ui_helpers import white, make_label, symbol_image


class ClickThroughTextField(AppKit.NSTextField):
    """A label that never intercepts mouse events, even at alpha 0 — AppKit hit-testing is
    based on frame containment, not opacity, so a plain label overlaid on top of a control
    (e.g. an inline error message drawn over an input field) silently swallows every click
    meant for the control underneath it unless hitTest_ is overridden like this."""

    def hitTest_(self, point):
        return None


class VerticallyCenteredCell(AppKit.NSTextFieldCell):
    """A plain NSTextFieldCell top-aligns its content whenever the field's own frame is taller
    than one line of text — noticeable here because EditableNameField's frame is sized for a
    comfortable double-click target, not just the text's own tight bounding box. Overriding
    where the title/editor/selection actually draws is the standard Cocoa fix."""

    @objc.python_method
    def _centeredRect(self, bounds):
        size = self.attributedStringValue().size()
        return AppKit.NSMakeRect(
            bounds.origin.x, bounds.origin.y + (bounds.size.height - size.height) / 2.0,
            bounds.size.width, size.height)

    def titleRectForBounds_(self, bounds):
        return self._centeredRect(bounds)

    def drawInteriorWithFrame_inView_(self, frame, view):
        objc.super(VerticallyCenteredCell, self).drawInteriorWithFrame_inView_(self._centeredRect(frame), view)

    def selectWithFrame_inView_editor_delegate_start_length_(self, frame, view, editor, delegate, start, length):
        objc.super(VerticallyCenteredCell, self).selectWithFrame_inView_editor_delegate_start_length_(
            self._centeredRect(frame), view, editor, delegate, start, length)

    def editWithFrame_inView_editor_delegate_event_(self, frame, view, editor, delegate, event):
        objc.super(VerticallyCenteredCell, self).editWithFrame_inView_editor_delegate_event_(
            self._centeredRect(frame), view, editor, delegate, event)


class EditableNameField(AppKit.NSTextField):
    """A list-row name that reads as plain text at rest — a soft OUTLINE fades in on hover
    (signaling "double-click to rename"), and only an actual double-click makes it genuinely
    editable (a darker, recessed-looking fill, no outline). Committing (Enter, or clicking
    elsewhere — resigning first responder fires the normal delegate path) should call
    endEditingAppearance() to revert back to plain-text look. Modeled on how a native macOS
    list (e.g. System Settings' Text Replacements) only shows an edit affordance on
    interaction, rather than every row permanently looking like its own little text-entry
    form."""

    HOVER_BORDER_COLOR = white(0.22)
    EDITING_FILL_COLOR = AppKit.NSColor.blackColor().colorWithAlphaComponent_(0.30)

    @classmethod
    def cellClass(cls):
        return VerticallyCenteredCell

    @objc.python_method
    def configure(self):
        self._tracking_area = None
        self.setBezeled_(False)
        self.setBordered_(False)
        self.setDrawsBackground_(False)
        self.setEditable_(False)
        self.setSelectable_(False)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(6.0)
        self.layer().setBackgroundColor_(AppKit.NSColor.clearColor().CGColor())
        self.layer().setBorderWidth_(1.0)
        self.layer().setBorderColor_(self.HOVER_BORDER_COLOR.colorWithAlphaComponent_(0.0).CGColor())

    def updateTrackingAreas(self):
        objc.super(EditableNameField, self).updateTrackingAreas()
        if self._tracking_area is not None:
            self.removeTrackingArea_(self._tracking_area)
        opts = AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInKeyWindow
        self._tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None)
        self.addTrackingArea_(self._tracking_area)

    def resetCursorRects(self):
        # A plain NSTextField installs an I-beam cursor rect for its own bounds regardless of
        # editable/selectable state — confirmed by direct testing (the cursor changed on hover
        # even at rest, before any real editing session existed). Force the normal arrow
        # cursor while in label mode; let the default (I-beam) behavior take over once actually
        # editing, where it's the correct cursor again.
        if not self.isEditable():
            self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.arrowCursor())
        else:
            objc.super(EditableNameField, self).resetCursorRects()

    def mouseEntered_(self, event):
        if not self.isEditable():
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setAnimationDuration_(0.15)
            self.layer().setBorderColor_(self.HOVER_BORDER_COLOR.CGColor())
            AppKit.CATransaction.commit()

    def mouseExited_(self, event):
        if not self.isEditable():
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setAnimationDuration_(0.15)
            self.layer().setBorderColor_(self.HOVER_BORDER_COLOR.colorWithAlphaComponent_(0.0).CGColor())
            AppKit.CATransaction.commit()

    def mouseDown_(self, event):
        # Single clicks are swallowed entirely while in label mode — no cursor, no selection,
        # nothing — only a genuine double-click starts editing. Once editable, clicks behave
        # exactly like a normal text field again (positioning the cursor, etc.).
        if not self.isEditable():
            if event.clickCount() >= 2:
                self._beginEditing()
            return
        objc.super(EditableNameField, self).mouseDown_(event)

    @objc.python_method
    def _beginEditing(self):
        self.setEditable_(True)
        self.setSelectable_(True)
        self.layer().setBorderColor_(self.HOVER_BORDER_COLOR.colorWithAlphaComponent_(0.0).CGColor())
        self.layer().setBackgroundColor_(self.EDITING_FILL_COLOR.CGColor())
        if self.window() is not None:
            self.window().makeFirstResponder_(self)
            self.window().invalidateCursorRectsForView_(self)
            editor = self.currentEditor()
            if editor is not None:
                editor.selectAll_(None)

    @objc.python_method
    def endEditingAppearance(self):
        """Called by the delegate (controlTextDidEndEditing_) once a rename commits — reverts
        to the plain-text, non-editable look, same as before the double-click."""
        self.setEditable_(False)
        self.setSelectable_(False)
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setAnimationDuration_(0.15)
        self.layer().setBackgroundColor_(AppKit.NSColor.clearColor().CGColor())
        AppKit.CATransaction.commit()
        if self.window() is not None:
            self.window().invalidateCursorRectsForView_(self)


class ScrubberView(AppKit.NSView):
    """Draggable playback-position track: a thin filled bar + round thumb inside a taller
    click/drag target. on_scrub fires live while dragging (for the time labels), on_scrub_end
    fires once on release (to actually seek) — seeking on every intermediate drag position
    would mean re-requesting audio dozens of times over the course of one drag.

    Three thumb sizes (idle -> hover -> pressed), each an animated transition, not a snap.
    Clicking ON the thumb grabs it in place (drags relative to where it already is) rather
    than jumping it under the cursor; clicking elsewhere on the track still jumps straight
    there. Travel is inset by half the RESTING (idle) size, so the thumb reaches the true
    ends of the track at rest — when it grows for hover/press while sitting at an end, it
    needs to overhang the VIEW's own bounds by a couple points rather than the track visibly
    falling short of the ends, so masksToBounds is explicitly off (NSView's auto-created
    backing layer defaults it ON, unlike a bare CALayer — this bit us once already on the
    About/Update card's shadow, which needed the same explicit override)."""

    IDLE_SIZE = 10.0
    HOVER_SIZE = 13.0
    PRESSED_SIZE = 16.0
    HIT_SLOP = 8.0  # invisible extra grab tolerance around the visual thumb, each side

    # Track (unplayed, to the right of the thumb) must always read as darker/less prominent
    # than the fill (played, to the left) so the two are never ambiguous — the fill is a
    # slight, not dramatic, step up from the track, and the track's own hover-brighten is
    # capped well below the fill so it can never approach/match it.
    TRACK_COLOR = white(0.12)
    TRACK_HOVER_COLOR = white(0.20)
    FILL_COLOR = white(0.40)

    @objc.python_method
    def configure(self):
        self.fraction = 0.0
        self.dragging = False
        self.hovering = False
        self.drag_offset = 0.0
        self.on_scrub = None
        self.on_scrub_end = None
        self._tracking_area = None
        self.setWantsLayer_(True)
        self.layer().setMasksToBounds_(False)
        self.track_layer = Quartz.CALayer.layer()
        self.track_layer.setBackgroundColor_(self.TRACK_COLOR.CGColor())
        self.fill_layer = Quartz.CALayer.layer()
        self.fill_layer.setBackgroundColor_(self.FILL_COLOR.CGColor())
        self.thumb_layer = Quartz.CALayer.layer()
        for layer in (self.track_layer, self.fill_layer, self.thumb_layer):
            self.layer().addSublayer_(layer)
        self._applyPositions()
        self._applyAppearance(animated=False)

    @objc.python_method
    def _currentThumbDiameter(self):
        if self.dragging:
            return self.PRESSED_SIZE
        if self.hovering:
            return self.HOVER_SIZE
        return self.IDLE_SIZE

    @objc.python_method
    def _travel(self):
        return max(1.0, self.bounds().size.width - self.IDLE_SIZE)

    @objc.python_method
    def _thumbCenterX(self):
        return self.IDLE_SIZE / 2.0 + self._travel() * max(0.0, min(1.0, self.fraction))

    @objc.python_method
    def _fractionForCenterX(self, center_x):
        return max(0.0, min(1.0, (center_x - self.IDLE_SIZE / 2.0) / self._travel()))

    @objc.python_method
    def _applyPositions(self):
        # Instant, no easing — this tracks the mouse (or a live playback tick) 1:1, and an
        # animated lag here would feel like the thumb is chasing the cursor.
        b = self.bounds()
        track_h = 4.0
        track_y = (b.size.height - track_h) / 2.0
        center_x = self._thumbCenterX()
        thumb_d = self._currentThumbDiameter()
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setDisableActions_(True)
        self.track_layer.setFrame_(NSMakeRect(0, track_y, b.size.width, track_h))
        self.track_layer.setCornerRadius_(track_h / 2.0)
        self.fill_layer.setFrame_(NSMakeRect(0, track_y, center_x, track_h))
        self.fill_layer.setCornerRadius_(track_h / 2.0)
        self.thumb_layer.setFrame_(NSMakeRect(center_x - thumb_d / 2.0, (b.size.height - thumb_d) / 2.0, thumb_d, thumb_d))
        self.thumb_layer.setCornerRadius_(thumb_d / 2.0)
        AppKit.CATransaction.commit()

    @objc.python_method
    def _applyAppearance(self, animated=True):
        # Size/brightness are state TRANSITIONS (idle -> hover -> pressed), not moment-to-
        # moment tracking, so these visibly grow/brighten into place rather than snapping.
        b = self.bounds()
        center_x = self._thumbCenterX()
        thumb_d = self._currentThumbDiameter()
        track_bright = self.hovering or self.dragging
        AppKit.CATransaction.begin()
        if animated:
            AppKit.CATransaction.setAnimationDuration_(0.15)
        else:
            AppKit.CATransaction.setDisableActions_(True)
        self.track_layer.setBackgroundColor_((self.TRACK_HOVER_COLOR if track_bright else self.TRACK_COLOR).CGColor())
        self.thumb_layer.setBackgroundColor_((AppKit.NSColor.whiteColor() if self.dragging else white(0.95)).CGColor())
        self.thumb_layer.setFrame_(NSMakeRect(center_x - thumb_d / 2.0, (b.size.height - thumb_d) / 2.0, thumb_d, thumb_d))
        self.thumb_layer.setCornerRadius_(thumb_d / 2.0)
        AppKit.CATransaction.commit()

    def setFrame_(self, frame):
        objc.super(ScrubberView, self).setFrame_(frame)
        if hasattr(self, "track_layer"):
            self._applyPositions()
            self._applyAppearance(animated=False)

    @objc.python_method
    def setFraction(self, fraction):
        self.fraction = fraction
        self._applyPositions()

    def updateTrackingAreas(self):
        objc.super(ScrubberView, self).updateTrackingAreas()
        if self._tracking_area is not None:
            self.removeTrackingArea_(self._tracking_area)
        opts = AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInKeyWindow
        self._tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None)
        self.addTrackingArea_(self._tracking_area)

    def mouseEntered_(self, event):
        self.hovering = True
        self._applyAppearance(animated=True)

    def mouseExited_(self, event):
        self.hovering = False
        self._applyAppearance(animated=True)

    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        center_x = self._thumbCenterX()
        hit_radius = max(self._currentThumbDiameter(), 16.0) / 2.0 + self.HIT_SLOP
        if abs(pt.x - center_x) <= hit_radius:
            # Click landed on the thumb itself — grab it where it already is instead of
            # snapping it under the cursor; drag position is tracked relative to this offset.
            self.drag_offset = pt.x - center_x
        else:
            # Click elsewhere on the track — jump straight there, same as before.
            self.drag_offset = 0.0
            self.fraction = self._fractionForCenterX(pt.x)
            self._applyPositions()
            if self.on_scrub:
                self.on_scrub(self.fraction)
        self.dragging = True
        self._applyAppearance(animated=True)

    def mouseDragged_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.fraction = self._fractionForCenterX(pt.x - self.drag_offset)
        self._applyPositions()
        if self.on_scrub:
            self.on_scrub(self.fraction)

    def mouseUp_(self, event):
        self.dragging = False
        self._applyAppearance(animated=True)
        if self.on_scrub_end:
            self.on_scrub_end(self.fraction)

    def mouseDownCanMoveWindow(self):
        # The main window is setMovableByWindowBackground_(True); plain NSViews default this
        # to YES, which would drag the whole window on top of (or instead of) actually
        # scrubbing. HoverButton doesn't need this override since NSControl already defaults
        # it to NO, but this is a plain NSView.
        return False


class LevelMeterView(AppKit.NSView):
    """Live single-fill level meter for the voice-recording flow — same track/fill CALayer
    shape as ScrubberView, just driven by setLevel_(level) (a live mic RMS reading) instead of
    a mouse drag. Idle (level=0) shows a flat, empty bar, matching "armed, not recording"."""

    TRACK_COLOR = white(0.12)
    FILL_COLOR = white(0.75)

    @objc.python_method
    def configure(self):
        self.level = 0.0
        self.setWantsLayer_(True)
        self.track_layer = Quartz.CALayer.layer()
        self.track_layer.setBackgroundColor_(self.TRACK_COLOR.CGColor())
        self.fill_layer = Quartz.CALayer.layer()
        self.fill_layer.setBackgroundColor_(self.FILL_COLOR.CGColor())
        self.layer().addSublayer_(self.track_layer)
        self.layer().addSublayer_(self.fill_layer)
        self._applyPositions()

    @objc.python_method
    def _applyPositions(self):
        b = self.bounds()
        h = b.size.height
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setDisableActions_(True)
        self.track_layer.setFrame_(NSMakeRect(0, 0, b.size.width, h))
        self.track_layer.setCornerRadius_(h / 2.0)
        fill_w = max(h, b.size.width * max(0.0, min(1.0, self.level)))
        self.fill_layer.setFrame_(NSMakeRect(0, 0, fill_w, h))
        self.fill_layer.setCornerRadius_(h / 2.0)
        AppKit.CATransaction.commit()

    def setFrame_(self, frame):
        objc.super(LevelMeterView, self).setFrame_(frame)
        if hasattr(self, "track_layer"):
            self._applyPositions()

    @objc.python_method
    def setLevel_(self, level):
        self.level = level
        self._applyPositions()


# ---------- controls ----------

class HoverButton(AppKit.NSButton):
    """Borderless button: hover fill fades in/out (0.18s by default), press scales to 0.94."""

    @objc.python_method
    def configure(self, base_alpha, hover_alpha, corner, fade_duration=0.18):
        self._tracking_area = None
        self._base_alpha = base_alpha
        self._hover_alpha = hover_alpha
        self._fade_duration = fade_duration
        self.setBordered_(False)
        self.setBezelStyle_(AppKit.NSBezelStyleRegularSquare)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(corner)
        self._fill(base_alpha, animated=False)

    @objc.python_method
    def _fill(self, alpha, animated=True):
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setAnimationDuration_(getattr(self, "_fade_duration", 0.18) if animated else 0.0)
        self.layer().setBackgroundColor_(white(alpha).CGColor())
        AppKit.CATransaction.commit()

    def updateTrackingAreas(self):
        objc.super(HoverButton, self).updateTrackingAreas()
        if self._tracking_area is not None:
            self.removeTrackingArea_(self._tracking_area)
        opts = AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInKeyWindow
        self._tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None
        )
        self.addTrackingArea_(self._tracking_area)

    def mouseEntered_(self, event):
        # AppKit re-checks tracking areas against the cursor's CURRENT position whenever a
        # view's geometry changes — including when content scrolls underneath a cursor that
        # never actually moved — which fired phantom hover highlights while scrolling a long
        # dropdown list. _suppress_hover (set only on scrollable dropdown rows) blocks that.
        if getattr(self, "_suppress_hover", None) and self._suppress_hover.get("active"):
            return
        if self.isEnabled():
            self._fill(self._hover_alpha)

    def mouseExited_(self, event):
        self._fill(self._base_alpha)


def icon_button(symbol, pt, frame, action, target, base=0.08, hover=0.16, corner=10.0, tint=0.85):
    btn = HoverButton.alloc().initWithFrame_(frame)
    btn.configure(base, hover, corner)
    btn.setTitle_("")
    btn.setTarget_(target)
    btn.setAction_(action)
    img = symbol_image(symbol, pt)
    if img:
        btn.setImage_(img)
        btn.setImagePosition_(AppKit.NSImageOnly)
        btn.setContentTintColor_(white(tint))
    return btn


def text_button(title, frame, action, target, font, base, hover, corner, color):
    btn = HoverButton.alloc().initWithFrame_(frame)
    btn.configure(base, hover, corner)
    btn.setTarget_(target)
    btn.setAction_(action)
    attrs = {
        AppKit.NSFontAttributeName: font,
        AppKit.NSForegroundColorAttributeName: color,
    }
    btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs))
    return btn


def cta_button(title, frame, action, target):
    """Prominent button: #F2F2F2 bg, dark text (Update Now / Continue / Relaunch)."""
    btn = HoverButton.alloc().initWithFrame_(frame)
    btn.configure(0.0, 0.0, 9.0)
    btn.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.95, 1.0).CGColor())
    btn._base_alpha = None  # custom fills below
    btn._fill = lambda *a, **k: None
    btn.setTarget_(target)
    btn.setAction_(action)
    attrs = {
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold),
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithWhite_alpha_(0.11, 1.0),
    }
    btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs))
    return btn


class BrightenOnHoverButton(HoverButton):
    """Text-only hover feedback: the title itself brightens instead of a background box
    appearing — for a button that should always read as plain text (e.g. a Cancel action),
    never as a filled control. Two overlaid labels (dim always visible, a bright copy fading
    in on top) rather than animating the attributed title's color directly — NSAttributedString
    color changes don't animate on their own, while a CALayer alpha fade (the same technique
    already used for the status label and every other fade in this app) does."""

    @objc.python_method
    def configureBrighten(self, title, font, dim_color, bright_color):
        self.configure(0.0, 0.0, 0.0)
        self._fill = lambda *a, **k: None  # never let the inherited hover fill touch this button
        self.setTitle_("")
        b = self.bounds()
        dim = AppKit.NSTextField.alloc().init()
        dim.setBezeled_(False)
        dim.setDrawsBackground_(False)
        dim.setEditable_(False)
        dim.setSelectable_(False)
        dim.setFont_(font)
        dim.setTextColor_(dim_color)
        dim.setAlignment_(AppKit.NSTextAlignmentCenter)
        dim.setStringValue_(title)
        dim.setFrame_(b)
        dim.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        bright = AppKit.NSTextField.alloc().init()
        bright.setBezeled_(False)
        bright.setDrawsBackground_(False)
        bright.setEditable_(False)
        bright.setSelectable_(False)
        bright.setFont_(font)
        bright.setTextColor_(bright_color)
        bright.setAlignment_(AppKit.NSTextAlignmentCenter)
        bright.setStringValue_(title)
        bright.setFrame_(b)
        bright.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        bright.setAlphaValue_(0.0)
        self.addSubview_(dim)
        self.addSubview_(bright)
        self._bright_label = bright

    def mouseEntered_(self, event):
        if getattr(self, "_suppress_hover", None) and self._suppress_hover.get("active"):
            return
        if self.isEnabled():
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setAnimationDuration_(0.15)
            self._bright_label.animator().setAlphaValue_(1.0)
            AppKit.CATransaction.commit()

    def mouseExited_(self, event):
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setAnimationDuration_(0.15)
        self._bright_label.animator().setAlphaValue_(0.0)
        AppKit.CATransaction.commit()


def text_button_brighten(title, frame, action, target, font, dim_color, bright_color):
    btn = BrightenOnHoverButton.alloc().initWithFrame_(frame)
    btn.configureBrighten(title, font, dim_color, bright_color)
    btn.setTarget_(target)
    btn.setAction_(action)
    return btn


class RecordButton(AppKit.NSView):
    """Record/stop button: the outer red circle is a fixed color that hover/press never
    touch — the previous icon_button-based version's own inherited hover fill (transparent,
    since a real fill would have clashed with the manually-set red) overwrote that red on the
    very first hover and never brought it back, since HoverButton's _fill doesn't know "red"
    is supposed to persist. The inner white shape grows on hover and again on press, springing
    back on release — the same idle/hover/pressed sizing behavior as ScrubberView's own thumb,
    reused here for a consistent feel rather than a from-scratch animation approach. Shows a
    circle (record) or a small rounded square (stop), swapped via setRecording_ rather than an
    image, so the shape itself can keep animating smoothly through state changes."""

    OUTER_COLOR = AppKit.NSColor.systemRedColor().colorWithAlphaComponent_(0.85)
    INNER_COLOR = AppKit.NSColor.whiteColor()
    IDLE_SCALE = 0.32
    HOVER_SCALE = 0.36
    PRESSED_SCALE = 0.40

    @objc.python_method
    def configure(self, on_click):
        self.on_click = on_click
        self.recording = False
        self.hovering = False
        self.pressed = False
        self._tracking_area = None
        self.setWantsLayer_(True)
        self.outer_layer = Quartz.CALayer.layer()
        self.outer_layer.setBackgroundColor_(self.OUTER_COLOR.CGColor())
        self.inner_layer = Quartz.CALayer.layer()
        self.inner_layer.setBackgroundColor_(self.INNER_COLOR.CGColor())
        self.layer().addSublayer_(self.outer_layer)
        self.layer().addSublayer_(self.inner_layer)
        self._applyPositions(animated=False)

    @objc.python_method
    def _currentScale(self):
        if self.pressed:
            return self.PRESSED_SCALE
        if self.hovering:
            return self.HOVER_SCALE
        return self.IDLE_SCALE

    @objc.python_method
    def _applyPositions(self, animated=True):
        b = self.bounds()
        d = min(b.size.width, b.size.height)
        AppKit.CATransaction.begin()
        if animated:
            AppKit.CATransaction.setAnimationDuration_(0.15)
        else:
            AppKit.CATransaction.setDisableActions_(True)
        self.outer_layer.setFrame_(NSMakeRect((b.size.width - d) / 2.0, (b.size.height - d) / 2.0, d, d))
        self.outer_layer.setCornerRadius_(d / 2.0)
        inner_d = d * self._currentScale()
        cx, cy = b.size.width / 2.0, b.size.height / 2.0
        self.inner_layer.setFrame_(NSMakeRect(cx - inner_d / 2.0, cy - inner_d / 2.0, inner_d, inner_d))
        # A small fixed corner radius (not half the size) reads as a rounded square once
        # recording — matching the familiar record/stop affordance shape change — while a
        # circle (radius = half the size) is used at rest.
        self.inner_layer.setCornerRadius_(inner_d * 0.18 if self.recording else inner_d / 2.0)
        AppKit.CATransaction.commit()

    def setFrame_(self, frame):
        objc.super(RecordButton, self).setFrame_(frame)
        if hasattr(self, "outer_layer"):
            self._applyPositions(animated=False)

    @objc.python_method
    def setRecording_(self, recording):
        self.recording = recording
        self._applyPositions()

    def updateTrackingAreas(self):
        objc.super(RecordButton, self).updateTrackingAreas()
        if self._tracking_area is not None:
            self.removeTrackingArea_(self._tracking_area)
        opts = AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInKeyWindow
        self._tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None)
        self.addTrackingArea_(self._tracking_area)

    def mouseEntered_(self, event):
        self.hovering = True
        self._applyPositions()

    def mouseExited_(self, event):
        self.hovering = False
        self._applyPositions()

    def mouseDown_(self, event):
        self.pressed = True
        self._applyPositions()

    def mouseUp_(self, event):
        self.pressed = False
        self._applyPositions()
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        if NSPointInRect(pt, self.bounds()) and self.on_click is not None:
            self.on_click(self)

    def mouseDownCanMoveWindow(self):
        return False


class FlatPopUpButton(HoverButton):
    """Borderless pseudo-popup: click opens a custom dark dropdown card (no native NSMenu chrome)."""

    def initWithFrame_pullsDown_(self, frame, pulls_down):
        self = objc.super(FlatPopUpButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.configure(0.08, 0.14, 9.0)
        self.setTitle_("")
        self._items = []
        self._selected = 0
        self._menu_target = None
        self._menu_action = None
        objc.super(FlatPopUpButton, self).setTarget_(self)
        objc.super(FlatPopUpButton, self).setAction_("_openDropdown:")

        label_h = 18
        self._title_label = make_label("", 13, 0.92)
        self._title_label.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        self._title_label.setFrame_(
            NSMakeRect(12, (frame.size.height - label_h) / 2.0, frame.size.width - 30, label_h))
        self._title_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
        self.addSubview_(self._title_label)

        chevron = AppKit.NSImageView.alloc().initWithFrame_(
            NSMakeRect(frame.size.width - 20, (frame.size.height - 12) / 2.0, 12, 12)
        )
        img = symbol_image("chevron.up.chevron.down", 9)
        if img:
            chevron.setImage_(img)
            chevron.setContentTintColor_(white(0.5))
        chevron.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        self.addSubview_(chevron)
        return self

    @objc.python_method
    def addItemWithTitle_(self, title):
        self._items.append(str(title))
        if len(self._items) == 1:
            self._title_label.setStringValue_(self._items[0])

    @objc.python_method
    def removeAllItems(self):
        self._items = []
        self._selected = 0
        self._title_label.setStringValue_("")

    @objc.python_method
    def selectItemWithTitle_(self, title):
        title = str(title)
        if title in self._items:
            self._selected = self._items.index(title)
            self._title_label.setStringValue_(title)

    @objc.python_method
    def selectItemAtIndex_(self, idx):
        if 0 <= idx < len(self._items):
            self._selected = idx
            self._title_label.setStringValue_(self._items[idx])

    @objc.python_method
    def indexOfSelectedItem(self):
        return self._selected

    @objc.python_method
    def titleOfSelectedItem(self):
        return self._items[self._selected] if self._items else ""

    def setTarget_(self, target):
        self._menu_target = target

    def setAction_(self, action):
        self._menu_action = action

    def _openDropdown_(self, sender):
        if not self._items:
            return
        delegate = AppKit.NSApp.delegate()
        if delegate.dropdown_anchor is self and delegate.dropdown_panel is not None:
            delegate._closeDropdown()
            return
        rows = [
            {"title": t, "selected": i == self._selected, "on_click": (lambda i=i: self._chooseItem(i))}
            for i, t in enumerate(self._items)
        ]
        delegate._showDropdown(self, rows, align="right", direction="up")

    @objc.python_method
    def _chooseItem(self, index):
        self._selected = index
        self._title_label.setStringValue_(self._items[index])
        if self._menu_target is not None and self._menu_action is not None:
            self._menu_target.performSelector_withObject_(self._menu_action, self)


class ControlRow(AppKit.NSView):
    """Left group (paste, stop) | play cluster CENTERED ON ROW | speed pinned right."""

    def layout(self):
        objc.super(ControlRow, self).layout()
        b = self.bounds()
        d = self.delegate
        # left group
        x = 0
        for v in (d.paste_btn, d.stop_btn):
            f = v.frame()
            v.setFrameOrigin_(NSMakePoint(x, (b.size.height - f.size.height) / 2.0))
            x += f.size.width + 8
        left_edge = x + 6  # clearance so back-15 can't run into stop

        sf = d.speed_popup.frame()
        right_edge = b.size.width - sf.size.width - 6  # clearance before the speed picker

        # center cluster: back15, play, fwd15, normally centered on the row with a 10pt gap —
        # but if the row is too narrow for that (small window), shrink the gap first (down to
        # 2pt) so the skip buttons push in toward play, and only if even that isn't enough,
        # clamp the cluster's position so it still can't overlap the left/right groups.
        widths = [d.back_btn.frame().size.width, d.play_btn.frame().size.width, d.fwd_btn.frame().size.width]
        max_gap, min_gap = 10.0, 2.0
        available = right_edge - left_edge
        ideal_total = sum(widths) + max_gap * 2
        gap = max_gap if ideal_total <= available else max(min_gap, max_gap - (ideal_total - available) / 2.0)
        total = sum(widths) + gap * 2

        cx = (b.size.width - total) / 2.0
        cx = max(left_edge, min(cx, right_edge - total))
        for v, w in zip((d.back_btn, d.play_btn, d.fwd_btn), widths):
            v.setFrameOrigin_(NSMakePoint(cx, (b.size.height - v.frame().size.height) / 2.0))
            cx += w + gap
        # speed right
        d.speed_popup.setFrameOrigin_(
            NSMakePoint(b.size.width - sf.size.width, (b.size.height - sf.size.height) / 2.0)
        )


class FocusTextView(AppKit.NSTextView):
    def becomeFirstResponder(self):
        ok = objc.super(FocusTextView, self).becomeFirstResponder()
        if ok and getattr(self, "focus_callback", None):
            self.focus_callback(True)
        return ok

    def resignFirstResponder(self):
        ok = objc.super(FocusTextView, self).resignFirstResponder()
        if ok and getattr(self, "focus_callback", None):
            self.focus_callback(False)
        return ok


class BackdropView(AppKit.NSVisualEffectView):
    """Blurred in-window overlay backdrop; click anywhere on it dismisses."""

    def mouseDown_(self, event):
        if getattr(self, "dismiss_callback", None):
            self.dismiss_callback()


class CardView(AppKit.NSView):
    def mouseDown_(self, event):
        # Clicking any empty space on a card (including Manage Voices' list background) must
        # commit whatever text field is currently being edited — a plain NSView doesn't become
        # first responder just by being clicked, so without this, an active field editor never
        # resigns and a rename never commits until something else happens to steal focus.
        if self.window() is not None:
            self.window().endEditingFor_(None)
        # Drag the whole app window from any empty space on the card, same as the main
        # window's own background (setMovableByWindowBackground_). This still keeps the click
        # from reaching the backdrop underneath (which would otherwise dismiss the overlay) —
        # performWindowDragWithEvent_ consumes the whole mouseDown/dragged/up sequence itself.
        self.window().performWindowDragWithEvent_(event)


class DropdownPanel(AppKit.NSPanel):
    """Borderless panel used for custom menus; must be key so the first click on a row registers."""

    def canBecomeKeyWindow(self):
        return True
