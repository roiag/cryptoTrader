#!/usr/bin/env bash
# =============================================================================
#  CryptoTrader — Setup & First Run
#  מושך מ-GitHub, מתקין סביבה, מוריד מודל Ollama, מריץ vision backtest
# =============================================================================

set -euo pipefail

# ── צבעים ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── סמלים ────────────────────────────────────────────────────────────────────
OK="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
ARROW="${CYAN}→${NC}"
WARN="${YELLOW}⚠${NC}"
STEP="${BLUE}◆${NC}"

# ── הגדרות ───────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/roiag/cryptoTrader.git"
REPO_DIR="cryptoTrader"
PYTHON_MIN="3.10"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2-vision}"
VISION_SAMPLE="${VISION_SAMPLE:-100}"   # כמה עסקאות לבדוק
OLLAMA_PORT=11434
OLLAMA_PID=""

# ── פונקציות עזר ─────────────────────────────────────────────────────────────

header() {
    clear
    echo ""
    echo -e "${CYAN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════╗"
    echo "  ║          CryptoTrader — Vision Backtest Setup        ║"
    echo "  ║     Multi-Agent Crypto Trading System by roiag       ║"
    echo "  ╚══════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo -e "  ${DIM}Repo:  ${REPO_URL}${NC}"
    echo -e "  ${DIM}Model: ${OLLAMA_MODEL}  |  Sample: ${VISION_SAMPLE} trades${NC}"
    echo ""
}

