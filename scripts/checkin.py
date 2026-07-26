#!/usr/bin/env python3
"""ArityFlow daily check-in for GitHub Actions.

Secrets / env:
  ARITYFLOW_USERNAME
  ARITYFLOW_PASSWORD
  ARITYFLOW_BASE_URL   (optional, default https://www.arityflow.top)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  QUOTA_DIVISOR        (optional, default 5000; raw quota / divisor = 🍀)
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_URL = os.environ.get("ARITYFLOW_BASE_URL", "https://www.arityflow.top").rstrip("/")
USERNAME = os.environ.get("ARITYFLOW_USERNAME", "").strip()
PASSWORD = os.environ.get("ARITYFLOW_PASSWORD", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
QUOTA_DIVISOR = float(os.environ.get("QUOTA_DIVISOR", "5000"))

OUT_DIR = Path(os.environ.get("CHECKIN_OUT_DIR", "artifacts"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT = OUT_DIR / "checkin.png"
RESULT_JSON = OUT_DIR / "result.json"

CST = timezone(timedelta(hours=8))


def clover(raw: float | int | None) -> str:
    if raw is None:
        return "N/A"
    return f"{float(raw) / QUOTA_DIVISOR:.2f}"


def require_env() -> None:
    missing = [
        name
        for name, val in [
            ("ARITYFLOW_USERNAME", USERNAME),
            ("ARITYFLOW_PASSWORD", PASSWORD),
            ("TELEGRAM_BOT_TOKEN", TG_TOKEN),
            ("TELEGRAM_CHAT_ID", TG_CHAT),
        ]
        if not val
    ]
    if missing:
        raise SystemExit(f"Missing required env: {', '.join(missing)}")


class ArityFlowClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "ArityFlow-CheckinBot/1.0 (+github-actions; "
                    "https://github.com/)"
                ),
                "Accept": "application/json",
            }
        )
        self.user_id: str | None = None
        self.username: str | None = None

    def login(self, username: str, password: str) -> dict:
        r = self.session.post(
            f"{self.base_url}/api/user/login",
            json={"username": username, "password": password},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Login failed: {data.get('message') or data}")
        user = data["data"]
        self.user_id = str(user["id"])
        self.username = user.get("username") or username
        self.session.headers["New-Api-User"] = self.user_id
        return user

    def self_info(self) -> dict:
        r = self.session.get(f"{self.base_url}/api/user/self", timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Get self failed: {data.get('message') or data}")
        return data["data"]

    def checkin_status(self) -> dict:
        r = self.session.get(f"{self.base_url}/api/user/checkin", timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            # Some deployments still return body under data even when message set
            if "data" in data:
                return data["data"]
            raise RuntimeError(f"Get checkin status failed: {data.get('message') or data}")
        return data.get("data") or {}

    def checkin(self) -> dict:
        r = self.session.post(
            f"{self.base_url}/api/user/checkin",
            json={},
            timeout=30,
        )
        # API may return 200 with success=false when already checked in
        try:
            data = r.json()
        except Exception:
            r.raise_for_status()
            raise
        return data


def take_profile_screenshot(client: ArityFlowClient, path: Path) -> bool:
    """Login state via cookies, open profile, screenshot. Returns True on success."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; skip screenshot")
        return False

    cookies = []
    for c in client.session.cookies:
        cookies.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain.lstrip(".") if c.domain else client.base_url.split("//", 1)[-1].split("/")[0],
                "path": c.path or "/",
                "httpOnly": bool(c.has_nonstandard_attr("HttpOnly") or getattr(c, "_rest", {}).get("HttpOnly")),
                "secure": bool(c.secure),
            }
        )

    # Ensure domain is set for Playwright
    host = client.base_url.split("//", 1)[-1].split("/")[0]
    for c in cookies:
        if not c.get("domain"):
            c["domain"] = host

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
            )
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()
            page.goto(f"{client.base_url}/profile", wait_until="networkidle", timeout=60000)
            # Wait a bit for SPA content
            page.wait_for_timeout(2000)
            # Try scroll check-in card into view
            try:
                page.get_by_text("Daily Check-in", exact=False).first.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                try:
                    page.get_by_text("每日签到", exact=False).first.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
            page.screenshot(path=str(path), full_page=True)
            browser.close()
        print(f"Screenshot saved: {path}")
        return path.exists() and path.stat().st_size > 0
    except Exception as e:
        print(f"Screenshot failed: {e}")
        traceback.print_exc()
        return False


