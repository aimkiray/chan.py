import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse
from typing import Optional

import requests


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


def send_text(kind: str, webhook: str, text: str, timeout_sec: float = 8.0, secret: Optional[str] = None) -> bool:
    kind = str(kind or "").strip().lower()
    webhook = str(webhook or "").strip()
    if not kind or not webhook or not text:
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
        return False

    try:
        resp = requests.post(webhook, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, timeout=float(timeout_sec))
        return bool(200 <= int(resp.status_code) < 300)
    except Exception:
        return False


def send_spug_sms(token_or_url: str, phone: str, text: str, timeout_sec: float = 8.0) -> bool:
    phone = str(phone or "").strip()
    token_or_url = str(token_or_url or "").strip()
    if not phone or not token_or_url:
        return False
    url = token_or_url if token_or_url.startswith("http://") or token_or_url.startswith("https://") else f"https://push.spug.cc/sms/{token_or_url}"
    payload = {"name": _spug_sms_name(text), "code": "000000", "to": phone}
    try:
        resp = requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, timeout=float(timeout_sec))
        return bool(200 <= int(resp.status_code) < 300)
    except Exception:
        return False


def send_text_env(text: str, timeout_sec: float = 8.0) -> bool:
    import os

    enabled = str(os.environ.get("MLCHAN_NOTIFY_ENABLED", "")).strip().lower()
    if enabled in ["0", "false", "no", "off", "disabled"]:
        return False
    kind = str(os.environ.get("MLCHAN_NOTIFY_KIND", "")).strip()
    webhook = str(os.environ.get("MLCHAN_NOTIFY_WEBHOOK", "")).strip()
    secret = str(os.environ.get("MLCHAN_NOTIFY_SECRET", "")).strip() or None
    if not kind or not webhook:
        return False
    return send_text(kind=kind, webhook=webhook, text=text, timeout_sec=timeout_sec, secret=secret)
