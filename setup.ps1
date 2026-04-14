# =============================================================================
#  CryptoTrader — Full Windows Setup (zero assumptions)
#  מתקין הכל מאפס: Git, Python, Ollama, מורד repo, מריץ vision backtest
#
#  ONE-LINER — הדבק ב-PowerShell (כל גרסה, אין צורך בהרשאות מיוחדות):
#
#    irm https://raw.githubusercontent.com/roiag/cryptoTrader/master/setup.ps1 | iex
#
#  אפשרויות:
#    $env:OLLAMA_MODEL  = "qwen2-vl:7b"  ; irm ... | iex
#    $env:VISION_SAMPLE = "200"           ; irm ... | iex
# =============================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# ── הגדרות ───────────────────────────────────────────────────────────────────
$REPO_URL      = "https://github.com/roiag/cryptoTrader.git"
$REPO_DIR      = "cryptoTrader"
$OLLAMA_MODEL  = if ($env:OLLAMA_MODEL)  { $env:OLLAMA_MODEL }  else { "llama3.2-vision" }
$VISION_SAMPLE = if ($env:VISION_SAMPLE) { $env:VISION_SAMPLE } else { "100" }
$OLLAMA_PORT   = 11434
$INSTALL_DIR   = Join-Path $env:USERPROFILE "CryptoTrader_Setup"
$PYTHON_VER    = "3.11.9"
$PYTHON_URL    = "https://www.python.org/ftp/python/$PYTHON_VER/python-$PYTHON_VER-amd64.exe"
$GIT_URL       = "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe"
$OLLAMA_URL    = "https://ollama.com/download/OllamaSetup.exe"

$script:OllamaProc = $null

# ── UI helpers ────────────────────────────────────────────────────────────────
function Write-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║       CryptoTrader — Full Automated Setup            ║" -ForegroundColor Cyan
    Write-Host "  ║   Installs everything from scratch on Windows        ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Model:  $OLLAMA_MODEL" -ForegroundColor DarkGray
    Write-Host "  Sample: $VISION_SAMPLE trades" -ForegroundColor DarkGray
    Write-Host ""
}

function Write-Section([string]$n, [string]$title) {
    Write-Host ""
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Blue
    Write-Host "  ◆  Step $n — $title" -ForegroundColor White
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Blue
}

function Write-OK([string]$m)   { Write-Host "  " -NoNewline; Write-Host "✓" -ForegroundColor Green  -NoNewline; Write-Host "  $m" }
function Write-Fail([string]$m) { Write-Host "  " -NoNewline; Write-Host "✗" -ForegroundColor Red    -NoNewline; Write-Host "  $m" -ForegroundColor Red }
function Write-Info([string]$m) { Write-Host "  " -NoNewline; Write-Host "→" -ForegroundColor Cyan   -NoNewline; Write-Host "  $m" }
function Write-Warn([string]$m) { Write-Host "  " -NoNewline; Write-Host "⚠" -ForegroundColor Yellow -NoNewline; Write-Host "  $m" -ForegroundColor Yellow }
function Write-Step([string]$m) { Write-Host "  " -NoNewline; Write-Host "..." -ForegroundColor DarkGray -NoNewline; Write-Host " $m" -ForegroundColor DarkGray }

function Write-Cmd([string]$c) {
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "  │  $c" -ForegroundColor Cyan
    Write-Host "  └──────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Download([string]$name, [string]$url) {
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────────────┐" -ForegroundColor DarkYellow
    Write-Host "  │  Downloading $name" -ForegroundColor DarkYellow
    Write-Host "  │  $($url.Substring(0, [Math]::Min($url.Length, 52)))" -ForegroundColor DarkGray
    Write-Host "  └──────────────────────────────────────────────────────┘" -ForegroundColor DarkYellow
    Write-Host ""
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:PATH = "$machine;$user"
}

