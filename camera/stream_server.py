#!/usr/bin/env python3
"""
Tachyon Traffic Monitor — HTTP Server
MJPEG 스트림 + 실시간 통계 대시보드를 서빙한다.
"""

import http.server
import json
import os
import time

FRAME_PATH = "/tmp/tachyon_stream/frame.jpg"
STATS_PATH = "/tmp/tachyon_stream/stats.json"

# ── HTML 대시보드 ──────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TACHYON TRAFFIC MONITOR</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow+Condensed:wght@300;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:      #04080a;
    --panel:   rgba(6, 16, 20, 0.92);
    --border:  rgba(0, 220, 160, 0.18);
    --accent:  #00dca0;
    --amber:   #f5a623;
    --dim:     rgba(255,255,255,0.28);
    --car:     #00dca0;
    --bus:     #f5a623;
    --mono:    'Share Tech Mono', monospace;
    --sans:    'Barlow Condensed', sans-serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: #c8e6e0;
    font-family: var(--sans);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* scan-line overlay */
  body::after {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.06) 2px,
      rgba(0,0,0,0.06) 4px
    );
    pointer-events: none;
    z-index: 100;
  }

  /* ── 헤더 ── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 20px;
    border-bottom: 1px solid var(--border);
    background: rgba(0,0,0,0.4);
    flex-shrink: 0;
  }

  .brand {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  .brand-title {
    font-family: var(--mono);
    font-size: 13px;
    letter-spacing: 0.22em;
    color: var(--accent);
    text-transform: uppercase;
  }
  .brand-sub {
    font-size: 11px;
    letter-spacing: 0.1em;
    color: var(--dim);
    text-transform: uppercase;
  }

  .live-badge {
    display: flex;
    align-items: center;
    gap: 7px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.15em;
    color: var(--accent);
  }
  .live-dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent);
    animation: blink 1.4s ease-in-out infinite;
  }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }

  /* ── 메인 레이아웃 ── */
  main {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 260px;
    gap: 0;
    overflow: hidden;
  }

  /* ── 스트림 영역 ── */
  .stream-wrap {
    position: relative;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }

  .stream-wrap img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }

  /* 스트림 오버레이 — 카메라 코너 마커 */
  .stream-wrap::before,
  .stream-wrap::after {
    content: '';
    position: absolute;
    width: 20px; height: 20px;
    border-color: var(--accent);
    border-style: solid;
    opacity: 0.6;
    pointer-events: none;
  }
  .stream-wrap::before { top: 12px; left: 12px; border-width: 2px 0 0 2px; }
  .stream-wrap::after  { bottom: 12px; right: 12px; border-width: 0 2px 2px 0; }

  /* 카운팅 라인 표시 */
  .count-line {
    position: absolute;
    top: 0; bottom: 0;
    left: 50%;
    width: 1px;
    background: rgba(245, 166, 35, 0.25);
    border-left: 1px dashed rgba(245, 166, 35, 0.4);
    pointer-events: none;
  }

  /* 스트림 노신호 */
  .no-signal {
    position: absolute;
    font-family: var(--mono);
    font-size: 12px;
    color: rgba(255,255,255,0.2);
    letter-spacing: 0.15em;
    display: none;
  }

  /* ── 사이드 패널 ── */
  .sidebar {
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .panel {
    padding: 16px;
    border-bottom: 1px solid var(--border);
  }

  .panel-label {
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.25em;
    color: var(--dim);
    text-transform: uppercase;
    margin-bottom: 12px;
  }

  /* ── 총계 카드 ── */
  .total-count {
    font-family: var(--mono);
    font-size: 52px;
    line-height: 1;
    color: var(--accent);
    text-shadow: 0 0 20px rgba(0,220,160,0.4);
    letter-spacing: -2px;
  }
  .total-label {
    font-size: 11px;
    letter-spacing: 0.12em;
    color: var(--dim);
    margin-top: 4px;
    text-transform: uppercase;
  }

  /* ── 차종별 카운트 ── */
  .vehicle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }
  .vehicle-row:last-child { margin-bottom: 0; }

  .v-type {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    min-width: 50px;
  }
  .v-type .dot {
    width: 6px; height: 6px; border-radius: 50%;
  }
  .v-type.car  { color: var(--car); }
  .v-type.car  .dot { background: var(--car); box-shadow: 0 0 6px var(--car); }
  .v-type.bus  { color: var(--bus); }
  .v-type.bus  .dot { background: var(--bus); box-shadow: 0 0 6px var(--bus); }

  .v-dirs {
    display: flex;
    gap: 6px;
    align-items: center;
    font-family: var(--mono);
    font-size: 13px;
  }
  .v-dir {
    display: flex;
    align-items: center;
    gap: 3px;
    color: rgba(255,255,255,0.55);
  }
  .v-dir .arrow { font-size: 10px; opacity: 0.5; }
  .v-dir .num   { min-width: 24px; text-align: right; color: #e0f0eb; }

  .v-total {
    font-family: var(--mono);
    font-size: 18px;
    color: #fff;
    min-width: 28px;
    text-align: right;
  }

  /* ── 방향 분포 바 ── */
  .dir-bar-wrap { margin-top: 8px; }
  .dir-bar-label {
    display: flex;
    justify-content: space-between;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--dim);
    margin-bottom: 4px;
  }
  .dir-bar {
    height: 4px;
    border-radius: 2px;
    background: rgba(255,255,255,0.07);
    overflow: hidden;
    position: relative;
  }
  .dir-bar-left {
    position: absolute; left: 0; top: 0; height: 100%;
    background: linear-gradient(90deg, #3af0c0, #00dca0);
    transition: width 0.6s ease;
    border-radius: 2px;
  }
  .dir-bar-right {
    position: absolute; right: 0; top: 0; height: 100%;
    background: linear-gradient(270deg, #f5a623, #f07b23);
    transition: width 0.6s ease;
    border-radius: 2px;
  }

  /* ── 최근 이벤트 ── */
  .events-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
    max-height: 220px;
    overflow-y: auto;
  }
  .events-list::-webkit-scrollbar { width: 2px; }
  .events-list::-webkit-scrollbar-thumb { background: var(--border); }

  .event-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    border-radius: 3px;
    background: rgba(255,255,255,0.03);
    border-left: 2px solid transparent;
    animation: fadeIn 0.3s ease;
    font-size: 12px;
  }
  .event-item.car { border-color: var(--car); }
  .event-item.bus { border-color: var(--bus); }

  @keyframes fadeIn { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: none; } }

  .ev-time  { font-family: var(--mono); font-size: 10px; color: var(--dim); min-width: 44px; }
  .ev-type  { font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; font-size: 11px; }
  .ev-type.car { color: var(--car); }
  .ev-type.bus { color: var(--bus); }
  .ev-dir   { font-family: var(--mono); font-size: 12px; color: rgba(255,255,255,0.4); }

  /* ── 세션 정보 ── */
  .session-info {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--dim);
    line-height: 1.7;
  }
  .session-info span { color: rgba(200,230,225,0.7); }

  /* ── 업데이트 시각 ── */
  .updated-at {
    font-family: var(--mono);
    font-size: 9px;
    color: rgba(255,255,255,0.15);
    letter-spacing: 0.1em;
    padding: 8px 16px;
    margin-top: auto;
    text-align: right;
  }
