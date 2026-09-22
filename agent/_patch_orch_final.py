from __future__ import annotations
import json, re, shutil, time, py_compile
from pathlib import Path

OV = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py")
HAR = Path(r"C:\Users\edwar\prism-ml\agent\harness.py")
STATE = Path(r"C:\Users\edwar\prism-ml\jobs\workspace\.overseer\state.json")
CONTRACT = Path(r"C:\Users\edwar\prism-ml\jobs\workspace\Descent\docs\ORCH_BOTFATHER.md")

HELPERS = r'''
def orch_mode(state: dict[str, Any], args: argparse.Namespace | None = None) -> str:
    if args is not None:
        cli = (getattr(args, "orch", None) or "").strip().lower()
        if cli in ("local", "botfather"):
            state["orch"] = cli
            return cli
    mode = (state.get("orch") or "").strip().lower()
    if mode in ("local", "botfather"):
        return mode
    state["orch"] = "botfather"
    return "botfather"


def parse_task_card(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            action = (obj.get("action") or "").lower()
            if action in ("enqueue", "task", "add_task"):
                inner = obj.get("task")
                obj = inner if isinstance(inner, dict) else obj
            tid = str(obj.get("id") or obj.get("task_id") or "").strip()
            title = str(obj.get("title") or obj.get("name") or "").strip()
            detail = str(obj.get("detail") or obj.get("instruction") or obj.get("text") or "").strip()
            if tid and (title or detail):
                return {"id": tid, "title": title or tid, "detail": detail or title, "status": "pending"}
    m = re.match(r"(?is)^TASK\s+([^\s|]+)\s*\|\s*([^|]+)\s*\|\s*(.+)$", raw)
    if m:
        return {"id": m.group(1).strip(), "title": m.group(2).strip(), "detail": m.group(3).strip(), "status": "pending"}
    return None


def enqueue_task(state: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    plan = state.setdefault("plan", [])
    existing = {str(t.get("id")): t for t in plan}
    tid = str(card["id"])
    if tid in existing and (existing[tid].get("status") or "") in ("pending", "running", "retry", "failed"):
        existing[tid]["title"] = card.get("title") or existing[tid].get("title")
        existing[tid]["detail"] = card.get("detail") or existing[tid].get("detail")
        existing[tid]["status"] = "pending"
        return existing[tid]
    if tid in existing:
        n = 2
        while f"{tid}.{n}" in existing:
            n += 1
        card = dict(card)
        card["id"] = f"{tid}.{n}"
    plan.append(card)
    return card


def ingest_botfather_inbox(job_dir: Path, state: dict[str, Any]) -> None:
    msgs = take_inbox(job_dir)
    if not msgs:
        return
    for m in msgs:
        dialogue(job_dir, "user", m)
        card = parse_task_card(m)
        if card:
            enq = enqueue_task(state, card)
            dialogue(job_dir, "system", f"Queued task {enq['id']}: {enq.get('title')}")
            log(job_dir, f"botfather enqueue {enq['id']}")
        else:
            parked = state.setdefault("botfather_inbox", [])
            parked.append({"at": utc_now(), "text": m[:4000]})
            state["botfather_inbox"] = parked[-40:]
            log(job_dir, "parked free-text for BotFather (not worker soup)")
    save_state(job_dir, state)


def auto_review_worker(result: dict[str, Any], task: dict[str, Any], state: dict[str, Any]) -> str:
    status = (result.get("status") or "").lower()
    files = result.get("files") or []
    ok = status in ("ok", "done", "success") or (
        status not in ("blocked", "need_split", "failed") and bool(files)
    )
    if ok:
        task["status"] = "done"
        task["summary"] = result.get("summary")
        task["files"] = files
        note = f"DONE {task.get('id')}: {result.get('summary') or ''}"
        state["notes"] = ((state.get("notes") or "") + "\n" + note).strip()[-3500:]
        return "accept"
    task["status"] = "failed"
    task["summary"] = result.get("summary")
    note = f"FAILED {task.get('id')}: {result.get('summary')}"
    state["notes"] = ((state.get("notes") or "") + "\n" + note).strip()[-3500:]
    return "wait_botfather"

'''

