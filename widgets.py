import time

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
        # Without this, becoming first responder (on double-click) draws AppKit's system
        # accent-color focus ring around the whole field — a blue/purple halo that has nothing
        # to do with this app's own hover/editing outline colors.
        self.setFocusRingType_(AppKit.NSFocusRingTypeNone)
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
        # setBordered_(False) does NOT disable NSButtonCell's own native highlight rendering —
        # confirmed directly: highlightsBy() still returns a mask including
        # NSChangeBackgroundCellMask by default, meaning the cell was independently darkening
        # its own background on press this whole time, underneath/alongside every _fill() call
        # this class makes. That's what read as a flicker on release — the cell's own native
        # highlight snapping off at a slightly different moment than this class's explicit
        # settle-back. Zeroing the mask hands 100% of the visual state to _fill(); the cell
        # itself no longer draws anything on its own.
        self.cell().setHighlightsBy_(0)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(corner)
        self._fill(base_alpha, animated=False)

    @objc.python_method
    def _fill(self, alpha, animated=True):
        layer = self.layer()
        target = white(alpha).CGColor()
        # A plain CATransaction-wrapped property set relies on CALayer's IMPLICIT action for
        # "backgroundColor" — but confirmed by direct testing, NSButton's own layer suppresses
        # that implicit action (this exact technique animates fine on EditableNameField, an
        # NSTextField, but produced a true instant swap with zero fade here — same code
        # pattern, different control class). An EXPLICIT animation sidesteps whatever NSButton
        # is doing to the implicit-action lookup entirely; setting the real backgroundColor
        # alongside it keeps the end state correct once the animation finishes and is removed.
        # A spring (not a fixed-duration eased curve) also reads noticeably smoother when hover
        # re-triggers rapidly (moving quickly across several controls) — each new animation
        # starts from wherever the previous one actually was instead of visibly restarting.
        if animated:
            pres = layer.presentationLayer()
            from_color = pres.backgroundColor() if pres is not None else layer.backgroundColor()
            anim = Quartz.CASpringAnimation.animationWithKeyPath_("backgroundColor")
            anim.setFromValue_(from_color)
            anim.setToValue_(target)
            anim.setMass_(1.0)
            anim.setStiffness_(400.0)
            anim.setDamping_(22.0)
            anim.setInitialVelocity_(0.0)
            anim.setDuration_(min(anim.settlingDuration(), 0.4))
            layer.addAnimation_forKey_(anim, "hoverFill")
        layer.setBackgroundColor_(target)

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

    def mouseDown_(self, event):
        # Genuinely missing until now — configure() sets setBordered_(False), which also
        # throws away NSButton's own native press-highlight, so every text_button/icon_button
        # in the app had hover feedback but nothing at all for an actual press-and-hold.
        # objc.super's mouseDown_ blocks here for the whole press/drag/release tracking loop
        # (that's also what actually fires the target/action on a valid click) — the pressed
        # fill has to be set BEFORE that call to be visible during the hold, and settled back
        # to hover-or-base AFTER it returns, once the final cursor position is known.
        # +0.06, not +0.10 — 0.10 read as too bright a jump for buttons whose hover_alpha is
        # already fairly low (e.g. 0.14), confirmed directly.
        if self.isEnabled():
            self._fill(min(1.0, self._hover_alpha + 0.06))
        objc.super(HoverButton, self).mouseDown_(event)
        # The click's own action may have already rebuilt/replaced the whole screen this
        # button lived on (Confirm, the mode/value pills, etc. all call showSettingsScreen
        # again) — by the time control returns here, `self` can already be detached from any
        # window. Touching its fill in that state visibly flickered, since the stale,
        # about-to-be-discarded instance's color change rendered independently of (and after)
        # the new screen that already replaced it. Skipping the settle-back entirely once the
        # button's no longer in a window sidesteps that; there's nothing left to visually
        # settle on a view that's already gone.
        if self.isEnabled() and self.window() is not None:
            point = self.convertPoint_fromView_(self.window().mouseLocationOutsideOfEventStream(), None)
            self._fill(self._hover_alpha if NSPointInRect(point, self.bounds()) else self._base_alpha)


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


