# Bonsai 2 server controller - start / stop / restart + live status.
# Closing this window does not stop llama-server.

if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') {
    $arg = "-NoProfile -STA -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arg
    exit
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$DemoDir = Split-Path $PSScriptRoot -Parent
$BinDir = "C:\Users\edwar\Llama-cpp"
$Exe     = Join-Path $BinDir "llama-server.exe"
$Model = "C:\Users\edwar\prism-ml\models\qwen3-30b-a3b\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf"  # Qwen default; was Bonsai
$Webui   = Join-Path $PSScriptRoot "webui-config.json"
$LogDir  = Join-Path $DemoDir "logs"
$OutLog  = Join-Path $LogDir "bonsai-server.out.log"
$ErrLog  = Join-Path $LogDir "bonsai-server.err.log"
$HostAddress = "0.0.0.0"
$Port        = 8080
$Ctx = "4096"

$script:busy = $false
$script:lastMsg = ""

function Get-LanIPv4 {
    $addrs = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -like "192.168.*" -and
            $_.PrefixOrigin -eq "Dhcp" -and
            $_.InterfaceAlias -notmatch "VPN|Nord|Virtual|VMware|Hyper-V|Loopback"
        } | Sort-Object -Property InterfaceMetric)
    if ($addrs) { return $addrs[0].IPAddress }
    $any = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.*" })
    if ($any) { return $any[0].IPAddress }
    return $null
}

function Get-ServerProcess {
    Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'prism-ml' -or $_.ExecutablePath -like "*prism-ml*" } |
        Select-Object -First 1
}

function Get-Health {
    try {
        $r = & curl.exe -sS -m 1 http://127.0.0.1:$Port/health 2>$null
        if ($r -match '"status"\s*:\s*"ok"') { return "ok" }
        if ($r) { return "unexpected" }
    } catch {}
    return "down"
}

function Get-State {
    $proc = Get-ServerProcess
    $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $health = if ($proc) { Get-Health } else { "down" }
    $lan = Get-LanIPv4
    [pscustomobject]@{
        Running = [bool]$proc
        Pid     = if ($proc) { $proc.ProcessId } else { $null }
        Listen  = if ($listen) { "$($listen.LocalAddress):$Port" } else { $null }
        Health  = $health
        LanIP   = $lan
        Local   = "http://127.0.0.1:$Port"
        Lan     = if ($lan) { "http://${lan}:$Port" } else { "(no LAN IP)" }
        Api     = if ($lan) { "http://${lan}:$Port/v1" } else { "http://127.0.0.1:$Port/v1" }
    }
}

