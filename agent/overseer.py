#!/usr/bin/env python3
"""Autonomous overseer + worker loop for local Bonsai (llama-server).

The chat UI has no filesystem or compiler tools, so the model will honestly
say it cannot edit files. This process is the missing tool runtime:

  overseer  -- thinking off, owns the plan and notes
  worker    -- thinking budgeted, may read/write files and run commands
  state     -- jobs/<name>/state.json  (continuity; think traces are dropped)

Example:
  .venv\\Scripts\\python.exe agent\\overseer.py --goal "Add a README to the workspace" --root C:\\work\\myproj
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

API_DEFAULT = "http://127.0.0.1:8080/v1"
MAX_TOOL_CHARS = 12000
MAX_WORKER_STEPS = 12
MAX_OVERSEER_STEPS = 6
THINK_BUDGET = 2048


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(job_dir: Path, msg: str) -> None:
    line = f"{utc_now()}  {msg}"
    print(line, flush=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    with (job_dir / "overseer.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    extra = Path(__file__).resolve().parents[1] / "logs" / "overseer.log"
    try:
        extra.parent.mkdir(parents=True, exist_ok=True)
        with extra.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def extract_json(text: str) -> Any | None:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def clip(s: str, n: int = MAX_TOOL_CHARS) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[: n - 80] + f"\n…[truncated {len(s) - n + 80} chars]"


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        rel = (rel or ".").replace("\\", "/").lstrip("/")
        path = (self.root / rel).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as e:
            raise PermissionError(f"path escapes workspace: {rel}") from e
        return path


def msvc_bin() -> Path | None:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if not vswhere.is_file():
        return None
    try:
        out = subprocess.check_output(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-find",
                r"VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe",
            ],
            text=True,
            timeout=15,
        ).strip().splitlines()
        if out:
            return Path(out[-1]).parent
    except (subprocess.SubprocessError, OSError):
        return None
    return None


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    extras: list[str] = []
    bin_dir = msvc_bin()
    if bin_dir:
        extras.append(str(bin_dir))
        extras.append(str(bin_dir.parent.parent / "x64"))  # lib often nearby; PATH still helps cl.exe
    venv_scripts = Path(__file__).resolve().parents[1] / ".venv" / "Scripts"
    if venv_scripts.is_dir():
        extras.append(str(venv_scripts))
    if extras:
        env["PATH"] = os.pathsep.join(extras) + os.pathsep + env.get("PATH", "")
    return env


WORKER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and folders under a workspace-relative path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative path, default ."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file. Use offset/limit for large files (1-based lines).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create a NEW text file, or overwrite only when creating from scratch. "
                "Prefer str_replace for edits. Content must be pure file text — NEVER paste "
                "read_file line-number prefixes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": (
                "PREFERRED edit tool. Replace exactly one occurrence of old_str with new_str "
                "in an existing file. old_str must match the file exactly (no line-number prefixes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Regex search across workspace text files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "glob": {"type": "string", "description": "Optional substring filter on file path"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the workspace (compiler, tests, git, python, dotnet, cl, etc.). cwd is the workspace root. Returns stdout/stderr/exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_sec": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_done",
            "description": "Finish this task. Call when the work is complete or you cannot proceed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["ok", "blocked", "need_split"],
                    },
                    "summary": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "summary"],
            },
        },
    },
]


class Tools:
    def __init__(self, ws: Workspace, env: dict[str, str], job_dir: Path | None = None):
        self.ws = ws
        self.env = env
        self.job_dir = job_dir
        self.done: dict[str, Any] | None = None

    def call(self, name: str, args: dict[str, Any]) -> str:
        args = args or {}
        try:
            if name == "list_dir":
                result = self.list_dir(str(args.get("path") or "."))
            elif name == "read_file":
                result = self.read_file(
                    str(args.get("path") or ""),
                    int(args.get("offset") or 1),
                    int(args.get("limit") or 200),
                )
            elif name == "write_file":
                result = self.write_file(str(args.get("path") or ""), str(args.get("content") or ""))
            elif name == "str_replace":
                result = self.str_replace(
                    str(args.get("path") or ""),
                    str(args.get("old_str") or ""),
                    str(args.get("new_str") if args.get("new_str") is not None else ""),
                )
            elif name == "search_files":
                result = self.search_files(str(args.get("pattern") or ""), str(args.get("glob") or ""))
            elif name == "run_command":
                result = self.run_command(str(args.get("command") or ""), int(args.get("timeout_sec") or 180))
            elif name == "report_done":
                files = args.get("files") or []
                if isinstance(files, str):
                    files = [files]
                result = self.report_done(str(args.get("status") or "ok"), str(args.get("summary") or ""), list(files))
            else:
                result = {"error": f"unknown tool {name}"}
        except Exception as e:
            result = {"error": str(e)}
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, indent=2)
        return clip(result)

    def list_dir(self, path: str = ".") -> dict[str, Any]:
        p = self.ws.resolve(path)
        if not p.exists():
            return {"error": f"missing {path}"}
        if p.is_file():
            return {"file": str(p.relative_to(self.ws.root)), "bytes": p.stat().st_size}
        names = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            tag = "dir" if child.is_dir() else "file"
            names.append(f"{tag}\t{child.relative_to(self.ws.root)}")
            if len(names) >= 200:
                names.append("…[truncated]")
                break
        return {"path": path, "entries": names}

    def read_file(self, path: str, offset: int = 1, limit: int = 200) -> dict[str, Any]:
        p = self.ws.resolve(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, int(offset or 1))
        limit = max(1, min(int(limit or 200), 400))
        chunk = lines[offset - 1 : offset - 1 + limit]
        # Do NOT prefix lines with "N|" — models copy that into write_file and corrupt sources.
        return {
            "path": str(p.relative_to(self.ws.root)).replace("\\", "/"),
            "total_lines": len(lines),
            "shown": f"{offset}-{offset + len(chunk) - 1}",
            "content": "\n".join(chunk),
        }

    _LINE_NUM_RE = re.compile(r"^(?:\s*)\d+\|", re.M)

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Fail-closed create/overwrite. Prefer str_replace for edits."""
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            return {"error": "refused empty write_file — use str_replace for edits, or pass non-empty content"}
        lines = content.splitlines()
        numbered = sum(1 for ln in lines if self._LINE_NUM_RE.match(ln))
        if lines and numbered >= max(3, int(0.5 * len(lines))):
            return {
                "error": (
                    "refused write_file: content looks like read_file output with line-number prefixes. "
                    "Rewrite without N| prefixes, or use str_replace."
                ),
                "numbered_lines": numbered,
                "total_lines": len(lines),
            }
        p = self.ws.resolve(path)
        if p.exists():
            try:
                old = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                old = ""
            if len(old) > 400 and len(content) < max(80, int(0.25 * len(old))):
                return {
                    "error": (
                        "refused tiny overwrite of large existing file — use str_replace for surgical edits"
                    ),
                    "existing_bytes": len(old.encode("utf-8")),
                    "new_bytes": len(content.encode("utf-8")),
                }
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        rel = str(p.relative_to(self.ws.root)).replace("\\", "/")
        if self.job_dir is not None and not any(part in ("bin", "obj", ".overseer") for part in p.parts):
            codeview(self.job_dir, "file", path=rel, bytes=p.stat().st_size)
        return {"wrote": rel, "bytes": p.stat().st_size}

    def str_replace(self, path: str, old_str: str, new_str: str) -> dict[str, Any]:
        """Surgical edit — preferred over write_file for existing sources.
        Exact match first; if missing, try unique match ignoring leading tabs/spaces per line.
        """
        if not old_str:
            return {"error": "old_str is required and must be non-empty"}
        if old_str == new_str:
            return {"error": "old_str and new_str are identical — nothing to change"}
        p = self.ws.resolve(path)
        if not p.exists():
            return {"error": f"file not found: {path}"}
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(self.ws.root)).replace("\\", "/")

        def _apply(matched: str, replacement: str) -> dict:
            if text.count(matched) != 1:
                return {"error": "internal: matched span not unique", "path": rel}
            new_text = text.replace(matched, replacement, 1)
            p.write_text(new_text, encoding="utf-8", newline="\n")
            if self.job_dir is not None and not any(part in ("bin", "obj", ".overseer") for part in p.parts):
                try:
                    codeview(self.job_dir, "file", path=rel, bytes=p.stat().st_size, via="str_replace")
                except TypeError:
                    codeview(self.job_dir, "file", path=rel, bytes=p.stat().st_size)
            return {
                "replaced": rel,
                "bytes": p.stat().st_size,
                "old_len": len(matched),
                "new_len": len(replacement),
            }

        count = text.count(old_str)
        if count == 1:
            return _apply(old_str, new_str)
        if count > 1:
            return {
                "error": f"old_str matched {count} times — include more surrounding context so it matches exactly once",
                "matches": count,
            }

        # Fallback: unique substring after strip (single-line fragments)
        stripped = old_str.strip("\r\n")
        if stripped and stripped != old_str and text.count(stripped) == 1:
            # indent-preserving: keep file's leading ws on that line if old_str was mid-line
            return _apply(stripped, new_str.strip("\r\n"))

        # Fallback: line-wise ignore leading whitespace
        def norm(s: str) -> str:
            return "\n".join(ln.lstrip(" \t") for ln in s.replace("\r\n", "\n").split("\n"))

        needle = norm(old_str)
        if not needle.strip():
            return {"error": "old_str not found in file — read the file again and copy exact text", "path": rel}

        t_norm_lines = text.replace("\r\n", "\n").split("\n")
        n_lines = needle.split("\n")
        n = len(n_lines)
        hits: list[str] = []
        for i in range(0, max(0, len(t_norm_lines) - n + 1)):
            window = "\n".join(t_norm_lines[i : i + n])
            if norm(window) == needle:
                hits.append(window)
        if len(hits) == 0:
            return {"error": "old_str not found in file — read the file again and copy exact text", "path": rel}
        if len(hits) > 1:
            return {
                "error": f"old_str matched {len(hits)} times (flex) — include more surrounding context",
                "matches": len(hits),
            }
        matched = hits[0]
        # Re-indent new_str to match first-line indent of matched window
        first = matched.split("\n", 1)[0]
        indent = first[: len(first) - len(first.lstrip(" \t"))]
        new_lines = new_str.replace("\r\n", "\n").split("\n")
        rebuilt = []
        for j, ln in enumerate(new_lines):
            core = ln.lstrip(" \t")
            if core == "" and j == len(new_lines) - 1:
                rebuilt.append("")
            else:
                rebuilt.append(indent + core if j == 0 else indent + core)
        # For body lines inside a block, model often includes relative tabs; keep simple: same indent all lines
        # Better: preserve relative indent from new_str after stripping common leading ws
        stripped_new = [ln.lstrip(" \t") for ln in new_lines]
        # detect common indent depth in matched for multi-line: use first-line indent only for line0;
        # subsequent matched lines' indent deltas
        m_lines = matched.split("\n")
        if len(m_lines) == len(stripped_new) and len(m_lines) > 1:
            out = []
            for ml, sn in zip(m_lines, stripped_new):
                ind = ml[: len(ml) - len(ml.lstrip(" \t"))]
                out.append(ind + sn if sn or True else ind)
            # fix empty: if sn=="" keep blank line without forcing indent-only clutter
            out2 = []
            for ml, sn in zip(m_lines, stripped_new):
                if sn == "":
                    out2.append("")
                else:
                    ind = ml[: len(ml) - len(ml.lstrip(" \t"))]
                    out2.append(ind + sn)
            replacement = "\n".join(out2)
        else:
            # single-line or length mismatch: replace matched span with new_str stripped, keeping first indent if single line
            if "\n" not in matched and "\n" not in new_str.strip("\r\n"):
                replacement = indent + new_str.strip() if matched[:1] in " \t" and not new_str[:1] in " \t" else new_str.strip("\r\n")
                # if matched is mid-line fragment with no leading ws, use new_str as-is stripped
                if matched == matched.lstrip(" \t"):
                    replacement = new_str.strip("\r\n")
            else:
                replacement = "\n".join(
                    (indent + sn if sn else "") for sn in stripped_new
                )
        return _apply(matched, replacement)

    def search_files(self, pattern: str, glob: str = "") -> dict[str, Any]:
        rx = re.compile(pattern)
        hits = []
        for p in self.ws.root.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.ws.root))
            if glob and glob not in rel.replace("\\", "/"):
                continue
            if p.stat().st_size > 1_000_000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}:{line[:200]}")
                    if len(hits) >= 40:
                        return {"matches": hits, "truncated": True}
        return {"matches": hits}

    def run_command(self, command: str, timeout_sec: int = 180) -> dict[str, Any]:
        timeout_sec = max(5, min(int(timeout_sec or 180), 300))
        before = self._source_mtimes()
        proc = subprocess.run(
            command,
            cwd=str(self.ws.root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=self.env,
        )
        self._emit_new_sources(before)
        return {
            "exit": proc.returncode,
            "stdout": clip(proc.stdout, 8000),
            "stderr": clip(proc.stderr, 4000),
        }

    _SOURCE_EXT = {".cs", ".csproj", ".sln", ".json", ".md", ".py", ".xml", ".xaml", ".resx", ".txt", ".hlsl"}

    def _source_mtimes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.ws.root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in ("bin", "obj", ".overseer", ".git", ".vs") for part in p.parts):
                continue
            if p.suffix.lower() not in self._SOURCE_EXT:
                continue
            rel = str(p.relative_to(self.ws.root)).replace("\\", "/")
            try:
                out[rel] = p.stat().st_mtime
            except OSError:
                pass
        return out

    def _emit_new_sources(self, before: dict[str, float]) -> None:
        if self.job_dir is None:
            return
        after = self._source_mtimes()
        for rel, mt in after.items():
            if mt > before.get(rel, 0) + 0.05:
                codeview(self.job_dir, "file", path=rel)

    def report_done(self, status: str, summary: str, files: list[str] | None = None) -> dict[str, Any]:
        self.done = {
            "status": status or "ok",
            "summary": summary or "",
            "files": files or [],
        }
        return {"accepted": True, "handoff": self.done}


