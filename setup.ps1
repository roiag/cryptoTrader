#Requires -Version 5.1
# =============================================================================
#  CryptoTrader -- Full Windows Setup
#  Installs Git, Python, Ollama, clones repo, runs vision backtest
# =============================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$REPO_URL      = "https://github.com/roiag/cryptoTrader.git"
$REPO_DIR      = "cryptoTrader"
$OLLAMA_MODEL  = if ($env:OLLAMA_MODEL)  { $env:OLLAMA_MODEL }  else { "llama3.2-vision" }
$VISION_SAMPLE = if ($env:VISION_SAMPLE) { $env:VISION_SAMPLE } else { "100" }
$OLLAMA_PORT   = 11434
$PYTHON_URL    = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$GIT_URL       = "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe"
$OLLAMA_URL    = "https://ollama.com/download/OllamaSetup.exe"

$script:OllamaProc  = $null
$script:VenvPython  = $null

# =============================================================================
#  UI helpers
# =============================================================================

function Write-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  +======================================================+" -ForegroundColor Cyan
    Write-Host "  |        CryptoTrader -- Full Automated Setup          |" -ForegroundColor Cyan
    Write-Host "  |    Installs everything from scratch on Windows       |" -ForegroundColor Cyan
    Write-Host "  +======================================================+" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Model:  $OLLAMA_MODEL" -ForegroundColor DarkGray
    Write-Host "  Sample: $VISION_SAMPLE trades" -ForegroundColor DarkGray
    Write-Host ""
}

function Write-Section([string]$n, [string]$title) {
    Write-Host ""
    Write-Host "  ----------------------------------------------------" -ForegroundColor Blue
    Write-Host "  [Step $n]  $title" -ForegroundColor White
    Write-Host "  ----------------------------------------------------" -ForegroundColor Blue
}

function Write-OK([string]$m) {
    Write-Host "  [OK]  " -ForegroundColor Green -NoNewline
    Write-Host $m
}
function Write-Fail([string]$m) {
    Write-Host "  [ERR] " -ForegroundColor Red -NoNewline
    Write-Host $m -ForegroundColor Red
}
function Write-Info([string]$m) {
    Write-Host "  [ >> ]" -ForegroundColor Cyan -NoNewline
    Write-Host " $m"
}
function Write-Warn([string]$m) {
    Write-Host "  [!!!] " -ForegroundColor Yellow -NoNewline
    Write-Host $m -ForegroundColor Yellow
}
function Write-Step([string]$m) {
    Write-Host "  [...] " -ForegroundColor DarkGray -NoNewline
    Write-Host $m -ForegroundColor DarkGray
}

function Write-Cmd([string]$c) {
    Write-Host ""
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "  |  $c" -ForegroundColor Cyan
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Cyan
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
    Write-Host "  Fix the issue above, then re-run setup.bat" -ForegroundColor DarkGray
    Write-Host ""
    Stop-OllamaIfStarted
    Read-Host "  Press ENTER to close"
    exit 1
}

function Download-File([string]$url, [string]$dest, [string]$label) {
    Write-Host ""
    Write-Host "  Downloading: $label" -ForegroundColor DarkYellow
    Write-Host "  From: $url" -ForegroundColor DarkGray
    Write-Host ""
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        $sizeMB = [math]::Round((Get-Item $dest).Length / 1MB, 1)
        Write-OK "$label downloaded (${sizeMB} MB)"
    }
    catch {
        Exit-Fatal "Failed to download $label`n  Error: $_"
    }
}

function Wait-ForOllama {
    Write-Host "  Waiting for Ollama server" -NoNewline
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $null = Invoke-RestMethod "http://localhost:${OLLAMA_PORT}/api/tags" -TimeoutSec 2
            Write-Host " ready!" -ForegroundColor Green
            return $true
        }
        catch { }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 1
    }
    Write-Host " timed out!" -ForegroundColor Red
    return $false
}

