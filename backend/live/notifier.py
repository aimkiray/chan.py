import base64
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.parse
from typing import Optional

import requests

# WARNING+ records reach stderr even without logging configuration
# (logging.lastResort), so dropped/failed notifications stay observable
# for both CLI callers and the FastAPI service.
logger = logging.getLogger(__name__)


def _sanitize_err(err_text: str, url: str, limit: int = 300) -> str:
    """Strip the request URL (webhook tokens live in the query string for
    most providers) out of exception text before it hits logs."""
    s = str(err_text or "")
    try:
        if url:
            s = s.replace(str(url), "<redacted-url>")
    except Exception:
        pass
    s = re.sub(r"\s+", " ", s).strip()
    return s[: int(limit)]


def _post_json(label: str, url: str, body: bytes, headers: dict, timeout_sec: float, retries: int = 2, backoff_sec: float = 0.5) -> bool:
    """POST with bounded retry+backoff and logged failures.

    Retries cover transient webhook flaps: connection/timeout exceptions,
    HTTP 429 and 5xx. Other 4xx are permanent (bad payload/token) and fail
    immediately. The URL itself is never logged — it embeds the token.
    """
    try:
        max_attempts = 1 + max(0, int(retries))
    except Exception:
        max_attempts = 3
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=float(timeout_sec))
            code = int(resp.status_code)
            if 200 <= code < 300:
                return True
            last_err = f"http_status={code}"
            retryable = bool(code == 429 or code >= 500)
        except Exception as e:
            last_err = _sanitize_err(f"{type(e).__name__}: {e}", url)
            retryable = True
        if attempt >= max_attempts or not retryable:
            logger.warning("%s failed after %d attempt(s): %s", label, attempt, last_err)
            return False
        delay = float(backoff_sec) * (2 ** (attempt - 1))
        logger.warning("%s attempt %d/%d failed: %s; retrying in %.1fs", label, attempt, max_attempts, last_err, delay)
        try:
            time.sleep(max(0.0, delay))
        except Exception:
            pass
    return False


def _to_chinese_num(n: int) -> str:
    if n == 0:
        return "零"
    chars = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    if n in chars:
        return chars[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + chars.get(n % 10, "")
    if n < 100:
        tens = n // 10
        unit = n % 10
        return chars[tens] + "十" + (chars[unit] if unit else "")
    return str(n)


def _spug_sms_name(text: str) -> str:
    def _replace_num(m: re.Match) -> str:
        try:
            return _to_chinese_num(int(m.group(0)))
        except Exception:
            return ""

    text = re.sub(r"\d{1,3}", _replace_num, str(text or ""))
    filtered = re.sub(r"[^\u4e00-\u9fa5a-zA-Z]", "", text)
    if not filtered:
        filtered = "调仓提醒"
    if len(filtered) > 10:
        filtered = filtered[:10]
    return filtered


def _spug_sms_summary(text: str, limit: int = 120) -> str:
    """Short message body for the Spug template variable.

    The old code sent a hardcoded code="000000", so the SMS carried only
    the (<=10 char) title and the actual alert text was lost."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        s = "-"
    return s[: int(limit)]


def build_dingtalk_signed_url(webhook: str, secret: Optional[str]) -> str:
    webhook = str(webhook or "").strip()
    secret = str(secret or "").strip() or None
    if not webhook or not secret:
        return webhook
    ts_ms = str(int(time.time() * 1000))
    string_to_sign = f"{ts_ms}\n{secret}".encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(mac))
    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={ts_ms}&sign={sign}"


def send_text(kind: str, webhook: str, text: str, timeout_sec: float = 8.0, secret: Optional[str] = None, retries: int = 2, backoff_sec: float = 0.5) -> bool:
    kind = str(kind or "").strip().lower()
    webhook = str(webhook or "").strip()
    if not kind or not webhook or not text:
        if kind and webhook and not text:
            logger.warning("notify[%s]: empty text; message dropped", kind)
        return False

    payload = None
    headers = {"Content-Type": "application/json"}
    if kind in ["feishu", "lark"]:
        payload = {"msg_type": "text", "content": {"text": str(text)}}
    elif kind in ["dingtalk", "ding"]:
        payload = {"msgtype": "text", "text": {"content": str(text)}}
        webhook = build_dingtalk_signed_url(webhook, secret)
    elif kind in ["generic", "webhook"]:
        payload = {"text": str(text)}
    else:
        logger.warning(
            "notify: unknown kind %r (supported: feishu/lark, dingtalk/ding, generic/webhook); message dropped",
            kind,
        )
        return False

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _post_json(f"notify[{kind}]", webhook, body, headers, timeout_sec, retries=retries, backoff_sec=backoff_sec)


def send_spug_sms(token_or_url: str, phone: str, text: str, timeout_sec: float = 8.0, retries: int = 2, backoff_sec: float = 0.5) -> bool:
    phone = str(phone or "").strip()
    token_or_url = str(token_or_url or "").strip()
    if not phone or not token_or_url:
        return False
    url = token_or_url if token_or_url.startswith("http://") or token_or_url.startswith("https://") else f"https://push.spug.cc/sms/{token_or_url}"
    payload = {"name": _spug_sms_name(text), "code": _spug_sms_summary(text), "to": phone}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _post_json("notify[spug_sms]", url, body, {"Content-Type": "application/json"}, timeout_sec, retries=retries, backoff_sec=backoff_sec)


def send_text_env(text: str, timeout_sec: float = 8.0, retries: int = 2, backoff_sec: float = 0.5) -> bool:
    import os

    enabled = str(os.environ.get("MLCHAN_NOTIFY_ENABLED", "")).strip().lower()
    if enabled in ["0", "false", "no", "off", "disabled"]:
        return False
    kind = str(os.environ.get("MLCHAN_NOTIFY_KIND", "")).strip()
    webhook = str(os.environ.get("MLCHAN_NOTIFY_WEBHOOK", "")).strip()
    secret = str(os.environ.get("MLCHAN_NOTIFY_SECRET", "")).strip() or None
    if not kind or not webhook:
        # Silent when notifications were never configured; warn only when
        # the user explicitly asked for them (or half-configured) so a typo
        # doesn't silently drop alerts.
        if enabled in ["1", "true", "yes", "on", "enabled"] or kind or webhook:
            logger.warning(
                "notify: enabled=%r but MLCHAN_NOTIFY_KIND/MLCHAN_NOTIFY_WEBHOOK incomplete; message dropped",
                enabled or "unset",
            )
        return False
    return send_text(kind=kind, webhook=webhook, text=text, timeout_sec=timeout_sec, secret=secret, retries=retries, backoff_sec=backoff_sec)
