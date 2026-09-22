$ErrorActionPreference = "Stop"
# Start Qwen2.5-Coder-14B-Instruct Q4_K_M on llama-server (coding default).
# Bonsai fallback (old): models\bonsai2-gguf\27B\Ternary-Bonsai-2-27B-PQ2_0.gguf

$DemoDir = Split-Path $PSScriptRoot -Parent
$BinCandidates = @(
    "bin\cuda\llama-server.exe",
    "bin\hip\llama-server.exe",
    "bin\vulkan\llama-server.exe",
    "bin\cpu\llama-server.exe"
)
$Bin = $null
foreach ($c in $BinCandidates) {
    $p = Join-Path $DemoDir $c
    if (Test-Path $p) { $Bin = $p; break }
}
if (-not $Bin) { throw "llama-server.exe not found under $DemoDir\bin" }

$ModelDir = Join-Path $DemoDir "models\qwen2.5-coder-14b"
$Model = Get-ChildItem -Path $ModelDir -Filter "*.gguf" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "Q4_K_M|q4_k_m" -and $_.Name -notmatch "mmproj" } |
    Sort-Object Length -Descending |
    Select-Object -First 1
if (-not $Model) { throw "Qwen Q4_K_M GGUF not found in $ModelDir — wait for download" }

$HostAddress = if ($env:QWEN_HOST) { $env:QWEN_HOST } elseif ($env:BONSAI_HOST) { $env:BONSAI_HOST } else { "0.0.0.0" }
$Port = if ($env:QWEN_PORT) { [int]$env:QWEN_PORT } else { 8080 }
$Ngl = if ($env:QWEN_NGL) { $env:QWEN_NGL } elseif ($env:BONSAI_NGL) { $env:BONSAI_NGL } else { "99" }
$Ctx = if ($env:QWEN_CTX) { $env:QWEN_CTX } elseif ($env:BONSAI_CTX -and $env:BONSAI_CTX -ne "0") { $env:BONSAI_CTX } else { "32768" }

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

Write-Host "=== Qwen2.5-Coder-14B Q4_K_M ==="
Write-Host "  Model: $($Model.FullName) ($([math]::Round($Model.Length/1GB,2)) GB)"
Write-Host "  Bin:   $Bin"
Write-Host "  Listen http://${HostAddress}:$Port  ctx=$Ctx ngl=$Ngl"

$LogDir = Join-Path $DemoDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "qwen-server.out.log"
$ErrLog = Join-Path $LogDir "qwen-server.err.log"

$p = Start-Process -FilePath $Bin -ArgumentList $launchArgs -WorkingDirectory $BinDir `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden -PassThru
if (-not $p) { throw "Failed to start llama-server" }
Write-Host "Started PID $($p.Id)"

$ok = $false
for ($i=0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
    if ($p.HasExited) { throw "llama-server exited early. See $ErrLog" }
}
if (-not $ok) { throw "Timed out waiting for /health. See $ErrLog" }
Write-Host "Healthy on :$Port"