</style>
</head>
<body>

<header>
  <div class="brand">
    <span class="brand-title">Tachyon Traffic Monitor</span>
    <span class="brand-sub">YOLO11n &middot; ByteTrack</span>
  </div>
  <div class="live-badge">
    <span class="live-dot"></span>
    LIVE
  </div>
</header>

<main>
  <div class="stream-wrap">
    <div class="count-line"></div>
    <img id="stream" src="/stream" alt="live" onerror="this.style.opacity=0.1">
    <span class="no-signal">NO SIGNAL</span>
  </div>

  <aside class="sidebar">
    <!-- 총계 -->
    <div class="panel">
      <div class="panel-label">Total Crossings</div>
      <div class="total-count" id="total">0</div>
      <div class="total-label">vehicles today</div>
    </div>

    <!-- 차종별 -->
    <div class="panel">
      <div class="panel-label">By Vehicle</div>

      <div class="vehicle-row">
        <div class="v-type car"><span class="dot"></span>Car</div>
        <div class="v-dirs">
          <div class="v-dir"><span class="arrow">←</span><span class="num" id="car-left">0</span></div>
          <div class="v-dir"><span class="arrow">→</span><span class="num" id="car-right">0</span></div>
        </div>
        <div class="v-total" id="car-total">0</div>
      </div>

      <div class="vehicle-row">
        <div class="v-type bus"><span class="dot"></span>Bus</div>
        <div class="v-dirs">
          <div class="v-dir"><span class="arrow">←</span><span class="num" id="bus-left">0</span></div>
          <div class="v-dir"><span class="arrow">→</span><span class="num" id="bus-right">0</span></div>
        </div>
        <div class="v-total" id="bus-total">0</div>
      </div>

      <!-- 방향 분포 -->
      <div class="dir-bar-wrap" style="margin-top:14px">
        <div class="dir-bar-label">
          <span>← LEFT</span>
          <span>RIGHT →</span>
        </div>
        <div class="dir-bar">
          <div class="dir-bar-left"  id="bar-left"  style="width:50%"></div>
          <div class="dir-bar-right" id="bar-right" style="width:50%"></div>
        </div>
      </div>
    </div>

    <!-- 최근 이벤트 -->
    <div class="panel" style="flex:1">
      <div class="panel-label">Recent Events</div>
      <div class="events-list" id="events">
        <div style="font-family:var(--mono);font-size:11px;color:var(--dim)">Waiting for detection...</div>
      </div>
    </div>

    <!-- 세션 정보 -->
    <div class="panel">
      <div class="panel-label">Session</div>
      <div class="session-info">
        Started: <span id="session-start">—</span><br>
        Camera: <span>Tachyon &middot; APC930</span><br>
        Model: <span>YOLO11n &middot; 320px</span>
      </div>
    </div>

    <div class="updated-at">UPDATED <span id="updated">—</span></div>
  </aside>
