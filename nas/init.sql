-- 차량 교차 이벤트 테이블
CREATE TABLE IF NOT EXISTS vehicle_crossings (
    id          SERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vehicle     TEXT        NOT NULL CHECK (vehicle IN ('car', 'bus', 'ambulance', 'police')),  -- ambulance/police는 추후 추가
    direction   TEXT        NOT NULL CHECK (direction IN ('left', 'right')),
    track_id    INTEGER     NOT NULL,
    confidence  REAL,
    camera_id   TEXT        NOT NULL DEFAULT 'tachyon-01'
);

CREATE INDEX IF NOT EXISTS idx_crossings_timestamp ON vehicle_crossings (timestamp);
CREATE INDEX IF NOT EXISTS idx_crossings_camera    ON vehicle_crossings (camera_id, timestamp);

-- 시간별 통계 뷰
CREATE OR REPLACE VIEW hourly_stats AS
SELECT
    date_trunc('hour', timestamp AT TIME ZONE 'Asia/Seoul') AS hour,
    camera_id,
    vehicle,
    direction,
    COUNT(*) AS cnt
FROM vehicle_crossings
GROUP BY 1, 2, 3, 4;

-- 일별 통계 뷰
CREATE OR REPLACE VIEW daily_stats AS
SELECT
    (timestamp AT TIME ZONE 'Asia/Seoul')::DATE AS day,
    camera_id,
    vehicle,
    direction,
    COUNT(*) AS cnt
FROM vehicle_crossings
GROUP BY 1, 2, 3, 4;
