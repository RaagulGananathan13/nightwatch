"""The public status page (spec F13): one static HTML file, rewritten on every publish.

It is status/index.html in the dashboard's bucket, served by the same CloudFront distribution,
so it costs nothing extra. It lists the newest published customer updates, and nothing reaches
it without an approver's click in Discord. It has no scripts: its content security policy
allows only its own stylesheet, by hash.

The page changes only when someone publishes. When an incident is over, publish the all-clear
(``/nw update`` on a resolved incident drafts it); until then the page says it is ongoing.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from html import escape
from typing import Any

from nightwatch.common.incidents import IncidentStore
from nightwatch.common.timeutil import from_iso

KEY = "status/index.html"
SHOWN = 20
LABELS = {"investigating": "Investigating", "resolved": "Resolved"}

STYLE = """
:root { color-scheme: light dark; --bg: #fcfcfd; --card: #ffffff; --fg: #1c2024;
  --muted: #60646c; --line: #e0e1e6; --ok: #18794e; --bad: #ce2c31; }
@media (prefers-color-scheme: dark) { :root { --bg: #111113; --card: #18191b; --fg: #edeef0;
  --muted: #b0b4ba; --line: #2e3135; --ok: #3dd68c; --bad: #ff9592; } }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 42rem; margin: 0 auto; padding: 2rem 1rem; }
h1 { font-size: 1.5rem; margin: 0 0 1rem; }
h2 { font-size: 1rem; margin: 0 0 0.5rem; }
.banner { padding: 0.75rem 1rem; border-radius: 0.5rem; color: #fff; font-weight: 600; }
.banner.ok { background: #18794e; }
.banner.bad { background: #ce2c31; }
article { background: var(--card); border: 1px solid var(--line); border-radius: 0.5rem;
  padding: 1rem; margin: 1rem 0; }
article p { margin: 0 0 0.5rem; overflow-wrap: anywhere; }
.tag { font-size: 0.8rem; font-weight: 700; text-transform: uppercase; margin-right: 0.5rem; }
.investigating { color: var(--bad); }
.resolved { color: var(--ok); }
.muted { color: var(--muted); font-size: 0.875rem; }
"""


def publish(store: IncidentStore, s3: Any, bucket: str, site_url: str, now: datetime) -> str:
    """Rewrite the page from the newest updates. Returns its public address."""
    s3.put_object(
        Bucket=bucket,
        Key=KEY,
        Body=render(store.status_updates(SHOWN), now).encode(),
        ContentType="text/html; charset=utf-8",
        CacheControl="public, max-age=60",  # a new update shows within a minute
    )
    return site_url.rstrip("/") + "/" + KEY


def render(updates: list[dict[str, Any]], now: datetime) -> str:
    """The whole page. ``updates`` come newest first, as status_updates() returns them."""
    latest: dict[str, str] = {}
    for update in updates:  # newest first, so the first seen is each incident's latest
        latest.setdefault(str(update["incident_id"]), str(update["state"]))
    ongoing = sum(state != "resolved" for state in latest.values())
    banner = (
        f'<p class="banner bad">We are working on {ongoing} problem{"s" if ongoing > 1 else ""}'
        "</p>"
        if ongoing
        else '<p class="banner ok">All systems operational</p>'
    )
    items = "\n".join(_article(u) for u in updates) or '<p class="muted">No incidents reported.</p>'
    digest = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
    policy = f"default-src 'none'; style-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}">
<title>Service status</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<h1>Service status</h1>
{banner}
<p class="muted">Last updated {_when(now)}</p>
{items}
</main>
</body>
</html>
"""


def _article(update: dict[str, Any]) -> str:
    state = str(update.get("state", ""))
    return (
        "<article>"
        f'<h2><span class="tag {escape(state)}">{LABELS.get(state, "Update")}</span>'
        f"{escape(str(update['public_name']))}</h2>"
        f"<p>{escape(str(update['text']))}</p>"
        f'<p class="muted">{_when(from_iso(str(update["published_at"])))}</p>'
        "</article>"
    )


def _when(moment: datetime) -> str:
    return f"{moment.day} {moment:%B %Y}, {moment:%H:%M} UTC"
