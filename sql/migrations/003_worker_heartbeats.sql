-- Migration: Add worker heartbeat tracking
-- Purpose: Monitor worker health and detect crashes
-- Phase: 2.2

-- Create worker_heartbeats table
CREATE TABLE IF NOT EXISTS pm_pipeline.worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    worker_type TEXT NOT NULL,  -- 'lead_list', 'company_research', 'contact_research'
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'active',  -- 'active', 'idle', 'processing', 'stopped'
    current_run_id UUID,
    current_task TEXT,
    lease_expires_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB,

    CONSTRAINT worker_heartbeats_worker_type_check CHECK (
        worker_type IN ('lead_list', 'company_research', 'contact_research')
    ),
    CONSTRAINT worker_heartbeats_status_check CHECK (
        status IN ('active', 'idle', 'processing', 'stopped')
    )
);

-- Index for finding dead workers
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_last_heartbeat
    ON pm_pipeline.worker_heartbeats(last_heartbeat_at);

-- Index for finding workers by type
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_worker_type
    ON pm_pipeline.worker_heartbeats(worker_type);

-- Index for finding workers by status
CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_status
    ON pm_pipeline.worker_heartbeats(status);

-- View: Active workers (heartbeat within last 5 minutes)
CREATE OR REPLACE VIEW pm_pipeline.v_active_workers AS
SELECT
    worker_id,
    worker_type,
    last_heartbeat_at,
    status,
    current_run_id,
    current_task,
    lease_expires_at,
    started_at,
    EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at)) AS seconds_since_heartbeat,
    metadata
FROM pm_pipeline.worker_heartbeats
WHERE last_heartbeat_at > NOW() - INTERVAL '5 minutes'
ORDER BY worker_type, worker_id;

-- View: Dead workers (no heartbeat in last 5 minutes, but not stopped)
CREATE OR REPLACE VIEW pm_pipeline.v_dead_workers AS
SELECT
    worker_id,
    worker_type,
    last_heartbeat_at,
    status,
    current_run_id,
    current_task,
    lease_expires_at,
    started_at,
    EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at)) AS seconds_since_heartbeat,
    metadata
FROM pm_pipeline.worker_heartbeats
WHERE last_heartbeat_at <= NOW() - INTERVAL '5 minutes'
  AND status != 'stopped'
ORDER BY last_heartbeat_at DESC;

-- Function: Upsert worker heartbeat
CREATE OR REPLACE FUNCTION pm_pipeline.upsert_worker_heartbeat(
    p_worker_id TEXT,
    p_worker_type TEXT,
    p_status TEXT DEFAULT 'active',
    p_current_run_id UUID DEFAULT NULL,
    p_current_task TEXT DEFAULT NULL,
    p_lease_expires_at TIMESTAMPTZ DEFAULT NULL,
    p_metadata JSONB DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO pm_pipeline.worker_heartbeats (
        worker_id,
        worker_type,
        last_heartbeat_at,
        status,
        current_run_id,
        current_task,
        lease_expires_at,
        started_at,
        metadata
    ) VALUES (
        p_worker_id,
        p_worker_type,
        NOW(),
        p_status,
        p_current_run_id,
        p_current_task,
        p_lease_expires_at,
        NOW(),
        p_metadata
    )
    ON CONFLICT (worker_id) DO UPDATE SET
        last_heartbeat_at = NOW(),
        status = EXCLUDED.status,
        current_run_id = EXCLUDED.current_run_id,
        current_task = EXCLUDED.current_task,
        lease_expires_at = EXCLUDED.lease_expires_at,
        metadata = EXCLUDED.metadata;
END;
$$;

-- Function: Mark worker as stopped
CREATE OR REPLACE FUNCTION pm_pipeline.stop_worker(
    p_worker_id TEXT
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE pm_pipeline.worker_heartbeats
    SET status = 'stopped',
        last_heartbeat_at = NOW(),
        current_run_id = NULL,
        current_task = NULL,
        lease_expires_at = NULL
    WHERE worker_id = p_worker_id;
END;
$$;

-- Function: Clean up stale workers (for maintenance)
CREATE OR REPLACE FUNCTION pm_pipeline.cleanup_stale_workers(
    p_stale_threshold_minutes INTEGER DEFAULT 60
)
RETURNS TABLE(
    worker_id TEXT,
    worker_type TEXT,
    last_heartbeat_at TIMESTAMPTZ
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    DELETE FROM pm_pipeline.worker_heartbeats
    WHERE last_heartbeat_at < NOW() - (p_stale_threshold_minutes || ' minutes')::INTERVAL
      AND status = 'stopped'
    RETURNING
        pm_pipeline.worker_heartbeats.worker_id,
        pm_pipeline.worker_heartbeats.worker_type,
        pm_pipeline.worker_heartbeats.last_heartbeat_at;
END;
$$;

-- Function: Get worker statistics
CREATE OR REPLACE FUNCTION pm_pipeline.get_worker_stats()
RETURNS TABLE(
    worker_type TEXT,
    total_workers BIGINT,
    active_workers BIGINT,
    idle_workers BIGINT,
    processing_workers BIGINT,
    dead_workers BIGINT
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        wh.worker_type,
        COUNT(*) AS total_workers,
        COUNT(*) FILTER (
            WHERE wh.last_heartbeat_at > NOW() - INTERVAL '5 minutes'
            AND wh.status IN ('active', 'idle', 'processing')
        ) AS active_workers,
        COUNT(*) FILTER (
            WHERE wh.status = 'idle'
            AND wh.last_heartbeat_at > NOW() - INTERVAL '5 minutes'
        ) AS idle_workers,
        COUNT(*) FILTER (
            WHERE wh.status = 'processing'
            AND wh.last_heartbeat_at > NOW() - INTERVAL '5 minutes'
        ) AS processing_workers,
        COUNT(*) FILTER (
            WHERE wh.last_heartbeat_at <= NOW() - INTERVAL '5 minutes'
            AND wh.status != 'stopped'
        ) AS dead_workers
    FROM pm_pipeline.worker_heartbeats wh
    GROUP BY wh.worker_type
    ORDER BY wh.worker_type;
$$;

COMMENT ON TABLE pm_pipeline.worker_heartbeats IS 'Tracks worker health and status for monitoring and dead worker detection';
COMMENT ON VIEW pm_pipeline.v_active_workers IS 'Workers with heartbeat within last 5 minutes';
COMMENT ON VIEW pm_pipeline.v_dead_workers IS 'Workers that have stopped sending heartbeats but are not marked as stopped';
COMMENT ON FUNCTION pm_pipeline.upsert_worker_heartbeat IS 'Update or insert worker heartbeat with current timestamp';
COMMENT ON FUNCTION pm_pipeline.stop_worker IS 'Mark a worker as stopped (graceful shutdown)';
COMMENT ON FUNCTION pm_pipeline.cleanup_stale_workers IS 'Remove old stopped workers from heartbeat table';
COMMENT ON FUNCTION pm_pipeline.get_worker_stats IS 'Get statistics about workers by type';
