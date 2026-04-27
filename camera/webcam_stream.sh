#!/bin/bash
# ============================================================
#  Tachyon USB 웹캠 스트리밍 시작 스크립트
#  실행: ./webcam_stream.sh
#  브라우저에서 http://<TACHYON_IP>:<STREAM_PORT> 으로 자동 접속
# ============================================================

# config.env 에서 설정 로드
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "오류: 설정 파일이 없습니다: $CONFIG_FILE"
    echo "   config.env.example 을 복사해서 config.env 를 만드세요:"
    echo "   cp config.env.example config.env"
    exit 1
fi

# shellcheck source=config.env
source "$CONFIG_FILE"

echo "================================================"
echo "  Tachyon 웹캠 스트리밍 시작"
echo "================================================"

echo "[연결 중] Tachyon SSH 접속..."
sshpass -p "$TACHYON_PW" ssh -o StrictHostKeyChecking=no root@$TACHYON_IP bash << 'REMOTE'

# ── 1. 기존 스트림 프로세스 정리 ──────────────────────────
echo "[1/4] 기존 스트림 프로세스 정리..."
pkill -f 'ffmpeg.*video' 2>/dev/null
pkill -f 'server.py'     2>/dev/null
sleep 1

# ── 2. QMMF 서비스 정지 (카메라 리소스 해제) ───────────────
echo "[2/4] QMMF 서비스 정지..."
systemctl stop qmmf-server.service 2>/dev/null
sleep 2

# ── 3. 웹캠 장치 탐색 + 접근 대기 ─────────────────────────
echo "[3/4] 웹캠 장치 탐색 중..."
WEBCAM_DEV=""

# UVC 장치 이름으로 video 노드 찾기
for dev in /dev/video2 /dev/video3 /dev/video4 /dev/video5; do
    [ -e "$dev" ] || continue
    devname=$(cat /sys/class/video4linux/$(basename $dev)/name 2>/dev/null)
    if echo "$devname" | grep -qi "APC930\|webcam\|uvc"; then
        WEBCAM_DEV="$dev"
        echo "   웹캠 발견: $dev ($devname)"
        break
    fi
done

if [ -z "$WEBCAM_DEV" ]; then
    echo "오류: 웹캠 장치를 찾을 수 없습니다."
    echo "   USB 웹캠이 Tachyon에 연결되어 있는지 확인하세요."
    exit 1
fi

# ── 4. ffmpeg + HTTP MJPEG 서버 시작 ───────────────────────
echo "[4/4] 스트리밍 서버 시작..."
mkdir -p /tmp/tachyon_stream

# UVC 재감지 사이클 사이 빈틈을 노려 ffmpeg 시작 (최대 60초 재시도)
echo "   장치 접근 대기 중 (최대 60초)..."
FFMPEG_PID=""
for attempt in $(seq 1 60); do
    # ffmpeg 시작 시도
    nohup ffmpeg -y \
        -f v4l2 -input_format mjpeg \
        -framerate 15 -video_size 640x480 \
        -i "$WEBCAM_DEV" \
        -vf fps=15 \
        -update 1 -q:v 3 \
        /tmp/tachyon_stream/frame.jpg \
        > /tmp/tachyon_stream/ffmpeg.log 2>&1 &
    FFMPEG_PID=$!

    sleep 2

    # 실행 중인지 확인
    if kill -0 $FFMPEG_PID 2>/dev/null; then
        echo "   ffmpeg 시작 성공 (${attempt}번째 시도)"
        break
    else
        FFMPEG_PID=""
        # 장치 busy면 재시도, 다른 오류면 중단
        if grep -q "Device or resource busy" /tmp/tachyon_stream/ffmpeg.log 2>/dev/null; then
            sleep 1  # 잠시 후 재시도
        else
            echo "오류: ffmpeg 시작 실패 (장치 오류)"
            tail -3 /tmp/tachyon_stream/ffmpeg.log
            exit 1
        fi
    fi
done

if [ -z "$FFMPEG_PID" ]; then
    echo "오류: 웹캠 장치에 접근할 수 없습니다 (60초 초과)."
    echo "   Tachyon을 재부팅하고 다시 시도하세요."
    exit 1
fi

# HTTP MJPEG 서버 스크립트
cat > /tmp/tachyon_stream/server.py << 'PYEOF'
#!/usr/bin/env python3
import http.server, time, os, sys

FRAME_PATH = '/tmp/tachyon_stream/frame.jpg'

HTML_PAGE = b'''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Tachyon Webcam</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d0d0d;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .title { color: #e0e0e0; font-size: 18px; font-weight: 600; margin-bottom: 16px; }
    .frame {
      border: 2px solid #1e90ff; border-radius: 10px;
      overflow: hidden; box-shadow: 0 0 30px rgba(30,144,255,0.3);
    }
    img { display: block; max-width: 95vw; }
    .info { color: #555; font-size: 12px; margin-top: 12px; }
    .dot {
      display: inline-block; width: 8px; height: 8px;
      background: #1e90ff; border-radius: 50%; margin-right: 6px;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  </style>
</head>
<body>
  <div class="title">Tachyon Webcam Live</div>
  <div class="frame"><img src="/stream" alt="Live Stream"/></div>
  <div class="info"><span class="dot"></span>APC930 USB Webcam &mdash; 640x480 @ 15fps</div>
</body>
</html>'''

class MJPEGHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.end_headers()
            while True:
                try:
                    if not os.path.exists(FRAME_PATH):
                        time.sleep(0.05); continue
                    with open(FRAME_PATH, 'rb') as f:
                        frame = f.read()
                    if len(frame) < 100:
                        time.sleep(0.05); continue
                    hdr = (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                           + str(len(frame)).encode() + b'\r\n\r\n')
                    self.wfile.write(hdr + frame + b'\r\n')
                    time.sleep(1 / 15)
                except (BrokenPipeError, ConnectionResetError): break
                except Exception: break
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE)

if __name__ == '__main__':
    server = http.server.HTTPServer(('0.0.0.0', 8080), MJPEGHandler)
    print('MJPEG 서버 시작: http://0.0.0.0:8080', flush=True)
    server.serve_forever()
PYEOF

nohup python3 /tmp/tachyon_stream/server.py \
    > /tmp/tachyon_stream/server.log 2>&1 &
sleep 2

if pgrep -f 'server.py' > /dev/null; then
    echo ""
    echo "✅ 스트리밍 서버 시작 완료!"
    echo "   URL: http://$TACHYON_IP:$STREAM_PORT"
else
    echo "오류: HTTP 서버 시작 실패"
    tail -5 /tmp/tachyon_stream/server.log
    exit 1
fi

REMOTE

RESULT=$?
echo ""

if [ $RESULT -eq 0 ]; then
    echo "================================================"
    echo "  스트리밍 시작 완료!"
    echo "  URL: http://$TACHYON_IP:$STREAM_PORT"
    echo "================================================"
    sleep 1
    open "http://$TACHYON_IP:$STREAM_PORT"
else
    echo "❌ 스트리밍 시작 실패."
    echo "   - 웹캠이 Tachyon에 연결되어 있는지 확인하세요."
    echo "   - 문제가 지속되면 Tachyon을 재부팅 후 다시 실행하세요."
fi
