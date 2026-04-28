#!/bin/bash
# ============================================================
#  Tachyon 차량 카운터 종료 스크립트
#  실행: ./stop_counter.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "오류: $CONFIG_FILE 파일이 없습니다."
    exit 1
fi

source "$CONFIG_FILE"

echo "================================================"
echo "  Tachyon 서버 종료"
echo "  IP: $TACHYON_IP"
echo "================================================"

sshpass -p "$TACHYON_PW" ssh -o StrictHostKeyChecking=no root@$TACHYON_IP bash << 'REMOTE'
pkill -f vehicle_counter.py 2>/dev/null && echo "[종료] vehicle_counter.py" || echo "[확인] vehicle_counter.py 실행 중 아님"
pkill -f stream_server.py   2>/dev/null && echo "[종료] stream_server.py"   || echo "[확인] stream_server.py 실행 중 아님"
pkill -f ffmpeg             2>/dev/null && echo "[종료] ffmpeg"             || echo "[확인] ffmpeg 실행 중 아님"

sleep 1
REMAINING=$(ps aux | grep -E "vehicle_counter|stream_server|ffmpeg" | grep -v grep | wc -l)
if [ "$REMAINING" -eq 0 ]; then
    echo "✓ 모든 프로세스 종료 완료"
else
    echo "⚠ 아직 남은 프로세스 있음, 강제 종료..."
    pkill -9 -f vehicle_counter.py 2>/dev/null
    pkill -9 -f stream_server.py   2>/dev/null
    pkill -9 -f ffmpeg             2>/dev/null
    echo "✓ 강제 종료 완료"
fi
REMOTE

echo "================================================"
echo "  완료"
echo "================================================"
