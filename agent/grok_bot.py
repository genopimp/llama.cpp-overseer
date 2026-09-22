#!/usr/bin/env python3
"""Phone/Grok front-end for the local Bonsai harness.

Starts the harness HTTP API (port 8787) and optionally a Telegram bot.
Natural-language chat is handled by Grok (XAI_API_KEY / GROK_API_KEY) or
local Bonsai if no key is set.

  .venv\\Scripts\\python.exe agent\\grok_bot.py
  TELEGRAM_BOT_TOKEN=...  optional
  XAI_API_KEY=...         optional (Grok manager); else Bonsai on :8080
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (
    JOB,
    PORT,
    bridge_token,
    compile_game,
    instruct,
    llama_ok,
    manager_chat,
    pending,
    serve,
    status,
    start_job,
    stop_job,
)

DEMO = Path(__file__).resolve().parents[1]
CHAT_ID_FILE = JOB / "telegram_chat_id.txt"
_last_q = ""


def telegram_token() -> str:
    cfg = DEMO / "agent" / "bridge.json"
    if cfg.is_file():
        try:
            t = json.loads(cfg.read_text(encoding="utf-8")).get("telegram_token") or ""
            if t:
                return str(t).strip()
        except json.JSONDecodeError:
            pass
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or "").strip()


def send_telegram(text: str, chat_id: str | None = None) -> None:
    tok = telegram_token()
    if not tok:
        return
    cid = chat_id or (CHAT_ID_FILE.read_text(encoding="utf-8").strip() if CHAT_ID_FILE.is_file() else "")
    if not cid:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": text[:3900]},
            timeout=20.0,
        )
    except httpx.HTTPError:
        pass


def handle_text(text: str, session: str) -> str:
    t = (text or "").strip()
    low = t.lower()
    if low in ("/status", "status"):
        s = status()
        q = s.get("pending_question") or ""
        return (
            f"llama {'up' if s.get('llama') else 'down'} | overseer {s.get('status')}"
            + (f" | {s.get('current')}" if s.get("current") else "")
            + (f"\nASKS: {q}" if q else "")
            + (f"\nexe: {s.get('artifact')}" if s.get("artifact") else "")
        )
    if low.startswith("/go "):
        return json.dumps(start_job(t[4:].strip()), indent=2)
    if low in ("/stop", "stop overseer"):
        return json.dumps(stop_job())
    if low.startswith("/say "):
        return json.dumps(instruct(t[5:].strip()))
    if low in ("/compile", "compile"):
        return json.dumps(compile_game(False), indent=2)
    if low in ("/run", "run game", "launch game"):
        return json.dumps(compile_game(True), indent=2)
    return manager_chat(t, session)


def telegram_loop():
    tok = telegram_token()
    if not tok:
        print("Telegram: no token (set TELEGRAM_BOT_TOKEN or agent/bridge.json). Web UI still works.", flush=True)
        return
    offset = 0
    print("Telegram: polling", flush=True)
    while True:
        try:
            r = httpx.get(
                f"https://api.telegram.org/bot{tok}/getUpdates",
                params={"timeout": 25, "offset": offset, "allowed_updates": json.dumps(["message"])},
                timeout=40.0,
            )
            data = r.json()
            for upd in data.get("result") or []:
                offset = int(upd["update_id"]) + 1
                msg = upd.get("message") or {}
                text = msg.get("text") or ""
                chat = msg.get("chat") or {}
                cid = str(chat.get("id") or "")
                if cid:
                    JOB.mkdir(parents=True, exist_ok=True)
                    CHAT_ID_FILE.write_text(cid, encoding="utf-8")
                if not text:
                    continue
                reply = handle_text(text, "tg:" + cid)
                send_telegram(reply, cid)
        except Exception as e:
            print("telegram error", e, flush=True)
            time.sleep(4)


def watch_questions():
    global _last_q
    while True:
        time.sleep(4)
        try:
            p = pending()
            q = p.get("question") or ""
            if p.get("waiting") and q and q != _last_q:
                _last_q = q
                send_telegram("Overseer needs you:\n" + q)
        except Exception:
            pass


def main():
    JOB.mkdir(parents=True, exist_ok=True)
    logf = JOB / "bridge.log"
    def blog(msg: str) -> None:
        line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "  " + msg
        print(line, flush=True)
        try:
            with logf.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    tok = bridge_token()
    try:
        httpd = serve("0.0.0.0", PORT)
    except OSError as e:
        blog("FAILED to bind 0.0.0.0:%s: %s" % (PORT, e))
        raise
    (JOB / "bridge.pid").write_text(str(os.getpid()), encoding="utf-8")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=watch_questions, daemon=True).start()
    mgr = "Grok (xAI)" if (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")) else "local Bonsai 27B"
    blog("listening 0.0.0.0:%s  manager=%s  llama=%s" % (PORT, mgr, "up" if llama_ok() else "DOWN"))
    blog("local  http://127.0.0.1:%s/?token=%s" % (PORT, tok))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    finally:
        try:
            pf = JOB / "bridge.pid"
            if pf.is_file() and pf.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pf.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
