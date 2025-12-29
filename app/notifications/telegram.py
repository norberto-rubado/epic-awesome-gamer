# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

_TELEGRAM_MESSAGE_MAX_LEN = 4096


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    disable_web_page_preview: bool = True


def _truncate_message(text: str) -> str:
    text = text.strip()
    if len(text) <= _TELEGRAM_MESSAGE_MAX_LEN:
        return text
    return text[: _TELEGRAM_MESSAGE_MAX_LEN - 20].rstrip() + "\n…(truncated)"


async def send_telegram_message(config: TelegramConfig, text: str) -> bool:
    text = _truncate_message(text)
    if not text:
        return True

    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = {
        "chat_id": config.chat_id,
        "text": text,
        "disable_web_page_preview": config.disable_web_page_preview,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as err:
        logger.warning(f"Telegram notification failed: {err}")
        return False