class ContextMenuButton(HoverButton):
    """A HoverButton that also shows a real, native right-click context menu — right-click is
    the one interaction in this app where reaching for actual NSMenu chrome is the correct
    call rather than a custom dark popover: it's the universal system convention (Finder,
    Mail, Notes, ...) and there's no existing in-app custom equivalent to stay consistent
    with, unlike the dropdown menus (which DO have one) or a color picker (which doesn't need
    native chrome at all). Set .context_menu_items to a list of (title, callback) tuples —
    a (None, None) entry renders as a separator — before this is ever right-clicked."""

    def rightMouseDown_(self, event):
        items = getattr(self, "context_menu_items", None)
        if not items:
            return
        menu = AppKit.NSMenu.alloc().init()
        for title, callback in items:
            if title is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, "_contextMenuItemClicked:", "")
            item.setTarget_(self)
            # NOT item._callback = callback — a bare NSMenuItem (unlike our own Python
            # subclasses, e.g. HoverButton) is a plain bridged Objective-C instance with no
            # Python-side __dict__ for PyObjC to hang an ad-hoc attribute on, so that raised
            # AttributeError the instant this ran, aborting the menu build before
            # popUpContextMenu_withEvent_forView_ was ever reached — confirmed by calling
            # rightMouseDown_ directly and watching it crash on exactly this line. This is
            # what actually made the whole right-click menu do nothing. representedObject is
            # the real, supported slot for attaching an arbitrary payload to a menu item.
            item.setRepresentedObject_(callback)
            menu.addItem_(item)
        AppKit.NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)

    def _contextMenuItemClicked_(self, sender):
        cb = sender.representedObject()
        if cb:
            cb()


class SegmentedPillControl(AppKit.NSView):
    """A rounded track with a highlight pill that slides beneath whichever segment is
    selected (e.g. a Recent/Saved tab switcher) — not just two independently-styled static
    buttons. Deliberately built to be kept alive as ONE instance across a selection change
    (call .select(index) on the same object) rather than rebuilt from scratch the way every
    other screen in this app is — a full rebuild would just snap the new state in with
    nothing to animate FROM. Callers should build this with its real final frame up front
    (its own width never needs to change after that — only reposition via autoresizing
    margins — so there's no resize-driven relayout to handle beyond what those margins
    already do on their own)."""

    @objc.python_method
    def configure(self, labels, on_change):
        self.labels = labels
        self.on_change = on_change
        self.selected_index = 0
        self.setWantsLayer_(True)
        self.layer().setBackgroundColor_(white(0.05).CGColor())
        self.layer().setBorderColor_(white(0.1).CGColor())
        self.layer().setBorderWidth_(1.0)
        self.indicator_layer = Quartz.CALayer.layer()
        self.indicator_layer.setBackgroundColor_(white(0.18).CGColor())
        self.layer().addSublayer_(self.indicator_layer)
        self.buttons = []
        font = AppKit.NSFont.systemFontOfSize_weight_(12.5, AppKit.NSFontWeightSemibold)
        for i, label in enumerate(labels):
            btn = BrightenOnHoverButton.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
            # Real colors get set below by _applySelectionColors — these are just placeholders
            # so configureBrighten has something to build its two overlaid labels from. No
            # explicit label_frame needed — it defaults to the button's own full bounds, which
            # is correctly vertically centered now that configureBrighten's labels use
            # _VerticallyCenteredTextField (see that class's docstring for why a plain
            # NSTextField wasn't centering on its own here).
            btn.configureBrighten(label, font, white(0.5), white(0.85))
            btn.setTarget_(self)
            btn.setAction_("_segClicked:")
            btn._seg_index = i
            self.addSubview_(btn)
            self.buttons.append(btn)
        self._applySelectionColors()
        self._layoutSegments(animated=False)

    @objc.python_method
    def select(self, index, animated=True):
        if index == self.selected_index:
            return
        self.selected_index = index
        self._applySelectionColors()
        self._layoutSegments(animated=animated)

    @objc.python_method
    def _applySelectionColors(self):
        # Directly recoloring the existing dim/bright label instances configureBrighten
        # already built — NOT calling configureBrighten again, which addSubview_s a fresh
        # label pair every time with no removal of the old ones (fine for a one-shot build,
        # would silently stack duplicate overlapping labels here).
        for i, btn in enumerate(self.buttons):
            if i == self.selected_index:
                btn._dim_label.setTextColor_(white(0.95))
                btn._bright_label.setTextColor_(white(0.95))
            else:
                btn._dim_label.setTextColor_(white(0.5))
                btn._bright_label.setTextColor_(white(0.85))

    @objc.python_method
    def _layoutSegments(self, animated):
        b = self.bounds()
        if b.size.width <= 0 or b.size.height <= 0 or not self.buttons:
            return
        n = len(self.buttons)
        seg_w = b.size.width / n
        pad = 3.0
        self.layer().setCornerRadius_(b.size.height / 2.0)
        for i, btn in enumerate(self.buttons):
            btn.setFrame_(NSMakeRect(i * seg_w, 0, seg_w, b.size.height))
        indicator_frame = NSMakeRect(self.selected_index * seg_w + pad, pad,
                                      seg_w - pad * 2, b.size.height - pad * 2)
        AppKit.CATransaction.begin()
        if animated:
            AppKit.CATransaction.setAnimationDuration_(0.22)
        else:
            AppKit.CATransaction.setDisableActions_(True)
        self.indicator_layer.setFrame_(indicator_frame)
        self.indicator_layer.setCornerRadius_((b.size.height - pad * 2) / 2.0)
        AppKit.CATransaction.commit()

    def _segClicked_(self, sender):
        idx = getattr(sender, "_seg_index", 0)
        if idx == self.selected_index:
            return
        self.select(idx, animated=True)
        if self.on_change:
            self.on_change(idx)


