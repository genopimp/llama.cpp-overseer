from pathlib import Path
import json
from datetime import datetime, timezone

root = Path(r"C:\Users\edwar\prism-ml\jobs\workspace")
st_path = root / ".overseer" / "state.json"
st = json.loads(st_path.read_text(encoding="utf-8")) if st_path.is_file() else {}
mem = root / "Descent" / "docs" / "memory"
mem.mkdir(parents=True, exist_ok=True)
(mem / "research").mkdir(exist_ok=True)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
day = now[:10]

goal = (st.get("goal") or "").strip()
plan = st.get("plan") or []

roadmap = {
    "updated": now,
    "orch": st.get("orch") or "botfather",
    "north_star": goal[:2000],
    "visual": {
        "reference": "Cogmind",
        "intent": "Cogmind-like information density and multi-pane HUD; fantasy theme instead of sci-fi chrome",
        "notes": [
            "Always-on map + message log + equipment/status panes",
            "High glyph/sprite density, low chrome",
            "Fantasy palette (parchment, blood, shrine gold) on near-black void",
            "Do not copy Cogmind assets or proprietary text",
        ],
    },
    "policy": {
        "task_size": "m7a-sized until a local write succeeds",
        "botfather": "one task card via POST /api/task",
        "worker": "local Qwen tools only; no self-replan",
        "no_grok_csharp": True,
        "plans_on_disk": "Descent/docs/memory/",
    },
    "phases": [
        {"id": "P0", "title": "Harness + orch", "status": "done", "notes": "fail-closed writes, str_replace, botfather orch, stock llama"},
        {"id": "P1", "title": "Prove tiny write", "status": "active", "notes": "m7a all-race 10% crit then m7b/m7c"},
        {"id": "P2", "title": "Combat juice", "status": "locked", "notes": "conditions, floats, ward/strike if design says"},
        {"id": "P3", "title": "SRD systems one-by-one", "status": "locked", "notes": "from DESIGN_SRD_COGMIND.md"},
        {"id": "P4", "title": "Cogmind-dense fantasy UI", "status": "locked", "notes": "multi-pane HUD mirroring Cogmind density"},
        {"id": "P5", "title": "Dynamic sprites polish", "status": "locked", "notes": "after logic exists"},
    ],
    "tasks": plan,
    "open_questions": [
        "Keep flat Atk/Def/Mag under the hood or jump to six attrs?",
        "Permadeath only vs soft meta unlocks after first stable combat slice?",
        "Exact pane layout / font metrics for Cogmind-dense fantasy HUD?",
    ],
}
(mem / "roadmap.json").write_text(json.dumps(roadmap, indent=2), encoding="utf-8")

lines = [
    "# Descent roadmap (long-term memory)",
    "",
    f"Updated: {now}",
    f"Orch: {roadmap['orch']}",
    "",
    "## Visual north star",
    "**Cogmind-like density**, fantasy skin.",
    "- Multi-pane: map + log + equip/status always visible",
    "- High information density, minimal chrome",
    "- Fantasy palette on near-black (not sci-fi)",
    "- Reference only — do not copy Cogmind assets",
    "",
    "## North star goal",
    goal or "(see .overseer/state.json)",
    "",
    "## Policy",
    "- BotFather posts one micro-task card at a time.",
    "- Local Qwen is worker-only. No self-replan from giant goals.",
    "- BotFather does not author Descent C# unless Edward asks.",
    "- Plans and decisions live under `docs/memory/` (durable).",
    "",
    "## Phases",
]
for p in roadmap["phases"]:
    lines.append(f"- [{p['status']}] **{p['id']} {p['title']}** — {p['notes']}")
lines += ["", "## Task queue"]
for t in plan:
    lines.append(f"- [{t.get('status')}] `{t.get('id')}` {t.get('title')}")
lines += ["", "## Open questions"]
for q in roadmap["open_questions"]:
    lines.append(f"- {q}")