def llm_trace(job_dir: Path, rec: dict[str, Any]) -> None:
    rec = dict(rec)
    rec.setdefault("ts", utc_now())
    for k in ("prompt", "response", "thinking", "result"):
        v = rec.get(k)
        if isinstance(v, str) and len(v) > 3500:
            rec[k] = v[:3500] + "…"
    JOBTRACE = job_dir / "llm_trace.jsonl"
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            with JOBTRACE.open("a", encoding="utf-8") as f:
                f.write(line)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
            if attempt == 3:
                try:
                    bak = job_dir / f"llm_trace.jsonl.lockbak-{int(time.time())}"
                    if JOBTRACE.exists():
                        JOBTRACE.replace(bak)
                except OSError:
                    pass
    # Never crash the worker over tracing.
    try:
        log(job_dir, f"llm_trace write skipped: {last_err}")
    except Exception:
        pass



def scrape_tool_calls(content: str) -> list[dict]:
    """Recover tool calls when the model pastes JSON / XML instead of using tool_calls."""
    if not content or not str(content).strip():
        return []
    raw = str(content).strip()

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

    def from_data(data, start_idx: int = 0) -> list[dict]:
        out: list[dict] = []
        if isinstance(data, list):
            for i, item in enumerate(data):
                c = one(item, start_idx + i)
                if c:
                    out.append(c)
        elif isinstance(data, dict):
            for key in ("tool_calls", "tools", "items", "function-calls", "function_calls"):
                if isinstance(data.get(key), list):
                    for i, item in enumerate(data[key]):
                        c = one(item, start_idx + i)
                        if c:
                            out.append(c)
                    return out
            c = one(data, start_idx)
            if c:
                out.append(c)
        return out

    # Qwen / llama often emit <function-calls>{...}</function-calls> (sometimes repeated).
    xml_blobs = re.findall(
        r"<function-calls?>\s*([\s\S]*?)\s*</function-calls?>",
        raw,
        flags=re.I,
    )
    if not xml_blobs:
        m = re.search(r"<function-calls?>\s*([\s\S]+)", raw, flags=re.I)
        if m:
            xml_blobs = [m.group(1)]

    candidates: list[str] = []
    for xb in xml_blobs:
        candidates.append(xb.strip())
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(raw)

    out: list[dict] = []
    seen = set()
    for blob in candidates:
        blob = re.sub(r"</?function-calls?>", "", blob, flags=re.I).strip()
        blob = blob.strip("`").strip()
        data = None
        try:
            data = json.loads(blob)
        except Exception:
            for i, ch in enumerate(blob):
                if ch not in "{[":
                    continue
                chunk = blob[i:]
                for end_i in range(len(chunk), 0, -1):
                    if chunk[end_i - 1] not in "}]":
                        continue
                    try:
                        data = json.loads(chunk[:end_i])
                        break
                    except Exception:
                        continue
                if data is None:
                    data = extract_json(chunk)
                if data is not None:
                    break
        if data is None:
            continue
        for c in from_data(data, len(out)):
            key = (c["function"]["name"], c["function"]["arguments"])
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        if out:
            return out
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
    allow = {"write_file", "str_replace", "run_command", "report_done"}
    out = [t for t in WORKER_TOOLS if ((t.get("function") or {}).get("name") or "") in allow]
    return out or WORKER_TOOLS


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0))

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        thinking: bool = False,
        budget: int = 0,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        job_dir: Path | None = None,
        agent: str = "llm",
        task: str = "",
        tool_choice: str | dict | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": "bonsai",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": bool(thinking)},
            "thinking_budget_tokens": int(budget if thinking else 0),
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice if tool_choice is not None else "required"
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user = m["content"]
                break
        r = self.http.post(self.base + "/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        if job_dir is not None:
            tools_used = []
            for c in msg.get("tool_calls") or []:
                tools_used.append(((c.get("function") or {}).get("name")) or "")
            llm_trace(
                job_dir,
                {
                    "agent": agent,
                    "task": task,
                    "prompt": last_user,
                    "response": msg.get("content") or "",
                    "thinking": msg.get("reasoning_content") or "",
                    "tools": [t for t in tools_used if t],
                },
            )
        return msg


def load_state(job_dir: Path, goal: str, root: str) -> dict[str, Any]:
    path = job_dir / "state.json"
    if path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        if root:
            state["root"] = root
        incoming = (goal or "").strip()
        original = (state.get("goal") or "").strip()
        if incoming and incoming != original:
            addenda = state.setdefault("addenda", [])
            if not addenda or (addenda[-1].get("text") if isinstance(addenda[-1], dict) else addenda[-1]) != incoming:
                addenda.append({"at": utc_now(), "text": incoming})
            state["latest_request"] = incoming
            state["request_consumed"] = None
        return state
    return {
        "goal": goal,
        "root": root,
        "status": "running",
        "notes": "",
        "plan": [],
        "current": None,
        "history": [],
        "addenda": [],
        "artifact": None,
        "updated": utc_now(),
    }


def write_notes_md(state: dict[str, Any]) -> None:
    root = state.get("root")
    if not root:
        return
    lines = [
        "# Overseer project notes",
        "",
        f"Updated: {state.get('updated')}",
        f"Status: {state.get('status')}",
        "",
        "## Original goal",
        state.get("goal") or "",
        "",
    ]
    addenda = state.get("addenda") or []
    if addenda:
        lines.append("## Later requests")
        for a in addenda:
            if isinstance(a, dict):
                lines.append(f"- {a.get('at')}: {a.get('text')}")
            else:
                lines.append(f"- {a}")
        lines.append("")
    if state.get("artifact"):
        lines += ["## Last build", state["artifact"], ""]
    lines += ["## Rolling notes", state.get("notes") or "(none)", "", "## Tasks"]
    for t in state.get("plan") or []:
        lines.append(f"- [{t.get('status')}] {t.get('id')} {t.get('title')}")
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
        Path(root, "NOTES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def save_state(job_dir: Path, state: dict[str, Any]) -> None:
    state["updated"] = utc_now()
    hist = state.get("history") or []
    if len(hist) > 40:
        state["history"] = hist[-40:]
    tmp = job_dir / "state.json.tmp"
    target = job_dir / "state.json"
    payload = json.dumps(state, indent=2, ensure_ascii=False)
    last_err: Exception | None = None
    for attempt in range(8):
        try:
            tmp.write_text(payload, encoding="utf-8")
            try:
                tmp.replace(target)
            except (PermissionError, OSError):
                # Windows: replace can fail if another bridge has the file open.
                try:
                    if target.exists():
                        target.unlink()
                except OSError:
                    time.sleep(0.05 * (attempt + 1))
                    raise
                tmp.replace(target)
            write_notes_md(state)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    try:
        log(job_dir, f"save_state failed after retries: {last_err}")
    except Exception:
        pass
    # Last-ditch: write alternate so work can continue.
    try:
        (job_dir / "state.json.fallback").write_text(payload, encoding="utf-8")
    except OSError:
        pass


def resolve_job_dir(root: Path, job_dir_arg: str | None, demo: Path) -> Path:
    if job_dir_arg:
        local = Path(job_dir_arg).expanduser().resolve()
    else:
        local = (root / ".overseer").resolve()
    local.mkdir(parents=True, exist_ok=True)
    legacy = demo / "jobs" / "active"
    if not (local / "state.json").is_file() and (legacy / "state.json").is_file():
        try:
            st = json.loads((legacy / "state.json").read_text(encoding="utf-8"))
            legacy_root = Path(st.get("root") or "").expanduser()
            if legacy_root.exists() and legacy_root.resolve() == root.resolve():
                for name in ("state.json", "dialogue.jsonl", "overseer.log", "inbox.jsonl", "inbox.json"):
                    src = legacy / name
                    dst = local / name
                    if src.is_file() and not dst.is_file():
                        shutil.copy2(src, dst)
        except Exception:
            pass
    return local


def stop_requested(job_dir: Path) -> bool:
    return (job_dir / "STOP").is_file()


def codeview(job_dir: Path, event: str, **fields: Any) -> None:
    rec = {"ts": utc_now(), "event": event}
    rec.update(fields)
    path = job_dir / "codeview.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def dialogue(job_dir: Path, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    rec = {"ts": utc_now(), "role": role, "text": text}
    path = job_dir / "dialogue.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(job_dir, f"[{role}] {text.replace(chr(10), ' / ')[:400]}")


def take_inbox(job_dir: Path) -> list[str]:
    texts: list[str] = []
    path = job_dir / "inbox.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            msgs = data.get("messages") or []
            texts.extend(str(m.get("text") or "").strip() for m in msgs)
        except json.JSONDecodeError:
            pass
        tmp = job_dir / "inbox.json.tmp"
        tmp.write_text(json.dumps({"messages": []}, indent=2), encoding="utf-8")
        tmp.replace(path)
    line_path = job_dir / "inbox.jsonl"
    if line_path.is_file():
        try:
            raw = line_path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        try:
            line_path.unlink()
        except OSError:
            pass
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                texts.append(str(rec.get("text") or rec.get("message") or "").strip())
            except json.JSONDecodeError:
                texts.append(line)
    return [t for t in texts if t]


def wait_for_user(job_dir: Path, state: dict[str, Any], question: str) -> str | None:
    q = (question or "Need your input to continue.").strip()
    dialogue(job_dir, "overseer", "QUESTION: " + q)
    (job_dir / "pending_question.json").write_text(
        json.dumps({"question": q, "ts": utc_now()}, indent=2), encoding="utf-8"
    )
    state["status"] = "waiting"
    save_state(job_dir, state)
    while True:
        if stop_requested(job_dir):
            return None
        msgs = take_inbox(job_dir)
        if msgs:
            reply = "\n".join(msgs)
            for m in msgs:
                dialogue(job_dir, "user", m)
            try:
                (job_dir / "pending_question.json").unlink()
            except OSError:
                pass
            state["status"] = "running"
            save_state(job_dir, state)
            return reply
        time.sleep(0.4)


def human_notes(job_dir: Path) -> str:
    msgs = take_inbox(job_dir)
    if not msgs:
        return ""
    for m in msgs:
        dialogue(job_dir, "user", m)
    return "Human messages:\n" + "\n".join(f"- {m}" for m in msgs)


def find_csprojs(root: Path) -> list[Path]:
    found = []
    for p in root.rglob("*.csproj"):
        if any(part in ("obj", "bin", ".git") for part in p.parts):
            continue
        found.append(p)
    return found


def find_built_exe(proj: Path) -> Path | None:
    release = proj.parent / "bin" / "Release"
    if not release.is_dir():
        return None
    named = list(release.rglob(proj.stem + ".exe"))
    if named:
        return named[0]
    exes = [
        e
        for e in release.rglob("*.exe")
        if e.name.lower() not in ("createdump.exe",) and "testhost" not in e.name.lower()
    ]
    return exes[0] if exes else None


def final_build(ws: Workspace, job_dir: Path, env: dict[str, str]) -> tuple[bool, str]:
    projects = find_csprojs(ws.root)
    if not projects:
        return False, "No .csproj found under the workspace."
    proj = projects[0]
    for p in projects:
        if (p.parent / "Program.cs").is_file():
            proj = p
            break
    dialogue(job_dir, "system", f"Final Release build: {proj}")
    try:
        proc = subprocess.run(
            ["dotnet", "build", str(proj), "-c", "Release", "--nologo"],
            cwd=str(proj.parent),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        return False, clip(out, 4000)
    exe = find_built_exe(proj)
    if exe is None:
        return False, "Build succeeded but no .exe was found under bin/Release.\n" + clip(out, 1500)
    return True, str(exe)


def kill_running_game() -> None:
    try:
        subprocess.run(
            ["taskkill", "/IM", "Descent.exe", "/F"],
            capture_output=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def human_wants_build(text: str) -> str | None:
    t = (text or "").lower()
    if any(
        s in t
        for s in (
            "compile and run",
            "build and run",
            "run the game",
            "launch the game",
            "start the game",
            "run descent",
            "launch descent",
        )
    ):
        return "run"
    if any(
        s in t
        for s in (
            "compile",
            "dotnet build",
            "release build",
            "build the app",
            "build descent",
            "build the game",
        )
    ):
        return "compile"
    return None


def do_compile(
    job_dir: Path,
    state: dict[str, Any],
    ws: Workspace,
    env: dict[str, str],
    run_after: bool,
) -> tuple[bool, str]:
    kill_running_game()
    ok, loc = final_build(ws, job_dir, env)
    if not ok:
        dialogue(job_dir, "system", "Compile failed (only because you asked to build).\n" + loc)
        log(job_dir, "compile failed: " + loc[:400])
        return False, loc
    state["artifact"] = loc
    marker = ws.root / "BUILD_OUTPUT.txt"
    marker.write_text(f"Build succeeded.\nRun this application:\n{loc}\n", encoding="utf-8")
    save_state(job_dir, state)
    msg = "Compile succeeded.\n" + loc
    if run_after:
        try:
            subprocess.Popen([loc], cwd=str(Path(loc).parent))
            msg += "\nLaunched the game."
        except OSError as e:
            msg += "\nLaunch failed: " + str(e)
    dialogue(job_dir, "overseer", msg)
    log(job_dir, msg.replace("\n", " | "))
    return True, loc


def mark_done(job_dir: Path, state: dict[str, Any], summary: str) -> None:
    state["status"] = "done"
    state["current"] = None
    if summary:
        state["notes"] = ((state.get("notes") or "") + "\n" + summary).strip()
    save_state(job_dir, state)
    dialogue(job_dir, "overseer", "Paused. No automatic compile. Ask to compile or run when you want.")
    log(job_dir, "DONE (no compile)")


def overseer_system(state: dict[str, Any]) -> str:
    return f"""You are the overseer for a local coding agent. You do NOT edit files yourself.
You maintain ONE active micro-task at a time. Broad themes are forbidden.

Workspace root: {state.get("root")}
North-star goal (do NOT copy this into worker tasks wholesale): {state.get("goal")}
Later requests: {json.dumps(state.get("addenda") or [], ensure_ascii=False)[:1200]}
Last build: {state.get("artifact") or "(none yet)"}
Active notes: {(state.get("notes") or "")[:800]}

Reply with a single JSON object, no markdown, one of:
{{"action":"plan","notes":"...","tasks":[{{"id":"t1","title":"...","detail":"..."}}]}}
{{"action":"accept","notes":"rolling notes, facts only"}}
{{"action":"retry","task_id":"tN","instruction":"sharper micro-instruction"}}
{{"action":"split","task_id":"tN","tasks":[{{"id":"tN.a","title":"...","detail":"..."}}]}}
{{"action":"finish","summary":"what shipped"}}
{{"action":"block","reason":"..."}}
{{"action":"ask","question":"one concrete human question"}}

MICRO-TASK RULES (mandatory):
- At most 1-3 pending tasks. Prefer exactly ONE pending plus an optional queued next.
- Each task names exact file path(s), the function/symbol to change, and a verify step.
- Good: "In Descent/src/Gfx/Pix.cs add ItemSprite(kind) for weapon/armor/ring/potion/scroll; call from inventory draw; then dotnet build -c Release."
- Bad: "implement item sprites", "deity system", "polish UI", "explore codebase".
- Definition of done MUST include file proof: write_file to a named path and/or Release build after a write.
- Do not mark a prior task done unless its target file changed or build after write succeeded.
- Never unlock the next theme until the current micro-task proof exists.
- Notes under ~600 tokens. No chain-of-thought.
- NEVER invent Hello World / Form1 tutorials.
- NEVER add launch-Descent.exe tasks. Compile only when the micro-task explicitly requires it.
- If a worker thrash-reads without writing, split into a smaller write-only task or retry with a hard write pin.
- Additional human requests append as new micro-tasks; do not discard completed work.
"""


def worker_system(state: dict[str, Any]) -> str:
    cl = msvc_bin()
    cl_note = f"MSVC cl.exe is on PATH ({cl})" if cl else "MSVC cl.exe was not found; prefer dotnet build -c Release."
    return f"""You are a worker with REAL tools. You CAN read/write workspace files and run shell/dotnet.
You execute ONE micro-task. You do not explore the repo for fun.

Workspace root: {state.get("root")}
{cl_note}

HARD POLICY:
1. Read at most 2-3 files that the task names. Then write_file. Then verify (dotnet build -c Release only if the task says so).
2. After 3 non-write tools (list_dir/search_files/read_file/run_command that is not the final build), you MUST write_file or report_done with status blocked.
3. Ban blind list_dir and search_files unless the task explicitly says a path is missing.
4. Never create hello.txt / Hello World / empty Form1.
5. Never rewrite files the notes mark DONE (e.g. RitualSystem.cs) unless the task says to fix a compile error there.
6. Never launch Descent.exe (locks the binary).
7. Do not expand scope. If the task is Pix.cs ItemSprite, do not start equipment UI or window resizing.
8. Prefer editing an existing file over creating new projects.
9. When finished, report_done with status ok|blocked|need_split, a short summary, and the files you changed.

Keep summaries short: what changed + how verified.
Never paste tool JSON into chat — the runtime only honors real tool_calls.
Chat without tool_calls is treated as blocked.
"""


def run_worker(client: Client, tools: Tools, state: dict[str, Any], task: dict[str, Any], job_dir: Path) -> dict[str, Any]:
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


def ask_overseer(client: Client, state: dict[str, Any], user: str, job_dir: Path) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": overseer_system(state)},
        {
            "role": "user",
            "content": user + "\n\nCurrent state JSON:\n" + json.dumps(
                {
                    "goal": state.get("goal"),
                    "notes": state.get("notes"),
                    "plan": state.get("plan"),
                    "history_tail": (state.get("history") or [])[-6:],
                },
                indent=2,
                ensure_ascii=False,
            )[:8000],
        },
    ]
    msg = client.chat(
        messages,
        tools=None,
        thinking=False,
        budget=0,
        max_tokens=1500,
        temperature=0.2,
        job_dir=job_dir,
        agent="overseer",
        task="plan",
    )
    content = msg.get("content") or ""
    parsed = extract_json(content)
    if not parsed:
        log(job_dir, "overseer JSON parse failed: " + content[:400])
        return {"action": "block", "reason": "overseer did not return JSON"}
    return parsed


def resolve_asks(
    client: Client,
    job_dir: Path,
    state: dict[str, Any],
    decision: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    while decision.get("action") == "ask":
        reply = wait_for_user(job_dir, state, str(decision.get("question") or "Need your input to continue."))
        if reply is None:
            return {"action": "block", "reason": "stopped while waiting for you"}
        extra = human_notes(job_dir)
        decision = ask_overseer(
            client,
            state,
            ((extra + "\n\n") if extra else "")
            + context
            + "\n\nYou asked the human. They replied:\n"
            + reply,
            job_dir,
        )
    return decision


def next_pending(state: dict[str, Any]) -> dict[str, Any] | None:
    # Prefer pending; also reclaim a stuck "running" task after an overseer restart.
    for t in state.get("plan") or []:
        if (t.get("status") or "") == "pending":
            return t
    for t in state.get("plan") or []:
        if (t.get("status") or "") == "running":
            t["status"] = "pending"
            return t
    return None


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    job_dir = Path(args.job_dir).expanduser().resolve() if args.job_dir else (Path(__file__).resolve().parents[1] / "jobs" / "active")
    job_dir.mkdir(parents=True, exist_ok=True)
    stop_path = job_dir / "STOP"
    if stop_path.is_file():
        stop_path.unlink()

    ws = Workspace(root)
    env = build_env()
    client = Client(args.api)
    tools = Tools(ws, env, job_dir)
    state = load_state(job_dir, args.goal, str(root))
    prev_status = state.get("status")
    mode = orch_mode(state, args)
    state["status"] = "running"
    save_state(job_dir, state)
    log(job_dir, f"orch_mode={mode}")
    (job_dir / "overseer.pid").write_text(str(os.getpid()), encoding="utf-8")
    log(job_dir, f"start pid={os.getpid()} goal={state.get('goal')!r} root={root} prev={prev_status}")
    dialogue(
        job_dir,
        "system",
        "Overseer "
        + ("resumed" if prev_status else "started")
        + f".\nOriginal goal: {state.get('goal')}\nWorkspace: {root}"
        + (f"\nLast build: {state.get('artifact')}" if state.get("artifact") else "")
        + (f"\nNew work this session: {state.get('latest_request')}" if state.get("latest_request") and state.get("request_consumed") != state.get("latest_request") else ""),
    )
    codeview(job_dir, "reset", reason="session", title="job start")

    deadline = time.time() + args.max_minutes * 60
    cycles = 0
    while time.time() < deadline:
        if stop_requested(job_dir):
            state["status"] = "stopped"
            save_state(job_dir, state)
            log(job_dir, "stopped by STOP file")
            return 0
        cycles += 1
        if cycles > args.max_cycles:
            state["status"] = "blocked"
            state["notes"] = (state.get("notes") or "") + "\nHit max cycles."
            save_state(job_dir, state)
            log(job_dir, "max cycles")
            return 2

        mode = orch_mode(state, args)

        if mode == "botfather":
            ingest_botfather_inbox(job_dir, state)
            parked = state.get("botfather_inbox") or []
            if parked:
                last = str((parked[-1] or {}).get("text") or "")
                build_mode = human_wants_build(last)
                if build_mode:
                    do_compile(job_dir, state, ws, env, run_after=(build_mode == "run"))
                    state["botfather_inbox"] = parked[:-1]
                    save_state(job_dir, state)
            req = ""
            extra = ""
        else:
            extra = human_notes(job_dir)
            build_mode = human_wants_build(extra)
            if build_mode:
                do_compile(job_dir, state, ws, env, run_after=(build_mode == "run"))
            req = (state.get("latest_request") or "").strip()
        if req and state.get("request_consumed") != req:
            log(job_dir, "overseer adding work for latest request")
            ctx = (
                (extra + "\n\n" if extra else "")
                + "CONTINUING PROJECT. Do not redo completed tasks unless they are broken.\n"
                + f"Original goal: {state.get('goal')}\n"
                + f"New work requested:\n{req}\n"
                + "Reply with action=plan and only the NEW pending tasks."
            )
            decision = resolve_asks(
                client, job_dir, state, ask_overseer(client, state, ctx, job_dir), ctx
            )
            if decision.get("action") == "plan" and decision.get("tasks"):
                existing = {t["id"] for t in state.get("plan") or []}
                added = []
                for i, t in enumerate(decision["tasks"], 1):
                    tid = str(t.get("id") or f"n{len(state.get('plan') or []) + i}")
                    if tid in existing:
                        tid = f"{tid}.{i}"
                    item = {
                        "id": tid,
                        "title": t.get("title") or tid,
                        "detail": t.get("detail") or "",
                        "status": "pending",
                    }
                    state.setdefault("plan", []).append(item)
                    added.append(item)
                if decision.get("notes"):
                    state["notes"] = decision["notes"][:4000]
                state["request_consumed"] = req
                save_state(job_dir, state)
                dialogue(
                    job_dir,
                    "overseer",
                    "Added work:\n" + "\n".join(f"- {t['id']} {t['title']}" for t in added),
                )
                continue
            state["request_consumed"] = req
            save_state(job_dir, state)

        if not state.get("plan"):
            if mode == "botfather":
                state["status"] = "idle"
                state["current"] = None
                save_state(job_dir, state)
                if cycles <= 1 or cycles % 15 == 0:
                    log(job_dir, "botfather idle — waiting for task card")
                    dialogue(job_dir, "system", "Idle (BotFather orch). Waiting for /api/task card.")
                time.sleep(2.0)
                cycles -= 1  # idle wait does not burn max-cycles
                state["status"] = "running"
                save_state(job_dir, state)
                continue
            log(job_dir, "overseer planning")
            ctx = (extra + "\n\n" if extra else "") + "No plan yet. Create a bite-size plan for the goal."
            decision = resolve_asks(
                client,
                job_dir,
                state,
                ask_overseer(client, state, ctx, job_dir),
                ctx,
            )
            if decision.get("action") == "plan" and decision.get("tasks"):
                tasks = []
                for i, t in enumerate(decision["tasks"], 1):
                    tid = str(t.get("id") or f"t{i}")
                    tasks.append(
                        {
                            "id": tid,
                            "title": t.get("title") or tid,
                            "detail": t.get("detail") or "",
                            "status": "pending",
                        }
                    )
                state["plan"] = tasks
                if decision.get("notes"):
                    state["notes"] = decision["notes"]
                save_state(job_dir, state)
                log(job_dir, f"plan with {len(tasks)} tasks")
                dialogue(
                    job_dir,
                    "overseer",
                    "Plan:\n" + "\n".join(f"- {t['id']} {t['title']}" for t in tasks),
                )
                continue
            state["status"] = "blocked"
            state["block_reason"] = decision.get("reason") or "planning failed"
            save_state(job_dir, state)
            log(job_dir, "planning failed: " + json.dumps(decision)[:400])
            return 3

        task = next_pending(state)
        if task is None:
            if mode == "botfather":
                state["status"] = "idle"
                state["current"] = None
                save_state(job_dir, state)
                if cycles % 15 == 0:
                    log(job_dir, "botfather idle queue — waiting for next task card")
                    dialogue(job_dir, "system", "Queue empty (BotFather orch). Waiting for next /api/task.")
                time.sleep(2.0)
                cycles -= 1  # idle wait does not burn max-cycles
                state["status"] = "running"
                save_state(job_dir, state)
                continue
            log(job_dir, "overseer review (queue empty)")
            ctx = (
                (extra + "\n\n" if extra else "")
                + "All current tasks are done. If the human asked for more work, add pending tasks. "
                "If they asked to compile or run, they already got that from the controller — do not add a build task. "
                "Otherwise action=finish to idle (no compile)."
            )
            decision = resolve_asks(
                client,
                job_dir,
                state,
                ask_overseer(client, state, ctx, job_dir),
                ctx,
            )
            action = decision.get("action")
            if action == "finish":
                mark_done(job_dir, state, decision.get("summary") or "")
                return 0
            if action == "plan" and decision.get("tasks"):
                existing = {t["id"] for t in state["plan"]}
                for i, t in enumerate(decision["tasks"], 1):
                    tid = str(t.get("id") or f"t{len(state['plan']) + i}")
                    if tid in existing:
                        tid = f"{tid}.{i}"
                    state["plan"].append(
                        {
                            "id": tid,
                            "title": t.get("title") or tid,
                            "detail": t.get("detail") or "",
                            "status": "pending",
                        }
                    )
                save_state(job_dir, state)
                continue
            if action == "block":
                state["status"] = "blocked"
                state["block_reason"] = decision.get("reason") or "blocked"
                save_state(job_dir, state)
                dialogue(job_dir, "overseer", "Blocked: " + state["block_reason"])
                log(job_dir, "blocked: " + state["block_reason"])
                return 4
            time.sleep(8)
            continue

        task["status"] = "running"
        state["current"] = task["id"]
        save_state(job_dir, state)
        log(job_dir, f"dispatch {task['id']} {task['title']}")
        dialogue(job_dir, "overseer", f"Starting {task['id']}: {task['title']}")
        codeview(job_dir, "reset", reason="task", task=task["id"], title=task.get("title") or task["id"])
        try:
            result = run_worker(client, tools, state, task, job_dir)
        except httpx.HTTPError as e:
            log(job_dir, f"API error: {e}")
            time.sleep(5)
            task["status"] = "retry"
            save_state(job_dir, state)
            continue
        except subprocess.TimeoutExpired:
            result = {"status": "need_split", "summary": "command timed out", "files": []}
        except Exception as e:
            log(job_dir, "worker crash: " + traceback.format_exc())
            result = {"status": "blocked", "summary": str(e), "files": []}

        log(job_dir, f"handoff {task['id']} {result.get('status')}: {str(result.get('summary'))[:300]}")
        dialogue(
            job_dir,
            "worker",
            f"{task['id']} {task['title']} → {result.get('status')}\n{result.get('summary') or ''}",
        )
        state.setdefault("history", []).append(
            {"task": task["id"], "status": result.get("status"), "summary": result.get("summary"), "at": utc_now()}
        )

        if mode == "botfather":
            action = auto_review_worker(result, task, state)
            save_state(job_dir, state)
            if action == "accept":
                dialogue(job_dir, "system", "Accepted %s (auto). BotFather may enqueue next." % task.get("id"))
            else:
                dialogue(job_dir, "system", "Task %s failed - waiting for BotFather (no local re-plan)." % task.get("id"))
                time.sleep(1.0)
            continue

        extra_after = human_notes(job_dir)
        ctx = (
            (extra_after + "\n\n" if extra_after else "")
            + f"Worker finished task {task['id']} ({task['title']}).\n"
            + f"status={result.get('status')}\n"
            + f"summary={result.get('summary')}\n"
            + f"files={result.get('files')}\n"
            + "Accept if good, retry with a sharper instruction, split if too large, ask the human if needed, or block."
        )
        decision = resolve_asks(
            client,
            job_dir,
            state,
            ask_overseer(client, state, ctx, job_dir),
            ctx,
        )
        action = decision.get("action") or "accept"
        if decision.get("notes"):
            state["notes"] = decision["notes"][:4000]

        if action == "retry":
            task["status"] = "retry"
            if decision.get("instruction"):
                task["detail"] = (task.get("detail") or "") + "\nRETRY: " + decision["instruction"]
        elif action == "split" and decision.get("tasks"):
            task["status"] = "split"
            for i, t in enumerate(decision["tasks"], 1):
                state["plan"].append(
                    {
                        "id": str(t.get("id") or f"{task['id']}.{i}"),
                        "title": t.get("title") or f"split {i}",
                        "detail": t.get("detail") or "",
                        "status": "pending",
                    }
                )
        elif action == "block":
            task["status"] = "failed"
            state["status"] = "blocked"
            state["block_reason"] = decision.get("reason") or result.get("summary")
            save_state(job_dir, state)
            dialogue(job_dir, "overseer", "Blocked: " + str(state["block_reason"]))
            log(job_dir, "blocked after worker: " + state["block_reason"])
            return 4
        elif action == "finish":
            task["status"] = "done"
            mark_done(job_dir, state, decision.get("summary") or "")
            return 0
        else:
            if result.get("status") == "blocked":
                task["status"] = "failed"
                state["status"] = "blocked"
                state["block_reason"] = result.get("summary")
                save_state(job_dir, state)
                return 4
            if result.get("status") == "need_split":
                task["status"] = "retry"
                task["detail"] = (task.get("detail") or "") + "\nMake this smaller; previous attempt hit limits."
            else:
                task["status"] = "done"
                task["summary"] = result.get("summary")
                task["files"] = result.get("files")

        state["current"] = None
        save_state(job_dir, state)

    state["status"] = "blocked"
    state["block_reason"] = "time limit"
    save_state(job_dir, state)
    log(job_dir, "time limit")
    return 5


def main() -> int:
    demo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Bonsai overseer/worker loop")
    p.add_argument("--goal", default="", help="What to accomplish")
    p.add_argument("--goal-file", default="", help="Read goal text from a file")
    p.add_argument("--root", default=str(demo / "jobs" / "workspace"), help="Workspace the tools may touch")
    p.add_argument("--job-dir", default="", help="Defaults to <workspace>/.overseer so memory stays with the project")
    p.add_argument("--api", default=API_DEFAULT)
    p.add_argument("--max-minutes", type=int, default=180)
    p.add_argument("--max-cycles", type=int, default=40)
    p.add_argument("--orch", default="", choices=["", "local", "botfather"],
                    help="local=LLM plans+codes; botfather=BotFather plans, LLM codes")
    args = p.parse_args()
    if args.goal_file:
        args.goal = Path(args.goal_file).read_text(encoding="utf-8").strip()
    args.root = str(Path(args.root).expanduser().resolve())
    Path(args.root).mkdir(parents=True, exist_ok=True)
    job_dir = resolve_job_dir(Path(args.root), args.job_dir or None, demo)
    args.job_dir = str(job_dir)
    if not args.goal:
        state_path = job_dir / "state.json"
        if state_path.is_file():
            args.goal = json.loads(state_path.read_text(encoding="utf-8")).get("goal") or ""
    if not args.goal:
        print("Pass --goal, or resume a workspace that already has .overseer/state.json", file=sys.stderr)
        return 1
    try:
        return run(args)
    except KeyboardInterrupt:
        return 0
    finally:
        pid_file = job_dir / "overseer.pid"
        try:
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
