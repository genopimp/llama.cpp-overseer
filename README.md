# llama.cpp Overseer

Local LLM overseer stack used on Windows (Alienware): stock **llama.cpp** server, **Qwen3-30B-A3B** launcher, WinForms **Bonsai Server** controller, and Python **overseer / harness / phone bridge**.

## Layout

| Path | Purpose |
|------|---------|
| `llama-cpp/` | Stock llama.cpp Windows CUDA binaries (`llama-server.exe`, etc.) |
| `scripts/BonsaiServerGui.*` | Desktop controller (start/stop model, start new work) |
| `scripts/bonsai-server-gui.ps1` | PowerShell twin of the controller |
| `scripts/start_qwen3_30b_a3b_server.ps1` | Launch Qwen3-30B-A3B on stock llama-server |
| `agent/` | Overseer worker, HTTP harness (`:8787`), Grok/phone bridge |
| `workspace/blank/` | Empty starter project root |

## Not included

- **GGUF weights** (~18GB). Download Qwen3-30B-A3B Q4_K_M separately and place at e.g. `models/qwen3-30b-a3b/`.
- Descent / other game projects.

## Quick start (Windows + NVIDIA)

1. Clone with LFS:
   ```bat
   git lfs install
   git clone https://github.com/genopimp/llama.cpp-overseer.git
   cd llama.cpp-overseer
   ```
2. Download the GGUF into `models/qwen3-30b-a3b/` (or edit the start script paths).
3. Point `scripts/start_qwen3_30b_a3b_server.ps1` / GUI at this clone's `llama-cpp\llama-server.exe` and your GGUF.
4. Start the model:
   ```bat
   powershell -ExecutionPolicy Bypass -File scripts\start_qwen3_30b_a3b_server.ps1
   ```
   Or run `scripts\BonsaiServerGui.exe`.
5. Start the harness/overseer bridge:
   ```bat
   .venv\Scripts\python.exe agent\grok_bot.py
   ```
   Phone UI defaults to port **8787**; llama-server to **8080**.

## Notes

- GUI was retargeted to stock llama.cpp + Qwen3 (ctx 4096, no mmproj).
- Large CUDA DLLs are stored with **Git LFS**.
