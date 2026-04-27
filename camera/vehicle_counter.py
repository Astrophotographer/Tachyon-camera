#!/usr/bin/env python3
"""
Tachyon Vehicle Counter
-----------------------
YOLO11n으로 차량(car/bus)을 탐지하고 ByteTrack으로 추적한다.
카운팅 라인 교차 시 차종·방향을 판별해 NAS API로 전송한다.
"""

import json
import os
import queue
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import subprocess

import cv2
import numpy as np
import requests
from ultralytics import YOLO

# ── 환경 변수 로드 ──────────────────────────────────────────────
def load_config() -> dict:
    config_path = Path(__file__).parent / "config.env"
    if not config_path.exists():
        print(f"오류: {config_path} 파일이 없습니다.")
        print("config.env.example을 복사해서 config.env를 만드세요.")
        sys.exit(1)

    cfg: dict = {}
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip().strip('"')
    return cfg


CONFIG = load_config()

NAS_API_URL   = CONFIG.get("NAS_API_URL", "http://192.168.0.100:8000")
API_SECRET    = CONFIG.get("API_SECRET", "")
CAMERA_ID     = CONFIG.get("CAMERA_ID", "tachyon-01")
COUNTING_LINE = float(CONFIG.get("COUNTING_LINE_X", "0.5"))
YOLO_MODEL    = CONFIG.get("YOLO_MODEL", "yolo11n.pt")
YOLO_IMGSZ    = int(CONFIG.get("YOLO_IMGSZ", "320"))
WEBCAM_DEV    = CONFIG.get("WEBCAM_DEV", "/dev/video2")
STREAM_FRAME  = CONFIG.get("STREAM_FRAME", "/tmp/tachyon_stream/frame.jpg")

# COCO 클래스: car=2, bus=5, truck=7(→ car로 통합)
VEHICLE_CLASSES = {2: "car", 5: "bus", 7: "car"}

COOLDOWN_SEC  = 3.0   # 동일 ID 재교차 방지 쿨다운 (초)
FRAME_SKIP    = 3     # 매 N번째 프레임만 추론
CONF_THRESH   = 0.45  # YOLO 신뢰도 임계값
BUFFER_PATH   = Path(__file__).parent / "data" / "offline_buffer.jsonl"
STATS_PATH    = Path(STREAM_FRAME).parent / "stats.json"

# 차종별 바운딩 박스 색상 (BGR)
BOX_COLORS = {
    "car": (0, 200, 0),    # 초록
    "bus": (255, 140, 0),  # 주황
}


# ── 데이터 클래스 ───────────────────────────────────────────────
@dataclass
class CrossingEvent:
    vehicle: str
    direction: str
    track_id: int
    confidence: float
    camera_id: str
    timestamp: str