section() {
    echo ""
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${STEP}  ${BOLD}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

success() {
    echo -e "  ${OK}  $1"
}

fail() {
    echo -e "  ${FAIL}  ${RED}$1${NC}"
}

info() {
    echo -e "  ${ARROW}  $1"
}

warn() {
    echo -e "  ${WARN}  ${YELLOW}$1${NC}"
}

step() {
    echo -e "  ${DIM}...${NC} $1"
}

die() {
    echo ""
    echo -e "  ${FAIL}  ${RED}${BOLD}FATAL: $1${NC}"
    echo ""
    echo -e "  ${DIM}Setup aborted. Fix the error above and re-run setup.sh${NC}"
    echo ""
    cleanup_on_exit
    exit 1
}

cleanup_on_exit() {
    if [[ -n "$OLLAMA_PID" ]] && kill -0 "$OLLAMA_PID" 2>/dev/null; then
        info "Stopping background Ollama server (PID $OLLAMA_PID)..."
        kill "$OLLAMA_PID" 2>/dev/null || true
    fi
}
trap cleanup_on_exit EXIT

# ── detect OS ─────────────────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux"  ;;
        Darwin*) echo "macos"  ;;
        MINGW*|CYGWIN*|MSYS*) echo "windows" ;;
        *)       echo "unknown" ;;
    esac
}
OS=$(detect_os)

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — בדיקת תנאים מוקדמים
# ─────────────────────────────────────────────────────────────────────────────
check_prerequisites() {
    section "Step 1/6 — Checking Prerequisites"
    local all_ok=true

    # Git
    if command -v git &>/dev/null; then
        success "git $(git --version | awk '{print $3}')"
    else
        fail "git is not installed"
        info "Install from: https://git-scm.com/downloads"
        all_ok=false
    fi

    # Python
    PYTHON_CMD=""
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
                PYTHON_CMD="$cmd"
                success "Python $("$cmd" --version 2>&1 | awk '{print $2}')"
                break
            fi
        fi
    done
    if [[ -z "$PYTHON_CMD" ]]; then
        fail "Python >= ${PYTHON_MIN} is required"
        info "Install from: https://python.org/downloads"
        all_ok=false
    fi

    # curl (for Ollama health check)
    if command -v curl &>/dev/null; then
        success "curl $(curl --version | head -1 | awk '{print $2}')"
    else
        warn "curl not found — will skip Ollama health check"
    fi

    # Disk space (need at least 10 GB for model)
    if command -v df &>/dev/null; then
        available_gb=$(df -BG . 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}' || echo "?")
        if [[ "$available_gb" != "?" && "$available_gb" -lt 10 ]]; then
            warn "Low disk space: ${available_gb}GB available (model needs ~8GB)"
        else
            success "Disk space: ${available_gb}GB available"
        fi
    fi

    if [[ "$all_ok" != true ]]; then
        die "Missing required tools. Install them and re-run."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — Clone / Pull repository
# ─────────────────────────────────────────────────────────────────────────────
clone_repo() {
    section "Step 2/6 — Repository"

    if [[ -d "$REPO_DIR/.git" ]]; then
        warn "Directory '$REPO_DIR' already exists — pulling latest changes"
        cd "$REPO_DIR"
        if git pull origin master 2>&1 | grep -qE "Already up to date|Fast-forward"; then
            success "Repository is up to date"
        else
            success "Repository updated successfully"
        fi
    else
        step "Cloning ${REPO_URL} ..."
        if git clone "$REPO_URL" "$REPO_DIR" 2>&1; then
            success "Cloned into ./${REPO_DIR}"
            cd "$REPO_DIR"
        else
            die "Failed to clone repository. Check your internet connection."
        fi
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — Python virtual environment + dependencies
# ─────────────────────────────────────────────────────────────────────────────
setup_python() {
    section "Step 3/6 — Python Environment"

    # Virtual environment
    if [[ -d ".venv" ]]; then
        success "Virtual environment already exists"
    else
        step "Creating virtual environment..."
        if $PYTHON_CMD -m venv .venv; then
            success "Virtual environment created (.venv/)"
        else
            die "Failed to create virtual environment"
        fi
    fi

    # Activate
    if [[ "$OS" == "windows" ]]; then
        VENV_PYTHON=".venv/Scripts/python"
        VENV_PIP=".venv/Scripts/pip"
    else
        VENV_PYTHON=".venv/bin/python"
        VENV_PIP=".venv/bin/pip"
    fi

    # Upgrade pip silently
    step "Upgrading pip..."
    $VENV_PYTHON -m pip install --upgrade pip -q

    # Install requirements
    if [[ ! -f "requirements.txt" ]]; then
        die "requirements.txt not found"
    fi

    step "Installing dependencies from requirements.txt..."
    echo ""

    # Show progress per package
    total=$(grep -c "^[^#]" requirements.txt 2>/dev/null || echo "?")
    info "Installing ${total} packages..."
    echo ""

    if $VENV_PIP install -r requirements.txt 2>&1 | while IFS= read -r line; do
        if echo "$line" | grep -qiE "^(Collecting|Downloading|Installing)"; then
            pkg=$(echo "$line" | awk '{print $2}' | cut -d'-' -f1)
            echo -e "    ${DIM}${line}${NC}"
        elif echo "$line" | grep -qiE "error|failed"; then
            echo -e "    ${RED}${line}${NC}"
        fi
    done; then
        echo ""
        success "All Python dependencies installed"
    else
        die "pip install failed. Check the output above."
    fi

    # Install mplfinance separately (needed for chart renderer)
    step "Installing mplfinance (chart renderer)..."
    if $VENV_PIP install mplfinance -q; then
        success "mplfinance installed"
    else
        die "Failed to install mplfinance"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — Ollama installation + model download
# ─────────────────────────────────────────────────────────────────────────────
setup_ollama() {
    section "Step 4/6 — Ollama Setup"

    # Check if already installed
    if command -v ollama &>/dev/null; then
        success "Ollama already installed ($(ollama --version 2>&1 | head -1))"
    else
        info "Ollama not found — installing..."
        echo ""

        case "$OS" in
            linux)
                step "Downloading Ollama installer for Linux..."
                if curl -fsSL https://ollama.com/install.sh | sh; then
                    success "Ollama installed successfully"
                else
                    die "Ollama installation failed. Visit https://ollama.com to install manually."
                fi
                ;;
            macos)
                if command -v brew &>/dev/null; then
                    step "Installing via Homebrew..."
                    brew install ollama && success "Ollama installed via Homebrew"
                else
                    fail "Homebrew not found"
                    die "Install Ollama manually from https://ollama.com/download/mac"
                fi
                ;;
            windows)
                warn "Automatic Ollama install not supported on Windows via this script."
                echo ""
                echo -e "  ${YELLOW}Please install Ollama manually:${NC}"
                echo -e "  ${ARROW}  1. Go to https://ollama.com/download"
                echo -e "  ${ARROW}  2. Download and run OllamaSetup.exe"
                echo -e "  ${ARROW}  3. Re-run this script after installation"
                echo ""
                read -r -p "  Press ENTER when Ollama is installed..." _
                if ! command -v ollama &>/dev/null; then
                    die "Ollama still not found. Make sure it's in your PATH."
                fi
                success "Ollama detected"
                ;;
            *)
                die "Unsupported OS. Install Ollama manually from https://ollama.com"
                ;;
        esac
    fi

    # Start Ollama server (if not already running)
    echo ""
    info "Checking Ollama server..."
    if curl -s "http://localhost:${OLLAMA_PORT}/api/tags" &>/dev/null; then
        success "Ollama server is already running on port ${OLLAMA_PORT}"
    else
        step "Starting Ollama server in background..."
        ollama serve > /tmp/ollama_server.log 2>&1 &
        OLLAMA_PID=$!

        # Wait for server to be ready (max 30s)
        echo -ne "  ${ARROW}  Waiting for Ollama to start"
        for i in $(seq 1 30); do
            if curl -s "http://localhost:${OLLAMA_PORT}/api/tags" &>/dev/null; then
                echo ""
                success "Ollama server is ready (PID: ${OLLAMA_PID})"
                break
            fi
            echo -n "."
            sleep 1
            if [[ $i -eq 30 ]]; then
                echo ""
                die "Ollama server did not start within 30 seconds. Check /tmp/ollama_server.log"
            fi
        done
    fi

    # Pull vision model
    echo ""
    info "Checking model: ${BOLD}${OLLAMA_MODEL}${NC}"

    # Check if model already downloaded
    if ollama list 2>/dev/null | grep -q "^${OLLAMA_MODEL%%:*}"; then
        success "Model '${OLLAMA_MODEL}' already downloaded"
    else
        echo ""
        echo -e "  ${WARN}  ${YELLOW}Model '${OLLAMA_MODEL}' not found locally — downloading now${NC}"
        echo -e "  ${DIM}  This may take several minutes depending on your connection speed${NC}"
        echo -e "  ${DIM}  Model size: ~5-8 GB${NC}"
        echo ""
        echo -e "${CYAN}  ┌──────────────────────────────────────────────────────┐${NC}"
        echo -e "${CYAN}  │  ollama pull ${OLLAMA_MODEL}                          ${NC}"
        echo -e "${CYAN}  └──────────────────────────────────────────────────────┘${NC}"
        echo ""

        if ollama pull "${OLLAMA_MODEL}"; then
            echo ""
            success "Model '${OLLAMA_MODEL}' downloaded successfully"
        else
            echo ""
            die "Failed to pull model '${OLLAMA_MODEL}'. Check your internet connection or try a different model:
       OLLAMA_MODEL=qwen2-vl:7b ./setup.sh
       OLLAMA_MODEL=moondream   ./setup.sh"
        fi
    fi

    # Quick model sanity check
    echo ""
    step "Running model smoke test..."
    echo ""
    echo -e "${CYAN}  ┌──────────────────────────────────────────────────────┐${NC}"
    echo -e "${CYAN}  │  Testing model response...                           │${NC}"
    echo -e "${CYAN}  └──────────────────────────────────────────────────────┘${NC}"

    test_response=$(ollama run "${OLLAMA_MODEL}" "Reply with exactly: OK" 2>&1 | head -3)
    if echo "$test_response" | grep -qi "ok"; then
        success "Model responded correctly"
    else
        warn "Model response unexpected: ${test_response:0:80}"
        warn "Vision quality may be reduced — continuing anyway"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — Math Backtest (generates the CSV that vision backtest needs)
