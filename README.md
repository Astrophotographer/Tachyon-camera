# Tachyon Traffic Monitor

Particle Tachyon 보드에 연결된 USB 웹캠으로 차량을 실시간 감지·카운팅하는 시스템.

## 기능

- **실시간 차량 감지** — YOLO11n + ByteTrack으로 차량 탐지 및 추적
- **차종 구분** — 일반 차량(Car) / 버스(Bus)
- **방향 구분** — 화면 중앙 카운팅 라인 교차 시 좌(←) / 우(→) 판별
- **라이브 대시보드** — 브라우저에서 실시간 스트림 + 통계 확인
- **NAS 연동** — 시놀로지 NAS Docker(PostgreSQL + FastAPI)에 이벤트 저장
- **오프라인 버퍼** — NAS 연결 불량 시 로컬에 임시 저장 후 자동 재전송

## 구성

```
Tachyon/
├── camera/              # Tachyon 제어 스크립트 (Mac에서 실행)
│   ├── webcam_stream.sh     # 단순 웹캠 스트리밍만 시작
│   ├── start_counter.sh     # 차량 카운터 + 대시보드 시작
│   ├── vehicle_counter.py   # YOLO 추론 + 카운팅 (Tachyon에서 실행됨)
│   ├── stream_server.py     # HTTP 대시보드 서버 (Tachyon에서 실행됨)
│   ├── config.env.example   # 설정 예시
│   └── .gitignore
└── nas/                 # NAS Docker 배포
    ├── docker-compose.yml   # PostgreSQL + FastAPI
    ├── init.sql             # DB 스키마 자동 초기화
    ├── .env.example         # NAS 환경 변수 예시
    └── api/
        ├── main.py          # FastAPI (이벤트 수신 + 통계 API)
        ├── Dockerfile
        └── requirements.txt
```

## 시작하기

### 1. 설정 파일 준비

```bash
cd camera
cp config.env.example config.env
# config.env 편집: TACHYON_IP, TACHYON_PW, NAS_API_URL 입력
```

### 2. NAS Docker 시작 (시놀로지 Container Manager)

```bash
cd nas
cp .env.example .env
# .env 편집: POSTGRES_PASSWORD, API_SECRET 입력
docker compose up -d
```

### 3. 차량 카운터 시작

```bash
cd camera

# 첫 실행 — Tachyon에 의존성 설치 (수 분 소요)
./start_counter.sh --install

# 이후 실행
./start_counter.sh
```

브라우저에서 `http://<TACHYON_IP>:8080` 접속

### 4. 웹캠 스트리밍만 필요한 경우

```bash
./webcam_stream.sh
```

## 대시보드

| URL | 내용 |
|-----|------|
| `http://<TACHYON_IP>:8080` | 메인 대시보드 (스트림 + 통계) |
| `http://<TACHYON_IP>:8080/stream` | MJPEG 스트림 직접 접근 |
| `http://<TACHYON_IP>:8080/stats` | 세션 통계 JSON |

## NAS API 엔드포인트

| 메서드 | URL | 설명 |
|--------|-----|------|
| `GET` | `/api/stats/realtime` | 오늘 누적 현황 |
| `GET` | `/api/stats/hourly` | 시간대별 통계 |
| `GET` | `/api/stats/daily` | 최근 30일 일별 통계 |
| `POST` | `/api/crossing` | 교차 이벤트 수신 (Tachyon → NAS) |

## 설정 항목 (config.env)

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `TACHYON_IP` | — | Tachyon 보드 IP 주소 |
| `TACHYON_PW` | — | SSH 비밀번호 |
| `NAS_API_URL` | — | NAS FastAPI 주소 (예: `http://192.168.0.x:8000`) |
| `API_SECRET` | — | NAS API 인증 시크릿 |
| `YOLO_MODEL` | `yolo11n.pt` | YOLO 모델 (`yolo11n` / `yolo11s` / `yolov8n`) |
| `YOLO_IMGSZ` | `320` | 추론 이미지 크기 (320=3.8fps / 416=1.6fps) |
| `COUNTING_LINE_X` | `0.5` | 카운팅 라인 위치 (화면 너비 비율) |
| `CAMERA_ID` | `tachyon-01` | 카메라 식별자 (다중 카메라 구분용) |

## 하드웨어

- **보드**: Particle Tachyon (Qualcomm QCM6490, ARM64, Ubuntu 20.04)
- **카메라**: APC930 USB 웹캠 (640×480 @ 15fps)
- **NAS**: 시놀로지 (Docker / Container Manager)

## 로그 확인

```bash
# Tachyon SSH 접속 후
tail -f /opt/tachyon-counter/counter.log   # 카운터 로그
tail -f /tmp/tachyon_stream/server.log     # 대시보드 서버 로그
```
