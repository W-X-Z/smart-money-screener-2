#!/bin/bash
# Mac 에서 평일 08:55 에 장중 반복 스캔(screener.loop)을 자동으로 시작하도록 launchd 에 등록한다.
#   bash tools/install_intraday.sh           # 등록 (다시 실행하면 갱신)
#   bash tools/install_intraday.sh remove    # 해제
# 로그: output/intraday.log
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.smartmoney.intraday"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/.venv/bin/python"

if [[ "${1:-}" == "remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "자동 시작을 해제했어요."
  exit 0
fi

[[ -x "$PY" ]] || { echo "$PY 가 없어요. README 의 'Mac 설치' 부터 해 주세요."; exit 1; }
[[ -f "$ROOT/.env" ]] || { echo ".env 가 없어요. 키움 키를 넣은 .env 를 먼저 만들어 주세요."; exit 1; }
mkdir -p "$ROOT/output" "$HOME/Library/LaunchAgents"

DAYS=""
for d in 1 2 3 4 5; do
  DAYS+="<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>55</integer></dict>"
done

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$PY</string><string>-u</string><string>-m</string><string>screener.loop</string>
  </array>
  <key>StartCalendarInterval</key><array>$DAYS</array>
  <key>StandardOutPath</key><string>$ROOT/output/intraday.log</string>
  <key>StandardErrorPath</key><string>$ROOT/output/intraday.log</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
</dict></plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "평일 08:55 에 장중 스캔이 자동으로 시작돼요. 로그: output/intraday.log"
echo "지금 바로 한 번 돌려 보려면: launchctl kickstart gui/$(id -u)/$LABEL"
