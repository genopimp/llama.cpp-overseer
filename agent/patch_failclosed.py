from __future__ import annotations
import py_compile, re, shutil
from datetime import datetime
from pathlib import Path

SRC = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py")
bak = SRC.with_suffix(f".py.bak-tools-{datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(SRC, bak)
text = SRC.read_text(encoding="utf-8")
orig = text
print("backup", bak.name)

HELPERS = r'''

def scrape_tool_calls(content: str) -> list[dict]:
    """Recover tool calls when the model pastes JSON instead of using tool_calls."""
    if not content or not str(content).strip():
        return []
    raw = str(content).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    blob = fence.group(1).strip() if fence else raw
    data = None
    try:
        data = json.loads(blob)
    except Exception:
        for i, ch in enumerate(blob):
            if ch in "{[":
                try:
                    data = json.loads(blob[i:])
                except Exception:
                    data = extract_json(blob[i:])
                if data is not None:
                    break
    if data is None:
        return []

    def one(obj: dict, idx: int) -> dict | None:
        if not isinstance(obj, dict):
            return None
        name = obj.get("name") or obj.get("tool") or ((obj.get("function") or {}).get("name"))
        if not name:
            return None
        args = (
            obj.get("arguments")
            or obj.get("parameters")
            or obj.get("args")
            or ((obj.get("function") or {}).get("arguments"))
            or {}
        )
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                args = {"raw": args}
        return {
            "id": obj.get("id") or f"scraped_{idx}_{name}",
            "type": "function",
            "function": {"name": str(name), "arguments": json.dumps(args, ensure_ascii=False)},
        }

    out: list[dict] = []
    if isinstance(data, list):
        for i, item in enumerate(data):
            c = one(item, i)
            if c:
                out.append(c)
    elif isinstance(data, dict):
        for key in ("tool_calls", "tools", "items"):
            if isinstance(data.get(key), list):
                for i, item in enumerate(data[key]):
                    c = one(item, i)
                    if c:
                        out.append(c)
                return out
        c = one(data, 0)
        if c:
            out.append(c)
    return out


def task_target_paths(task: dict) -> list[str]:
    blob = f"{task.get('title') or ''}\n{task.get('detail') or ''}"
    found: list[str] = []
    for m in re.finditer(
        r"(?<![\w./])((?:Descent|src)[/\\][\w./\\-]+\.(?:cs|json|md|txt|py))",
        blob,
        re.I,
    ):
        p = m.group(1).replace("\\", "/")
        if p not in found:
            found.append(p)
    for m in re.finditer(
        r"(?:write_file|path|file)\s*[:=]\s*[`\"]?([\w./\\-]+\.(?:cs|json|md|txt|py))",
        blob,
        re.I,
    ):
        p = m.group(1).replace("\\", "/")
        if p not in found:
            found.append(p)
    return found[:4]


def worker_tools_for_task(task: dict) -> list[dict]:
    detail = f"{task.get('title') or ''}\n{task.get('detail') or ''}".lower()
    explore = any(
        k in detail for k in ("list_dir", "search_files", "explore", "find where", "locate file")
    )
    if explore or not task_target_paths(task):
        return WORKER_TOOLS
    allow = {"write_file", "run_command", "report_done"}
    out = [t for t in WORKER_TOOLS if ((t.get("function") or {}).get("name") or "") in allow]
    return out or WORKER_TOOLS

'''

if "def scrape_tool_calls(" not in text:
    at = text.find("\nclass Client:")
    if at < 0:
        raise SystemExit("class Client not found")
    text = text[:at] + HELPERS + text[at:]
    print("inserted helpers")
else:
    print("helpers present")

if "tool_choice: str | dict | None = None" not in text:
    text, n = re.subn(
        r"(        task: str = \"\",\n)(    \) -> dict\[str, Any\]:)",
        r"\1        tool_choice: str | dict | None = None,\n\2",
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"chat signature patch failed n={n}")
    text, n2 = re.subn(
        r'body\["tool_choice"\] = "auto"',
        'body["tool_choice"] = tool_choice if tool_choice is not None else "required"',
        text,
        count=1,
    )
    if n2 != 1:
        raise SystemExit(f"tool_choice body patch failed n={n2}")
    print("patched chat tool_choice")
else:
    print("chat tool_choice already patched")

m = re.search(r"\ndef run_worker\(", text)
if not m:
    raise SystemExit("run_worker missing")
start = m.start() + 1
m2 = re.search(r"\n\ndef [a-zA-Z_]", text[start + 20 :])
if not m2:
    raise SystemExit("run_worker end missing")
end = start + 20 + m2.start() + 1

NEW = r'''def run_worker(client: Client, tools: Tools, state: dict[str, Any], task: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    tools.done = None
    targets = task_target_paths(task)
    worker_tools = worker_tools_for_task(task)
    explore = len(worker_tools) > 3

    injected: list[str] = []
    if targets and not explore:
        for rel in targets:
            try:
                p = tools.ws.resolve(rel)
                if p.is_file():
                    body = p.read_text(encoding="utf-8", errors="replace")
                    injected.append(
                        f"--- {rel} (current, {len(body.splitlines())} lines) ---\n{clip(body, 12000)}"
                    )
            except Exception as e:
                injected.append(f"--- {rel} (unreadable: {e}) ---")

    policy = (
        "FAIL-CLOSED TOOL POLICY:\n"
        "- You MUST use real tools (tool_calls). Never paste tool JSON as chat.\n"
        "- Chat-only replies are rejected as blocked.\n"
        "- For edit tasks: write_file the target, then run_command for Release build if required, then report_done.\n"
        "- Do NOT call list_dir / search_files / read_file unless the task explicitly requires exploration.\n"
    )
    if targets and not explore:
        policy += (
            f"- Target path(s): {', '.join(targets)}\n"
            "- File contents are already below — write_file the full updated file next.\n"
        )

    user_parts = [
        f"Rolling notes (context only):\n{(state.get('notes') or '(none)')[:700]}",
        f"MICRO-TASK id={task['id']} — {task['title']}\n{task.get('detail','')}",
        policy,
        "Call report_done when finished. Prefer write_file before report_done ok.",
    ]
    if injected:
        user_parts.append("INJECTED FILE(S) — edit and write_file:\n" + "\n\n".join(injected))

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": worker_system(state)},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    non_write_streak = 0
    wrote_files: list[str] = []
    empty_tool_retries = 0

    for step in range(MAX_WORKER_STEPS):
        if stop_requested(job_dir):
            return {"status": "stopped", "summary": "stop file seen", "files": wrote_files}
        log(job_dir, f"worker {task['id']} step {step + 1}")
        msg = client.chat(
            messages,
            tools=worker_tools,
            thinking=False,
            budget=0,
            max_tokens=4096,
            temperature=0.2,
            job_dir=job_dir,
            agent="worker",
            task=str(task.get("id") or ""),
            tool_choice="required",
        )
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""

        if not tool_calls:
            scraped = scrape_tool_calls(content)
            if scraped:
                log(job_dir, f"  scraped {len(scraped)} tool call(s) from chat content")
                tool_calls = scraped
            else:
                empty_tool_retries += 1
                log(job_dir, f"  FAIL-CLOSED: no tool_calls (retry {empty_tool_retries})")
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "REJECTED: you returned chat with no tool_calls. "
                            "Use the tools API — call write_file (and/or run_command / report_done). "
                            "Do not paste JSON. Do not claim done without write_file."
                        ),
                    }
                )
                if empty_tool_retries >= 2:
                    return {
                        "status": "blocked",
                        "summary": "model returned chat without tool_calls twice (fail-closed)",
                        "files": wrote_files,
                    }
                continue

        assistant: dict[str, Any] = {"role": "assistant", "content": content or ""}
        assistant["tool_calls"] = tool_calls
        messages.append(assistant)

        rejected_done = False
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            alias = {
                "write": "write_file",
                "writefile": "write_file",
                "read": "read_file",
                "readfile": "read_file",
                "search": "search_files",
                "bash": "run_command",
                "shell": "run_command",
                "done": "report_done",
            }
            name = alias.get(name.replace("-", "_").lower(), name)
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args if isinstance(raw_args, dict) else {}

            if not explore and name in ("list_dir", "search_files", "read_file"):
                result = (
                    f"[BLOCKED TOOL] {name} is disabled for this edit micro-task. "
                    f"Targets already injected: {targets or '(none)'}. Call write_file next."
                )
                log(job_dir, f"  blocked tool {name}")
            else:
                log(job_dir, f"  tool {name} {json.dumps(args, ensure_ascii=False)[:300]}")
                result = tools.call(name, args if isinstance(args, dict) else {})

            if name == "write_file":
                non_write_streak = 0
                path = str((args or {}).get("path") or "")
                if path and path not in wrote_files:
                    wrote_files.append(path)
            elif name != "report_done":
                non_write_streak += 1

            if non_write_streak >= 3 and name not in ("write_file", "report_done"):
                result = (
                    (result or "")
                    + "\n\n[OVERSEER POLICY] 3+ non-write tools used. "
                    + "NEXT tool MUST be write_file to the task target, or report_done blocked."
                )

            llm_trace(
                job_dir,
                {
                    "agent": "tool",
                    "task": str(task.get("id") or ""),
                    "tool": name,
                    "prompt": json.dumps(args, ensure_ascii=False)[:1200],
                    "response": (result or "")[:2000],
                },
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "name": name,
                    "content": result,
                }
            )
            if tools.done is not None:
                done = dict(tools.done)
                if done.get("status") == "ok" and targets and not wrote_files and not explore:
                    tools.done = None
                    rejected_done = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "REJECTED report_done ok: you did not write_file any target "
                                f"({', '.join(targets)}). write_file first, then report_done."
                            ),
                        }
                    )
                    break
                return done
        if rejected_done:
            continue

    return {"status": "need_split", "summary": "hit tool-step limit without report_done", "files": wrote_files}


'''

text = text[:start] + NEW + text[end:]
print("replaced run_worker")

old_foot = "Keep summaries short: what changed + how verified.\n\"\"\""
if "Chat without tool_calls is treated as blocked." not in text:
    if old_foot in text:
        text = text.replace(
            old_foot,
            "Keep summaries short: what changed + how verified.\n"
            "Never paste tool JSON into chat — the runtime only honors real tool_calls.\n"
            "Chat without tool_calls is treated as blocked.\n\"\"\"",
            1,
        )
        print("updated worker_system")
    else:
        print("WARN worker_system footer not found; trying alternate")
        alt = "Keep summaries short: what changed + how verified."
        if alt in text and "Chat without tool_calls" not in text:
            text = text.replace(
                alt,
                alt
                + "\nNever paste tool JSON into chat — the runtime only honors real tool_calls."
                + "\nChat without tool_calls is treated as blocked.",
                1,
            )
            print("updated worker_system alt")

if text == orig:
    raise SystemExit("NO CHANGES")
SRC.write_text(text, encoding="utf-8")
print("wrote bytes", SRC.stat().st_size)
py_compile.compile(str(SRC), doraise=True)
print("syntax ok")
t2 = SRC.read_text(encoding="utf-8")
print("has scrape", "def scrape_tool_calls(" in t2)
print("has required default", 'else "required"' in t2)
print("has FAIL-CLOSED", "FAIL-CLOSED" in t2)
print("thinking False in run_worker", "thinking=False" in t2[t2.find("def run_worker"):t2.find("def run_worker")+2500])
