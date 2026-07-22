#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
AGENT=""
SKILL_DIR=""
WITH_ASR=true
INSTALL_SYSTEM=true
DRY_RUN=false

usage() {
    cat <<'EOF'
Install manuloom on a Debian/Ubuntu server.

Run this as the same Unix user that runs the Agent service.

Usage:
  ./install.sh --agent hermes
  ./install.sh --agent codex
  ./install.sh --skill-dir /absolute/path/to/agent/skills/manuloom

Options:
  --agent hermes|codex       Link this repository into a known Agent skill directory.
  --skill-dir PATH           Link to an explicit skill directory instead.
  --minimal                  Skip FunASR packages and model download.
  --skip-system-packages     Do not install apt packages.
  --dry-run                  Print actions without changing the machine.
  -h, --help                 Show this help.

The full installation downloads the CPU PyTorch runtime and the three prepared
FunASR models. It does not install an Agent, configure Feishu, or create API keys.
EOF
}

fail() {
    printf 'install: %s\n' "$1" >&2
    exit 2
}

run() {
    if [ "$DRY_RUN" = true ]; then
        printf '+'
        for item in "$@"; do
            printf ' %s' "$item"
        done
        printf '\n'
    else
        "$@"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --agent)
            [ "$#" -ge 2 ] || fail "--agent requires a value"
            AGENT=$2
            shift 2
            ;;
        --skill-dir)
            [ "$#" -ge 2 ] || fail "--skill-dir requires a value"
            SKILL_DIR=$2
            shift 2
            ;;
        --minimal)
            WITH_ASR=false
            shift
            ;;
        --skip-system-packages)
            INSTALL_SYSTEM=false
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

[ -z "$AGENT" ] || [ -z "$SKILL_DIR" ] || fail "use either --agent or --skill-dir, not both"

if [ -z "$SKILL_DIR" ]; then
    case "$AGENT" in
        hermes)
            SKILL_DIR="$HOME/.hermes/skills/manuloom"
            ;;
        codex)
            SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/manuloom"
            ;;
        "")
            fail "choose --agent hermes, --agent codex, or --skill-dir PATH"
            ;;
        *)
            fail "unsupported --agent value: $AGENT"
            ;;
    esac
fi

case "$SKILL_DIR" in
    /*) ;;
    *) fail "--skill-dir must be an absolute path" ;;
esac

if [ "$DRY_RUN" = false ] && [ "$(id -u)" -eq 0 ]; then
    fail "do not run as root; run as the Unix user that owns the Agent service"
fi

if [ "$INSTALL_SYSTEM" = true ]; then
    command -v apt-get >/dev/null 2>&1 || fail "automatic system packages require Debian/Ubuntu; use --skip-system-packages after installing them manually"
    if [ "$(id -u)" -eq 0 ]; then
        PRIV=""
    else
        command -v sudo >/dev/null 2>&1 || fail "sudo is required for apt packages; use --skip-system-packages after installing them manually"
        PRIV="sudo"
    fi
    if [ -n "$PRIV" ]; then
        run "$PRIV" apt-get update
        run "$PRIV" apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-chi-sim python3 python3-pip git
    else
        run apt-get update
        run apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-chi-sim python3 python3-pip git
    fi
fi

PYTHON=${VTM_INSTALL_PYTHON:-python3}
command -v "$PYTHON" >/dev/null 2>&1 || fail "Python 3 was not found"
if [ "$DRY_RUN" = false ]; then
    "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        || fail "Python 3.10 or newer is required"
fi

PIP_BREAK=false
if "$PYTHON" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    PIP_BREAK=true
fi

pip_user() {
    if [ "$PIP_BREAK" = true ]; then
        run "$PYTHON" -m pip install --user --break-system-packages "$@"
    else
        run "$PYTHON" -m pip install --user "$@"
    fi
}

pip_user -r "$SCRIPT_DIR/scripts/requirements.txt"

if [ "$WITH_ASR" = true ]; then
    pip_user --index-url https://download.pytorch.org/whl/cpu torch torchaudio
    pip_user -r "$SCRIPT_DIR/scripts/requirements-asr-cn.txt"
    run "$PYTHON" "$SCRIPT_DIR/scripts/video_manuscript.py" prepare-asr
fi

TARGET_PARENT=$(dirname -- "$SKILL_DIR")
if [ -e "$SKILL_DIR" ] || [ -L "$SKILL_DIR" ]; then
    if [ -d "$SKILL_DIR" ]; then
        TARGET_REAL=$(CDPATH= cd -- "$SKILL_DIR" && pwd -P)
        if [ "$TARGET_REAL" != "$SCRIPT_DIR" ]; then
            fail "skill target already exists and points elsewhere: $SKILL_DIR"
        fi
    else
        fail "skill target already exists and is not a directory: $SKILL_DIR"
    fi
else
    run mkdir -p "$TARGET_PARENT"
    run ln -s "$SCRIPT_DIR" "$SKILL_DIR"
fi

run "$SCRIPT_DIR/scripts/manuloom" doctor

cat <<EOF

Installation complete.
Skill: $SKILL_DIR

Next steps:
1. Configure your own text-model API key and optional vision-model API key.
2. Point VTM_VAULT at your Obsidian Vault.
3. Restart the Agent service, then run: scripts/manuloom doctor

No API key, Cookie, Feishu credential, or private note was created by this installer.
EOF
