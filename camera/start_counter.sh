#!/bin/bash
# ============================================================
#  Tachyon 차량 카운터 시작 스크립트
#  실행: ./start_counter.sh
#  옵션: ./start_counter.sh --install   (첫 실행 시 의존성 설치)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "오류: $CONFIG_FILE 파일이 없습니다."
    echo "   cp config.env.example config.env"
    exit 1
fi

source "$CONFIG_FILE"

INSTALL_MODE=0
[ "$1" = "--install" ] && INSTALL_MODE=1

echo "================================================"
echo "  Tachyon 차량 카운터 시작"
echo "  NAS API: $NAS_API_URL"
echo "================================================"

echo "[연결 중] Tachyon SSH 접속..."

sshpass -p "$TACHYON_PW" ssh -o StrictHostKeyChecking=no root@$TACHYON_IP bash << REMOTE
set -e

# ── 의존성 설치 (--install 플래그 시에만) ──────────────────────
if [ "$INSTALL_MODE" -eq 1 ]; then
    echo "[설치] 패키지 업데이트..."
    apt-get update -qq

    echo "[설치] Python 패키지 설치 중 (처음이면 수 분 소요)..."
    pip3 install --quiet --upgrade pip
    # PyTorch ARM64 (Ubuntu 20.04용 CPU 전용)
    pip3 install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu
    pip3 install --quiet ultralytics opencv-python-headless requests
    echo "[설치] 완료"
fi

# ── 기존 카운터 프로세스 정리 ───────────────────────────────────
echo "[1/3] 기존 프로세스 정리..."
pkill -9 -f 'vehicle_counter.py' 2>/dev/null || true
pkill -9 -f 'ffmpeg.*video'      2>/dev/null || true
pkill -9 -f 'server.py'          2>/dev/null || true
sleep 2  # 파일 디스크립터 완전 해제 대기

# ── QMMF 서비스 정지 ────────────────────────────────────────────
echo "[2/3] QMMF 서비스 정지..."
systemctl stop qmmf-server.service 2>/dev/null || true
sleep 2

# ── 스크립트 파일 동기화 ────────────────────────────────────────
mkdir -p /opt/tachyon-counter/data
REMOTE

# 스크립트 파일을 Tachyon에 복사
echo "[동기화] 스크립트 파일 전송..."
sshpass -p "$TACHYON_PW" scp -o StrictHostKeyChecking=no \
    "$SCRIPT_DIR/vehicle_counter.py" \
    "$SCRIPT_DIR/stream_server.py" \
    "$SCRIPT_DIR/config.env" \
    root@$TACHYON_IP:/opt/tachyon-counter/

sshpass -p "$TACHYON_PW" ssh -o StrictHostKeyChecking=no root@$TACHYON_IP bash << REMOTE2

cd /opt/tachyon-counter

# ── 웹캠 장치 탐색 + 접근 대기 ─────────────────────────────────
echo "[3/3] 웹캠 장치 탐색..."
WEBCAM_DEV=""
for dev in /dev/video2 /dev/video3 /dev/video4 /dev/video5; do
    [ -e "\$dev" ] || continue
    devname=\$(cat /sys/class/video4linux/\$(basename \$dev)/name 2>/dev/null)
    if echo "\$devname" | grep -qi "APC930\\|webcam\\|uvc"; then
        WEBCAM_DEV="\$dev"
        echo "   웹캠: \$dev (\$devname)"
        break
    fi
done

if [ -z "\$WEBCAM_DEV" ]; then
    echo "오류: 웹캠을 찾을 수 없습니다."
    exit 1
fi

# config.env에 WEBCAM_DEV 반영
if ! grep -q "^WEBCAM_DEV=" config.env; then
    echo "WEBCAM_DEV=\"\$WEBCAM_DEV\"" >> config.env
else
    sed -i "s|^WEBCAM_DEV=.*|WEBCAM_DEV=\"\$WEBCAM_DEV\"|" config.env
fi

# 스트림 디렉토리 준비
mkdir -p /tmp/tachyon_stream

# ── 대시보드 서버 시작 ───────────────────────────────────────────
echo "대시보드 서버 시작 중..."
nohup python3 /opt/tachyon-counter/stream_server.py 8080 \
    > /tmp/tachyon_stream/server.log 2>&1 &
sleep 1

# ── 차량 카운터 시작 ────────────────────────────────────────────
echo ""
echo "차량 카운터 시작 중..."
nohup python3 /opt/tachyon-counter/vehicle_counter.py \
    > /opt/tachyon-counter/counter.log 2>&1 &
COUNTER_PID=\$!
sleep 5

if kill -0 \$COUNTER_PID 2>/dev/null; then
    echo ""
    echo "✅ 차량 카운터 시작 완료!"
    echo "   대시보드: http://$TACHYON_IP:8080"
    echo "   로그:     /opt/tachyon-counter/counter.log"
    echo "   NAS API:  $NAS_API_URL"
else
    echo "❌ 차량 카운터 시작 실패. 로그 확인:"
    tail -20 /opt/tachyon-counter/counter.log
    exit 1
fi

REMOTE2

RESULT=$?
echo ""
if [ $RESULT -eq 0 ]; then
    echo "================================================"
    echo "  시작 완료!"
    echo "  대시보드:      http://$TACHYON_IP:8080"
    echo "  NAS 통계 API:  $NAS_API_URL/api/stats/realtime"
    echo "================================================"
    sleep 1
    open "http://$TACHYON_IP:8080"
else
    echo "❌ 시작 실패. Tachyon 로그를 확인하세요."
fi
