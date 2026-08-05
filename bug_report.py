"""Submits a tester's problem report as a GitHub issue in a dedicated private repo (never the
public source repo) — see the design discussion this was built from: embedding a token here is
a deliberate, scale-appropriate tradeoff for a small handful of trusted testers, not a
permanent architecture. Before wider distribution, this should move to a server-side proxy so
no credential ships inside the app at all; see bug_report_token.py's header for the token's
own scope, and REPORT_REPO below for exactly what a leaked token could touch.

Deliberately does NOT include the raw text a tester was reading, even in the session log —
only its length. A leaked token (this app is unsigned and its bytecode is trivially
extractable, same as everything else in it) would let an extractor read past reports in
REPORT_REPO, so what actually gets stored there is the mitigation that matters at this scale.
"""
import json
import platform
import ssl
import urllib.error
import urllib.request

import certifi

from bug_report_token import REPORT_TOKEN

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
REPORT_REPO = "Redcupss/sonoscript-reports"


class ReportError(Exception):
    pass


def reporting_configured():
    return bool(REPORT_TOKEN)


def system_info_line():
    mac_version = platform.mac_ver()[0] or "unknown macOS version"
    return f"macOS {mac_version} ({platform.machine()})"


def build_report(answers, session_log, app_version, app_build):
    """answers is a dict with keys: activity, provider_voice, problem, when, notes, contact —
    all free text, all optional (missing/blank ones render as a placeholder rather than being
    silently omitted, so an empty answer is visibly a choice the tester made, not a bug)."""
    def field(key, placeholder="(not specified)"):
        return (answers.get(key) or "").strip() or placeholder

    lines = [
        f"**What were they doing:** {field('activity')}",
        f"**Provider / voice:** {field('provider_voice')}",
        f"**What went wrong:** {field('problem')}",
        f"**When:** {field('when')}",
        f"**Additional notes:** {field('notes', '(none)')}",
        "",
        f"**Reporter contact (optional):** {field('contact', '(not provided)')}",
        "",
        "---",
        f"**App version:** {app_version} (build {app_build})",
        f"**System:** {system_info_line()}",
        "",
        "**This session's generation log:**",
    ]
    if not session_log:
        lines.append("_(no generations recorded this session)_")
    for entry in session_log:
        error_suffix = f" — **error: {entry['error']}**" if entry.get("error") else ""
        lines.append(
            f"- `{entry['timestamp']}` — {entry['provider']} / {entry['voice']} — "
            f"{entry['chars']} chars, {entry['duration']:.1f}s{error_suffix}"
        )
    title = f"[Report] {field('problem', 'Untitled')[:70]}"
    return title, "\n".join(lines)


def submit_report(title, body):
    if not REPORT_TOKEN:
        raise ReportError("Reporting isn't set up in this build yet.")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPORT_REPO}/issues",
        method="POST",
        headers={
            "Authorization": f"Bearer {REPORT_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        data=json.dumps({"title": title, "body": body, "labels": ["bug-report"]}).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
            if resp.status not in (200, 201):
                raise ReportError(f"GitHub returned status {resp.status}.")
    except urllib.error.HTTPError as e:
        raise ReportError(f"Couldn't send report: {e.code} {e.reason}")
    except (urllib.error.URLError, OSError) as e:
        raise ReportError(f"Couldn't send report: {e}")
