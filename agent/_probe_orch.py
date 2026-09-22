from pathlib import Path
src = Path(r"C:\Users\edwar\prism-ml\agent\overseer.py").read_text(encoding="utf-8")
needles = [
    "extra = human_notes",
    "human_wants_build",
    "do_compile",
    "latest_request",
    "request_consumed",
    'if not state.get("plan")',
    "next_pending",
    "extra_after = human_notes",
    "def ask_overseer",
    "args = p.parse_args",
    "max-cycles",
    "max_cycles",
    "load_state(job_dir",
    "take_inbox",
    "utc_now",
    "save_state",
    "def run(",
]
for needle in needles:
    i = src.find(needle)
    print(repr(needle), i)
    if i >= 0:
        print(repr(src[i:i+200]))
        print("---")
