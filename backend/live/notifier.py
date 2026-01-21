import json
from typing import Optional

import requests


def send_text(kind: str, webhook: str, text: str, timeout_sec: float = 8.0) -> bool:
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
    elif kind in ["generic", "webhook"]:
        payload = {"text": str(text)}
    else:
        return False

    try:
        resp = requests.post(webhook, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, timeout=float(timeout_sec))
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
    if not kind or not webhook:
        return False
    return send_text(kind=kind, webhook=webhook, text=text, timeout_sec=timeout_sec)

