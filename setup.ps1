# =============================================================================
#  CryptoTrader — Setup & First Run (Windows PowerShell)
#  מושך מ-GitHub, מתקין סביבה, מוריד מודל Ollama, מריץ vision backtest
# =============================================================================
#
#  הרצה:
#    powershell -ExecutionPolicy Bypass -File setup.ps1
#
#  אפשרויות:
#    $env:OLLAMA_MODEL  = "qwen2-vl:7b"   # מודל שונה
#    $env:VISION_SAMPLE = "200"            # כמות עסקאות
# =============================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # מהיר יותר ל-Invoke-WebRequest

# ── הגדרות ───────────────────────────────────────────────────────────────────
$REPO_URL      = "https://github.com/roiag/cryptoTrader.git"
$REPO_DIR      = "cryptoTrader"
$OLLAMA_MODEL  = if ($env:OLLAMA_MODEL)  { $env:OLLAMA_MODEL }  else { "llama3.2-vision" }
$VISION_SAMPLE = if ($env:VISION_SAMPLE) { $env:VISION_SAMPLE } else { "100" }
$OLLAMA_PORT   = 11434
$OllamaProcess = $null

# ── פונקציות UI ──────────────────────────────────────────────────────────────

function Write-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║          CryptoTrader — Vision Backtest Setup        ║" -ForegroundColor Cyan
    Write-Host "  ║     Multi-Agent Crypto Trading System by roiag       ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Repo:  $REPO_URL" -ForegroundColor DarkGray
    Write-Host "  Model: $OLLAMA_MODEL  |  Sample: $VISION_SAMPLE trades" -ForegroundColor DarkGray
    Write-Host ""
}

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Blue
    Write-Host "  ◆  $Title" -ForegroundColor White
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Blue
}

function Write-OK([string]$Msg) {
    Write-Host "  " -NoNewline
    Write-Host "✓" -ForegroundColor Green -NoNewline
    Write-Host "  $Msg"
}

function Write-Fail([string]$Msg) {
    Write-Host "  " -NoNewline
    Write-Host "✗" -ForegroundColor Red -NoNewline
    Write-Host "  $Msg" -ForegroundColor Red
}

function Write-Info([string]$Msg) {
    Write-Host "  " -NoNewline
    Write-Host "→" -ForegroundColor Cyan -NoNewline
    Write-Host "  $Msg"
}

function Write-Warn([string]$Msg) {
    Write-Host "  " -NoNewline
    Write-Host "⚠" -ForegroundColor Yellow -NoNewline
    Write-Host "  $Msg" -ForegroundColor Yellow
}

function Write-Step([string]$Msg) {
    Write-Host "  " -NoNewline
    Write-Host "..." -ForegroundColor DarkGray -NoNewline
    Write-Host "  $Msg" -ForegroundColor DarkGray
}

function Write-Command([string]$Cmd) {
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "  │  $Cmd" -ForegroundColor Cyan
    Write-Host "  └──────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    Write-Host ""
}

