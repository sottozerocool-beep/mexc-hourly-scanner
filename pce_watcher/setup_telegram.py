#!/usr/bin/env python3
"""Show the chat IDs that have contacted a Telegram bot, without echoing its token."""

from __future__ import annotations

import getpass
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    print("Apri Telegram, invia /start al tuo bot e poi torna qui.")
    token = getpass.getpass("Token BotFather (non verrà mostrato): ").strip()
    if not token:
        print("Token mancante", file=sys.stderr)
        return 1
    request = Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        headers={"User-Agent": "Core-PCE-BTC-Watcher/1.0"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Errore Telegram: {exc}", file=sys.stderr)
        return 1

    chats: dict[str, str] = {}
    for update in payload.get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        name = chat.get("title") or chat.get("username") or chat.get("first_name") or "chat"
        chats[str(chat_id)] = name
    if not chats:
        print("Nessuna chat trovata. Invia /start al bot e riprova.")
        return 2
    for chat_id, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={chat_id}  ({name})")
    print("Salva token e chat ID come GitHub Actions secrets; non commetterli nel repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