function Stop-OllamaIfStarted {
    if ($null -ne $script:OllamaProc -and -not $script:OllamaProc.HasExited) {
        Stop-Process -Id $script:OllamaProc.Id -Force -ErrorAction SilentlyContinue
    }
}

function Exit-Fatal([string]$msg) {
    Write-Host ""
    Write-Fail "FATAL: $msg"
    Write-Host ""
    Write-Host "  Fix the issue above and re-run the script." -ForegroundColor DarkGray
    Write-Host ""
    Stop-OllamaIfStarted
    exit 1
}

function Download-File([string]$url, [string]$dest, [string]$label) {
    Write-Download $label $url
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        $sizeMB = [math]::Round((Get-Item $dest).Length / 1MB, 1)
        Write-OK "$label downloaded (${sizeMB} MB)"
    } catch {
        Exit-Fatal "Failed to download $label from $url`n  $_"
    }
}

function Wait-ForOllama {
    Write-Host "  → Waiting for Ollama to start" -NoNewline
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $null = Invoke-RestMethod "http://localhost:${OLLAMA_PORT}/api/tags" -TimeoutSec 2
            Write-Host " ready!" -ForegroundColor Green
            return $true
        } catch { }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 1
    }
    Write-Host " timeout!" -ForegroundColor Red
    return $false
}

