#!/usr/bin/env python3
"""Patch overseer.py: unnumbered read_file, str_replace tool, fail-closed write_file."""
from pathlib import Path
import shutil, time, re, py_compile

path = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py")
bak = path.with_suffix(f".py.bak-harness-{int(time.time())}")
shutil.copy2(path, bak)
print("backup", bak)
src = path.read_text(encoding="utf-8")

old_tool = """    {
        \"type\": \"function\",
        \"function\": {
            \"name\": \"write_file\",
            \"description\": \"Create or overwrite a text file in the workspace.\",
            \"parameters\": {
                \"type\": \"object\",
                \"properties\": {
                    \"path\": {\"type\": \"string\"},
                    \"content\": {\"type\": \"string\"},
                },
                \"required\": [\"path\", \"content\"],
            },
        },
    },
"""

new_tools = """    {
        \"type\": \"function\",
        \"function\": {
            \"name\": \"write_file\",
            \"description\": (
                \"Create a NEW text file, or overwrite only when creating from scratch. \"
                \"Prefer str_replace for edits. Content must be pure file text — NEVER paste \"
                \"read_file line-number prefixes.\"
            ),
            \"parameters\": {
                \"type\": \"object\",
                \"properties\": {
                    \"path\": {\"type\": \"string\"},
                    \"content\": {\"type\": \"string\"},
                },
                \"required\": [\"path\", \"content\"],
            },
        },
    },
    {
        \"type\": \"function\",
        \"function\": {
            \"name\": \"str_replace\",
            \"description\": (
                \"PREFERRED edit tool. Replace exactly one occurrence of old_str with new_str \"
                \"in an existing file. old_str must match the file exactly (no line-number prefixes).\"
            ),
            \"parameters\": {
                \"type\": \"object\",
                \"properties\": {
                    \"path\": {\"type\": \"string\"},
                    \"old_str\": {\"type\": \"string\"},
                    \"new_str\": {\"type\": \"string\"},
                },
                \"required\": [\"path\", \"old_str\", \"new_str\"],
            },
        },
    },
"""

if old_tool not in src:
    raise SystemExit("write_file tool block not found")
src = src.replace(old_tool, new_tools, 1)
print("ok: tool defs")

old_call = """            elif name == \"write_file\":
                result = self.write_file(str(args.get(\"path\") or \"\"), str(args.get(\"content\") or \"\"))
            elif name == \"search_files\":"""
new_call = """            elif name == \"write_file\":
                result = self.write_file(str(args.get(\"path\") or \"\"), str(args.get(\"content\") or \"\"))
            elif name == \"str_replace\":
                result = self.str_replace(
                    str(args.get(\"path\") or \"\"),
                    str(args.get(\"old_str\") or \"\"),
                    str(args.get(\"new_str\") if args.get(\"new_str\") is not None else \"\"),
                )
            elif name == \"search_files\":"""
if old_call not in src:
    raise SystemExit("call dispatch not found")
src = src.replace(old_call, new_call, 1)
print("ok: dispatch")

old_allow = '    allow = {"write_file", "run_command", "report_done"}'
new_allow = '    allow = {"write_file", "str_replace", "run_command", "report_done"}'
if old_allow not in src:
    raise SystemExit("allow set not found")
src = src.replace(old_allow, new_allow, 1)
print("ok: allow")

needle = '        numbered = [f"{i + offset}|{line}" for i, line in enumerate(chunk)]\n'
idx = src.find(needle)
if idx < 0:
    raise SystemExit("numbered lines needle not found")
end = src.find("\n        }\n", idx)
if end < 0:
    raise SystemExit("end of read_file return not found")
end = end + len("\n        }\n")
new_exact = (
    '        # Do NOT prefix lines with "N|" — models copy that into write_file and corrupt sources.\n'
    '        return {\n'
    '            "path": str(p.relative_to(self.ws.root)).replace("\\\\", "/"),\n'
    '            "total_lines": len(lines),\n'
    '            "shown": f"{offset}-{offset + len(chunk) - 1}",\n'
    '            "content": "\\n".join(chunk),\n'
    '        }\n'
)
src = src[:idx] + new_exact + src[end:]
print("ok: read_file")

wf_start = src.find("    def write_file(self, path: str, content: str)")
if wf_start < 0:
    raise SystemExit("write_file def not found")
wf_rest = src[wf_start + 4:]
next_def = wf_rest.find("\n    def ")
if next_def < 0:
    raise SystemExit("next def after write_file not found")
wf_end = wf_start + 4 + next_def + 1

new_wf = '''    _LINE_NUM_RE = re.compile(r"^(?:\\s*)\\d+\\|", re.M)

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
        p.write_text(content, encoding="utf-8", newline="\\n")
        rel = str(p.relative_to(self.ws.root)).replace("\\\\", "/")
        if self.job_dir is not None and not any(part in ("bin", "obj", ".overseer") for part in p.parts):
            codeview(self.job_dir, "file", path=rel, bytes=p.stat().st_size)
        return {"wrote": rel, "bytes": p.stat().st_size}

    def str_replace(self, path: str, old_str: str, new_str: str) -> dict[str, Any]:
        """Surgical edit — preferred over write_file for existing sources."""
        if not old_str:
            return {"error": "old_str is required and must be non-empty"}
        if old_str == new_str:
            return {"error": "old_str and new_str are identical — nothing to change"}
        p = self.ws.resolve(path)
        if not p.exists():
            return {"error": f"file not found: {path}"}
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_str)
        if count == 0:
            return {
                "error": "old_str not found in file — read the file again and copy exact text",
                "path": str(p.relative_to(self.ws.root)).replace("\\\\", "/"),
            }
        if count > 1:
            return {
                "error": f"old_str matched {count} times — include more surrounding context so it matches exactly once",
                "matches": count,
            }
        new_text = text.replace(old_str, new_str, 1)
        p.write_text(new_text, encoding="utf-8", newline="\\n")
        rel = str(p.relative_to(self.ws.root)).replace("\\\\", "/")
        if self.job_dir is not None and not any(part in ("bin", "obj", ".overseer") for part in p.parts):
            try:
                codeview(self.job_dir, "file", path=rel, bytes=p.stat().st_size, via="str_replace")
            except TypeError:
                codeview(self.job_dir, "file", path=rel, bytes=p.stat().st_size)
        return {
            "replaced": rel,
            "bytes": p.stat().st_size,
            "old_len": len(old_str),
            "new_len": len(new_str),
        }

'''

if re.search(r"^import re\b", src, re.M) is None:
    lines = src.splitlines(keepends=True)
    last_imp = 0
    for i, ln in enumerate(lines[:80]):
        if ln.startswith("import ") or ln.startswith("from "):
            last_imp = i
    lines.insert(last_imp + 1, "import re\n")
    src = "".join(lines)
    print("ok: added import re")
else:
    print("ok: re already imported")

src = src[:wf_start] + new_wf + src[wf_end:]
print("ok: write_file + str_replace")

path.write_text(src, encoding="utf-8", newline="\n")
py_compile.compile(str(path), doraise=True)
print("COMPILE OK", path)
print("has str_replace:", "def str_replace" in src)
print("no numbered prefix gen:", 'f"{i + offset}|{line}"' not in src)