lines += [
    "",
    "## Related",
    "- [DESIGN_SRD_COGMIND.md](../DESIGN_SRD_COGMIND.md)",
    "- [ORCH_BOTFATHER.md](../ORCH_BOTFATHER.md)",
    "- `decisions.md`, `research/`",
]
(mem / "ROADMAP.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

dec = mem / "decisions.md"
block = (
    f"- {day}: Visual style locked — **Cogmind-like UI density** with fantasy theme "
    f"(not sci-fi chrome). Reference game: Cogmind. Do not copy assets.\n"
    f"- {day}: Plans persist under `Descent/docs/memory/` as long-term memory.\n"
    f"- {day}: orch=botfather — BotFather plans, local Qwen codes.\n"
)
if dec.exists():
    text = dec.read_text(encoding="utf-8")
    if "Cogmind-like UI density" not in text:
        if not text.startswith("#"):
            text = "# Decisions log\n\n" + text
        dec.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
else:
    dec.write_text("# Decisions log\n\n" + block, encoding="utf-8")

rq = mem / "research" / "QUEUE.md"
if not rq.exists():
    rq.write_text(
        "# Research queue\n\n"
        "Research bot writes findings here. BotFather turns them into design + task cards.\n"
        "Research bot does **not** edit game C#.\n\n"
        "## Pending\n"
        "- [ ] Map public SRD combat/ability/condition primitives → Descent names\n"
        "- [ ] Cogmind UI density patterns → fantasy HUD pane layout (sizes, log, equip, status)\n"
        "- [ ] Item taxonomy + rarity budgets for roguelike loot\n"
        "- [ ] Procedural sprite constraints for WinForms (palette, tile size, seed)\n\n"
        "## Done\n"
        "- (none yet)\n",
        encoding="utf-8",
    )
else:
    text = rq.read_text(encoding="utf-8")
    if "Cogmind UI density" not in text:
        text = text.replace(
            "## Pending\n",
            "## Pending\n- [ ] Cogmind UI density patterns → fantasy HUD pane layout (sizes, log, equip, status)\n",
            1,
        )
        rq.write_text(text, encoding="utf-8")

(mem / "README.md").write_text(
    "# Descent long-term memory\n\n"
    "Chat can be cleared; this folder should not.\n\n"
    "| File | Role |\n|------|------|\n"
    "| `ROADMAP.md` / `roadmap.json` | Phases + task queue |\n"
    "| `decisions.md` | Locked choices (incl. Cogmind visual target) |\n"
    "| `research/` | Research bot outputs + queue |\n\n"
    "BotFather reads these before posting `/api/task` cards.\n",
    encoding="utf-8",
)

# Patch design doc visual section if present
design = root / "Descent" / "docs" / "DESIGN_SRD_COGMIND.md"
if design.exists():
    d = design.read_text(encoding="utf-8")
    marker = "## Visual direction (LOCKED)"
    section = (
        "\n\n## Visual direction (LOCKED)\n\n"
        "**Reference:** Cogmind (Grid Sage Games) — information density and multi-pane layout only.\n\n"
        "- Always-visible map, message log, and equipment/status panes\n"
        "- High glyph/sprite density; minimal window chrome\n"
        "- Fantasy skin: parchment / blood / shrine gold on near-black (not sci-fi neon)\n"
        "- Procedural sprites stay; UI layout should feel Cogmind-dense\n"
        "- Do **not** copy Cogmind art, fonts, or proprietary text\n"
    )
    if marker in d:
        # replace existing locked section through next ## or EOF
        import re
        d2 = re.sub(r"\n## Visual direction \(LOCKED\)\n.*?(?=\n## |\Z)", section, d, count=1, flags=re.S)
        design.write_text(d2, encoding="utf-8")
    else:
        design.write_text(d.rstrip() + section + "\n", encoding="utf-8")

# note in state
notes = (st.get("notes") or "")
if "Cogmind-like UI density" not in notes:
    st["notes"] = (notes + f"\n[{day}] Visual: Cogmind-dense fantasy UI locked. Plans: docs/memory/.").strip()[-4000:]
    st_path.write_text(json.dumps(st, indent=2), encoding="utf-8")

print("OK")
for p in sorted(mem.rglob("*")):
    if p.is_file():
        print(p.relative_to(mem), p.stat().st_size)