def send_telegram(text: str, photo: Path | None = None) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram not configured; skip notify")
        return

    if photo and photo.exists() and photo.stat().st_size > 0:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
        with photo.open("rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": TG_CHAT, "caption": text[:1024]},
                files={"photo": ("checkin.png", f, "image/png")},
                timeout=60,
            )
        if resp.ok:
            print("Telegram photo sent")
            return
        print(f"sendPhoto failed ({resp.status_code}): {resp.text[:300]}; fallback to text")

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Telegram sendMessage failed: {resp.status_code} {resp.text[:300]}")
    print("Telegram text sent")


def build_message(result: dict) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    status = result.get("status", "unknown")
    lines = [
        "🍀 ArityFlow 每日签到",
        f"时间: {now}",
        f"账号: {result.get('username', '?')}",
        f"状态: {status}",
    ]
    if result.get("message"):
        lines.append(f"接口: {result['message']}")
    if result.get("awarded_raw") is not None:
        lines.append(f"本次获得: +🍀 {clover(result['awarded_raw'])}")
    if result.get("balance_raw") is not None:
        lines.append(f"当前余额: 🍀 {clover(result['balance_raw'])}")
    if result.get("checkin_count") is not None:
        lines.append(f"累计签到: {result['checkin_count']} 次")
    if result.get("checked_in_today") is not None:
        lines.append(f"今日已签: {'是' if result['checked_in_today'] else '否'}")
    if result.get("error"):
        lines.append(f"错误: {result['error']}")
    lines.append(f"站点: {BASE_URL}")
    return "\n".join(lines)


def run() -> int:
    require_env()
    result: dict = {
        "ok": False,
        "status": "failed",
        "username": USERNAME,
        "base_url": BASE_URL,
    }
    client = ArityFlowClient(BASE_URL)
    exit_code = 1
    screenshot_ok = False

    try:
        user = client.login(USERNAME, PASSWORD)
        result["username"] = user.get("username") or USERNAME
        result["user_id"] = user.get("id")

        before = client.self_info()
        result["balance_before_raw"] = before.get("quota")

        status = client.checkin_status()
        stats = status.get("stats") or {}
        already = bool(stats.get("checked_in_today"))
        result["checkin_count_before"] = stats.get("checkin_count")
        result["checked_in_today_before"] = already

        api = client.checkin()
        result["api_response"] = api
        result["message"] = api.get("message") or ""

        if api.get("success"):
            result["status"] = "checked_in"
            result["ok"] = True
            # Prefer explicit award field if present
            awarded = None
            data = api.get("data")
            if isinstance(data, dict):
                awarded = data.get("quota_awarded") or data.get("quota") or data.get("award")
            result["awarded_raw"] = awarded
            exit_code = 0
        elif "已签到" in str(api.get("message") or "") or already:
            result["status"] = "already_checked_in"
            result["ok"] = True
            # Pull today's award from records if any
            records = stats.get("records") or []
            today = datetime.now(CST).strftime("%Y-%m-%d")
            for rec in records:
                if str(rec.get("checkin_date")) == today:
                    result["awarded_raw"] = rec.get("quota_awarded")
                    break
            exit_code = 0
        else:
            result["status"] = "failed"
            result["error"] = api.get("message") or str(api)
            exit_code = 1

        after = client.self_info()
        result["balance_raw"] = after.get("quota")
        if result.get("awarded_raw") is None and result.get("balance_before_raw") is not None:
            delta = (after.get("quota") or 0) - (result["balance_before_raw"] or 0)
            if delta > 0:
                result["awarded_raw"] = delta

        status2 = client.checkin_status()
        stats2 = status2.get("stats") or {}
        result["checked_in_today"] = stats2.get("checked_in_today")
        result["checkin_count"] = stats2.get("checkin_count")
        if result.get("awarded_raw") is None:
            today = datetime.now(CST).strftime("%Y-%m-%d")
            for rec in stats2.get("records") or []:
                if str(rec.get("checkin_date")) == today:
                    result["awarded_raw"] = rec.get("quota_awarded")
                    break

    except Exception as e:
        result["status"] = "failed"
        result["ok"] = False
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(result["traceback"])
        exit_code = 1

    # Screenshot even on failure if we have a session
    try:
        if client.user_id:
            screenshot_ok = take_profile_screenshot(client, SCREENSHOT)
    except Exception as e:
        print(f"screenshot outer fail: {e}")
    result["screenshot"] = str(SCREENSHOT) if screenshot_ok else None

    RESULT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    msg = build_message(result)
    print(msg)

    try:
        send_telegram(msg, SCREENSHOT if screenshot_ok else None)
    except Exception as e:
        print(f"Telegram notify failed: {e}")
        # Still keep check-in exit code; mark notify failure in result
        result["telegram_error"] = str(e)
        RESULT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if exit_code == 0:
            exit_code = 2  # check-in ok, notify failed

    return exit_code


if __name__ == "__main__":
    sys.exit(run())