# =============================================================================
#  Auto-elevate to Administrator
# =============================================================================
function Ensure-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if (-not $isAdmin) {
        Write-Host ""
        Write-Warn "Administrator required -- re-launching with elevated privileges..."
        Write-Host ""

        $scriptPath = $MyInvocation.MyCommand.Path
        if (-not $scriptPath) {
            $scriptPath = "$env:TEMP\cryptotrader_setup.ps1"
            $MyInvocation.MyCommand.ScriptBlock | Out-String |
                Set-Content $scriptPath -Encoding UTF8
        }

        $psArgs = "-ExecutionPolicy Bypass -File `"$scriptPath`""
        Start-Process powershell -Verb RunAs -ArgumentList $psArgs -Wait
        exit 0
    }

    Write-OK "Running as Administrator"
}

# =============================================================================
#  STEP 1 -- Git
# =============================================================================
function Install-Git {
    Write-Section "1/6" "Git"

    if (Get-Command git -ErrorAction SilentlyContinue) {
        $ver = (git --version 2>&1) -replace "git version ", ""
        Write-OK "Git already installed: $ver"
        return
    }

    # Try winget first (Windows 10/11 with App Installer)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Installing Git via winget..."
        Write-Cmd "winget install --id Git.Git --silent"
        winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Refresh-Path
        if (Get-Command git -ErrorAction SilentlyContinue) {
            Write-OK "Git installed via winget"
            return
        }
    }

    # Fallback: direct download
    $installer = "$env:TEMP\git-setup.exe"
    Download-File $GIT_URL $installer "Git for Windows"
    Write-Step "Running Git installer silently..."
    $proc = Start-Process $installer -ArgumentList @(
        "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-", "/CLOSEAPPLICATIONS"
    ) -PassThru -Wait
    if ($proc.ExitCode -ne 0) {
        Exit-Fatal "Git installer failed (exit code $($proc.ExitCode))"
    }
    Refresh-Path

    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-OK "Git installed successfully"
    }
    else {
        Exit-Fatal "Git was installed but is not in PATH. Please restart your PC and re-run setup.bat"
    }
}

# =============================================================================
#  STEP 2 -- Python
# =============================================================================
function Install-Python {
    Write-Section "2/6" "Python 3.11"

    foreach ($cmd in @("python", "python3", "py")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $verStr = (& $cmd --version 2>&1) -replace "Python ", ""
            $parts  = $verStr.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $script:Python = $cmd
                Write-OK "Python $verStr already installed"
                return
            }
        }
    }

    # Try winget
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Installing Python 3.11 via winget..."
        Write-Cmd "winget install --id Python.Python.3.11 --silent"
        winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Refresh-Path
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $script:Python = "python"
            $ver = (python --version 2>&1) -replace "Python ", ""
            Write-OK "Python $ver installed via winget"
            return
        }
    }

    # Fallback: direct download
    $installer = "$env:TEMP\python-setup.exe"
    Download-File $PYTHON_URL $installer "Python 3.11.9"
    Write-Step "Running Python installer silently..."
    $proc = Start-Process $installer -ArgumentList @(
        "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1", "Include_test=0"
    ) -PassThru -Wait
    if ($proc.ExitCode -ne 0) {
        Exit-Fatal "Python installer failed (exit code $($proc.ExitCode))"
    }
    Refresh-Path

    $script:Python = $null
    foreach ($cmd in @("python", "python3")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $script:Python = $cmd
            break
        }
    }
    # Try known install path
    $knownPath = "C:\Program Files\Python311\python.exe"
    if (-not $script:Python -and (Test-Path $knownPath)) {
        $script:Python = $knownPath
    }
    if (-not $script:Python) {
        Exit-Fatal "Python was installed but is not in PATH. Please restart your PC and re-run setup.bat"
    }

    $ver = (& $script:Python --version 2>&1) -replace "Python ", ""
    Write-OK "Python $ver installed successfully"
}

# =============================================================================
#  STEP 3 -- Repository + Python environment
# =============================================================================
function Setup-Repo {
    Write-Section "3/6" "Repository + Python Environment"

    # Clone or pull
    if (Test-Path "$REPO_DIR\.git") {
        Write-Warn "Folder '$REPO_DIR' already exists -- pulling latest changes"
        Push-Location $REPO_DIR
        git pull origin master 2>&1 | Out-Null
        Write-OK "Repository updated"
    }
    else {
        Write-Step "Cloning from GitHub..."
        Write-Cmd "git clone $REPO_URL"
        git clone $REPO_URL $REPO_DIR 2>&1
        if ($LASTEXITCODE -ne 0) {
            Exit-Fatal "git clone failed. Check your internet connection or GitHub access."
        }
        Write-OK "Repository cloned to .\$REPO_DIR"
        Push-Location $REPO_DIR
    }

    # Virtual environment
    $script:VenvPython = ".\.venv\Scripts\python.exe"
    $venvPip           = ".\.venv\Scripts\pip.exe"

    if (Test-Path ".venv") {
        Write-OK "Virtual environment already exists"
    }
    else {
        Write-Step "Creating virtual environment..."
        & $script:Python -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            Exit-Fatal "Failed to create virtual environment"
        }
        Write-OK "Virtual environment created"
    }

    Write-Step "Upgrading pip..."
    & $script:VenvPython -m pip install --upgrade pip -q

    Write-Step "Installing Python packages..."
    Write-Cmd "pip install -r requirements.txt"
    & $venvPip install -r requirements.txt 2>&1 | ForEach-Object {
        if ($_ -match "^(Collecting|Downloading|Installing|Successfully)") {
            Write-Host "    $_" -ForegroundColor DarkGray
        }
        elseif ($_ -match "(?i)error") {
            Write-Host "    $_" -ForegroundColor Red
        }
    }
    if ($LASTEXITCODE -ne 0) {
        Exit-Fatal "pip install -r requirements.txt failed"
    }

    Write-Step "Installing mplfinance (chart renderer)..."
    & $venvPip install mplfinance -q
    if ($LASTEXITCODE -ne 0) {
        Exit-Fatal "Failed to install mplfinance"
    }

    Write-OK "All Python packages installed"
}

# =============================================================================
#  STEP 4 -- Ollama: install, start server, pull model
# =============================================================================
function Setup-Ollama {
    Write-Section "4/6" "Ollama + Vision Model"

    # Install if missing
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $installer = "$env:TEMP\OllamaSetup.exe"
        Download-File $OLLAMA_URL $installer "Ollama"
        Write-Step "Running Ollama installer silently..."
        Start-Process $installer -ArgumentList "/SILENT" -Wait
        Start-Sleep -Seconds 3
        Refresh-Path

        # Also check common install paths
        foreach ($p in @(
            "$env:LOCALAPPDATA\Programs\Ollama",
            "$env:ProgramFiles\Ollama"
        )) {
            if (Test-Path "$p\ollama.exe") {
                $env:PATH += ";$p"
                break
            }
        }
        Refresh-Path

        if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
            Exit-Fatal "Ollama installed but not found in PATH. Restart your PC and re-run setup.bat"
        }
        Write-OK "Ollama installed"
    }
    else {
        $ver = (ollama --version 2>&1 | Select-Object -First 1)
        Write-OK "Ollama already installed: $ver"
    }

    # Start server if not running
    Write-Host ""
    $serverRunning = $false
    try {
        $null = Invoke-RestMethod "http://localhost:${OLLAMA_PORT}/api/tags" -TimeoutSec 3
        $serverRunning = $true
    }
    catch { }

    if ($serverRunning) {
        Write-OK "Ollama server already running on port $OLLAMA_PORT"
    }
    else {
        Write-Step "Starting Ollama server in background..."
        $script:OllamaProc = Start-Process -FilePath "ollama" `
            -ArgumentList "serve" `
            -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput "$env:TEMP\ollama_out.log" `
            -RedirectStandardError  "$env:TEMP\ollama_err.log"

        if (-not (Wait-ForOllama)) {
            $log = Get-Content "$env:TEMP\ollama_err.log" -ErrorAction SilentlyContinue |
                   Select-Object -Last 5
            Exit-Fatal "Ollama server did not start.`n  Log: $($log -join ' | ')"
        }
        Write-OK "Ollama server is ready (PID: $($script:OllamaProc.Id))"
    }

    # Pull model if not present
    Write-Host ""
    $baseModel  = $OLLAMA_MODEL.Split(":")[0]
    $modelList  = (ollama list 2>&1) -join " "

    if ($modelList -match $baseModel) {
        Write-OK "Model '$OLLAMA_MODEL' already downloaded"
    }
    else {
        Write-Warn "Downloading model '$OLLAMA_MODEL' -- may take 10-30 min (~6-8 GB)"
        Write-Host "  Ollama progress is shown below:" -ForegroundColor DarkGray
        Write-Host ""
        Write-Cmd "ollama pull $OLLAMA_MODEL"
        ollama pull $OLLAMA_MODEL
        if ($LASTEXITCODE -ne 0) {
            $smallerModel = "moondream"
            Exit-Fatal "Failed to pull model '$OLLAMA_MODEL'. Try a smaller model by setting: set OLLAMA_MODEL=$smallerModel"
        }
        Write-Host ""
        Write-OK "Model '$OLLAMA_MODEL' is ready"
    }

    # Quick sanity check
    Write-Host ""
    Write-Step "Testing model response (may be slow on first call)..."
    $testResp = ollama run $OLLAMA_MODEL "Reply with one word: READY" 2>&1 |
                Select-Object -First 2
    if ($testResp -match "(?i)ready|ok") {
        Write-OK "Model is responding correctly"
    }
    else {
        Write-Warn "Unexpected response: $($testResp -join ' ') -- continuing anyway"
    }
}