function Stop-OllamaIfStarted {
    if ($null -ne $OllamaProcess -and -not $OllamaProcess.HasExited) {
        Write-Info "Stopping background Ollama server (PID $($OllamaProcess.Id))..."
        Stop-Process -Id $OllamaProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

function Exit-WithError([string]$Msg) {
    Write-Host ""
    Write-Fail "FATAL: $Msg"
    Write-Host ""
    Write-Host "  Setup aborted. Fix the error above and re-run setup.ps1" -ForegroundColor DarkGray
    Write-Host ""
    Stop-OllamaIfStarted
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — בדיקת תנאים מוקדמים
# ─────────────────────────────────────────────────────────────────────────────
function Check-Prerequisites {
    Write-Section "Step 1/6 — Checking Prerequisites"
    $allOk = $true

    # Git
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $ver = (git --version 2>&1) -replace "git version ", ""
        Write-OK "git $ver"
    } else {
        Write-Fail "git is not installed"
        Write-Info "Install from: https://git-scm.com/downloads"
        $allOk = $false
    }

    # Python
    $script:PYTHON_CMD = $null
    foreach ($cmd in @("python", "python3", "py")) {
        $py = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($py) {
            $ver = (& $cmd --version 2>&1) -replace "Python ", ""
            $parts = $ver.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $script:PYTHON_CMD = $cmd
                Write-OK "Python $ver"
                break
            }
        }
    }
    if (-not $script:PYTHON_CMD) {
        Write-Fail "Python >= 3.10 is required"
        Write-Info "Install from: https://python.org/downloads"
        $allOk = $false
    }

    # Disk space
    $drive = (Get-Location).Drive.Name + ":"
    $disk  = Get-PSDrive -Name (Get-Location).Drive.Name -ErrorAction SilentlyContinue
    if ($disk) {
        $freeGB = [math]::Round($disk.Free / 1GB, 1)
        if ($freeGB -lt 10) {
            Write-Warn "Low disk space: ${freeGB}GB (model needs ~8GB)"
        } else {
            Write-OK "Disk space: ${freeGB}GB available"
        }
    }

    if (-not $allOk) {
        Exit-WithError "Missing required tools. Install them and re-run."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — Clone / Pull
# ─────────────────────────────────────────────────────────────────────────────
function Clone-Repo {
    Write-Section "Step 2/6 — Repository"

    if (Test-Path "$REPO_DIR\.git") {
        Write-Warn "Directory '$REPO_DIR' already exists — pulling latest changes"
        Push-Location $REPO_DIR
        $pull = git pull origin master 2>&1
        if ($pull -match "Already up to date|Fast-forward") {
            Write-OK "Repository is up to date"
        } else {
            Write-OK "Repository updated"
        }
        Pop-Location
    } else {
        Write-Step "Cloning $REPO_URL ..."
        git clone $REPO_URL $REPO_DIR 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-OK "Cloned into .\$REPO_DIR"
        } else {
            Exit-WithError "Failed to clone repository. Check your internet connection."
        }
    }

    Set-Location $REPO_DIR
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — Python environment + dependencies
# ─────────────────────────────────────────────────────────────────────────────
function Setup-Python {
    Write-Section "Step 3/6 — Python Environment"

    $script:VENV_PYTHON = ".\.venv\Scripts\python.exe"
    $script:VENV_PIP    = ".\.venv\Scripts\pip.exe"

    if (Test-Path ".venv") {
        Write-OK "Virtual environment already exists"
    } else {
        Write-Step "Creating virtual environment..."
        & $script:PYTHON_CMD -m venv .venv
        if ($LASTEXITCODE -eq 0) {
            Write-OK "Virtual environment created (.venv\)"
        } else {
            Exit-WithError "Failed to create virtual environment"
        }
    }

    # Upgrade pip
    Write-Step "Upgrading pip..."
    & $script:VENV_PYTHON -m pip install --upgrade pip -q

    # Install requirements
    Write-Step "Installing dependencies..."
    Write-Host ""
    Write-Command "pip install -r requirements.txt"

    $pipOutput = & $script:VENV_PIP install -r requirements.txt 2>&1
    $failed = $false
    foreach ($line in $pipOutput) {
        if ($line -match "^(Collecting|Downloading|Installing)") {
            Write-Host "    $line" -ForegroundColor DarkGray
        } elseif ($line -match "error|ERROR|failed|FAILED") {
            Write-Host "    $line" -ForegroundColor Red
            $failed = $true
        } elseif ($line -match "Successfully installed") {
            Write-Host "    $line" -ForegroundColor Green
        }
    }

    if ($failed -or $LASTEXITCODE -ne 0) {
        Exit-WithError "pip install failed. Check output above."
    }

    # mplfinance
    Write-Step "Installing mplfinance (chart renderer)..."
    & $script:VENV_PIP install mplfinance -q
    if ($LASTEXITCODE -eq 0) {
        Write-OK "mplfinance installed"
    } else {
        Exit-WithError "Failed to install mplfinance"
    }

    Write-OK "All Python dependencies installed"
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — Ollama
# ─────────────────────────────────────────────────────────────────────────────
function Setup-Ollama {
    Write-Section "Step 4/6 — Ollama Setup"

    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollama) {
        $ver = (ollama --version 2>&1 | Select-Object -First 1)
        Write-OK "Ollama installed: $ver"
    } else {
        Write-Warn "Ollama not found"
        Write-Host ""
        Write-Host "  Please install Ollama manually:" -ForegroundColor Yellow
        Write-Info "1. Go to https://ollama.com/download"
        Write-Info "2. Download and run OllamaSetup.exe"
        Write-Info "3. Press ENTER here when done"
        Write-Host ""
        Read-Host "  Press ENTER when Ollama is installed"
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollama) {
            Exit-WithError "Ollama still not found. Make sure it's in your PATH and restart the terminal."
        }
        Write-OK "Ollama detected"
    }

    # Start server if not running
    Write-Host ""
    Write-Info "Checking Ollama server..."
    $running = $false
    try {
        $resp = Invoke-RestMethod "http://localhost:${OLLAMA_PORT}/api/tags" -TimeoutSec 3
        $running = $true
    } catch { }

    if ($running) {
        Write-OK "Ollama server is already running on port $OLLAMA_PORT"
    } else {
        Write-Step "Starting Ollama server in background..."
        $script:OllamaProcess = Start-Process -FilePath "ollama" -ArgumentList "serve" `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput "$env:TEMP\ollama_server.log"

        # Wait up to 30 seconds
        Write-Host "  → Waiting for Ollama to start" -NoNewline
        $ready = $false
        for ($i = 0; $i -lt 30; $i++) {
            try {
                $null = Invoke-RestMethod "http://localhost:${OLLAMA_PORT}/api/tags" -TimeoutSec 2
                $ready = $true
                break
            } catch { }
            Write-Host "." -NoNewline
            Start-Sleep -Seconds 1
        }
        Write-Host ""
        if ($ready) {
            Write-OK "Ollama server is ready (PID: $($script:OllamaProcess.Id))"
        } else {
            Exit-WithError "Ollama server did not start within 30 seconds. Check $env:TEMP\ollama_server.log"
        }
    }

    # Pull model
    Write-Host ""
    Write-Info "Checking model: $OLLAMA_MODEL"

    $modelList = ollama list 2>&1
    $baseModel = $OLLAMA_MODEL.Split(":")[0]
    if ($modelList -match $baseModel) {
        Write-OK "Model '$OLLAMA_MODEL' already downloaded"
    } else {
        Write-Warn "Model '$OLLAMA_MODEL' not found locally — downloading now"
        Write-Host "  Model size: ~5-8 GB — this may take several minutes" -ForegroundColor DarkGray
        Write-Host ""
        Write-Command "ollama pull $OLLAMA_MODEL"

        ollama pull $OLLAMA_MODEL
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-OK "Model '$OLLAMA_MODEL' downloaded successfully"
        } else {
            Write-Host ""
            Exit-WithError "Failed to pull model '$OLLAMA_MODEL'.
       Try a different model:
         `$env:OLLAMA_MODEL='qwen2-vl:7b'; .\setup.ps1
         `$env:OLLAMA_MODEL='moondream';   .\setup.ps1"
        }
    }

    # Smoke test
    Write-Host ""
    Write-Step "Running model smoke test..."
    Write-Command "ollama run $OLLAMA_MODEL `"Reply with exactly: OK`""

    $testResp = ollama run $OLLAMA_MODEL "Reply with exactly: OK" 2>&1 | Select-Object -First 3
    if ($testResp -match "ok") {
        Write-OK "Model responded correctly"
    } else {
        Write-Warn "Unexpected response: $($testResp -join ' ')"
        Write-Warn "Vision quality may vary — continuing anyway"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — Math Backtest
# ─────────────────────────────────────────────────────────────────────────────
function Run-MathBacktest {
    Write-Section "Step 5/6 — Math Backtest (Generating Signal Data)"

    if (Test-Path "combined_results.csv") {
        $rows = (Get-Content "combined_results.csv").Count
        Write-OK "combined_results.csv already exists ($rows rows — skipping math backtest)"
        Write-Info "Delete combined_results.csv and re-run to regenerate."
        return
    }

    Write-Host ""
    Write-Info "Running math backtest on BTC + ETH (2022-2025)..."
    Write-Host "  First run downloads ~100k candles — may take 5-10 minutes" -ForegroundColor DarkGray
    Write-Host "  Subsequent runs use local cache and are instant" -ForegroundColor DarkGray
    Write-Host ""
    Write-Command "python run_backtest.py --both --export results.csv"

    & $script:VENV_PYTHON run_backtest.py `
        --both `
        --threshold 4.5 `
        --start 2022-01-01 `
        --end 2025-01-01 `
        --export results.csv

    if ($LASTEXITCODE -eq 0 -and (Test-Path "combined_results.csv")) {
        $rows = (Get-Content "combined_results.csv").Count
        Write-Host ""
        Write-OK "Math backtest complete — $rows trade signals saved"
    } else {
        Exit-WithError "Math backtest failed. Check the output above."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6 — Vision Backtest
# ─────────────────────────────────────────────────────────────────────────────
function Run-VisionBacktest {
    Write-Section "Step 6/6 — Vision Backtest"

    Write-Host ""
    Write-Info "Analyzing $VISION_SAMPLE trades with model: $OLLAMA_MODEL"
    Write-Host "  For each trade: renders chart → sends to Ollama → records result" -ForegroundColor DarkGray
    Write-Host ""
    Write-Command "python run_vision_backtest.py --sample $VISION_SAMPLE --model $OLLAMA_MODEL"

    & $script:VENV_PYTHON run_vision_backtest.py `
        --input combined_results.csv `
        --model $OLLAMA_MODEL `
        --sample $VISION_SAMPLE `
        --output vision_results.csv `
        --delay 0.3

    if ($LASTEXITCODE -eq 0 -and (Test-Path "vision_results.csv")) {
        $rows = (Get-Content "vision_results.csv").Count
        Write-Host ""
        Write-OK "Vision backtest complete — vision_results.csv ($rows rows)"
    } else {
        Write-Fail "Vision backtest encountered errors"
        Write-Host ""
        Write-Host "  Possible causes:" -ForegroundColor DarkGray
        Write-Info "Ollama ran out of memory → try: `$env:OLLAMA_MODEL='moondream'; .\setup.ps1"
        Write-Info "Model not responding    → run 'ollama serve' in another terminal"
        Write-Info "No combined_results.csv → delete it and re-run"
        Stop-OllamaIfStarted
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
function Write-Summary {
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "  ║                  Setup Complete!                     ║" -ForegroundColor Green
    Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    Write-OK "Repository cloned/updated"
    Write-OK "Python environment ready (.venv\)"
    Write-OK "Ollama model: $OLLAMA_MODEL"
    Write-OK "Math backtest: combined_results.csv"
    Write-OK "Vision backtest: vision_results.csv"
    Write-Host ""
    Write-Host "  Next steps:" -ForegroundColor White
    Write-Info "Open vision_results.csv to see AGREE vs DISAGREE win rates"
    Write-Info "Run full backtest:  python run_backtest.py --both"
    Write-Info "Run full vision:    python run_vision_backtest.py"
    Write-Info "Start live bot:     python scheduler.py"
    Write-Host ""
    Write-Host "  Results in: $(Get-Location)" -ForegroundColor DarkGray
    Write-Host ""
}

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
Write-Header
Check-Prerequisites
Clone-Repo
Setup-Python
Setup-Ollama
Run-MathBacktest
Run-VisionBacktest
Write-Summary
Stop-OllamaIfStarted
