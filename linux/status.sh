#!/usr/bin/env bash
# ==========================================================================
# status.sh  –  quick health-check for a CRUX-in-a-box Linux instance
# ==========================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { printf "${GREEN}✔${NC} %s\n" "$*"; }
fail() { printf "${RED}✘${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }

echo "====== CRUX-in-a-box  ·  Linux status ======"
echo ""

# ----- VNC -----
if systemctl is-active --quiet vncserver@1; then
  pass "VNC server is running"
else
  fail "VNC server is NOT running"
fi

# ----- GitHub CLI -----
if command -v gh &>/dev/null; then
  if gh auth status &>/dev/null; then
    pass "gh CLI: authenticated"
  else
    warn "gh CLI: installed but NOT authenticated"
  fi
else
  fail "gh CLI: not installed"
fi

# ----- AWS CLI -----
if command -v aws &>/dev/null; then
  if aws sts get-caller-identity &>/dev/null; then
    pass "aws CLI: authenticated"
  else
    warn "aws CLI: installed but NOT authenticated"
  fi
else
  fail "aws CLI: not installed"
fi

# ----- gogcli -----
if command -v gog &>/dev/null; then
  pass "gog CLI: installed"
  # TODO: confirm gog logged in
else
  warn "gog CLI: not installed"
fi

# ----- openclaw -----
if command -v openclaw &>/dev/null; then
  pass "openclaw: installed"
else
  fail "openclaw: not installed"
fi

# ----- Telemetry plugin -----
if [ -d "$HOME/openclaw-telemetry-hal" ]; then
  pass "Telemetry plugin repo present"
  # TODO: confirm telemetry is configured in the config
else
  fail "Telemetry plugin repo NOT found"
fi

# ----- monitor.sh -----
if pgrep -f "monitor.sh" > /dev/null; then
  pass "monitor.sh is running ($(pgrep -cf 'monitor.sh') process(es))"
else
  fail "monitor.sh is NOT running"
fi

# ----- Monitor output -----
MONITOR_DIR="$HOME/monitor_output"
if [ -d "$MONITOR_DIR" ]; then
  SCREENSHOTS=$(find "$MONITOR_DIR" -name 'screen_*.png' 2>/dev/null | wc -l)
  BACKUPS=$(find "$MONITOR_DIR" -name 'backup_*.tar.gz' 2>/dev/null | wc -l)
  HEALTH_FILE="$HOME/last_update.txt"
  if [ -f "$HEALTH_FILE" ]; then
    LAST_UPDATE=$(cat "$HEALTH_FILE")
    pass "Monitor output: $SCREENSHOTS screenshots, $BACKUPS backups (last update: $LAST_UPDATE)"
  else
    warn "Monitor output: $SCREENSHOTS screenshots, $BACKUPS backups (no health file yet)"
  fi
else
  warn "Monitor output directory not found yet"
fi

echo ""
echo "============================================="
