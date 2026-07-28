import hashlib
import json
import os
import shlex
import ssl
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

import certifi
import AppKit
import AVFoundation
import objc
from Foundation import NSObject, NSURL

CONFIG_PATH = os.path.expanduser("~/Library/Application Support/ClaudeReader/config.json")
API_BASE = "https://api.elevenlabs.io/v1"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
PLACEHOLDER_TEXT = "Paste text here (⌘V)..."

APP_VERSION = "1.0.1"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/Redcupss/sonoscript/main/latest.json"


def version_tuple(version_string):
    parts = []
    for piece in version_string.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f)
    os.chmod(CONFIG_PATH, 0o600)


class HoverButton(AppKit.NSButton):
    def initWithFrame_title_action_target_symbol_accent_(
        self, frame, title, action, target, symbol, accent
    ):
        self = objc.super(HoverButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self._tracking_area = None
        self._base_alpha = 0.0
        self._hover_alpha = 0.16

        self.setTitle_("")
        self.setBordered_(False)
        self.setBezelStyle_(AppKit.NSBezelStyleRegularSquare)
        self.setTarget_(target)
        self.setAction_(action)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(7.0)
        self._applyFill_(self._base_alpha)

        if symbol:
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
            if image:
                image.setTemplate_(True)
                self.setImage_(image)
                self.setImagePosition_(AppKit.NSImageOnly)
                self.setContentTintColor_(AppKit.NSColor.whiteColor())
        return self

    @objc.python_method
    def _applyFill_(self, alpha):
        self.layer().setBackgroundColor_(AppKit.NSColor.whiteColor().colorWithAlphaComponent_(alpha).CGColor())

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
        self._applyFill_(self._hover_alpha)

    def mouseExited_(self, event):
        self._applyFill_(self._base_alpha)


class FlatPopUpButton(AppKit.NSPopUpButton):
    def initWithFrame_pullsDown_(self, frame, pulls_down):
        self = objc.super(FlatPopUpButton, self).initWithFrame_pullsDown_(frame, pulls_down)
        if self is None:
            return None
        self.setBordered_(False)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(8.0)
        self.layer().setBackgroundColor_(AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.08).CGColor())

        chevron = AppKit.NSImageView.alloc().initWithFrame_(
            AppKit.NSMakeRect(frame.size.width - 20, (frame.size.height - 12) / 2.0, 12, 12)
        )
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "chevron.up.chevron.down", None
        )
        if image:
            image.setTemplate_(True)
            chevron.setImage_(image)
            chevron.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())
        chevron.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        self.addSubview_(chevron)
        return self


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.config = load_config()
        self.tts_speed = 1.0
        self.voice_ids = []
        self.voice_labels = []
        self.audio_player = None

        self.build_main_menu()
        self.build_ui()

        if not self.config.get("api_key"):
            self.promptForApiKey()
        else:
            self.fetchVoices()

        self.checkForUpdates(silent=True)

    def build_main_menu(self):
        main_menu = AppKit.NSMenu.alloc().init()

        app_menu_item = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(app_menu_item)
        app_menu = AppKit.NSMenu.alloc().init()
        api_key_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Set ElevenLabs API Key...", "changeApiKey:", ""
        )
        api_key_item.setTarget_(self)
        app_menu.addItem_(api_key_item)
        update_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Check for Updates...", "checkForUpdatesClicked:", ""
        )
        update_item.setTarget_(self)
        app_menu.addItem_(update_item)
        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit SonoScript", "terminate:", "q"
        )
        app_menu.addItem_(quit_item)
        app_menu_item.setSubmenu_(app_menu)

        edit_menu_item = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(edit_menu_item)
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key in [
            ("Undo", "undo:", "z"),
            ("Redo", "redo:", "Z"),
            (None, None, None),
            ("Cut", "cut:", "x"),
            ("Copy", "copy:", "c"),
            ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a"),
        ]:
            if title is None:
                edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            edit_menu.addItem_(item)
        edit_menu_item.setSubmenu_(edit_menu)

        AppKit.NSApp.setMainMenu_(main_menu)

    def build_ui(self):
        rect = AppKit.NSMakeRect(0, 0, 440, 440)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskFullSizeContentView
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        self.window.setTitle_("SonoScript")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        self.window.setAppearance_(AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        self.window.center()
        self.window.setMinSize_(AppKit.NSMakeSize(320, 300))
        self.window.setMovableByWindowBackground_(True)

        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self.window.setContentView_(effect)
        content = effect

        self.usage_label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(20, 394, 400, 18))
        self.usage_label.setStringValue_("")
        self.usage_label.setBezeled_(False)
        self.usage_label.setDrawsBackground_(False)
        self.usage_label.setEditable_(False)
        self.usage_label.setSelectable_(False)
        self.usage_label.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self.usage_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        self.usage_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        content.addSubview_(self.usage_label)

        card = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(20, 130, 400, 258))
        card.setWantsLayer_(True)
        card.layer().setBackgroundColor_(AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.07).CGColor())
        card.layer().setBorderColor_(AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.12).CGColor())
        card.layer().setBorderWidth_(1.0)
        card.layer().setCornerRadius_(14.0)
        card.layer().setMasksToBounds_(True)
        card.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content.addSubview_(card)

        scroll = AppKit.NSScrollView.alloc().initWithFrame_(card.bounds())
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        self.placeholder_label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(8, 258 - 30, 384, 20)
        )
        self.placeholder_label.setStringValue_(PLACEHOLDER_TEXT)
        self.placeholder_label.setBezeled_(False)
        self.placeholder_label.setDrawsBackground_(False)
        self.placeholder_label.setEditable_(False)
        self.placeholder_label.setSelectable_(False)
        self.placeholder_label.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self.placeholder_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        self.placeholder_label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        card.addSubview_(self.placeholder_label)

        self.text_view = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 400, 258))
        self.text_view.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self.text_view.setRichText_(False)
        self.text_view.setEditable_(True)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextContainerInset_(AppKit.NSMakeSize(8, 8))
        self.text_view.setVerticallyResizable_(True)
        self.text_view.setHorizontallyResizable_(False)
        self.text_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.text_view.setString_("")
        self.text_view.setDelegate_(self)

        scroll.setDocumentView_(self.text_view)
        card.addSubview_(scroll)

        self.char_count_label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(20, 110, 400, 18))
        self.char_count_label.setStringValue_("0 characters")
        self.char_count_label.setBezeled_(False)
        self.char_count_label.setDrawsBackground_(False)
        self.char_count_label.setEditable_(False)
        self.char_count_label.setSelectable_(False)
        self.char_count_label.setAlignment_(AppKit.NSTextAlignmentRight)
        self.char_count_label.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self.char_count_label.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
        self.char_count_label.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        content.addSubview_(self.char_count_label)

        btn_size = 42
        gap = 14
        popup_w = 68
        popup_h = 26
        btn_y = 65

        transport_width = btn_size * 4 + gap * 3
        transport_x = 20 + (400 - transport_width) / 2.0

        paste_btn = self._make_button(
            "Paste", AppKit.NSMakeRect(20, btn_y, btn_size, btn_size), "pasteClicked:", "doc.on.clipboard"
        )
        content.addSubview_(paste_btn)

        transport_container = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(transport_x, btn_y, transport_width, btn_size)
        )
        transport_container.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin)
        content.addSubview_(transport_container)

        seek_back_btn = self._make_button(
            "Back 15s",
            AppKit.NSMakeRect(0, 0, btn_size, btn_size),
            "seekBack15Clicked:",
            "gobackward.15",
        )
        transport_container.addSubview_(seek_back_btn)

        read_btn = self._make_button(
            "Read Aloud",
            AppKit.NSMakeRect(btn_size + gap, 0, btn_size, btn_size),
            "readClicked:",
            "play.fill",
        )
        read_btn.setKeyEquivalent_("\r")
        transport_container.addSubview_(read_btn)

        seek_forward_btn = self._make_button(
            "Forward 15s",
            AppKit.NSMakeRect(2 * (btn_size + gap), 0, btn_size, btn_size),
            "seekForward15Clicked:",
            "goforward.15",
        )
        transport_container.addSubview_(seek_forward_btn)

        stop_btn = self._make_button(
            "Stop",
            AppKit.NSMakeRect(3 * (btn_size + gap), 0, btn_size, btn_size),
            "stopClicked:",
            "stop.fill",
        )
        transport_container.addSubview_(stop_btn)

        speed_x = 20 + btn_size + gap
        speed_y = btn_y + (btn_size - popup_h) / 2.0
        self.speed_popup = FlatPopUpButton.alloc().initWithFrame_pullsDown_(
            AppKit.NSMakeRect(speed_x, speed_y, popup_w, popup_h), False
        )
        for label in ["0.7x", "0.8x", "0.9x", "1.0x", "1.1x", "1.2x"]:
            self.speed_popup.addItemWithTitle_(label)
        self.speed_popup.selectItemWithTitle_("1.0x")
        self.speed_popup.setTarget_(self)
        self.speed_popup.setAction_("speedPopupChanged:")
        content.addSubview_(self.speed_popup)

        self.status_label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(20, 45, 400, 12))
        self.status_label.setStringValue_("")
        self.status_label.setBezeled_(False)
        self.status_label.setDrawsBackground_(False)
        self.status_label.setEditable_(False)
        self.status_label.setSelectable_(False)
        self.status_label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.status_label.setFont_(AppKit.NSFont.systemFontOfSize_(9))
        self.status_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        self.status_label.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        content.addSubview_(self.status_label)

        voice_label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(20, 15, 60, 20))
        voice_label.setStringValue_("Voice")
        voice_label.setBezeled_(False)
        voice_label.setDrawsBackground_(False)
        voice_label.setEditable_(False)
        voice_label.setSelectable_(False)
        content.addSubview_(voice_label)

        self.voice_popup = FlatPopUpButton.alloc().initWithFrame_pullsDown_(
            AppKit.NSMakeRect(85, 12, 335, 26), False
        )
        self.voice_popup.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.voice_popup.setTarget_(self)
        self.voice_popup.setAction_("voiceChanged:")
        content.addSubview_(self.voice_popup)

        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self.updateCharCount()

    @objc.python_method
    def _make_button(self, title, frame, action, symbol=None, accent=False):
        return HoverButton.alloc().initWithFrame_title_action_target_symbol_accent_(
            frame, title, action, self, symbol, accent
        )

    def textDidChange_(self, notification):
        self.updateCharCount()

    @objc.python_method
    def updateCharCount(self):
        text = str(self.text_view.string())
        self.placeholder_label.setHidden_(bool(text))
        self.char_count_label.setStringValue_(f"{len(text):,} characters")

    def setStatus_(self, text):
        self.status_label.setStringValue_(text)

    def showError_(self, message):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("SonoScript")
        alert.setInformativeText_(message)
        alert.runModal()

    def promptForApiKey(self):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Enter your ElevenLabs API Key")
        alert.setInformativeText_(
            "Get one at elevenlabs.io -> your profile -> API Keys."
        )
        field = AppKit.NSSecureTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 300, 24))
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        response = alert.runModal()
        if response == AppKit.NSAlertFirstButtonReturn:
            key = str(field.stringValue()).strip()
            if key:
                self.config["api_key"] = key
                save_config(self.config)
                self.fetchVoices()

    def changeApiKey_(self, sender):
        self.promptForApiKey()

    def fetchVoices(self):
        self.setStatus_("Loading voices...")
        threading.Thread(target=self._fetchVoicesWorker, daemon=True).start()

    @objc.python_method
    def _fetchVoicesWorker(self):
        api_key = self.config.get("api_key", "")
        req = urllib.request.Request(
            f"{API_BASE}/voices",
            headers={"xi-api-key": api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", f"ElevenLabs rejected the request (HTTP {e.code}). Check your API key.", False
            )
            return
        except urllib.error.URLError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", f"Could not reach ElevenLabs: {e.reason}", False
            )
            return

        voices = data.get("voices", [])
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "populateVoices:", voices, False
        )

    def handleVoiceFetchError_(self, message):
        self.setStatus_("")
        self.showError_(message)

    def populateVoices_(self, voices):
        self.voice_ids = [v.get("voice_id") for v in voices]
        self.voice_labels = []
        for v in voices:
            name = v.get("name", "Unknown")
            accent = (v.get("labels") or {}).get("accent")
            self.voice_labels.append(f"{name} ({accent})" if accent else name)

        self.voice_popup.removeAllItems()
        for label in self.voice_labels:
            self.voice_popup.addItemWithTitle_(label)

        saved_voice_id = self.config.get("voice_id")
        selected_index = self.voice_ids.index(saved_voice_id) if saved_voice_id in self.voice_ids else 0
        if self.voice_labels:
            self.voice_popup.selectItemAtIndex_(selected_index)
            self.config["voice_id"] = self.voice_ids[selected_index]
            save_config(self.config)
        self.setStatus_("")
        self.fetchUsage()

    def fetchUsage(self):
        threading.Thread(target=self._fetchUsageWorker, daemon=True).start()

    @objc.python_method
    def _fetchUsageWorker(self):
        api_key = self.config.get("api_key", "")
        req = urllib.request.Request(
            f"{API_BASE}/user",
            headers={"xi-api-key": api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
                data = json.load(resp)
        except (urllib.error.HTTPError, urllib.error.URLError):
            return

        subscription = data.get("subscription", {})
        used = subscription.get("character_count")
        limit = subscription.get("character_limit")
        if used is None or limit is None:
            return
        remaining = limit - used
        text = f"{used:,} / {limit:,} characters used this period ({remaining:,} left)"
        self.performSelectorOnMainThread_withObject_waitUntilDone_("updateUsageLabel:", text, False)

    def updateUsageLabel_(self, text):
        self.usage_label.setStringValue_(text)

    def pasteClicked_(self, sender):
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        text = pasteboard.stringForType_(AppKit.NSStringPboardType)
        if text:
            self.text_view.setString_(text)
            self.updateCharCount()

    def readClicked_(self, sender):
        text = str(self.text_view.string())
        voice_id = self.config.get("voice_id")
        api_key = self.config.get("api_key")
        if not text or not voice_id or not api_key:
            return
        self.stopClicked_(None)
        self.setStatus_("Generating...")
        threading.Thread(target=self._speakWorker, args=(text, voice_id, api_key), daemon=True).start()

    @objc.python_method
    def _speakWorker(self, text, voice_id, api_key):
        speed = self.tts_speed
        body = json.dumps(
            {
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE}/text-to-speech/{voice_id}",
            data=body,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                audio = resp.read()
        except urllib.error.HTTPError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", f"ElevenLabs request failed (HTTP {e.code}).", False
            )
            return
        except urllib.error.URLError as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", f"Could not reach ElevenLabs: {e.reason}", False
            )
            return

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(audio)
        tmp.close()

        url = NSURL.fileURLWithPath_(tmp.name)
        player, error = AVFoundation.AVAudioPlayer.alloc().initWithContentsOfURL_error_(url, None)
        if player is None:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", "Could not play the generated audio.", False
            )
            return
        player.prepareToPlay()
        player.play()
        self.audio_player = player

        self.performSelectorOnMainThread_withObject_waitUntilDone_("setStatus:", "", False)
        self.fetchUsage()

    def stopClicked_(self, sender):
        if self.audio_player is not None:
            self.audio_player.stop()

    def seekBack15Clicked_(self, sender):
        if self.audio_player is not None:
            new_time = max(0.0, self.audio_player.currentTime() - 15.0)
            self.audio_player.setCurrentTime_(new_time)

    def seekForward15Clicked_(self, sender):
        if self.audio_player is not None:
            duration = self.audio_player.duration()
            new_time = min(duration, self.audio_player.currentTime() + 15.0)
            self.audio_player.setCurrentTime_(new_time)

    def speedPopupChanged_(self, sender):
        title = str(self.speed_popup.titleOfSelectedItem())
        try:
            self.tts_speed = float(title.rstrip("x"))
        except ValueError:
            self.tts_speed = 1.0

    def voiceChanged_(self, sender):
        index = self.voice_popup.indexOfSelectedItem()
        if 0 <= index < len(self.voice_ids):
            self.config["voice_id"] = self.voice_ids[index]
            save_config(self.config)

    def checkForUpdatesClicked_(self, sender):
        self.checkForUpdates(silent=False)

    @objc.python_method
    def checkForUpdates(self, silent):
        threading.Thread(target=self._checkForUpdatesWorker, args=(silent,), daemon=True).start()

    @objc.python_method
    def _checkForUpdatesWorker(self, silent):
        req = urllib.request.Request(UPDATE_MANIFEST_URL)
        try:
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
                manifest = json.load(resp)
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
            if not silent:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "handleVoiceFetchError:", "Could not check for updates. Check your internet connection.", False
                )
            return

        remote_version = manifest.get("version", APP_VERSION)
        result = {
            "silent": silent,
            "available": version_tuple(remote_version) > version_tuple(APP_VERSION),
            "version": remote_version,
            "download_url": manifest.get("download_url", ""),
            "sha256": manifest.get("sha256", ""),
            "notes": manifest.get("notes", ""),
        }
        self.performSelectorOnMainThread_withObject_waitUntilDone_("handleUpdateResult:", result, False)

    def handleUpdateResult_(self, result):
        if result["available"]:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(f"SonoScript {result['version']} is available")
            notes = result["notes"] or "A new version is ready to install."
            alert.setInformativeText_(notes)
            alert.addButtonWithTitle_("Update Now")
            alert.addButtonWithTitle_("Later")
            response = alert.runModal()
            if response == AppKit.NSAlertFirstButtonReturn and result["download_url"]:
                self.setStatus_("Downloading update...")
                threading.Thread(
                    target=self._installUpdateWorker,
                    args=(result["download_url"], result["sha256"]),
                    daemon=True,
                ).start()
        elif not result["silent"]:
            self.showError_("You're up to date! SonoScript " + APP_VERSION)

    @objc.python_method
    def _installUpdateWorker(self, download_url, expected_sha256):
        req = urllib.request.Request(download_url)
        try:
            with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
                data = resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", f"Update download failed: {e}", False
            )
            return

        if expected_sha256:
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256.lower() != expected_sha256.lower():
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "handleVoiceFetchError:",
                    "The downloaded update failed verification and was not installed.",
                    False,
                )
                return

        tmp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(tmp_dir, "update.zip")
        with open(zip_path, "wb") as f:
            f.write(data)

        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            subprocess.run(["ditto", "-x", "-k", zip_path, extract_dir], check=True)
        except subprocess.CalledProcessError:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", "Could not unpack the update.", False
            )
            return

        extracted_app = None
        for name in os.listdir(extract_dir):
            if name.endswith(".app"):
                extracted_app = os.path.join(extract_dir, name)
                break
        if not extracted_app:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", "The update package did not contain an app.", False
            )
            return

        current_app_path = str(AppKit.NSBundle.mainBundle().bundlePath())
        if not current_app_path or not current_app_path.endswith(".app"):
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "handleVoiceFetchError:", "Could not determine the app location; update aborted.", False
            )
            return

        script_path = os.path.join(tmp_dir, "install_update.sh")
        script = (
            "#!/bin/sh\n"
            "sleep 1\n"
            f"rm -rf {shlex.quote(current_app_path)}\n"
            f"mv {shlex.quote(extracted_app)} {shlex.quote(current_app_path)}\n"
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

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True


if __name__ == "__main__":
    app = AppKit.NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    app.run()