def main():
    bak = OV.with_suffix(f".py.bak-orch-{int(time.time())}")
    shutil.copy2(OV, bak)
    print("backup", bak)
    src = OV.read_text(encoding="utf-8")

    if "--orch" not in src:
        old = '    p.add_argument("--max-cycles", type=int, default=40)\n    args = p.parse_args()'
        new = (
            '    p.add_argument("--max-cycles", type=int, default=40)\n'
            '    p.add_argument("--orch", default="", choices=["", "local", "botfather"],\n'
            '                    help="local=LLM plans+codes; botfather=BotFather plans, LLM codes")\n'
            '    args = p.parse_args()'
        )
        if old not in src:
            raise SystemExit("argparse missing")
        src = src.replace(old, new, 1)
        print("argparse ok")

    if "def orch_mode(" not in src:
        needle = "def ask_overseer(client: Client, state: dict[str, Any], user: str, job_dir: Path) -> dict[str, Any]:"
        if needle not in src:
            raise SystemExit("ask_overseer missing")
        src = src.replace(needle, HELPERS + "\n" + needle, 1)
        print("helpers ok")

    if not re.search(r"^import re\b", src, re.M):
        src = src.replace("import json\n", "import json\nimport re\n", 1)
        print("import re ok")

    if "orch_mode(state, args)" not in src:
        old = (
            "    state = load_state(job_dir, args.goal, str(root))\n"
            '    prev_status = state.get("status")\n'
            '    state["status"] = "running"\n'
            "    save_state(job_dir, state)\n"
        )
        new = (
            "    state = load_state(job_dir, args.goal, str(root))\n"
            '    prev_status = state.get("status")\n'
            "    mode = orch_mode(state, args)\n"
            '    state["status"] = "running"\n'
            "    save_state(job_dir, state)\n"
            '    log(job_dir, f"orch_mode={mode}")\n'
        )
        if old not in src:
            raise SystemExit("boot missing")
        src = src.replace(old, new, 1)
        print("boot ok")

    if "ingest_botfather_inbox(job_dir, state)" not in src:
        old = (
            "        extra = human_notes(job_dir)\n"
            "        build_mode = human_wants_build(extra)\n"
            "        if build_mode:\n"
            '            do_compile(job_dir, state, ws, env, run_after=(build_mode == "run"))\n'
            "\n"
            '        req = (state.get("latest_request") or "").strip()\n'
            '        if req and state.get("request_consumed") != req:\n'
        )
        new = (
            "        mode = orch_mode(state, args)\n"
            "\n"
            '        if mode == "botfather":\n'
            "            ingest_botfather_inbox(job_dir, state)\n"
            '            parked = state.get("botfather_inbox") or []\n'
            "            if parked:\n"
            '                last = str((parked[-1] or {}).get("text") or "")\n'
            "                build_mode = human_wants_build(last)\n"
            "                if build_mode:\n"
            '                    do_compile(job_dir, state, ws, env, run_after=(build_mode == "run"))\n'
            '                    state["botfather_inbox"] = parked[:-1]\n'
            "                    save_state(job_dir, state)\n"
            '            req = ""\n'
            '            extra = ""\n'
            "        else:\n"
            "            extra = human_notes(job_dir)\n"
            "            build_mode = human_wants_build(extra)\n"
            "            if build_mode:\n"
            '                do_compile(job_dir, state, ws, env, run_after=(build_mode == "run"))\n'
            '            req = (state.get("latest_request") or "").strip()\n'
            '        if req and state.get("request_consumed") != req:\n'
        )
        if old not in src:
            i = src.find("extra = human_notes(job_dir)")
            raise SystemExit("loop top missing: " + repr(src[i:i+280]))
        src = src.replace(old, new, 1)
        print("loop top ok")

    if "botfather idle" not in src:
        old = (
            '        if not state.get("plan"):\n'
            '            log(job_dir, "overseer planning")\n'
        )
        new = (
            '        if not state.get("plan"):\n'
            '            if mode == "botfather":\n'
            '                state["status"] = "idle"\n'
            '                state["current"] = None\n'
            "                save_state(job_dir, state)\n"
            "                if cycles <= 1 or cycles % 15 == 0:\n"
            '                    log(job_dir, "botfather idle — waiting for task card")\n'
            '                    dialogue(job_dir, "system", "Idle (BotFather orch). Waiting for /api/task card.")\n'
            "                time.sleep(2.0)\n"
            '                state["status"] = "running"\n'
            "                save_state(job_dir, state)\n"
            "                continue\n"
            '            log(job_dir, "overseer planning")\n'
        )
        if old not in src:
            raise SystemExit("empty plan missing")
        src = src.replace(old, new, 1)
        print("empty plan ok")

    if "botfather idle queue" not in src:
        old = (
            "        task = next_pending(state)\n"
            "        if task is None:\n"
            '            log(job_dir, "overseer review (queue empty)")\n'
        )
        new = (
            "        task = next_pending(state)\n"
            "        if task is None:\n"
            '            if mode == "botfather":\n'
            '                state["status"] = "idle"\n'
            '                state["current"] = None\n'
            "                save_state(job_dir, state)\n"
            "                if cycles % 15 == 0:\n"
            '                    log(job_dir, "botfather idle queue — waiting for next task card")\n'
            '                    dialogue(job_dir, "system", "Queue empty (BotFather orch). Waiting for next /api/task.")\n'
            "                time.sleep(2.0)\n"
            '                state["status"] = "running"\n'
            "                save_state(job_dir, state)\n"
            "                continue\n"
            '            log(job_dir, "overseer review (queue empty)")\n'
        )
        if old not in src:
            raise SystemExit("empty queue missing")
        src = src.replace(old, new, 1)
        print("empty queue ok")

    if "auto_review_worker(" not in src:
        old = "        extra_after = human_notes(job_dir)\n        ctx = (\n"
        new = (
            '        if mode == "botfather":\n'
            "            action = auto_review_worker(result, task, state)\n"
            "            save_state(job_dir, state)\n"
            '            if action == "accept":\n'
            "                dialogue(job_dir, \"system\", f\"Accepted {task['id']} (auto). BotFather may enqueue next.\")\n"
            "            else:\n"
            "                dialogue(job_dir, \"system\", f\"Task {task['id']} failed — waiting for BotFather (no local re-plan).\")\n"
            "                time.sleep(1.0)\n"
            "            continue\n\n"
            "        extra_after = human_notes(job_dir)\n"
            "        ctx = (\n"
        )
        if old not in src:
            raise SystemExit("post-worker missing")
        src = src.replace(old, new, 1)
        print("post-worker ok")

    OV.write_text(src, encoding="utf-8", newline="\n")
    py_compile.compile(str(OV), doraise=True)
    print("OVERSEER COMPILE OK")

    hbak = HAR.with_suffix(f".py.bak-orch-{int(time.time())}")
    shutil.copy2(HAR, hbak)
    h = HAR.read_text(encoding="utf-8")
    if "def enqueue_task_api" not in h:
        fn = """
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
        f.write(json.dumps({"ts": stamp, "text": json.dumps(card, ensure_ascii=False)}, ensure_ascii=False) + "\\n")
    with (JOB / "dialogue.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, "role": "system", "text": f"API queued task {tid}: {title}"}, ensure_ascii=False) + "\\n")
    return {"ok": True, "task": card, "orch": "botfather", "alive": overseer_alive()}

"""
        fn = fn.replace("\\\\n", "\\n")
        if "def instruct(" not in h:
            raise SystemExit("instruct missing")
        h = h.replace("def instruct(", fn + "def instruct(", 1)
        old = '        if path == "/api/inbox":\n            self._send(200, instruct(str(data.get("text") or data.get("message") or "")))'
        new = (
            '        if path == "/api/task":\n'
            '            self._send(200, enqueue_task_api(data if isinstance(data, dict) else {}))\n'
            '        elif path == "/api/inbox":\n'
            '            text = str(data.get("text") or data.get("message") or "")\n'
            '            stripped = text.strip()\n'
            "            if stripped.startswith('{') and ('\\\"id\\\"' in stripped or '\\\"task_id\\\"' in stripped):\n"
            '                try:\n'
            '                    self._send(200, enqueue_task_api(json.loads(stripped)))\n'
            '                except Exception:\n'
            '                    self._send(200, instruct(text))\n'
            '            else:\n'
            '                self._send(200, instruct(text))'
        )
        # fix the id check escaping for the written file
        new = '''        if path == "/api/task":
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
                self._send(200, instruct(text))'''
        if old not in h:
            raise SystemExit("inbox route missing")
        h = h.replace(old, new, 1)
        if '"orch"' not in h:
            h = h.replace(
                '"status": st.get("status"),',
                '"status": st.get("status"),\n        "orch": st.get("orch") or "botfather",',
                1,
            )
        HAR.write_text(h, encoding="utf-8", newline="\n")
        py_compile.compile(str(HAR), doraise=True)
        print("HARNESS COMPILE OK")
    else:
        print("harness already patched")

    if STATE.is_file():
        st = json.loads(STATE.read_text(encoding="utf-8"))
        st["orch"] = "botfather"
        for t in st.get("plan") or []:
            if (t.get("status") or "") == "running":
                t["status"] = "failed"
                t["summary"] = "reset on orch=botfather switch"
        note = "[orch] BotFather mode: local LLM is worker-only; no giant goal re-prompt."
        if note not in (st.get("notes") or ""):
            st["notes"] = ((st.get("notes") or "") + "\n" + note).strip()[-4000:]
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
        print("state orch=botfather")

    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(
        "# BotFather orchestration contract\n\n"
        "## Roles\n\n"
        "- **BotFather (Grok)**: owns the plan. Emits ONE micro-task at a time. Reviews results.\n"
        "- **Local LLM (Qwen)**: worker only. One task card + tools. Never re-plans the project.\n"
        "- **Harness**: `POST /api/task` enqueues a card. Free-text inbox is parked for BotFather, not re-fed as planner soup.\n\n"
        "## Task card\n\n"
        "```json\n"
        '{"id":"m7a","title":"All-race 10% crit in Hit","detail":"ONLY: In Descent/src/Sim/Run.cs Hit(), add one Chance(10) crit for all races via str_replace. Then dotnet build -c Release. Max 1 read_file. Forbidden: list_dir, whole-file rewrite."}\n'
        "```\n\n"
        "Or: `TASK m7a | All-race 10% crit | ONLY: ...`\n\n"
        "## Rules\n\n"
        "1. One pending coding task at a time.\n"
        "2. Detail names exact file + symbol + verify command.\n"
        "3. Prefer `str_replace` over full-file writes.\n"
        "4. On failure, BotFather posts a smaller card — local model does not self-replan.\n"
        "5. Design doc stays out of the worker prompt.\n\n"
        "## Flag\n\n"
        "`python agent/overseer.py --root jobs/workspace --orch botfather`\n"
        '`state.json` -> `"orch": "botfather"` (default).\n',
        encoding="utf-8",
    )
    print("contract", CONTRACT)

if __name__ == "__main__":
    main()
