from pathlib import Path
import shutil, time, re, json

path = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py")
bak = path.with_suffix(path.suffix + f".bak-scrape-{int(time.time())}")
shutil.copy2(path, bak)
print("backup", bak)

src = path.read_text(encoding="utf-8")
start = src.find("def scrape_tool_calls(content: str)")
if start < 0:
    raise SystemExit("scrape_tool_calls not found")
end = src.find("\ndef task_target_paths(", start)
if end < 0:
    raise SystemExit("task_target_paths not found")

new = r'''def scrape_tool_calls(content: str) -> list[dict]:
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


'''

path.write_text(src[:start] + new + src[end+1:], encoding="utf-8")
print("patched ok")

def extract_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None

ns = {"json": json, "re": re, "extract_json": extract_json}
exec(new, ns)
sample = """<function-calls>
  {
    "name": "write_file",
    "arguments": {
      "path": "test.txt",
      "content": "hello"
    }
  }
</function-calls>``````

</function-calls>``````
"""
got = ns["scrape_tool_calls"](sample)
print("scrape", json.dumps(got)[:400])
assert got and got[0]["function"]["name"] == "write_file"
print("TEST OK")
