$ErrorActionPreference = "Stop"
# Start Qwen3-30B-A3B Q4_K_M on llama-server. Default ctx 4096 for 12GB VRAM.

$Root = Split-Path $PSScriptRoot -Parent
# Prefer Edward's stock llama.cpp (C:\Users\edwar\llama-cpp); keep Bonsai bins as fallback for Ternary quants only.
$Bin = $null
$BinCandidates = @(
    "C:\Users\edwar\llama-cpp\llama-server.exe",
    (Join-Path $Root "bin\cuda-stock\llama-server.exe"),
    (Join-Path $Root "bin\cuda\llama-server.exe"),
    (Join-Path $Root "bin\hip\llama-server.exe"),
    (Join-Path $Root "bin\vulkan\llama-server.exe"),
    (Join-Path $Root "bin\cpu\llama-server.exe")
)
foreach ($cand in $BinCandidates) {
    if (Test-Path $cand) { $Bin = $cand; break }
}
if (-not $Bin) { throw "llama-server.exe not found (checked stock llama-cpp + $Root\bin)" }
Write-Host "Using llama-server: $Bin"

$ModelDir = Join-Path $Root "models\qwen3-30b-a3b"
$Model = Get-ChildItem -Path $ModelDir -Filter "*.gguf" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "Q4_K_M|q4_k_m" -and $_.Name -notmatch "mmproj" -and $_.Length -gt 10GB } |
    Sort-Object Length -Descending |
    Select-Object -First 1
if (-not $Model) { throw "Qwen3-30B-A3B Q4_K_M GGUF not found in $ModelDir - wait for download" }

$HostAddress = if ($env:QWEN_HOST) { $env:QWEN_HOST } else { "0.0.0.0" }
$Port = if ($env:QWEN_PORT) { [int]$env:QWEN_PORT } else { 8080 }
$Ngl = if ($env:QWEN_NGL) { $env:QWEN_NGL } else { "99" }
$Ctx = if ($env:QWEN_CTX) { $env:QWEN_CTX } else { "4096" }

Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 2

$BinDir = Split-Path $Bin -Parent
$env:Path = "$BinDir;$env:Path"

$launchArgs = @(
    "-m", $Model.FullName,
    "--host", $HostAddress,
    "--port", "$Port",
    "-ngl", $Ngl,
    "-fa", "on",
    "-c", $Ctx,
    "--temp", "0.2",
    "--top-p", "0.9",
    "--top-k", "20",
    "--jinja"
)

$gb = [math]::Round($Model.Length/1GB, 2)
Write-Host "=== Qwen3-30B-A3B Q4_K_M ==="
Write-Host "  Model: $($Model.FullName) ($gb GB)"
Write-Host "  Bin:   $Bin"
Write-Host "  Listen http://${HostAddress}:$Port  ctx=$Ctx ngl=$Ngl"

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "qwen3-30b-a3b-server.out.log"
$ErrLog = Join-Path $LogDir "qwen3-30b-a3b-server.err.log"

$p = Start-Process -FilePath $Bin -ArgumentList $launchArgs -WorkingDirectory $BinDir `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden -PassThru
if (-not $p) { throw "Failed to start llama-server" }
Write-Host "Started PID $($p.Id)"

$ok = $false
for ($i=0; $i -lt 240; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
    if ($p.HasExited) { throw "llama-server exited early. See $ErrLog" }
}
if (-not $ok) { throw "Timed out waiting for /health. See $ErrLog" }
Write-Host "Healthy on :$Port"
