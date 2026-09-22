#!/usr/bin/env python3
"""Local harness API for the Bonsai overseer.

Grok bots, the phone web UI, and the desktop GUI all talk to this instead of
poking job files directly.

  GET  /api/status
  GET  /api/dialogue
  GET  /api/log
  GET  /api/pending
  POST /api/inbox     {"text": "..."}
  POST /api/start     {"goal": "..."}
  POST /api/stop
  POST /api/chat      {"message": "..."}   manager LLM + tools
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

DEMO = Path(__file__).resolve().parents[1]
ROOT = DEMO / "jobs" / "workspace"
JOB = ROOT / ".overseer"
PY = DEMO / ".venv" / "Scripts" / "python.exe"
OVERSEER = DEMO / "agent" / "overseer.py"
LLAMA = "http://127.0.0.1:8080/v1"
XAI = "https://api.x.ai/v1"
PORT = 8787

_sessions: dict[str, list[dict]] = {}
_sessions_lock = threading.Lock()
_last_notify = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_path() -> Path:
    JOB.mkdir(parents=True, exist_ok=True)
    p = JOB / "bridge_token.txt"
    if not p.is_file():
        p.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    return p


def bridge_token() -> str:
    return token_path().read_text(encoding="utf-8").strip()


def load_state() -> dict:
    p = JOB / "state.json"
    if not p.is_file():
        return {"status": "idle", "root": str(ROOT)}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "unreadable"}


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def overseer_alive() -> bool:
    pf = JOB / "overseer.pid"
    if not pf.is_file():
        return False
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    return pid_exists(pid)


def llama_ok() -> bool:
    try:
        r = httpx.get("http://127.0.0.1:8080/health", timeout=2.0)
        return "ok" in r.text
    except httpx.HTTPError:
        return False


def tail(path: Path, n: int = 40) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def status() -> dict:
    st = load_state()
    pending = ""
    pq = JOB / "pending_question.json"
    if pq.is_file():
        try:
            pending = json.loads(pq.read_text(encoding="utf-8")).get("question") or ""
        except json.JSONDecodeError:
            pending = pq.read_text(encoding="utf-8")[:500]
    return {
        "ok": True,
        "llama": llama_ok(),
        "overseer_alive": overseer_alive(),
        "status": st.get("status") or "idle",
        "current": st.get("current"),
        "goal": st.get("goal"),
        "latest_request": st.get("latest_request"),
        "artifact": st.get("artifact"),
        "notes": (st.get("notes") or "")[-1200:],
        "pending_question": pending,
        "root": str(ROOT),
        "updated": st.get("updated"),
        "plan": [
            {"id": t.get("id"), "title": t.get("title"), "status": t.get("status")}
            for t in (st.get("plan") or [])[-12:]
        ],
    }



def enqueue_task_api(payload: dict) -> dict:
    JOB.mkdir(parents=True, exist_ok=True)
    st_path = JOB / "state.json"
    if st_path.is_file():
        try:
            state = json.loads(st_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    else:
        state = {"goal": "", "plan": [], "notes": "", "status": "idle"}
    state["orch"] = "botfather"
    tid = str(payload.get("id") or payload.get("task_id") or "").strip()
    title = str(payload.get("title") or payload.get("name") or "").strip()
    detail = str(payload.get("detail") or payload.get("instruction") or payload.get("text") or "").strip()
    if not tid or not (title or detail):
        return {"ok": False, "error": "need id and title|detail"}
    plan = state.setdefault("plan", [])
    card = {"id": tid, "title": title or tid, "detail": detail or title, "status": "pending"}
    replaced = False
    for t in plan:
        if str(t.get("id")) == tid and (t.get("status") or "") in ("pending", "running", "retry", "failed"):
            t.update(card)
            replaced = True
            break
    if not replaced:
        plan.append(card)
    stop = JOB / "STOP"
    if stop.is_file():
        try:
            stop.unlink()
        except OSError:
            pass
    if state.get("status") in ("paused", "stopped", "blocked"):
        state["status"] = "idle"
    stamp = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    state["updated"] = stamp
    st_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    with (JOB / "inbox.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, "text": json.dumps(card, ensure_ascii=False)}, ensure_ascii=False) + "\n")
    with (JOB / "dialogue.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, "role": "system", "text": f"API queued task {tid}: {title}"}, ensure_ascii=False) + "\n")
    return {"ok": True, "task": card, "orch": "botfather", "alive": overseer_alive()}

def instruct(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}
    JOB.mkdir(parents=True, exist_ok=True)
    rec = {"ts": utc_now(), "text": text}
    with (JOB / "inbox.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    drec = {"ts": utc_now(), "role": "user", "text": text}
    with (JOB / "dialogue.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(drec, ensure_ascii=False) + "\n")
    return {"ok": True, "queued": True, "alive": overseer_alive()}


def start_job(goal: str) -> dict:
    goal = (goal or "").strip()
    JOB.mkdir(parents=True, exist_ok=True)
    stop = JOB / "STOP"
    if stop.is_file():
        stop.unlink()
    if overseer_alive():
        instruct(goal or "Continue. Improve depth and systems.")
        return {"ok": True, "mode": "inbox", "detail": "overseer already running; sent as instruction"}
    if not PY.is_file() or not OVERSEER.is_file():
        return {"ok": False, "error": "overseer.py or venv python missing"}
    args = [str(PY), "-u", str(OVERSEER), "--root", str(ROOT), "--api", LLAMA,
            "--max-minutes", "600", "--max-cycles", "5000"]
    if goal:
        args += ["--goal", goal]
    subprocess.Popen(
        args,
        cwd=str(DEMO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {"ok": True, "mode": "started", "goal": goal}


def stop_job() -> dict:
    JOB.mkdir(parents=True, exist_ok=True)
    (JOB / "STOP").write_text(utc_now(), encoding="utf-8")
    pf = JOB / "overseer.pid"
    if pf.is_file():
        try:
            pid = int(pf.read_text(encoding="utf-8").strip())
            os.kill(pid, 9)
        except (ValueError, OSError):
            pass
    return {"ok": True, "stopped": True}


def pending() -> dict:
    pq = JOB / "pending_question.json"
    if not pq.is_file():
        return {"ok": True, "waiting": False, "question": ""}
    try:
        q = json.loads(pq.read_text(encoding="utf-8")).get("question") or ""
    except json.JSONDecodeError:
        q = pq.read_text(encoding="utf-8")[:800]
    return {"ok": True, "waiting": True, "question": q}


def xai_key() -> str:
    return (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or "").strip()


def manager_chat(user_text: str, session_id: str = "default") -> str:
    """Grok if a key is present, otherwise Qwen3-30B-A3B, with harness tools."""
    tools = [
        {"type": "function", "function": {
            "name": "harness_status",
            "description": "Get overseer/llama status, current task, pending question, last notes.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "harness_instruct",
            "description": "Send an instruction to the running overseer (improve depth, add a system, answer a question).",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }},
        {"type": "function", "function": {
            "name": "harness_start",
            "description": "Start or resume the overseer job with an optional new goal/addendum.",
            "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}},
        }},
        {"type": "function", "function": {
            "name": "harness_stop",
            "description": "Stop the overseer.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "harness_log",
            "description": "Tail the overseer log.",
            "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}},
        }},
        {"type": "function", "function": {
            "name": "harness_dialogue",
            "description": "Tail the overseer/worker dialogue.",
            "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}},
        }},
        {"type": "function", "function": {
            "name": "harness_compile",
            "description": "Release-build Descent. Set run=true only if the user asked to launch the game.",
            "parameters": {"type": "object", "properties": {"run": {"type": "boolean"}}},
        }},
    ]
    system = (
        "You are the phone/Grok front-end for a local game-dev harness. "
        "A Bonsai 2 27B overseer+workers write a WinForms roguelite called Descent. "
        "You do not write the game yourself. You manage work: status, instruct, start, stop, compile. "
        "When the user wants more depth/systems, call harness_instruct or harness_start with a concrete addendum. "
        "NEVER compile or launch the game unless the user explicitly asked. Running the exe locks the binary. "
        "If they say compile, call harness_compile with run=false. If they say run/launch, run=true. "
        "If status.pending_question is set, the overseer is blocked on the human — relay it and send their answer via harness_instruct. "
        "Be concise. Confirm what you dispatched."
    )
    with _sessions_lock:
        hist = _sessions.setdefault(session_id, [])
        hist.append({"role": "user", "content": user_text})
        hist[:] = hist[-16:]
        messages = [{"role": "system", "content": system}] + list(hist)

    key = xai_key()
    if key:
        url, model, headers = XAI + "/chat/completions", "grok-4-1-fast", {"Authorization": f"Bearer {key}"}
    else:
        url, model, headers = LLAMA + "/chat/completions", "bonsai", {}

    client = httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))
    body_base = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": 700,
        "tools": tools,
        "tool_choice": "auto",
        "chat_template_kwargs": {"enable_thinking": False},
        "thinking_budget_tokens": 0,
    }
    try:
        for _ in range(6):
            r = client.post(url, headers=headers, json={**body_base, "messages": messages})
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""
            if not tool_calls:
                with _sessions_lock:
                    hist.append({"role": "assistant", "content": content})
                return content or "(no reply)"
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except json.JSONDecodeError:
                    args = {}
                result = run_tool(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False)[:8000],
                })
        return "Tool loop limit."
    except httpx.HTTPError as e:
        return f"Manager LLM error: {e}. Harness still works via /status and /inbox."
    finally:
        client.close()


def run_tool(name: str, args: dict) -> dict:
    if name == "harness_status":
        return status()
    if name == "harness_instruct":
        return instruct(str(args.get("text") or ""))
    if name == "harness_start":
        return start_job(str(args.get("goal") or ""))
    if name == "harness_stop":
        return stop_job()
    if name == "harness_log":
        return {"lines": tail(JOB / "overseer.log", int(args.get("n") or 25))}
    if name == "harness_dialogue":
        return {"lines": tail(JOB / "dialogue.jsonl", int(args.get("n") or 20))}
    if name == "harness_trace":
        return {"events": trace_events(int(args.get("after") or 0), int(args.get("n") or 40))}
    if name == "harness_compile":
        return compile_game(bool(args.get("run")))
    return {"error": f"unknown tool {name}"}


def compile_game(run_after: bool = False) -> dict:
    try:
        subprocess.run(["taskkill", "/IM", "Descent.exe", "/F"], capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        pass
    proj = ROOT / "Descent" / "Descent.csproj"
    if not proj.is_file():
        cs = list(ROOT.rglob("*.csproj"))
        cs = [p for p in cs if "obj" not in p.parts and "bin" not in p.parts]
        if not cs:
            return {"ok": False, "error": "no csproj"}
        proj = cs[0]
    try:
        proc = subprocess.run(
            ["dotnet", "build", str(proj), "-c", "Release", "--nologo"],
            cwd=str(proj.parent),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    out = (proc.stdout or "") + ("\n" + (proc.stderr or ""))
    if proc.returncode != 0:
        return {"ok": False, "error": out[-3000:]}
    exes = list((proj.parent / "bin" / "Release").rglob(proj.stem + ".exe"))
    exe = str(exes[0]) if exes else ""
    launched = False
    if run_after and exe:
        try:
            subprocess.Popen([exe], cwd=str(Path(exe).parent))
            launched = True
        except OSError as e:
            return {"ok": True, "exe": exe, "launched": False, "error": str(e)}
    instruct("Human requested a compile" + (" and run." if run_after else ".") + " Do not add your own build/run tasks.")
    return {"ok": True, "exe": exe, "launched": launched, "log": out[-1500:]}


def trace_events(after: int = 0, n: int = 80) -> list[dict]:
    path = JOB / "llm_trace.jsonl"
    if not path.is_file():
        return []
    out: list[dict] = []
    i = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        i += 1
        if i <= after:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec["i"] = i
        out.append(rec)
        if len(out) >= n:
            break
    return out


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Overseer — Qwen3-30B-A3B</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; font: 15px/1.4 system-ui,sans-serif; background:#0e120e; color:#e6ece4;
  display:flex; flex-direction:column; height:100dvh; }
header { padding:10px 12px; background:#1c221c; flex:0 0 auto; }
header b { color:#50b45a; }
#st { font-size:12px; color:#96a094; margin-top:4px; white-space:pre-wrap; }
#legend { font-size:11px; color:#7a8478; margin-top:4px; }
#legend span { margin-right:10px; }
.overseer { color:#62d06c; } .worker { color:#7eb4e8; } .tool { color:#e0b24a; } .you { color:#eee; }
#trace { flex:1 1 auto; overflow:auto; padding:8px 10px; font: 12px/1.45 ui-monospace, Consolas, monospace; }
.ev { margin:0 0 8px; padding:7px 9px 7px 10px; border-radius:8px; background:#161c16; border-left:4px solid #444; }
.ev.overseer { border-left-color:#50b45a; }
.ev.worker { border-left-color:#5a9fd4; }
.ev.tool { border-left-color:#c9a227; }
.ev.user,.ev.you { border-left-color:#d8ddd4; }
.who { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; }
.body { white-space:pre-wrap; word-break:break-word; margin-top:4px; color:#cdd4c8; }
.think { color:#9aa890; font-style:italic; }
form { display:flex; gap:8px; padding:8px 10px; background:#101410; flex:0 0 auto; }
input { flex:1; padding:12px; border-radius:10px; border:1px solid #3a4a3a; background:#1a201a; color:inherit; }
button { padding:12px 14px; border:0; border-radius:10px; background:#3e8c48; color:#fff; font-weight:600; }
#chat { max-height:22vh; overflow:auto; padding:0 10px; flex:0 0 auto; }
.msg { margin:6px 0; padding:7px 10px; border-radius:10px; }
.me { background:#2a4a2e; }
.bot { background:#222822; }
.sys { background:#3a3020; font-size:13px; }
</style></head><body>
<section id="newWork" style="margin:12px 0;padding:12px;border:1px solid #3a3a3a;border-radius:8px;background:#1a1a1a"><h2 style="margin:0 0 8px;font-size:1.1rem">Start new work</h2><p style="margin:0 0 8px;opacity:.85">Local model: <strong>Qwen3-30B-A3B</strong> (stock llama.cpp :8080)</p><textarea id="goalBox" rows="4" placeholder="New work - describe the project or task" style="width:100%;box-sizing:border-box;padding:8px;border-radius:6px;border:1px solid #555;background:#111;color:#eee"></textarea><div style="margin-top:8px"><button type="button" id="btnStartNewWork" onclick="startNewWork()" style="padding:10px 16px;font-weight:700;cursor:pointer">Start new work</button></div></section><script>async function startNewWork(){const t=(document.getElementById("goalBox")||{}).value||"";const goal=(t&&t.trim())?t.trim():"Blank project - await instructions";try{const headers={"Content-Type":"application/json"};if(typeof hdr==="function") Object.assign(headers,hdr());const r=await fetch("/api/start",{method:"POST",headers,body:JSON.stringify({goal})});const j=await r.json();if(typeof toast==="function") toast(j.ok?"Started: "+goal.slice(0,80):(j.error||"start failed"));else alert(j.ok?"Started":"Failed");if(j.ok&&typeof pollStatus==="function") pollStatus();}catch(e){if(typeof toast==="function") toast("start error: "+e); else alert(e);}}</script>
<header>
  <b>Live LLM</b> · VPN browser is enough — no Telegram required
  <div id="st">connecting…</div>
  <div id="legend"><span class="overseer">■ overseer</span><span class="worker">■ worker</span><span class="tool">■ tool</span></div>
</header>
<div id="trace"></div>
<div id="chat"></div>
<form id="f"><input id="i" autocomplete="off" placeholder="Instruct the overseer: more dungeon depth, new systems…"/><button>Send</button></form>
<script>
const T = new URLSearchParams(location.search).get('token') || '';
const hdr = {'Content-Type':'application/json', 'X-Bridge-Token': T};
let after = 0, stick = true;
trace.addEventListener('scroll', ()=>{ stick = (trace.scrollHeight - trace.scrollTop - trace.clientHeight) < 40; });
function bubble(cls, t){ const d=document.createElement('div'); d.className='msg '+cls; d.textContent=t; chat.appendChild(d); chat.scrollTop=chat.scrollHeight; }
function addEv(ev){
  const a = (ev.agent||'llm').toLowerCase();
  const d = document.createElement('div');
  d.className = 'ev '+a;
  const who = (ev.agent||'?') + (ev.task ? ' · '+ev.task : '') + (ev.tool ? ' · '+ev.tool : '');
  const tools = (ev.tools&&ev.tools.length) ? '\n[tools: '+ev.tools.join(', ')+']' : '';
  let body = '';
  if(ev.prompt) body += '▸ '+ev.prompt;
  if(ev.thinking) body += (body?'\n':'')+'('+ev.thinking+')';
  if(ev.response) body += (body?'\n':'')+ev.response;
  d.innerHTML = '<div class="who '+a+'">'+who+'</div><div class="body">'
    + body.replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + tools.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</div>';
  trace.appendChild(d);
  if(stick) trace.scrollTop = trace.scrollHeight;
}
async function pullTrace(){
  const r = await fetch('/api/trace?after='+after+'&token='+encodeURIComponent(T), {headers:hdr});
  const j = await r.json();
  (j.events||[]).forEach(ev => { after = ev.i; addEv(ev); });
}
async function refresh(){
  const r = await fetch('/api/status?token='+encodeURIComponent(T), {headers:hdr});
  const s = await r.json();
  let line = (s.llama?'LLM up':'LLM down')+' · overseer '+(s.status||'idle');
  if(s.current) line += ' · '+s.current;
  if(s.pending_question) line += '\nASKS: '+s.pending_question;
  st.textContent = line;
  if(s.pending_question && s.pending_question !== window._q){
    window._q = s.pending_question;
    bubble('sys', 'Overseer asks: '+s.pending_question);
  }
}
f.onsubmit = async (e)=>{
  e.preventDefault();
  const text = i.value.trim(); if(!text) return;
  i.value=''; bubble('me', text);
  const r = await fetch('/api/chat?token='+encodeURIComponent(T), {method:'POST', headers:hdr, body:JSON.stringify({message:text})});
  const j = await r.json();
  bubble('bot', j.reply || JSON.stringify(j));
  refresh();
};
refresh(); pullTrace();
setInterval(refresh, 4000);
setInterval(pullTrace, 1200);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _ok_auth(self) -> bool:
        tok = bridge_token()
        q = parse_qs(urlparse(self.path).query)
        got = (self.headers.get("X-Bridge-Token") or (q.get("token") or [""])[0] or "").strip()
        return secrets.compare_digest(got, tok)

    def _send(self, code: int, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else (
            body.encode("utf-8") if ctype != "application/json" else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bridge-Token")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not self._ok_auth():
                self._send(
                    401,
                    "<!doctype html><meta charset=utf-8><body style='background:#111;color:#ddd;font:16px sans-serif;padding:2rem'>"
                    "<p>This page needs the bridge token from the Bonsai Server URL (auto-opened on launch).</p>"
                    "<p>Use the full URL it copies (it includes <code>?token=…</code>).</p></body>",
                    "text/html",
                )
                return
            self._send(200, INDEX_HTML, "text/html")
            return
        if not self._ok_auth():
            self._send(401, {"ok": False, "error": "bad token"})
            return
        if path == "/api/status":
            self._send(200, status())
        elif path == "/api/dialogue":
            self._send(200, {"lines": tail(JOB / "dialogue.jsonl", 50)})
        elif path == "/api/log":
            self._send(200, {"lines": tail(JOB / "overseer.log", 50)})
        elif path == "/api/pending":
            self._send(200, pending())
        elif path == "/api/trace":
            q = parse_qs(urlparse(self.path).query)
            after = int((q.get("after") or ["0"])[0] or 0)
            self._send(200, {"events": trace_events(after, 80)})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._ok_auth():
            self._send(401, {"ok": False, "error": "bad token"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            data = {}
        path = urlparse(self.path).path
        if path == "/api/task":
            self._send(200, enqueue_task_api(data if isinstance(data, dict) else {}))
        elif path == "/api/inbox":
            text = str(data.get("text") or data.get("message") or "")
            stripped = text.strip()
            if stripped.startswith("{") and ('"id"' in stripped or '"task_id"' in stripped):
                try:
                    self._send(200, enqueue_task_api(json.loads(stripped)))
                except Exception:
                    self._send(200, instruct(text))
            else:
                self._send(200, instruct(text))
        elif path == "/api/start":
            self._send(200, start_job(str(data.get("goal") or data.get("text") or "")))
        elif path == "/api/stop":
            self._send(200, stop_job())
        elif path == "/api/compile":
            self._send(200, compile_game(bool(data.get("run"))))
        elif path == "/api/chat":
            sid = str(data.get("session") or self.client_address[0])
            msg = str(data.get("message") or data.get("text") or "")
            self._send(200, {"ok": True, "reply": manager_chat(msg, sid)})
        else:
            self._send(404, {"ok": False, "error": "not found"})


def serve(host: str = "0.0.0.0", port: int = PORT):
    JOB.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd


def main():
    tok = bridge_token()
    httpd = serve()
    print(f"harness http://127.0.0.1:{PORT}/?token={tok}", flush=True)
    print(f"phone   http://<this-pc-lan-ip>:{PORT}/?token={tok}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