class _VerticallyCenteredTextField(AppKit.NSTextField):
    """A plain NSTextField, except its cell actually centers content vertically instead of
    top-aligning it whenever the field's frame is taller than one line of text — see
    VerticallyCenteredCell. cellClass() is the standard Cocoa hook for this: it's consulted by
    NSTextField's own init/initWithFrame_, so every field built this way gets the corrected
    cell automatically, with no per-instance cell-swapping needed."""

    @classmethod
    def cellClass(cls):
        return VerticallyCenteredCell


class BrightenOnHoverButton(HoverButton):
    """Text-only hover feedback: the title itself brightens instead of a background box
    appearing — for a button that should always read as plain text (e.g. a Cancel action),
    never as a filled control. Two overlaid labels (dim always visible, a bright copy fading
    in on top) rather than animating the attributed title's color directly — NSAttributedString
    color changes don't animate on their own, while a CALayer alpha fade (the same technique
    already used for the status label and every other fade in this app) does."""

    @objc.python_method
    def configureBrighten(self, title, font, dim_color, bright_color, align=None, label_frame=None):
        self.configure(0.0, 0.0, 0.0)
        self._fill = lambda *a, **k: None  # never let the inherited hover fill touch this button
        self.setTitle_("")
        align = align if align is not None else AppKit.NSTextAlignmentCenter
        # A caller-supplied frame/alignment (list rows: left-aligned, inset from the row's own
        # edges) instead of always filling+centering the whole bounds (buttons like Cancel) —
        # same overlay technique either way, just laid out differently.
        frame = label_frame if label_frame is not None else self.bounds()
        autoresize = (AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable) if label_frame is None else (
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
        # _VerticallyCenteredTextField, NOT plain NSTextField — a plain NSTextFieldCell
        # top-aligns once its frame is taller than the text's own tight bounding box, which
        # every caller's frame always is (a comfortable click target, not a snug text box).
        # Confirmed by direct pixel measurement on the segmented pill control (SegmentedPillControl):
        # text sat ~8pt above the frame's true vertical center — a caller-side label_frame resize
        # alone doesn't fix it, since it still top-aligns within whatever frame it's given; the
        # cell itself has to center. Applied here (not just at the one call site that surfaced
        # it) since every other BrightenOnHoverButton user has the same latent issue, just less
        # visually obvious where the frame height is already close to the text's own height.
        dim = _VerticallyCenteredTextField.alloc().init()
        dim.setBezeled_(False)
        dim.setDrawsBackground_(False)
        dim.setEditable_(False)
        dim.setSelectable_(False)
        dim.setFont_(font)
        dim.setTextColor_(dim_color)
        dim.setAlignment_(align)
        dim.setStringValue_(title)
        dim.setFrame_(frame)
        dim.setAutoresizingMask_(autoresize)
        bright = _VerticallyCenteredTextField.alloc().init()
        bright.setBezeled_(False)
        bright.setDrawsBackground_(False)
        bright.setEditable_(False)
        bright.setSelectable_(False)
        bright.setFont_(font)
        bright.setTextColor_(bright_color)
        bright.setAlignment_(align)
        bright.setStringValue_(title)
        bright.setFrame_(frame)
        bright.setAutoresizingMask_(autoresize)
        bright.setAlphaValue_(0.0)
        self.addSubview_(dim)
        self.addSubview_(bright)
        self._bright_label = bright
        self._dim_label = dim

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


class PulsingLabel(AppKit.NSTextField):
    """A label that can show a soft, looping left-to-right brightness sweep across its own
    text — signals "actively working" (e.g. during TTS generation, which has no reliable
    percentage to show as a real progress bar) instead of sitting there looking possibly
    frozen. Same overlay idea as BrightenOnHoverButton (a dim base copy always visible, a
    brighter copy on top) but here the bright copy's own layer gets a CAGradientLayer mask
    with one narrow bright band, and a repeating NSTimer explicitly steps that band's
    location each frame — a genuine CAAnimation added directly to the mask layer (tried both
    a transform and a "locations" animation) never visibly moved at all, confirmed directly:
    its presentationLayer value WAS updating correctly in isolation, but nothing was pumping
    a fresh composite of the layer it was masking, so the render just sat on one frame
    forever. Explicitly setting the mask's geometry on a timer forces a real redraw each
    tick, sidestepping whatever isn't re-compositing an animating mask on its own here."""

    PULSE_DURATION = 2.2

    @objc.python_method
    def configurePulse(self, font, base_color, bright_color, align=AppKit.NSTextAlignmentLeft):
        self.setBezeled_(False)
        self.setDrawsBackground_(False)
        self.setEditable_(False)
        self.setSelectable_(False)
        self.setFont_(font)
        self.setTextColor_(base_color)
        self.setAlignment_(align)
        self.setWantsLayer_(True)
        self._pulse_font = font
        self._pulse_align = align
        self._base_color = base_color
        self._bright_color = bright_color
        self._bright_label = None
        self._pulse_gradient = None
        self._pulse_timer = None
        self._pulse_start = None
        self._pulsing = False

    @objc.python_method
    def resetBaseColor(self):
        # Callers (e.g. _flashInlineError) may set textColor directly to flash a red error on
        # this same label — nothing else ever restores it afterward, so the NEXT normal status
        # message silently inherits that stale red. Call this whenever showing a real status.
        self.setTextColor_(self._base_color)

    @objc.python_method
    def startPulsing(self):
        if self._bright_label is None:
            bright = AppKit.NSTextField.alloc().init()
            bright.setBezeled_(False)
            bright.setDrawsBackground_(False)
            bright.setEditable_(False)
            bright.setSelectable_(False)
            bright.setFont_(self._pulse_font)
            bright.setAlignment_(self._pulse_align)
            bright.setTextColor_(self._bright_color)
            bright.setFrame_(self.bounds())
            bright.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            bright.setWantsLayer_(True)
            self.addSubview_(bright)
            self._bright_label = bright

            gradient = Quartz.CAGradientLayer.layer()
            gradient.setFrame_(bright.bounds())
            gradient.setStartPoint_(NSMakePoint(0.0, 0.5))
            gradient.setEndPoint_(NSMakePoint(1.0, 0.5))
            clear = AppKit.NSColor.blackColor().colorWithAlphaComponent_(0.0).CGColor()
            solid = AppKit.NSColor.whiteColor().CGColor()
            gradient.setColors_([clear, clear, solid, clear, clear])
            bright.layer().setMask_(gradient)
            self._pulse_gradient = gradient

        self._bright_label.setStringValue_(self.stringValue())
        self._bright_label.setHidden_(False)
        if self._pulsing:
            return  # already running — just needed the text refresh above
        self._pulsing = True
        self._pulse_start = time.time()

        offsets = [-0.35, -0.15, 0.0, 0.15, 0.35]

        def tick(t):
            if not self._pulsing:
                return
            elapsed = time.time() - self._pulse_start
            phase = (elapsed % self.PULSE_DURATION) / self.PULSE_DURATION
            # Fully off the left edge at phase 0, fully off the right edge at phase 1 — the
            # band travels THROUGH the visible 0..1 span in between. CAGradientLayer tolerates
            # locations outside 0..1 (a standard technique for a stop that starts/ends
            # offscreen rather than popping in/out abruptly at the edges).
            center = -0.4 + phase * 1.8
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setDisableActions_(True)
            self._pulse_gradient.setLocations_([center + o for o in offsets])
            AppKit.CATransaction.commit()

        # NOT scheduledTimerWithTimeInterval_repeats_block_ — that only fires in
        # NSDefaultRunLoopMode, which AppKit suspends during interactive tracking loops (a live
        # window resize drag, a scrollbar drag, menu tracking), so the pulse visibly freezes
        # mid-resize and jumps back on mouse-up (confirmed directly, same underlying cause as
        # ShimmerBorderView's own resize glitch below). Adding it to NSRunLoopCommonModes
        # instead keeps it ticking through those tracking loops too.
        self._pulse_timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(1.0 / 30.0, True, tick)
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(self._pulse_timer, AppKit.NSRunLoopCommonModes)

    @objc.python_method
    def stopPulsing(self):
        self._pulsing = False
        if self._pulse_timer is not None:
            self._pulse_timer.invalidate()
            self._pulse_timer = None
        if self._bright_label is not None:
            self._bright_label.setHidden_(True)


class ShimmerBorderView(AppKit.NSView):
    """A plain container view (the main text card) whose rounded-rect border can show the
    same traveling brightness sweep as PulsingLabel — a bright duplicate of the border's
    stroke (drawn via CAShapeLayer, matching the view's own corner radius), masked by a
    CAGradientLayer whose "locations" a repeating NSTimer steps each frame. Only the outline
    shimmers; the card's own fill/background and the existing static border underneath are
    untouched — the bright stroke just sits on top, normally hidden, revealed only where the
    traveling mask currently is."""

    SHIMMER_DURATION = 2.2

    @objc.python_method
    def configureShimmerBorder(self, corner_radius, bright_color):
        self._shimmer_radius = corner_radius
        self._shimmer_color = bright_color
        self._shimmer_layer = None
        self._shimmer_gradient = None
        self._shimmer_timer = None
        self._shimmer_start = None
        self._shimmering = False

    @objc.python_method
    def _layoutShimmerBorder(self):
        # Re-derives the stroke path and both layers' frames from the view's CURRENT bounds —
        # called both on initial creation and on every resize (see setFrame_ below). Without
        # this, the path/frame baked in at creation time stay put while the view itself grows
        # or shrinks around them, leaving the glow stuck tracing the view's OLD size (confirmed
        # directly: after a window resize, the stroke ended up running through the middle of
        # the now-larger box instead of along its edge).
        if self._shimmer_layer is None:
            return
        b = self.bounds()
        inset = NSMakeRect(0.5, 0.5, b.size.width - 1.0, b.size.height - 1.0)
        path = Quartz.CGPathCreateWithRoundedRect(inset, self._shimmer_radius, self._shimmer_radius, None)
        AppKit.CATransaction.begin()
        AppKit.CATransaction.setDisableActions_(True)
        self._shimmer_layer.setFrame_(b)
        self._shimmer_layer.setPath_(path)
        self._shimmer_gradient.setFrame_(b)
        AppKit.CATransaction.commit()

    def setFrame_(self, frame):
        objc.super(ShimmerBorderView, self).setFrame_(frame)
        self._layoutShimmerBorder()

    @objc.python_method
    def startBorderShimmer(self):
        if self._shimmer_layer is None:
            shape = Quartz.CAShapeLayer.layer()
            shape.setFillColor_(None)
            shape.setStrokeColor_(self._shimmer_color.CGColor())
            shape.setLineWidth_(1.0)
            self.layer().addSublayer_(shape)
            self._shimmer_layer = shape

            gradient = Quartz.CAGradientLayer.layer()
            # CAGradientLayer's unit space has (0,0) at the bottom-left and (1,1) at the
            # top-right regardless of view flippedness — top-left to bottom-right is (0,1) to (1,0).
            gradient.setStartPoint_(NSMakePoint(0.0, 1.0))
            gradient.setEndPoint_(NSMakePoint(1.0, 0.0))
            clear = AppKit.NSColor.blackColor().colorWithAlphaComponent_(0.0).CGColor()
            solid = AppKit.NSColor.whiteColor().CGColor()
            gradient.setColors_([clear, clear, solid, clear, clear])
            shape.setMask_(gradient)
            self._shimmer_gradient = gradient
            self._layoutShimmerBorder()

        self._shimmer_layer.setHidden_(False)
        if self._shimmering:
            return
        self._shimmering = True
        self._shimmer_start = time.time()
        offsets = [-0.35, -0.15, 0.0, 0.15, 0.35]

        def tick(t):
            if not self._shimmering:
                return
            elapsed = time.time() - self._shimmer_start
            phase = (elapsed % self.SHIMMER_DURATION) / self.SHIMMER_DURATION
            center = -0.4 + phase * 1.8
            AppKit.CATransaction.begin()
            AppKit.CATransaction.setDisableActions_(True)
            self._shimmer_gradient.setLocations_([center + o for o in offsets])
            AppKit.CATransaction.commit()

        # NSRunLoopCommonModes, not the plain scheduled-timer default mode — confirmed directly:
        # a timer only in NSDefaultRunLoopMode stops firing for the whole duration of a live
        # window-resize drag (AppKit runs NSEventTrackingRunLoopMode during that drag instead),
        # so the shimmer visibly froze mid-drag and snapped back on mouse-up. Common modes
        # covers both, so it keeps animating through the drag.
        self._shimmer_timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(1.0 / 30.0, True, tick)
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(self._shimmer_timer, AppKit.NSRunLoopCommonModes)

    @objc.python_method
    def stopBorderShimmer(self):
        self._shimmering = False
        if self._shimmer_timer is not None:
            self._shimmer_timer.invalidate()
            self._shimmer_timer = None
        if self._shimmer_layer is not None:
            self._shimmer_layer.setHidden_(True)

    def setStringValue_(self, value):
        objc.super(PulsingLabel, self).setStringValue_(value)
        # Guarded with getattr — configurePulse hasn't necessarily run yet the first time
        # AppKit/our own construction code sets an initial string value.
        if getattr(self, "_pulsing", False) and getattr(self, "_bright_label", None) is not None:
            self._bright_label.setStringValue_(value)


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
    # Bigger jump than idle->hover on purpose: mouseUp_ releases back to HOVER_SCALE (the
    # cursor is still over the button in the normal click case), so the gap between PRESSED
    # and HOVER is what actually reads as "shrinks back down on release." At the old 0.40 it
    # was only a couple points different from HOVER_SCALE and the release was imperceptible.
    # 0.52 (the first attempt at widening this) overcorrected — confirmed directly as "way too
    # big," nearly filling the red circle. 0.44 keeps a clearly visible press/release delta
    # without the pressed state dominating the whole button.
    PRESSED_SCALE = 0.44

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
        self._chevron = chevron
        return self

    def setEnabled_(self, enabled):
        # This is a fully custom-drawn control (no native bezel), so disabling it via the
        # standard NSButton mechanism alone is functionally correct but visually silent —
        # nothing dims to signal why clicking it does nothing. Fading the label/chevron gives
        # that same "grayed out" read a native disabled control gets for free.
        objc.super(FlatPopUpButton, self).setEnabled_(enabled)
        alpha = 1.0 if enabled else 0.35
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setAlphaValue_(alpha)
        if getattr(self, "_chevron", None) is not None:
            self._chevron.setAlphaValue_(alpha)

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
