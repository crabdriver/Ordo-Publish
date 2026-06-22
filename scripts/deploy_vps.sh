#!/usr/bin/env bash
# deployment script for ordo-publish on Debian/Ubuntu VPS

set -euo pipefail

echo "=================================================="
echo "      ordo-publish VPS Deployment Script          "
echo "=================================================="

# Check if run as root
if [ "$EUID" -ne 0 ]; then
  echo "[WARNING] Please run this script as root or with sudo if package installation fails."
fi

# Detect OS
if [ -f /etc/debian_version ]; then
  echo "[INFO] Debian/Ubuntu detected. Installing dependencies..."
  apt-get update -y
  
  # Install general requirements & graphics stack
  apt-get install -y \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    wget \
    curl \
    unzip \
    psmisc

  # Install Google Chrome if not already present
  if ! command -v google-chrome &> /dev/null && ! command -v google-chrome-stable &> /dev/null; then
    echo "[INFO] Google Chrome not found. Installing google-chrome-stable..."
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/googlechrome-keyring.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
    apt-get update -y
    apt-get install -y google-chrome-stable || apt-get install -y chromium-browser || echo "[WARN] Failed to install chrome via apt. Please install manually."
  else
    echo "[OK] Chrome binary already installed."
  fi
else
  echo "[ERROR] Unsupported OS. Please install packages (xvfb, x11vnc, novnc, websockify, python3-venv, nodejs, npm, chrome) manually."
  exit 1
fi

# Set repository base directory
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[INFO] Setting up directories in repository root: $REPO_DIR"

mkdir -p "$REPO_DIR/data/inbox"
mkdir -p "$REPO_DIR/data/jobs"
mkdir -p "$REPO_DIR/data/logs"
mkdir -p "$REPO_DIR/covers"
mkdir -p "$REPO_DIR/browser/profile"

# Setup Python virtual environment
echo "[INFO] Configuring Python virtual environment..."
if [ ! -d "$REPO_DIR/.venv" ]; then
  python3 -m venv "$REPO_DIR/.venv"
fi

# Activate virtualenv and install packages
echo "[INFO] Installing Python dependencies..."
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
if [ -f "$REPO_DIR/requirements.txt" ]; then
  "$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"
fi

# Setup symbolic link for noVNC if needed
if [ -d /usr/share/novnc ] && [ ! -f /usr/share/novnc/index.html ] && [ -f /usr/share/novnc/vnc.html ]; then
  echo "[INFO] Creating index.html symlink for noVNC..."
  ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html || true
fi

echo "=================================================="
echo "[SUCCESS] Deployment setup completed successfully!"
echo "To run the worker task daemon, use:"
echo "  $REPO_DIR/.venv/bin/python ordo_worker.py daemon"
echo ""
echo "To start the headless debug Chrome with virtual graphics support, use:"
echo "  $REPO_DIR/.venv/bin/python ordo_worker.py start-browser --xvfb"
echo "=================================================="
