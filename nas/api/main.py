import os
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ── 설정 ──────────────────────────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"].replace(
    "postgresql://", "postgresql+asyncpg://"
)
API_SECRET = os.environ.get("API_SECRET", "")

engine = create_async_engine(DATABASE_URL, pool_size=5, max_overflow=10)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI(title="Tachyon Vehicle Counter API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 의존성 ─────────────────────────────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def verify_secret(x_api_secret: str = Header(default="")):
    if API_SECRET and x_api_secret != API_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API secret")


# ── 모델 ──────────────────────────────────────────────────────
class CrossingEvent(BaseModel):
    vehicle: Literal["car", "bus", "ambulance", "police"]  # 현재 car/bus만 사용
    direction: Literal["left", "right"]
    track_id: int
    confidence: Optional[float] = None
    camera_id: str = "tachyon-01"
    timestamp: Optional[datetime] = None  # 없으면 서버 시간 사용


class CrossingBatch(BaseModel):
    events: list[CrossingEvent]


# ── 이벤트 수신 ────────────────────────────────────────────────
@app.post("/api/crossing", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_secret)])
async def post_crossing(event: CrossingEvent, db: AsyncSession = Depends(get_db)):
    """Tachyon에서 단일 교차 이벤트 전송"""
    ts = event.timestamp or datetime.utcnow()
    await db.execute(
        text("""
            INSERT INTO vehicle_crossings (timestamp, vehicle, direction, track_id, confidence, camera_id)
            VALUES (:ts, :vehicle, :direction, :track_id, :confidence, :camera_id)
        """),
        {"ts": ts, "vehicle": event.vehicle, "direction": event.direction,
         "track_id": event.track_id, "confidence": event.confidence, "camera_id": event.camera_id},
    )
    await db.commit()
    return {"status": "ok"}


@app.post("/api/crossings/batch", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_secret)])
async def post_crossings_batch(batch: CrossingBatch, db: AsyncSession = Depends(get_db)):
    """오프라인 버퍼된 이벤트 일괄 전송"""
    for event in batch.events:
        ts = event.timestamp or datetime.utcnow()
        await db.execute(
            text("""
                INSERT INTO vehicle_crossings (timestamp, vehicle, direction, track_id, confidence, camera_id)
                VALUES (:ts, :vehicle, :direction, :track_id, :confidence, :camera_id)
            """),
            {"ts": ts, "vehicle": event.vehicle, "direction": event.direction,
             "track_id": event.track_id, "confidence": event.confidence, "camera_id": event.camera_id},
        )
    await db.commit()
    return {"status": "ok", "saved": len(batch.events)}


# ── 통계 API ───────────────────────────────────────────────────
@app.get("/api/stats/realtime")
async def get_realtime(camera_id: str = "tachyon-01", db: AsyncSession = Depends(get_db)):
    """오늘 누적 현황 (차종×방향별)"""
    result = await db.execute(
        text("""
            SELECT vehicle, direction, COUNT(*) AS cnt
            FROM vehicle_crossings
            WHERE camera_id = :camera_id
              AND (timestamp AT TIME ZONE 'Asia/Seoul')::DATE = CURRENT_DATE AT TIME ZONE 'Asia/Seoul'
            GROUP BY vehicle, direction
            ORDER BY vehicle, direction
        """),
        {"camera_id": camera_id},
    )
    rows = result.fetchall()
    data: dict = {}
    total = 0
    for vehicle, direction, cnt in rows:
        data.setdefault(vehicle, {})[direction] = cnt
        total += cnt
    return {"date": str(date.today()), "camera_id": camera_id, "breakdown": data, "total": total}


@app.get("/api/stats/hourly")
async def get_hourly(
    camera_id: str = "tachyon-01",
    target_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """특정 날짜(기본 오늘)의 시간대별 통계"""
    day = target_date or str(date.today())
    result = await db.execute(
        text("""
            SELECT
                EXTRACT(HOUR FROM timestamp AT TIME ZONE 'Asia/Seoul') AS hour,
                vehicle,
                direction,
                COUNT(*) AS cnt
            FROM vehicle_crossings
            WHERE camera_id = :camera_id
              AND (timestamp AT TIME ZONE 'Asia/Seoul')::DATE = :day::DATE
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
        """),
        {"camera_id": camera_id, "day": day},
    )
    rows = result.fetchall()
    hours: dict = {str(h): {} for h in range(24)}
    for hour, vehicle, direction, cnt in rows:
        h = str(int(hour))
        hours[h].setdefault(vehicle, {})[direction] = cnt
    return {"date": day, "camera_id": camera_id, "hours": hours}


@app.get("/api/stats/daily")
async def get_daily(
    camera_id: str = "tachyon-01",
    days: int = 30,
    db: AsyncSession = Depends(get_db),
):
    """최근 N일간 일별 통계"""
    since = date.today() - timedelta(days=days - 1)
    result = await db.execute(
        text("""
            SELECT
                (timestamp AT TIME ZONE 'Asia/Seoul')::DATE AS day,
                vehicle,
                direction,
                COUNT(*) AS cnt
            FROM vehicle_crossings
            WHERE camera_id = :camera_id
              AND (timestamp AT TIME ZONE 'Asia/Seoul')::DATE >= :since
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
        """),
        {"camera_id": camera_id, "since": since},
    )
    rows = result.fetchall()
    data: dict = {}
    for day, vehicle, direction, cnt in rows:
        d = str(day)
        data.setdefault(d, {}).setdefault(vehicle, {})[direction] = cnt
    return {"camera_id": camera_id, "days": days, "data": data}


@app.get("/health")
async def health():
    return {"status": "ok"}