# ─────────────────────────────────────────────────────────────────────────────
#  Auto-elevate to Administrator
# ─────────────────────────────────────────────────────────────────────────────
function Ensure-Admin {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if (-not $isAdmin) {
        Write-Host ""
        Write-Host "  ⚠  Administrator privileges required — re-launching..." -ForegroundColor Yellow
        Write-Host ""
        # שמור את הסקריפט הזה לקובץ זמני ורץ כ-Admin
        $scriptPath = $MyInvocation.MyCommand.Path
        if (-not $scriptPath) {
            # רץ דרך irm | iex — שמור לקובץ זמני
            $scriptPath = "$env:TEMP\cryptotrader_setup.ps1"
            $MyInvocation.MyCommand.ScriptBlock | Out-String | Set-Content $scriptPath -Encoding UTF8
        }
        $args = "-ExecutionPolicy Bypass -File `"$scriptPath`""
        Start-Process powershell -Verb RunAs -ArgumentList $args -Wait
        exit 0
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — Git
# ─────────────────────────────────────────────────────────────────────────────
function Install-Git {
    Write-Section "1/6" "Git"

    if (Get-Command git -ErrorAction SilentlyContinue) {
        $ver = (git --version 2>&1) -replace "git version ",""
        Write-OK "Git already installed: $ver"
        return
    }

    # נסה winget (Windows 11 / Windows 10 עם App Installer)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Installing Git via winget..."
        Write-Cmd "winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements"
        winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Refresh-Path
        if (Get-Command git -ErrorAction SilentlyContinue) {
            Write-OK "Git installed via winget"
            return
        }
    }

    # Fallback — הורדה ישירה
    $installer = "$env:TEMP\git-setup.exe"
    Download-File $GIT_URL $installer "Git for Windows"
    Write-Step "Installing Git silently..."
    $p = Start-Process $installer -ArgumentList @(
        "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-",
        "/CLOSEAPPLICATIONS", "/COMPONENTS=icons,ext\reg\shellhere,assoc,assoc_sh"
    ) -PassThru -Wait
    if ($p.ExitCode -ne 0) { Exit-Fatal "Git installer exited with code $($p.ExitCode)" }
    Refresh-Path
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-OK "Git installed successfully"
    } else {
        Exit-Fatal "Git installed but not found in PATH. Restart PowerShell and re-run."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — Python
# ─────────────────────────────────────────────────────────────────────────────
function Install-Python {
    Write-Section "2/6" "Python"

    # בדוק אם כבר מותקן (3.10+)
    foreach ($cmd in @("python", "python3", "py")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $verStr = (& $cmd --version 2>&1) -replace "Python ",""
            $parts  = $verStr.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $script:PYTHON = $cmd
                Write-OK "Python $verStr already installed"
                return
            }
        }
    }

    # נסה winget
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Installing Python 3.11 via winget..."
        Write-Cmd "winget install --id Python.Python.3.11 --silent --accept-package-agreements"
        winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Refresh-Path
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $ver = (python --version 2>&1) -replace "Python ",""
            $script:PYTHON = "python"
            Write-OK "Python $ver installed via winget"
            return
        }
    }

    # Fallback — הורדה ישירה
    $installer = "$env:TEMP\python-setup.exe"
    Download-File $PYTHON_URL $installer "Python $PYTHON_VER"
    Write-Step "Installing Python silently (all users, adds to PATH)..."
    $p = Start-Process $installer -ArgumentList @(
        "/quiet",
        "InstallAllUsers=1",
        "PrependPath=1",
        "Include_test=0",
        "Include_pip=1"
    ) -PassThru -Wait
    if ($p.ExitCode -ne 0) { Exit-Fatal "Python installer exited with code $($p.ExitCode)" }
    Refresh-Path

    # מצא את ה-python שנוסף
    $script:PYTHON = $null
    foreach ($cmd in @("python", "python3")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $script:PYTHON = $cmd
            break
        }
    }
    # fallback לנתיב ישיר
    $directPath = "C:\Program Files\Python311\python.exe"
    if (-not $script:PYTHON -and (Test-Path $directPath)) {
        $script:PYTHON = $directPath
    }
    if (-not $script:PYTHON) {
        Exit-Fatal "Python installed but not found in PATH. Restart PowerShell and re-run."
    }
    $ver = (& $script:PYTHON --version 2>&1) -replace "Python ",""
    Write-OK "Python $ver installed successfully"
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — Clone / Pull repo + venv + dependencies
# ─────────────────────────────────────────────────────────────────────────────
function Setup-Repo {
    Write-Section "3/6" "Repository + Python Environment"

    # Clone
    if (Test-Path "$REPO_DIR\.git") {
        Write-Warn "Folder '$REPO_DIR' exists — pulling latest changes"
        Push-Location $REPO_DIR
        git pull origin master 2>&1 | Out-Null
        Write-OK "Repository updated"
    } else {
        Write-Step "Cloning from GitHub..."
        Write-Cmd "git clone $REPO_URL"
        git clone $REPO_URL $REPO_DIR 2>&1
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "git clone failed. Check internet connection." }
        Write-OK "Repository cloned to .\$REPO_DIR"
        Push-Location $REPO_DIR
    }

    # Virtual environment
    $script:VENV_PY  = ".\.venv\Scripts\python.exe"
    $script:VENV_PIP = ".\.venv\Scripts\pip.exe"

    if (Test-Path ".venv") {
        Write-OK "Virtual environment already exists"
    } else {
        Write-Step "Creating virtual environment..."
        & $script:PYTHON -m venv .venv
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to create virtual environment" }
        Write-OK "Virtual environment created"
    }

    Write-Step "Upgrading pip..."
    & $script:VENV_PY -m pip install --upgrade pip -q

    # Install requirements
    Write-Step "Installing Python packages..."
    Write-Cmd "pip install -r requirements.txt"
    & $script:VENV_PIP install -r requirements.txt 2>&1 | ForEach-Object {
        if ($_ -match "^(Collecting|Downloading|Installing|Successfully)") {
            Write-Host "    $_" -ForegroundColor DarkGray
        } elseif ($_ -match "error|ERROR") {
            Write-Host "    $_" -ForegroundColor Red
        }
    }
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "pip install -r requirements.txt failed" }

    Write-Step "Installing mplfinance (chart renderer)..."
    & $script:VENV_PIP install mplfinance -q
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to install mplfinance" }

    Write-OK "All Python packages installed"
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — Ollama: install + start + pull model
# ─────────────────────────────────────────────────────────────────────────────
function Setup-Ollama {
    Write-Section "4/6" "Ollama + Vision Model"

    # ── התקנה ─────────────────────────────────────────────────────────────────
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $installer = "$env:TEMP\OllamaSetup.exe"
        Download-File $OLLAMA_URL $installer "Ollama"
        Write-Step "Installing Ollama silently..."
        $p = Start-Process $installer -ArgumentList "/SILENT" -PassThru -Wait
        # Ollama installer might return non-zero on some machines — just check if binary exists
        Refresh-Path
        Start-Sleep -Seconds 3   # installer needs a moment to finish writing files

        # חפש את ollama גם בנתיבים ישירים
        $ollamaPaths = @(
            "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
            "$env:ProgramFiles\Ollama\ollama.exe",
            "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe"
        )
        foreach ($p in $ollamaPaths) {
            if (Test-Path $p) {
                $env:PATH = "$env:PATH;$(Split-Path $p)"
                break
            }
        }
        Refresh-Path

        if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
            Exit-Fatal "Ollama installed but not found. Try restarting PowerShell and re-running."
        }
        $ver = (ollama --version 2>&1 | Select-Object -First 1)
        Write-OK "Ollama installed: $ver"
    } else {
        $ver = (ollama --version 2>&1 | Select-Object -First 1)
        Write-OK "Ollama already installed: $ver"
    }

    # ── הפעל שרת ──────────────────────────────────────────────────────────────
    Write-Host ""
    $running = $false
    try {
        $null = Invoke-RestMethod "http://localhost:${OLLAMA_PORT}/api/tags" -TimeoutSec 3
        $running = $true
    } catch { }

    if ($running) {
        Write-OK "Ollama server already running on port $OLLAMA_PORT"
    } else {
        Write-Step "Starting Ollama server in background..."
        $script:OllamaProc = Start-Process -FilePath "ollama" -ArgumentList "serve" `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "$env:TEMP\ollama_out.log" `
            -RedirectStandardError  "$env:TEMP\ollama_err.log"

        if (-not (Wait-ForOllama)) {
            $log = Get-Content "$env:TEMP\ollama_err.log" -ErrorAction SilentlyContinue | Select-Object -Last 5
            Exit-Fatal "Ollama server failed to start.`n  Last log: $($log -join ' | ')"
        }
        Write-OK "Ollama server is ready (PID: $($script:OllamaProc.Id))"
    }

    # ── משוך מודל ─────────────────────────────────────────────────────────────
    Write-Host ""
    $baseModel = $OLLAMA_MODEL.Split(":")[0]
    $modelList = (ollama list 2>&1) -join " "

    if ($modelList -match $baseModel) {
        Write-OK "Model '$OLLAMA_MODEL' already downloaded"
    } else {
        Write-Warn "Downloading model '$OLLAMA_MODEL' — this may take 10-30 minutes (~6-8 GB)"
        Write-Host "  Progress is shown below by Ollama directly:" -ForegroundColor DarkGray
        Write-Host ""
        Write-Cmd "ollama pull $OLLAMA_MODEL"

        ollama pull $OLLAMA_MODEL
        if ($LASTEXITCODE -ne 0) {
            Exit-Fatal "Failed to download model '$OLLAMA_MODEL'.
  Try a smaller model: `$env:OLLAMA_MODEL='moondream'; irm ... | iex"
        }
        Write-Host ""
        Write-OK "Model '$OLLAMA_MODEL' ready"
    }

    # ── בדיקת תגובה ───────────────────────────────────────────────────────────
    Write-Host ""
    Write-Step "Testing model response (first response may be slow)..."
    $testOut = ollama run $OLLAMA_MODEL "Say only: READY" 2>&1 | Select-Object -First 2
    if ($testOut -match "READY|ready|ok|OK") {
        Write-OK "Model is responding correctly"
    } else {
        Write-Warn "Unexpected response: $($testOut -join ' ')"
        Write-Warn "Model may still work — continuing..."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — Math Backtest
# ─────────────────────────────────────────────────────────────────────────────
function Run-MathBacktest {
    Write-Section "5/6" "Math Backtest — Generating Signal Data"

    if (Test-Path "combined_results.csv") {
        $rows = (Get-Content "combined_results.csv").Count
        Write-OK "combined_results.csv already exists ($rows rows) — skipping"
        Write-Info "Delete the file and re-run to regenerate."
        return
    }

    Write-Host ""
    Write-Info "Running math backtest on BTC + ETH (2022-2025)..."
    Write-Host "  Downloads ~100k candles on first run — usually 5-10 minutes" -ForegroundColor DarkGray
    Write-Host "  Cached locally for all future runs" -ForegroundColor DarkGray
    Write-Cmd "python run_backtest.py --both --threshold 4.5 --export results.csv"

    & $script:VENV_PY run_backtest.py `
        --both `
        --threshold 4.5 `
        --start 2022-01-01 `
        --end 2025-01-01 `
        --export results.csv

    if ($LASTEXITCODE -ne 0 -or -not (Test-Path "combined_results.csv")) {
        Exit-Fatal "Math backtest failed. Check output above."
    }
    $rows = (Get-Content "combined_results.csv").Count
    Write-OK "Math backtest complete — $rows trade signals saved"
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6 — Vision Backtest
# ─────────────────────────────────────────────────────────────────────────────
function Run-VisionBacktest {
    Write-Section "6/6" "Vision Backtest"

    Write-Host ""
    Write-Info "Analyzing $VISION_SAMPLE trades with: $OLLAMA_MODEL"
    Write-Host "  Renders chart → sends to Ollama → records agreement with math signal" -ForegroundColor DarkGray
    Write-Cmd "python run_vision_backtest.py --sample $VISION_SAMPLE --model $OLLAMA_MODEL"

    & $script:VENV_PY run_vision_backtest.py `
        --input combined_results.csv `
        --model $OLLAMA_MODEL `
        --sample $VISION_SAMPLE `
        --output vision_results.csv `
        --delay 0.3

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Vision backtest had errors"
        Write-Host ""
        Write-Host "  Possible fixes:" -ForegroundColor DarkGray
        Write-Info "Out of memory → use smaller model: `$env:OLLAMA_MODEL='moondream'"
        Write-Info "Ollama stopped → re-run the script (server will restart)"
        Stop-OllamaIfStarted
        exit 1
    }

    if (Test-Path "vision_results.csv") {
        $rows = (Get-Content "vision_results.csv").Count
        Write-OK "Vision backtest complete — vision_results.csv ($rows rows)"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Summary
# ─────────────────────────────────────────────────────────────────────────────
function Write-Summary {
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "  ║                  All Done!                           ║" -ForegroundColor Green
    Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    Write-OK "Git installed"
    Write-OK "Python installed"
    Write-OK "Ollama + model: $OLLAMA_MODEL"
    Write-OK "Math backtest:  combined_results.csv"
    Write-OK "Vision backtest: vision_results.csv"
    Write-Host ""
    Write-Host "  Next steps:" -ForegroundColor White
    Write-Info "Open vision_results.csv — filter 'agreement' column"
    Write-Info "  AGREE trades → higher win rate?"
    Write-Info "  If yes → vision IS a useful filter"
    Write-Host ""
    Write-Info "Run full vision (all trades): cd $REPO_DIR; python run_vision_backtest.py"
    Write-Info "Start live bot:               cd $REPO_DIR; python scheduler.py"
    Write-Host ""
    Write-Host "  Folder: $(Get-Location)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Press any key to close..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
Ensure-Admin
Write-Header
Install-Git
Install-Python
Setup-Repo        # clone + venv + pip (גם עושה Push-Location ל-REPO_DIR)
Setup-Ollama
Run-MathBacktest
Run-VisionBacktest
Write-Summary
Stop-OllamaIfStarted
