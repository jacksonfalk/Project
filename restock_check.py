#!/usr/bin/env python3
"""
One-shot restock check, designed to be run on a schedule by GitHub Actions.

Each run: fetch the product page once, compare with the last known state
(state.json), and send an ntfy push notification only when something changes
(out of stock -> in stock, or when the site/page needs attention).

Environment:
  NTFY_TOPIC   your private ntfy topic (set as a GitHub Actions secret)

Flags:
  --test       just send a test notification and exit
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

PRODUCT_URL = "https://rerediyshop-asia.com/product/video-of-packing-your-order/"
STATE_FILE = "state.json"
FAILURES_BEFORE_ALERT = 4  # ~20 minutes of failures at a 5-minute schedule

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_STATE = {
    "stock": None,           # "in", "out" or None (never checked)
    "failures": 0,           # consecutive failed fetches (capped)
    "unknown_alerted": False,
}


def log(msg):
    print(f"[{datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC] {msg}", flush=True)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def notify(topic, title, message, click_url=None, priority="high"):
    headers = {"Title": title, "Priority": priority, "Tags": "shopping_cart"}
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
        log(f"Notification sent: {title}")
    except Exception as e:
        log(f"Could not send notification: {e}")


def check_stock(html):
    """Return True (in stock), False (out of stock) or None (can't tell)."""
    # Ignore the "Related products" section: it has Add to cart buttons for
    # other items that would cause false alerts.
    main = html
    cut = re.search(r'class="[^"]*related[^"]*products', html, re.I)
    if cut:
        main = html[: cut.start()]

    if re.search(r'class="[^"]*\bout-of-stock\b', main, re.I):
        return False
    if re.search(r">\s*(out of stock|sold out)\s*<", main, re.I):
        return False
    if re.search(r"single_add_to_cart_button", main, re.I):
        return True
    if re.search(r'class="[^"]*\bin-stock\b', main, re.I):
        return True
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return {**DEFAULT_STATE, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def run_check(topic):
    state = load_state()
    original = dict(state)

    try:
        html = fetch(PRODUCT_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"Fetch failed: {e}")
        if state["failures"] < FAILURES_BEFORE_ALERT:
            state["failures"] += 1
            if state["failures"] == FAILURES_BEFORE_ALERT:
                notify(
                    topic,
                    "Restock watcher: can't reach site",
                    f"{FAILURES_BEFORE_ALERT} failed checks in a row: {e}. "
                    "The shop may be blocking GitHub's servers or be down.",
                    priority="default",
                )
        if state != original:
            save_state(state)
        return

    state["failures"] = 0
    result = check_stock(html)

    if result is None:
        log("Could not determine stock status (page layout may have changed).")
        if not state["unknown_alerted"]:
            notify(
                topic,
                "Restock watcher: check needed",
                "Couldn't read the stock status on the page. "
                "The layout may have changed.",
                PRODUCT_URL,
                priority="default",
            )
            state["unknown_alerted"] = True
    else:
        state["unknown_alerted"] = False
        new = "in" if result else "out"
        log("IN STOCK" if result else "Out of stock")
        if new == "in" and state["stock"] != "in":
            notify(
                topic,
                "Back in stock!",
                "'Video of packing your order' is available now. "
                "Add it to your cart with another product, quickly.",
                PRODUCT_URL,
                priority="urgent",
            )
        state["stock"] = new

    if state != original:
        save_state(state)


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        sys.exit("NTFY_TOPIC is not set. Add it as a repository secret.")

    if "--test" in sys.argv:
        notify(topic, "Restock watcher test", "Notifications are working.", PRODUCT_URL)
        return

    run_check(topic)


if __name__ == "__main__":
    main()
