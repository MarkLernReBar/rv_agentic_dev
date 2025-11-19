# Worker Management Guide

## Problem: Why Were Workers Not Running?

### Root Cause

The RV Agentic system **requires workers to be manually started** and has **no automatic restart mechanism**. This means:

1. **Manual startup required** - Workers don't start automatically when the system boots
2. **No auto-restart** - If a worker crashes or exits, it stays dead
3. **No process monitoring** - No system was in place to detect and restart failed workers
4. **Heartbeat monitor not running** - The existing monitoring infrastructure wasn't active

### What Happened

When you submitted a lead list task:
- The Streamlit UI created a run in the database
- No workers were running to process it
- The task sat idle with no progress
- Database showed 4 dead workers from previous sessions

---

## Solution: Automated Worker Management

### Quick Start (Development)

Use the provided management scripts:

```bash
# Start all workers
./start_all_workers.sh

# Check status
./check_worker_status.sh

# Stop all workers
./stop_all_workers.sh
```

These scripts:
- ✅ Start all required workers in the background
- ✅ Create log files in `logs/` directory
- ✅ Check for already-running workers (won't duplicate)
- ✅ Query database to verify worker health

---

## Production Deployment (Recommended)

### Option 1: Supervisor (Linux/Production)

Supervisor provides:
- ✅ Auto-start on system boot
- ✅ Auto-restart on crash
- ✅ Centralized log management
- ✅ Easy start/stop/restart commands

**Setup:**

```bash
# 1. Install supervisor
pip install supervisor

# 2. Update paths in supervisor.conf
# Edit: /Users/marklerner/RV_Agentic_FrontEnd_Dev/supervisor.conf
# Update 'directory', 'command', and 'user' paths

# 3. Copy config to supervisor directory
sudo cp supervisor.conf /etc/supervisor/conf.d/rv_agentic.conf

# 4. Reload supervisor
sudo supervisorctl reread
sudo supervisorctl update

# 5. Start all workers
sudo supervisorctl start rv_agentic:*
```

**Daily Commands:**

```bash
# Check status
sudo supervisorctl status rv_agentic:*

# Restart all workers
sudo supervisorctl restart rv_agentic:*

# View logs
sudo supervisorctl tail -f rv_agentic:lead_list

# Stop all workers
sudo supervisorctl stop rv_agentic:*
```

### Option 2: systemd (Linux Alternative)

Create service files in `/etc/systemd/system/`:

```ini
# /etc/systemd/system/rv-agentic-workers.service
[Unit]
Description=RV Agentic Worker System
After=network.target postgresql.service

[Service]
Type=simple
User=marklerner
WorkingDirectory=/Users/marklerner/RV_Agentic_FrontEnd_Dev
ExecStart=/Users/marklerner/RV_Agentic_FrontEnd_Dev/start_all_workers.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable rv-agentic-workers
sudo systemctl start rv-agentic-workers
sudo systemctl status rv-agentic-workers
```

### Option 3: PM2 (Node.js-based)

```bash
# Install PM2
npm install -g pm2

# Create ecosystem file
pm2 ecosystem
```

Edit `ecosystem.config.js`:
```javascript
module.exports = {
  apps: [
    {
      name: 'rv-heartbeat-monitor',
      script: '.venv/bin/python',
      args: '-m rv_agentic.workers.heartbeat_monitor',
      cwd: '/Users/marklerner/RV_Agentic_FrontEnd_Dev',
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: 'rv-lead-list',
      script: '.venv/bin/python',
      args: '-m rv_agentic.workers.lead_list_runner',
      cwd: '/Users/marklerner/RV_Agentic_FrontEnd_Dev',
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
    },
    // Add other workers...
  ]
};
```

Start with:
```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup  # Enable auto-start on boot
```

---

## Worker Architecture

### The Four Workers

1. **Heartbeat Monitor** (`heartbeat_monitor.py`)
   - Detects dead workers
   - Releases stuck database leases
   - Sends email alerts
   - Runs every 60 seconds

2. **Lead List Runner** (`lead_list_runner.py`)
   - Company discovery (stage: `company_discovery`)
   - Finds companies matching criteria
   - Oversample strategy (2x target)

3. **Company Research Runner** (`company_research_runner.py`)
   - Company enrichment (stage: `company_research`)
   - ICP analysis and signals
   - PMS detection

4. **Contact Research Runner** (`contact_research_runner.py`)
   - Contact discovery (stage: `contact_discovery`)
   - Decision maker identification
   - Email verification

### Worker Health System

Workers maintain health via:

1. **Heartbeat Thread** - Each worker sends heartbeat every 30s to database
2. **Lease System** - Workers acquire leases on tasks to prevent double-processing
3. **Automatic Cleanup** - Heartbeat monitor releases leases from dead workers
4. **Status Views** - Database views track active/dead workers

---

## Monitoring and Alerts

### Email Alerts

Set environment variables for automatic alerts:

```bash
# .env.local
HEARTBEAT_MONITOR_ALERT_EMAIL=your-email@example.com
HEARTBEAT_MONITOR_INTERVAL=60  # Check every 60 seconds

# SMTP settings (required for alerts)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@example.com
```

When workers die, you'll receive emails like:

```
Subject: ⚠️ Dead Workers Detected (2)

The following workers have stopped sending heartbeats:

- lead-list-abc123... (lead_list): last seen 5.2 min ago, task: run-xyz
- company-research-def456... (company_research): last seen 3.8 min ago, task: run-xyz

Leases from these workers have been automatically released.
Please check worker logs and restart if needed.
```

### Manual Monitoring

```bash
# Check worker status
./check_worker_status.sh

# View live logs
tail -f logs/lead_list_runner.log

# Check database directly
python debug_runs.py
```

---

## Troubleshooting

### Workers Won't Start

1. **Check virtual environment:**
   ```bash
   ls -la .venv/bin/python
   .venv/bin/python --version  # Should be 3.10+
   ```

2. **Check environment variables:**
   ```bash
   cat .env.local | grep -E "POSTGRES_URL|OPENAI_API_KEY"
   ```

3. **Check permissions:**
   ```bash
   chmod +x start_all_workers.sh
   ```

4. **Check for port conflicts:**
   ```bash
   lsof -i :5678  # n8n MCP server
   ```

### Workers Keep Dying

1. **Check logs:**
   ```bash
   tail -100 logs/lead_list_runner.log
   grep ERROR logs/*.log
   ```

2. **Common causes:**
   - Missing environment variables
   - Database connection issues
   - MCP server unreachable
   - OpenAI API rate limits
   - Out of memory

3. **Verify dependencies:**
   ```bash
   .venv/bin/pip list | grep -E "openai|psycopg|httpx"
   ```

### No Progress on Runs

1. **Check workers are running:**
   ```bash
   ./check_worker_status.sh
   ```

2. **Check run stage:**
   ```bash
   python debug_runs.py
   ```

3. **Check for blocked domains:**
   ```sql
   SELECT COUNT(*) FROM pm_pipeline.v_blocked_domains;
   ```

4. **Check worker leases:**
   ```sql
   SELECT * FROM pm_pipeline.company_candidates
   WHERE worker_id IS NOT NULL
   AND worker_lease_until > NOW();
   ```

---

## Best Practices

### Development

- Use `./start_all_workers.sh` for quick startup
- Run `./check_worker_status.sh` frequently to monitor health
- Keep logs in `logs/` directory (gitignored)
- Use `RUN_FILTER_ID` env var to test specific runs

### Production

- **MUST USE** supervisor, systemd, or PM2
- Enable email alerts for dead workers
- Set up log rotation (supervisor handles this automatically)
- Monitor disk space in `logs/` directory
- Set up external monitoring (UptimeRobot, Pingdom, etc.)
- Configure auto-restart with backoff (avoid restart loops)

### Operational

- **Check worker status before submitting large runs**
- Restart workers after code deploys
- Monitor worker memory usage (should be stable)
- Archive old log files regularly
- Keep heartbeat monitor running at all times

---

## Migration Checklist

To prevent "no workers running" in the future:

- [ ] Choose production process manager (Supervisor recommended)
- [ ] Update paths in `supervisor.conf`
- [ ] Install and configure supervisor/systemd/PM2
- [ ] Enable auto-start on system boot
- [ ] Configure email alerts
- [ ] Set up external monitoring
- [ ] Document ops runbook for your team
- [ ] Test full restart procedure
- [ ] Verify workers auto-restart after crash
- [ ] Add to deployment checklist

---

## Files Reference

| File | Purpose |
|------|---------|
| `start_all_workers.sh` | Start all workers (development) |
| `stop_all_workers.sh` | Stop all workers (development) |
| `check_worker_status.sh` | Check worker health |
| `supervisor.conf` | Supervisor configuration (production) |
| `debug_runs.py` | Database status checker |
| `logs/*.log` | Worker log files |
| `src/rv_agentic/workers/heartbeat_monitor.py` | Worker health monitor |

---

## Summary

**The Problem:** Workers require manual startup with no auto-restart.

**The Solution:**
- **Development:** Use provided shell scripts
- **Production:** Deploy with Supervisor (recommended)
- **Monitoring:** Enable email alerts and external checks

**Never Again:** With proper process management, workers will:
- Start automatically on system boot
- Restart automatically on crash
- Alert you when problems occur
- Provide centralized logging
