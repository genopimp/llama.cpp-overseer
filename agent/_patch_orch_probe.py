# -*- coding: utf-8 -*-
from pathlib import Path
import re, shutil, time, py_compile

ov = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py")
har = Path(r"C:\Users\edwar\prism-ml\agent\harness.py")
bak = ov.with_suffix(f".py.bak-orch-{int(time.time())}")
shutil.copy2(ov, bak)
print("backup", bak)

src = ov.read_text(encoding="utf-8")

# 1) Add --orch argparse
old_args = '''    p.add_argument("--max-minutes", type=int, default=180)
    p.add_argument("--max-cycles", type=int, default=40)
    args = p.parse_args()'''
new_args = '''    p.add_argument("--max-minutes", type=int, default=180)
    p.add_argument("--max-cycles", type=int, default=40)
    p.add_argument(
        "--orch",
        default="",
        choices=["", "local", "botfather"],
        help="local=LLM plans+codes; botfather=BotFather plans, local LLM only codes (default from state.orch or botfather)",
    )
    args = p.parse_args()'''
if old_args not in src:
    raise SystemExit("argparse block not found")
src = src.replace(old_args, new_args, 1)

# 2) Insert helpers before ask_overseer
helpers = r'''
def orch_mode(state: dict[str, Any], args: argparse.Namespace | None = None) -> str:
    """Who owns planning. botfather = BotFather/Grok plans; local LLM only runs worker tools."""
    if args is not None:
        cli = (getattr(args, "orch", None) or "").strip().lower()
        if cli in ("local", "botfather"):
            state["orch"] = cli
            return cli
    mode = (state.get("orch") or "").strip().lower()
    if mode in ("local", "botfather"):
        return mode
    # Default to botfather so we stop re-feeding giant goal soup to a local planner.
    state["orch"] = "botfather"
    return "botfather"


def parse_task_card(text: str) -> dict[str, Any] | None:
    """Accept structured BotFather task cards; ignore free-text steers (those are for BotFather, not the worker)."""
    raw = (text or "").strip()
    if not raw:
        return None
    # JSON object
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            if (obj.get("action") or "").lower() in ("enqueue", "task", "add_task"):
                obj = obj.get("task") or obj
            tid = str(obj.get("id") or obj.get("task_id") or "").strip()
            title = str(obj.get("title") or obj.get("name") or "").strip()
            detail = str(obj.get("detail") or obj.get("instruction") or obj.get("text") or "").strip()
            if tid and (title or detail):
                return {
                    "id": tid,
                    "title": title or tid,
                    "detail": detail or title,
                    "status": "pending",
                }
    # TASK id | title | detail
    m = re.match(r"(?is)^TASK\s+([^\s|]+)\s*\|\s*([^|]+)\s*\|\s*(.+)$", raw)
    if m:
        return {
            "id": m.group(1).strip(),
            "title": m.group(2).strip(),
            "detail": m.group(3).strip(),
            "status": "pending",
        }
    return None


def enqueue_task(state: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    plan = state.setdefault("plan", [])
    existing = {str(t.get("id")): t for t in plan}
    tid = str(card["id"])
    if tid in existing and (existing[tid].get("status") or "") in ("pending", "running", "retry"):
        existing[tid]["title"] = card.get("title") or existing[tid].get("title")
        existing[tid]["detail"] = card.get("detail") or existing[tid].get("detail")
        existing[tid]["status"] = "pending"
        return existing[tid]
    # if same id was done/failed, create a fresh attempt suffix
    if tid in existing:
        n = 2
        while f"{tid}.{n}" in existing:
            n += 1
        tid = f"{tid}.{n}"
        card = dict(card)
        card["id"] = tid
    plan.append(card)
    return card


def ingest_botfather_inbox(job_dir: Path, state: dict[str, Any]) -> str:
    """Drain inbox: task cards -> plan; free text logged for BotFather only (not fed to local planner)."""
    msgs = take_inbox(job_dir)
    if not msgs:
        return ""
    free: list[str] = []
    for m in msgs:
        dialogue(job_dir, "user", m)
        card = parse_task_card(m)
        if card:
            enqueued = enqueue_task(state, card)
            dialogue(
                job_dir,
                "system",
                f"BotFather queued task {enqueued['id']}: {enqueued.get('title')}",
            )
            log(job_dir, f"botfather enqueue {enqueued['id']} {enqueued.get('title')}")
        else:
            free.append(m)
            # Park free-text for BotFather visibility; do NOT append to addenda soup for the local model.
            parked = state.setdefault("botfather_inbox", [])
            parked.append({"at": utc_now(), "text": m[:4000]})
            state["botfather_inbox"] = parked[-30:]
    save_state(job_dir, state)
    if free:
        return "Human notes (for BotFather; not worker context):\n" + "\n".join(f"- {m}" for m in free)
    return ""


def auto_review_worker(result: dict[str, Any], task: dict[str, Any], state: dict[str, Any]) -> str:
    """BotFather-mode post-worker policy: no local LLM re-plan. Return action name."""
    status = (result.get("status") or "").lower()
    files = result.get("files") or []
    if status in ("ok", "done", "success") or (status not in ("blocked", "need_split", "failed") and files):
        task["status"] = "done"
        task["summary"] = result.get("summary")
        task["files"] = files
        note = f"DONE {task.get('id')}: {result.get('summary') or ''} files={files}"
        state["notes"] = ((state.get("notes") or "") + "\n" + note).strip()[-3500:]
        return "accept"
    if status == "need_split":
        task["status"] = "failed"
        task["summary"] = result.get("summary")
        state["notes"] = ((state.get("notes") or "") + f"\nNEED_SPLIT {task.get('id')}: {result.get('summary')}").strip()[-3500:]
        return "wait_botfather"
    task["status"] = "failed"
    task["summary"] = result.get("summary")
    state["notes"] = ((state.get("notes") or "") + f"\nFAILED {task.get('id')}: {result.get('summary')}").strip()[-3500:]
    return "wait_botfather"


'''