function Start-BonsaiServer {
    if (-not (Test-Path $Exe))   { throw "llama-server.exe not found:`n$Exe`nRun setup.ps1 and install the CUDA binaries." }
    if (-not (Test-Path $Model)) { throw "Model not found:`n$Model" }
    if (Get-ServerProcess) { $script:lastMsg = "Already running."; return }

    $taken = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($taken) { throw "Port $Port is already in use by PID $($taken.OwningProcess)." }

    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    foreach ($f in @($OutLog, $ErrLog)) {
        if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }

    $env:Path = "$BinDir;$env:Path"
    $args = @(
        "-m", $Model,
        "--host", $HostAddress, "--port", "$Port",
        "-ngl", "99", "-fa", "on", "-c", $Ctx,
        "--temp", "1.0", "--top-p", "0.95", "--top-k", "20", "--jinja",
        "--webui-config-file", $Webui,
        "-np", "1"
    )
    $p = Start-Process -FilePath $Exe -ArgumentList $args -WorkingDirectory $BinDir `
        -NoNewWindow -PassThru -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    if (-not $p) { throw "Failed to start llama-server." }
    $script:lastMsg = "Starting (PID $($p.Id))..."
}

function Stop-BonsaiServer {
    $proc = Get-ServerProcess
    if (-not $proc) { $script:lastMsg = "Already stopped."; return }
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(8)
    do {
        Start-Sleep -Milliseconds 200
        if (-not (Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue)) { break }
    } while ((Get-Date) -lt $deadline)
    $script:lastMsg = "Stopped."
}

# ── UI ──────────────────────────────────────────────────────────────────────
$bg     = [Drawing.Color]::FromArgb(255, 16, 20, 16)
$panel  = [Drawing.Color]::FromArgb(255, 28, 34, 28)
$fg     = [Drawing.Color]::FromArgb(255, 232, 236, 230)
$muted  = [Drawing.Color]::FromArgb(255, 150, 160, 148)
$green  = [Drawing.Color]::FromArgb(255, 80, 180, 90)
$red    = [Drawing.Color]::FromArgb(255, 200, 80, 70)
$amber  = [Drawing.Color]::FromArgb(255, 210, 160, 60)
$btnBg  = [Drawing.Color]::FromArgb(255, 42, 52, 42)
$accent = [Drawing.Color]::FromArgb(255, 62, 140, 72)

$form = New-Object Windows.Forms.Form
$form.Text = "Local LLM Server (Qwen3-30B-A3B)"
$form.Size = New-Object Drawing.Size(460, 430)
$form.MinimumSize = New-Object Drawing.Size(460, 430)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.BackColor = $bg
$form.ForeColor = $fg
$form.Font = New-Object Drawing.Font("Segoe UI", 9.5)
try { $form.Icon = [Drawing.Icon]::ExtractAssociatedIcon($Exe) } catch {}

function New-Label($text, $x, $y, $w, $h, $color, $size, $bold) {
    $l = New-Object Windows.Forms.Label
    $l.Text = $text
    $l.Location = New-Object Drawing.Point($x, $y)
    $l.Size = New-Object Drawing.Size($w, $h)
    $l.ForeColor = $color
    $l.BackColor = [Drawing.Color]::Transparent
    $style = if ($bold) { [Drawing.FontStyle]::Bold } else { [Drawing.FontStyle]::Regular }
    $l.Font = New-Object Drawing.Font("Segoe UI", $size, $style)
    $form.Controls.Add($l) | Out-Null
    return $l
}

function New-Btn($text, $x, $y, $w, $enabledColor) {
    $b = New-Object Windows.Forms.Button
    $b.Text = $text
    $b.Location = New-Object Drawing.Point($x, $y)
    $b.Size = New-Object Drawing.Size($w, 34)
    $b.FlatStyle = "Flat"
    $b.FlatAppearance.BorderSize = 0
    $b.BackColor = $btnBg
    $b.ForeColor = $fg
    $b.Cursor = [Windows.Forms.Cursors]::Hand
    $form.Controls.Add($b) | Out-Null
    return $b
}

$title = New-Label "Qwen3-30B-A3B Q4_K_M" 20 16 280 28 $fg 14 $true
$sub   = New-Label "CUDA  |  llama-server  |  port $Port" 20 44 320 20 $muted 9 $false

$dot = New-Object Windows.Forms.Panel
$dot.Size = New-Object Drawing.Size(14, 14)
$dot.Location = New-Object Drawing.Point(22, 82)
$dot.BackColor = $red
$form.Controls.Add($dot)

$status = New-Label "Stopped" 44 76 380 22 $fg 12 $true
$pidLbl = New-Label "PID  -" 22 108 200 18 $muted 9 $false
$health = New-Label "Health  -" 230 108 200 18 $muted 9 $false
$bind   = New-Label "Bind  -" 22 128 410 18 $muted 9 $false
$local  = New-Label "Local  http://127.0.0.1:$Port" 22 156 410 18 $fg 9 $false
$lan    = New-Label "LAN    -" 22 176 410 18 $fg 9 $false
$api    = New-Label "API    -" 22 196 410 18 $fg 9 $false
$msg    = New-Label "Closing this window does not stop the server." 22 226 410 18 $muted 8.5 $false

$btnStart   = New-Btn "Start"    22  258 130
$btnStop    = New-Btn "Stop"     162 258 130
$btnRestart = New-Btn "Restart"  302 258 130
$btnOpen    = New-Btn "Open chat" 22  302 200
$btnCopy    = New-Btn "Copy API URL" 232 302 200

$hint = New-Label "Remote clients:  LAN URL  +  /v1" 22 348 410 18 $muted 8.5 $false

function Set-DotColor([Drawing.Color]$c) {
    $dot.BackColor = $c
}

function Update-Ui {
  try {
    $s = Get-State
    if ($script:busy) {
        # keep last starting/stopping text; still refresh pid/health if present
    }
    if ($s.Running -and $s.Health -eq "ok") {
        $status.Text = "Running"
        $status.ForeColor = $green
        Set-DotColor $green
    } elseif ($s.Running) {
        $status.Text = "Starting / not healthy"
        $status.ForeColor = $amber
        Set-DotColor $amber
    } else {
        $status.Text = "Stopped"
        $status.ForeColor = $red
        Set-DotColor $red
    }
    $pidLbl.Text = if ($s.Pid) { "PID  $($s.Pid)" } else { "PID  -" }
    $health.Text = "Health  $($s.Health)"
    $bind.Text   = if ($s.Listen) { "Bind  $($s.Listen)" } else { "Bind  (not listening)" }
    $local.Text  = "Local  $($s.Local)"
    $lan.Text    = "LAN    $($s.Lan)"
    $api.Text    = "API    $($s.Api)"
    if ($script:lastMsg) { $msg.Text = $script:lastMsg }

    $canStart = -not $s.Running -and -not $script:busy
    $canStop  = $s.Running -and -not $script:busy
    $btnStart.Enabled = $canStart
    $btnStop.Enabled  = $canStop
    $btnRestart.Enabled = -not $script:busy
    $btnOpen.Enabled  = ($s.Health -eq "ok")
    $btnCopy.Enabled  = $true
    $btnStart.BackColor = if ($canStart) { $accent } else { $btnBg }
  } catch {
    $script:lastMsg = "Status error: $($_.Exception.Message)"
  }
}

function Invoke-Safe([scriptblock]$work) {
    if ($script:busy) { return }
    $script:busy = $true
    $btnStart.Enabled = $btnStop.Enabled = $btnRestart.Enabled = $false
    try {
        & $work
    } catch {
        $script:lastMsg = $_.Exception.Message
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "Bonsai Server", "OK", "Error") | Out-Null
    } finally {
        $script:busy = $false
        Update-Ui
    }
}

$btnStart.Add_Click({ Invoke-Safe { Start-BonsaiServer } })
$btnStop.Add_Click({ Invoke-Safe { Stop-BonsaiServer } })
$btnRestart.Add_Click({
    Invoke-Safe {
        if (Get-ServerProcess) { Stop-BonsaiServer }
        Start-BonsaiServer
    }
})
$btnOpen.Add_Click({ Start-Process "http://127.0.0.1:$Port/" })
$btnCopy.Add_Click({
    $s = Get-State
    [Windows.Forms.Clipboard]::SetText($s.Api)
    $script:lastMsg = "Copied $($s.Api)"
    Update-Ui
})

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1500
$timer.Add_Tick({ Update-Ui })
$form.Add_Shown({
    $form.TopMost = $true
    $form.Activate()
    $form.BringToFront()
    Update-Ui
    $timer.Start()
    $form.TopMost = $false
})
$form.Add_FormClosed({ $timer.Stop() })

[void][Windows.Forms.Application]::Run($form)
