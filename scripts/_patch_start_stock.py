from pathlib import Path
p = Path(r"C:\Users\edwar\prism-ml\scripts\start_qwen3_30b_a3b_server.ps1")
t = p.read_text(encoding="utf-8")
old = '''$BinCandidates = @(
    "bin\\cuda\\llama-server.exe",
    "bin\\hip\\llama-server.exe",
    "bin\\vulkan\\llama-server.exe",
    "bin\\cpu\\llama-server.exe"
)
$Bin = $null
foreach ($c in $BinCandidates) {
    $p = Join-Path $Root $c
    if (Test-Path $p) { $Bin = $p; break }
}
if (-not $Bin) { throw "llama-server.exe not found under $Root\\bin" }'''

new = '''# Prefer Edward's stock llama.cpp (C:\\Users\\edwar\\llama-cpp); keep Bonsai bins as fallback for Ternary quants only.
$Bin = $null
$BinCandidates = @(
    "C:\\Users\\edwar\\llama-cpp\\llama-server.exe",
    (Join-Path $Root "bin\\cuda-stock\\llama-server.exe"),
    (Join-Path $Root "bin\\cuda\\llama-server.exe"),
    (Join-Path $Root "bin\\hip\\llama-server.exe"),
    (Join-Path $Root "bin\\vulkan\\llama-server.exe"),
    (Join-Path $Root "bin\\cpu\\llama-server.exe")
)
foreach ($cand in $BinCandidates) {
    if (Test-Path $cand) { $Bin = $cand; break }
}
if (-not $Bin) { throw "llama-server.exe not found (checked stock llama-cpp + $Root\\bin)" }
Write-Host "Using llama-server: $Bin"'''

if old not in t:
    # try softer match
    import re
    m = re.search(r'\$BinCandidates = @\([\s\S]*?if \(-not \$Bin\) \{ throw .*?\}', t)
    if not m:
        raise SystemExit('block not found')
    print('regex block:', repr(m.group(0)[:200]))
    t = t[:m.start()] + new + t[m.end():]
else:
    t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8', newline='\n')
print('patched ok')
print(p.read_text(encoding='utf-8')[:1200])
