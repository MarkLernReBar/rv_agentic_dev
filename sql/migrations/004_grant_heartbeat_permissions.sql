-- Migration: Grant permissions for heartbeat system
-- Purpose: Allow application user to use heartbeat functions and tables
-- Phase: 2.4 (Real production testing)

-- Note: Replace 'postgres' with your actual database user/role
-- You can find your user by running: SELECT current_user;

-- Grant table permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE pm_pipeline.worker_heartbeats TO postgres;
GRANT SELECT ON TABLE pm_pipeline.v_active_workers TO postgres;
GRANT SELECT ON TABLE pm_pipeline.v_dead_workers TO postgres;

-- Grant function execution permissions
GRANT EXECUTE ON FUNCTION pm_pipeline.upsert_worker_heartbeat(TEXT, TEXT, TEXT, UUID, TEXT, TIMESTAMPTZ, JSONB) TO postgres;
GRANT EXECUTE ON FUNCTION pm_pipeline.stop_worker(TEXT) TO postgres;
GRANT EXECUTE ON FUNCTION pm_pipeline.get_worker_stats() TO postgres;
GRANT EXECUTE ON FUNCTION pm_pipeline.cleanup_stale_workers(INTEGER) TO postgres;

-- Grant usage on schema (if not already granted)
GRANT USAGE ON SCHEMA pm_pipeline TO postgres;

-- Verify permissions
SELECT
    grantee,
    table_schema,
    table_name,
    privilege_type
FROM information_schema.table_privileges
WHERE table_schema = 'pm_pipeline'
  AND table_name = 'worker_heartbeats'
  AND grantee = current_user;

-- Expected output:
-- grantee  | table_schema | table_name        | privilege_type
-- postgres | pm_pipeline  | worker_heartbeats | SELECT
-- postgres | pm_pipeline  | worker_heartbeats | INSERT
-- postgres | pm_pipeline  | worker_heartbeats | UPDATE
-- postgres | pm_pipeline  | worker_heartbeats | DELETE
