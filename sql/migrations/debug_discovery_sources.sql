-- Debug query to see where companies came from for a specific run
-- Replace 'YOUR_RUN_ID' with your actual run UUID

SELECT
    discovery_source,
    COUNT(*) as company_count,
    ARRAY_AGG(domain ORDER BY created_at LIMIT 5) as sample_domains
FROM pm_pipeline.company_candidates
WHERE run_id = 'YOUR_RUN_ID'
GROUP BY discovery_source
ORDER BY company_count DESC;

-- Also check the full list with timestamps
SELECT
    created_at,
    discovery_source,
    domain,
    name,
    state
FROM pm_pipeline.company_candidates
WHERE run_id = 'YOUR_RUN_ID'
ORDER BY created_at;