# ─────────────────────────────────────────────────────────────────────────────
run_math_backtest() {
    section "Step 5/6 — Math Backtest (Generating Signal Data)"

    if [[ -f "combined_results.csv" ]]; then
        row_count=$(wc -l < "combined_results.csv" 2>/dev/null || echo "?")
        success "combined_results.csv already exists (${row_count} rows — skipping math backtest)"
        info "Delete combined_results.csv and re-run to regenerate."
        return
    fi

    echo ""
    info "Running math backtest on BTC + ETH (2022-2025)..."
    echo -e "  ${DIM}First run downloads ~100k candles — may take 5-10 minutes${NC}"
    echo -e "  ${DIM}Subsequent runs use local cache and are instant${NC}"
    echo ""
    echo -e "${CYAN}  ┌──────────────────────────────────────────────────────┐${NC}"
    echo -e "${CYAN}  │  python run_backtest.py --both --export results.csv  │${NC}"
    echo -e "${CYAN}  └──────────────────────────────────────────────────────┘${NC}"
    echo ""

    if $VENV_PYTHON run_backtest.py \
        --both \
        --threshold 4.5 \
        --start 2022-01-01 \
        --end 2025-01-01 \
        --export results.csv; then

        # run_backtest.py creates combined_results.csv automatically
        if [[ -f "combined_results.csv" ]]; then
            rows=$(wc -l < "combined_results.csv")
            echo ""
            success "Math backtest complete — ${rows} trade signals saved to combined_results.csv"
        else
            die "Math backtest finished but combined_results.csv was not created."
        fi
    else
        die "Math backtest failed. Check the output above."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6 — Vision Backtest
# ─────────────────────────────────────────────────────────────────────────────
run_vision_backtest() {
    section "Step 6/6 — Vision Backtest"

    echo ""
    info "Analyzing ${VISION_SAMPLE} trades with vision model: ${BOLD}${OLLAMA_MODEL}${NC}"
    echo -e "  ${DIM}For each trade: renders chart → sends to Ollama → records result${NC}"
    echo ""
    echo -e "${CYAN}  ┌──────────────────────────────────────────────────────────────────┐${NC}"
    echo -e "${CYAN}  │  python run_vision_backtest.py --sample ${VISION_SAMPLE} --model ${OLLAMA_MODEL}  │${NC}"
    echo -e "${CYAN}  └──────────────────────────────────────────────────────────────────┘${NC}"
    echo ""

    if $VENV_PYTHON run_vision_backtest.py \
        --input combined_results.csv \
        --model "${OLLAMA_MODEL}" \
        --sample "${VISION_SAMPLE}" \
        --output vision_results.csv \
        --delay 0.3; then

        echo ""
        if [[ -f "vision_results.csv" ]]; then
            rows=$(wc -l < "vision_results.csv" 2>/dev/null || echo "?")
            success "Vision backtest complete — results saved to vision_results.csv (${rows} rows)"
        else
            warn "vision_results.csv not found — check output above"
        fi
    else
        fail "Vision backtest encountered errors"
        echo ""
        echo -e "  ${DIM}Possible causes:${NC}"
        echo -e "  ${ARROW}  Ollama ran out of memory — try: ${YELLOW}OLLAMA_MODEL=moondream ./setup.sh${NC}"
        echo -e "  ${ARROW}  Model not responding     — run: ${YELLOW}ollama serve${NC} in another terminal"
        echo -e "  ${ARROW}  combined_results.csv missing — delete it and re-run step 5"
        exit 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════╗"
    echo "  ║                  Setup Complete!                     ║"
    echo "  ╚══════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    echo -e "  ${OK}  Repository cloned/updated"
    echo -e "  ${OK}  Python environment ready (.venv/)"
    echo -e "  ${OK}  Ollama model: ${OLLAMA_MODEL}"
    echo -e "  ${OK}  Math backtest: combined_results.csv"
    echo -e "  ${OK}  Vision backtest: vision_results.csv"

    echo ""
    echo -e "  ${BOLD}Next steps:${NC}"
    echo -e "  ${ARROW}  Open ${BOLD}vision_results.csv${NC} to see AGREE vs DISAGREE win rates"
    echo -e "  ${ARROW}  Run full backtest: ${YELLOW}python run_backtest.py --both${NC}"
    echo -e "  ${ARROW}  Run full vision:   ${YELLOW}python run_vision_backtest.py${NC}"
    echo -e "  ${ARROW}  Start live bot:    ${YELLOW}python scheduler.py${NC}"
    echo ""
    echo -e "  ${DIM}Results directory:  $(pwd)${NC}"
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
main() {
    header
    check_prerequisites
    clone_repo
    setup_python
    setup_ollama
    run_math_backtest
    run_vision_backtest
    print_summary
}

main "$@"