</main>

<script>
const DIR_ARROW = { left: '←', right: '→' };
let prevTotal = 0;

async function fetchStats() {
  try {
    const r = await fetch('/stats');
    if (!r.ok) return;
    const d = await r.json();

    const car = d.counts?.car || { left: 0, right: 0 };
    const bus = d.counts?.bus || { left: 0, right: 0 };
    const total = d.total || 0;

    document.getElementById('total').textContent = total;
    document.getElementById('car-left').textContent  = car.left;
    document.getElementById('car-right').textContent = car.right;
    document.getElementById('car-total').textContent = car.left + car.right;
    document.getElementById('bus-left').textContent  = bus.left;
    document.getElementById('bus-right').textContent = bus.right;
    document.getElementById('bus-total').textContent = bus.left + bus.right;

    // 방향 분포 바
    const leftTotal  = (car.left  || 0) + (bus.left  || 0);
    const rightTotal = (car.right || 0) + (bus.right || 0);
    const t = leftTotal + rightTotal || 1;
    document.getElementById('bar-left').style.width  = (leftTotal  / t * 100) + '%';
    document.getElementById('bar-right').style.width = (rightTotal / t * 100) + '%';

    // 세션 시작
    if (d.session_start) {
      document.getElementById('session-start').textContent = d.session_start.slice(11, 19);
    }
    document.getElementById('updated').textContent = d.updated || '—';

    // 최근 이벤트
    const events = d.recent || [];
    if (events.length > 0) {
      const list = document.getElementById('events');
      list.innerHTML = [...events].reverse().map(e => `
        <div class="event-item ${e.vehicle}">
          <span class="ev-time">${e.time}</span>
          <span class="ev-type ${e.vehicle}">${e.vehicle.toUpperCase()}</span>
          <span class="ev-dir">${DIR_ARROW[e.direction] || e.direction}</span>
        </div>
      `).join('');
    }

    // 총계 변화 시 숫자 깜빡임
    if (total !== prevTotal) {
      const el = document.getElementById('total');
      el.style.textShadow = '0 0 30px rgba(0,220,160,0.9)';
      setTimeout(() => el.style.textShadow = '0 0 20px rgba(0,220,160,0.4)', 300);
      prevTotal = total;
    }
  } catch(e) {}
}

fetchStats();
setInterval(fetchStats, 2000);
</script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path == "/stream":
            self._serve_mjpeg()
        elif self.path == "/stats":
            self._serve_stats()
        else:
            self._serve_html()

    def _serve_html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_stats(self):
        try:
            with open(STATS_PATH, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            data = b'{"total":0,"counts":{},"recent":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        while True:
            try:
                if not os.path.exists(FRAME_PATH):
                    time.sleep(0.05)
                    continue
                with open(FRAME_PATH, "rb") as f:
                    frame = f.read()
                if len(frame) < 100:
                    time.sleep(0.05)
                    continue
                hdr = (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                )
                self.wfile.write(hdr + frame + b"\r\n")
                time.sleep(1 / 15)
            except (BrokenPipeError, ConnectionResetError):
                break
            except Exception:
                break


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = http.server.HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Dashboard: http://0.0.0.0:{port}", flush=True)
    server.serve_forever()
