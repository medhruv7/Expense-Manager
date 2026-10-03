#!/usr/bin/env bash
# Install / update / uninstall a macOS LaunchAgent that keeps the server running (starts at login, restarts on crash).
#
# macOS privacy controls (TCC) stop background services from reading ~/Library/CloudStorage (OneDrive etc.),
# so the app is deployed to ~/.expense-manager/app and runs from there. Re-run this script after changing code
# to redeploy. Your data (~/.expense-manager/expenses.db) is not touched.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd)"
LABEL="com.expense-manager"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HOME_DIR="$HOME/.expense-manager"
APP="$HOME_DIR/app"
mkdir -p "$HOME_DIR" "$HOME/Library/LaunchAgents"

stop() { launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; }

if [ "${1:-}" = "uninstall" ]; then
  stop
  rm -f "$PLIST"
  echo "Removed $LABEL (deployed copy left in $APP; delete it if you like)"
  exit 0
fi

echo "Deploying to $APP ..."
mkdir -p "$APP"
rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'tests' \
  --exclude '.git' "$SRC/" "$APP/"
[ -f "$SRC/.env" ] && cp "$SRC/.env" "$APP/.env" && chmod 600 "$APP/.env"
if [ ! -x "$APP/.venv/bin/python" ]; then
  PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)
  "$PY" -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$APP/run.sh</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME_DIR/server.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/server.log</string>
</dict>
</plist>
EOF

stop
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL. Logs: $HOME_DIR/server.log"