needle = "def ask_overseer(client: Client, state: dict[str, Any], user: str, job_dir: Path) -> dict[str, Any]:"
if needle not in src:
    raise SystemExit("ask_overseer def not found")
if "def orch_mode(" not in src:
    src = src.replace(needle, helpers + "\n" + needle, 1)
    print("inserted helpers")
else:
    print("helpers already present")

# 3) Slim worker_system — do not inject north-star goal / addenda
old_worker_sys_start = 'def worker_system(state: dict[str, Any]) -> str:'
# Find and replace the goal-heavy parts if any — worker_system currently doesn't include goal in HARD POLICY version
# But ensure notes mention botfather:
if "BotFather owns planning" not in src:
    src = src.replace(
        "You execute ONE micro-task. You do not explore the repo for fun.",
        "You execute ONE micro-task. You do not explore the repo for fun.\n"
        "BotFather owns planning — ignore any broad game vision; only do the micro-task below.",
        1,
    )
    print("worker_system note")

# 4) Patch run() — after state load, set orch; replace main loop branches
# Inject orch mode right after state = load_state...
old_boot = '''    state = load_state(job_dir, args.goal, str(root))
    prev_status = state.get("status")
    state["status"] = "running"
    save_state(job_dir, state)'''
# Check exact load_state call
m = re.search(r"state = load_state\([^\n]+\)\n\s*prev_status = state\.get\(\"status\"\)\n\s*state\[\"status\"\] = \"running\"\n\s*save_state\(job_dir, state\)", src)
if not m:
    # try alternate
    m = re.search(r"state = load_state\([^\n]+\)\n.*?\n\s*save_state\(job_dir, state\)", src)
    print("boot match alt", bool(m))
    if m:
        print(repr(m.group(0)[:200]))
else:
    print("boot match primary")

# Find exact bootstrapping
idx = src.find("state = load_state(")
print("load_state at", idx)
print(repr(src[idx:idx+350]))