# =============================================================================
#  STEP 5 -- Math Backtest (generates combined_results.csv)
# =============================================================================
function Run-MathBacktest {
    Write-Section "5/6" "Math Backtest -- Generating Signal Data"

    if (Test-Path "combined_results.csv") {
        $rows = (Get-Content "combined_results.csv").Count
        Write-OK "combined_results.csv already exists ($rows rows) -- skipping"
        Write-Info "Delete the file and re-run to regenerate."
        return
    }

    Write-Host ""
    Write-Info "Running math backtest on BTC + ETH (2022-2025)..."
    Write-Host "  Downloads ~100k candles on first run (5-10 min)" -ForegroundColor DarkGray
    Write-Host "  Cached locally for all future runs" -ForegroundColor DarkGray
    Write-Cmd "python run_backtest.py --both --threshold 4.5 --export results.csv"

    & $script:VenvPython run_backtest.py `
        --both `
        --threshold 4.5 `
        --start 2022-01-01 `
        --end   2025-01-01 `
        --export results.csv

    if ($LASTEXITCODE -ne 0) {
        Exit-Fatal "Math backtest failed. See output above."
    }
    if (-not (Test-Path "combined_results.csv")) {
        Exit-Fatal "Math backtest finished but combined_results.csv was not created."
    }
    $rows = (Get-Content "combined_results.csv").Count
    Write-OK "Math backtest complete -- $rows trade signals saved"
}

# =============================================================================
#  STEP 6 -- Vision Backtest
# =============================================================================
function Run-VisionBacktest {
    Write-Section "6/6" "Vision Backtest"

    Write-Host ""
    Write-Info "Analyzing $VISION_SAMPLE trades with model: $OLLAMA_MODEL"
    Write-Host "  For each trade: renders chart -> sends to Ollama -> records result" -ForegroundColor DarkGray
    Write-Cmd "python run_vision_backtest.py --sample $VISION_SAMPLE --model $OLLAMA_MODEL"

    & $script:VenvPython run_vision_backtest.py `
        --input  combined_results.csv `
        --model  $OLLAMA_MODEL `
        --sample $VISION_SAMPLE `
        --output vision_results.csv `
        --delay  0.3

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Vision backtest had errors (see above)"
        Write-Host ""
        Write-Info "Possible fixes:"
        Write-Info "  Out of memory -> use: set OLLAMA_MODEL=moondream"
        Write-Info "  Ollama stopped -> re-run setup.bat"
        Stop-OllamaIfStarted
        Read-Host "  Press ENTER to close"
        exit 1
    }

    if (Test-Path "vision_results.csv") {
        $rows = (Get-Content "vision_results.csv").Count
        Write-OK "Vision backtest complete -- vision_results.csv ($rows rows)"
    }
}

# =============================================================================
#  Summary
# =============================================================================
function Write-Summary {
    Write-Host ""
    Write-Host "  +======================================================+" -ForegroundColor Green
    Write-Host "  |                   All Done!                          |" -ForegroundColor Green
    Write-Host "  +======================================================+" -ForegroundColor Green
    Write-Host ""
    Write-OK "Git installed"
    Write-OK "Python installed"
    Write-OK "Ollama + model: $OLLAMA_MODEL"
    Write-OK "Math backtest:   combined_results.csv"
    Write-OK "Vision backtest: vision_results.csv"
    Write-Host ""
    Write-Host "  Next steps:" -ForegroundColor White
    Write-Info "Open vision_results.csv in Excel"
    Write-Info "Filter the 'agreement' column:"
    Write-Info "  AGREE trades -> higher win rate? Vision is useful!"
    Write-Host ""
    Write-Host "  Results folder: $(Get-Location)" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "  Press ENTER to close"
}

# =============================================================================
#  MAIN
# =============================================================================
Ensure-Admin
Write-Header
Install-Git
Install-Python
Setup-Repo
Setup-Ollama
Run-MathBacktest
Run-VisionBacktest
Write-Summary
Stop-OllamaIfStarted