# ── NAS 전송 큐 (오프라인 버퍼 포함) ────────────────────────────
class EventSender:
    """
    별도 스레드에서 NAS API로 이벤트를 전송한다.
    NAS가 오프라인이면 로컬 파일에 버퍼링하고 재연결 시 재전송한다.
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        BUFFER_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._restore_buffer()
        threading.Thread(target=self._worker, daemon=True).start()

    def _restore_buffer(self):
        if BUFFER_PATH.exists():
            with open(BUFFER_PATH) as f:
                for line in f:
                    try:
                        self._queue.put(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        pass
            BUFFER_PATH.unlink()
            print(f"[EventSender] 오프라인 버퍼 {self._queue.qsize()}건 복원")

    def send(self, event: CrossingEvent):
        self._queue.put(asdict(event))

    def _worker(self):
        pending: list[dict] = []
        headers = {"X-Api-Secret": API_SECRET, "Content-Type": "application/json"}

        while True:
            # 큐에서 이벤트 수집 (최대 1초 대기)
            try:
                item = self._queue.get(timeout=1.0)
                pending.append(item)
            except queue.Empty:
                pass

            if not pending:
                continue

            try:
                if len(pending) == 1:
                    resp = requests.post(
                        f"{NAS_API_URL}/api/crossing",
                        json=pending[0], headers=headers, timeout=5,
                    )
                else:
                    resp = requests.post(
                        f"{NAS_API_URL}/api/crossings/batch",
                        json={"events": pending}, headers=headers, timeout=10,
                    )
                resp.raise_for_status()
                pending.clear()
            except Exception as e:
                print(f"[EventSender] NAS 전송 실패 ({e}), {len(pending)}건 버퍼링")
                with open(BUFFER_PATH, "a") as f:
                    for item in pending:
                        f.write(json.dumps(item) + "\n")
                pending.clear()
                time.sleep(5)


# ── 세션 통계 추적 ─────────────────────────────────────────────
class StatsTracker:
    def __init__(self):
        self.session_start = datetime.now().isoformat()
        self.counts: dict = {
            "car":  {"left": 0, "right": 0},
            "bus":  {"left": 0, "right": 0},
        }
        self.recent_events: list = []   # 최근 20건
        self._last_write = 0.0

    def record(self, vehicle: str, direction: str):
        self.counts.setdefault(vehicle, {"left": 0, "right": 0})
        self.counts[vehicle][direction] += 1
        event = {
            "vehicle": vehicle,
            "direction": direction,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        self.recent_events.append(event)
        if len(self.recent_events) > 20:
            self.recent_events.pop(0)
        self._flush()

    def _flush(self):
        now = time.time()
        if now - self._last_write < 1.0:
            return
        self._last_write = now
        total = sum(
            v["left"] + v["right"] for v in self.counts.values()
        )
        data = {
            "session_start": self.session_start,
            "counts": self.counts,
            "total": total,
            "recent": self.recent_events[-10:],
            "updated": datetime.now().strftime("%H:%M:%S"),
        }
        try:
            tmp = str(STATS_PATH) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, STATS_PATH)
        except Exception:
            pass

    def flush_periodic(self):
        """주기적으로 강제 플러시 (타이머용)"""
        self._last_write = 0.0
        self._flush()


# ── 방향 판단 ───────────────────────────────────────────────────
class DirectionTracker:
    """
    각 track_id의 centroid x 이동 방향으로 좌/우를 판단한다.
    카운팅 라인(line_x_ratio)을 교차할 때만 이벤트를 발생시킨다.
    """

    def __init__(self, line_x_ratio: float):
        self._ratio = line_x_ratio
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        self._cooldown: dict[int, float] = {}

    def update(self, track_id: int, cx: float, frame_width: int) -> Optional[str]:
        line_x = frame_width * self._ratio
        history = self._history[track_id]
        history.append(cx)

        if len(history) < 2:
            return None

        now = time.time()
        if now - self._cooldown.get(track_id, 0) < COOLDOWN_SEC:
            return None

        prev_x, curr_x = history[-2], history[-1]
        if prev_x < line_x <= curr_x:
            direction = "right"
        elif prev_x > line_x >= curr_x:
            direction = "left"
        else:
            return None

        self._cooldown[track_id] = now
        return direction

    def cleanup(self, active_ids: set):
        stale = set(self._history) - active_ids
        for tid in stale:
            del self._history[tid]
            self._cooldown.pop(tid, None)


# ── 메인 ────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  Tachyon Vehicle Counter")
    print(f"  모델: {YOLO_MODEL}  추론크기: {YOLO_IMGSZ}")
    print(f"  카운팅 라인: {COUNTING_LINE * 100:.0f}%")
    print(f"  NAS: {NAS_API_URL}")
    print("=" * 50)

    yolo = YOLO(YOLO_MODEL)
    dir_tracker = DirectionTracker(COUNTING_LINE)
    sender = EventSender()
    stats = StatsTracker()

    # ffmpeg 서브프로세스로 웹캠 접근 (Tachyon에서 OpenCV V4L2/FFMPEG 백엔드 불안정)
    # ffmpeg가 V4L2 디바이스를 직접 열고, bgr24 rawvideo를 stdout 파이프로 전달
    FRAME_W, FRAME_H = 640, 480
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "v4l2", "-input_format", "mjpeg",
        "-framerate", "15", "-video_size", f"{FRAME_W}x{FRAME_H}",
        "-i", WEBCAM_DEV,
        "-vf", f"fps={15 // FRAME_SKIP}",   # FRAME_SKIP 반영한 FPS
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-"
    ]

    ffmpeg_proc = None
    for attempt in range(30):
        try:
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            # 첫 프레임 수신 확인
            raw = ffmpeg_proc.stdout.read(FRAME_W * FRAME_H * 3)
            if len(raw) == FRAME_W * FRAME_H * 3:
                print(f"웹캠 시작 성공 (시도 {attempt + 1}): {WEBCAM_DEV}")
                # 첫 프레임 push back은 불가하므로 그냥 처리
                break
            ffmpeg_proc.kill()
            ffmpeg_proc = None
        except Exception as e:
            if ffmpeg_proc:
                ffmpeg_proc.kill()
                ffmpeg_proc = None
        print(f"웹캠 대기 중 ({attempt + 1}/30)...")
        time.sleep(2)

    if ffmpeg_proc is None:
        print(f"오류: {WEBCAM_DEV} 에 접근할 수 없습니다.")
        sys.exit(1)

    frame_size = FRAME_W * FRAME_H * 3
    print(f"웹캠 시작: {WEBCAM_DEV} ({FRAME_W}x{FRAME_H})")

    frame_count = 0
    total_crossings = 0
    fps_timer = time.time()
    fps_frames = 0

    def read_frame():
        """ffmpeg 파이프에서 한 프레임(bgr24 raw) 읽기"""
        raw = ffmpeg_proc.stdout.read(frame_size)
        if len(raw) != frame_size:
            return None
        return np.frombuffer(raw, dtype=np.uint8).reshape(FRAME_H, FRAME_W, 3)

    try:
        while True:
            frame = read_frame()
            if frame is None:
                print("ffmpeg 스트림 끊김, 재시작 중...")
                ffmpeg_proc.kill()
                time.sleep(2)
                try:
                    ffmpeg_proc = subprocess.Popen(
                        ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                    )
                except Exception:
                    pass
                continue

            frame_count += 1
            fps_frames += 1

            if time.time() - fps_timer >= 10:
                fps = fps_frames / (time.time() - fps_timer)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"FPS: {fps:.1f}  누적 교차: {total_crossings}")
                fps_timer = time.time()
                fps_frames = 0
                stats.flush_periodic()

            h, w = frame.shape[:2]
            line_x = int(w * COUNTING_LINE)

            # ── YOLO11n 추론 + ByteTrack 추적 ──────────────────
            results = yolo.track(
                frame,
                imgsz=YOLO_IMGSZ,
                conf=CONF_THRESH,
                classes=list(VEHICLE_CLASSES.keys()),
                tracker="bytetrack.yaml",
                persist=True,
                verbose=False,
            )

            annotated = frame.copy()
            cv2.line(annotated, (line_x, 0), (line_x, h), (0, 255, 255), 2)

            active_ids: set = set()

            if results and results[0].boxes is not None:
                for box in results[0].boxes:
                    if box.id is None:
                        continue

                    track_id = int(box.id)
                    cls_id   = int(box.cls)
                    conf     = float(box.conf)
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    cx = (x1 + x2) / 2

                    active_ids.add(track_id)
                    label = VEHICLE_CLASSES.get(cls_id, "car")

                    # ── 카운팅 라인 교차 판단 ──────────────────
                    direction = dir_tracker.update(track_id, cx, w)
                    if direction:
                        total_crossings += 1
                        stats.record(label, direction)
                        sender.send(CrossingEvent(
                            vehicle=label,
                            direction=direction,
                            track_id=track_id,
                            confidence=round(conf, 3),
                            camera_id=CAMERA_ID,
                            timestamp=datetime.utcnow().isoformat() + "Z",
                        ))
                        print(f"  → {label:4s} {direction:5s} "
                              f"(id:{track_id:3d} conf:{conf:.2f})")

                    # ── 바운딩 박스 ─────────────────────────────
                    color = BOX_COLORS.get(label, (200, 200, 200))
                    cv2.rectangle(annotated,
                                  (int(x1), int(y1)), (int(x2), int(y2)),
                                  color, 2)
                    cv2.putText(annotated,
                                f"{label} #{track_id} {conf:.2f}",
                                (int(x1), int(y1) - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

                dir_tracker.cleanup(active_ids)

            # ── 통계 오버레이 ───────────────────────────────────
            cv2.putText(annotated, f"Total: {total_crossings}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(annotated, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

            # ── 스트림 프레임 업데이트 ──────────────────────────
            stream_path = Path(STREAM_FRAME)
            if stream_path.parent.exists():
                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    tmp = str(stream_path) + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(buf.tobytes())
                    os.replace(tmp, stream_path)

    except KeyboardInterrupt:
        print("\n종료.")
    finally:
        if ffmpeg_proc:
            ffmpeg_proc.kill()


if __name__ == "__main__":
    main()
