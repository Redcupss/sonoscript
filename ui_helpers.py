import random

import AppKit
import Quartz
from Foundation import NSMakeRect, NSMakePoint


def white(a):
    return AppKit.NSColor.whiteColor().colorWithAlphaComponent_(a)


def fix_anchor(view):
    """Center layer anchorPoint so scale animations grow from the middle."""
    layer = view.layer()
    f = view.frame()
    layer.setAnchorPoint_(NSMakePoint(0.5, 0.5))
    layer.setPosition_(NSMakePoint(f.origin.x + f.size.width / 2.0, f.origin.y + f.size.height / 2.0))


def build_waveform_bars(icon_bg, scale=1.0):
    """A hand-built, independently-animated bar cluster (a true oscilloscope trace, not
    equalizer bars on a fixed baseline) — not the static "waveform" SF Symbol, whose own
    "variable color" effect only animates brightness/opacity, not actual height. Each bar is
    center-anchored (both top and bottom move) with a small per-bar phase stagger over a
    shared duration (a traveling wave) rather than fully independent random phases — full
    independence let one bar's random peak land completely out of step with flat neighbors,
    an incoherent-looking spike. `scale` lets the same cluster be built bigger for the
    first-launch splash without duplicating this whole animation setup a second time.
    """
    bar_w, bar_gap = 3.0 * scale, 3.0 * scale
    base_heights = [8.0 * scale, 14.0 * scale, 20.0 * scale, 14.0 * scale, 8.0 * scale]
    total_w = len(base_heights) * bar_w + (len(base_heights) - 1) * bar_gap
    ib = icon_bg.bounds()
    start_x = (ib.size.width - total_w) / 2.0
    center_y = ib.size.height / 2.0
    shared_duration = 1.3
    start_time = Quartz.CACurrentMediaTime()
    for i, h in enumerate(base_heights):
        bar = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(start_x + i * (bar_w + bar_gap), center_y - h / 2.0, bar_w, h))
        bar.setWantsLayer_(True)
        bar.layer().setBackgroundColor_(white(0.85).CGColor())
        bar.layer().setCornerRadius_(bar_w / 2.0)
        icon_bg.addSubview_(bar)
        fix_anchor(bar)
        anim = Quartz.CABasicAnimation.animationWithKeyPath_("transform.scale.y")
        anim.setFromValue_(0.88)
        anim.setToValue_(1.12)
        anim.setDuration_(shared_duration * random.uniform(0.92, 1.08))
        anim.setBeginTime_(start_time + i * 0.16)
        anim.setAutoreverses_(True)
        anim.setRepeatCount_(float("inf"))
        anim.setTimingFunction_(
            Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseInEaseOut))
        bar.layer().addAnimation_forKey_(anim, "wave")
        # scale alone barely moved the bottom edge (half the height delta, split from a fixed
        # center) — an added vertical drift shifts the whole bar, so the bottom edge's position
        # visibly changes too, not just the top. Kept on the SAME phase as the scale animation
        # (not offset) — an out-of-phase drift was compounding asymmetrically with the scale at
        # different points in the cycle, reading as "reaches further up than down" even though
        # each animation alone is symmetric.
        drift = Quartz.CABasicAnimation.animationWithKeyPath_("transform.translation.y")
        drift.setFromValue_(-1.8 * scale)
        drift.setToValue_(1.8 * scale)
        drift.setDuration_(shared_duration * random.uniform(0.92, 1.08))
        drift.setBeginTime_(start_time + i * 0.16)
        drift.setAutoreverses_(True)
        drift.setRepeatCount_(float("inf"))
        drift.setTimingFunction_(
            Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseInEaseOut))
        bar.layer().addAnimation_forKey_(drift, "drift")


def make_label(text, size, alpha, weight=AppKit.NSFontWeightRegular, align=AppKit.NSTextAlignmentLeft):
    lbl = AppKit.NSTextField.alloc().init()
    lbl.setStringValue_(text)
    lbl.setBezeled_(False)
    lbl.setDrawsBackground_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
    lbl.setTextColor_(white(alpha))
    lbl.setAlignment_(align)
    return lbl


def symbol_image(name, point_size, weight=AppKit.NSFontWeightRegular):
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    conf = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(point_size, weight)
    img = img.imageWithSymbolConfiguration_(conf)
    img.setTemplate_(True)
    return img


def format_playback_time(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"
