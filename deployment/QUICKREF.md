# Quick Reference - RV Agentic Production Operations

## One-Time Deployment

```bash
# On EC2 instance, run once
sudo ./deployment/deploy_ec2.sh --workers 2
```

---

## Daily Operations

### Check Status
```bash
# All services
sudo systemctl status 'rv-agentic-*' --no-pager

# Active workers in database
cd /home/ubuntu/rv-agentic && .venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from rv_agentic.services import supabase_client
workers = supabase_client.get_active_workers()
print(f'✅ {len(workers)} active workers')
"
```

### View Logs
```bash
# Real-time - all workers
sudo journalctl -u 'rv-agentic-*' -f

# Real-time - specific worker
sudo journalctl -u rv-agentic-lead-list@1 -f

# Last 50 errors
sudo journalctl -u 'rv-agentic-*' -p err -n 50 --no-pager
```

### Restart Workers
```bash
# Restart all
sudo systemctl restart 'rv-agentic-*'

# Restart specific worker
sudo systemctl restart rv-agentic-lead-list@1

# Rolling restart (zero downtime)
for i in 1 2; do
  sudo systemctl restart rv-agentic-lead-list@$i
  sleep 10
done
```

### Code Deployment
```bash
cd /home/ubuntu/rv-agentic
git pull
.venv/bin/pip install -e .
sudo systemctl restart 'rv-agentic-*'
```

---

## Scaling

### Scale Up (Add Workers)
```bash
# Add instance 3
sudo systemctl enable rv-agentic-lead-list@3
sudo systemctl start rv-agentic-lead-list@3

# Add to all worker types
for service in lead-list company-research contact-research; do
  sudo systemctl enable rv-agentic-${service}@3
  sudo systemctl start rv-agentic-${service}@3
done
```

### Scale Down (Remove Workers)
```bash
# Remove instance 3
sudo systemctl stop rv-agentic-lead-list@3
sudo systemctl disable rv-agentic-lead-list@3
```

---

## Troubleshooting

### Workers Not Processing
```bash
# Check database for active workers
cd /home/ubuntu/rv-agentic && .venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from rv_agentic.services import supabase_client
print('Active:', len(supabase_client.get_active_workers()))
print('Dead:', len(supabase_client.get_dead_workers()))
"

# Check for errors in logs
sudo journalctl -u 'rv-agentic-*' -p err -n 20
```

### Worker Crashed
```bash
# systemd will auto-restart, but check why
sudo journalctl -u rv-agentic-lead-list@1 -n 100

# Restart if needed
sudo systemctl restart rv-agentic-lead-list@1
```

### High CPU/Memory
```bash
# Find culprit
ps aux | grep python | sort -nk 3  # By CPU
ps aux | grep python | sort -nk 4  # By memory

# Restart specific worker
sudo systemctl restart rv-agentic-lead-list@1
```

---

## Monitoring

### CloudWatch Logs
```
Log Group: /aws/ec2/rv-agentic/workers
```

### Key Metrics to Watch
- Active workers (should be > 0)
- Dead workers (should be 0)
- CPU usage (should be < 70%)
- Memory usage (should be < 80%)
- Disk usage (should be < 80%)

---

## Emergency Procedures

### Total System Restart
```bash
sudo systemctl stop 'rv-agentic-*'
sudo systemctl start 'rv-agentic-*'
```

### Release All Stuck Leases
```bash
psql $POSTGRES_URL -c "
UPDATE pm_pipeline.company_candidates
SET worker_id = NULL, worker_lease_until = NULL
WHERE worker_lease_until > NOW();
"
```

### Check Database Connectivity
```bash
psql $POSTGRES_URL -c "SELECT 1;"
```

---

## Files and Locations

| Item | Location |
|------|----------|
| Application | `/home/ubuntu/rv-agentic` |
| Environment | `/home/ubuntu/rv-agentic/.env.production` |
| Service Files | `/etc/systemd/system/rv-agentic-*.service` |
| Logs | `journalctl -u rv-agentic-*` |
| CloudWatch Logs | `/aws/ec2/rv-agentic/workers` |

---

## Support

For detailed documentation, see:
- **[EC2_PRODUCTION_GUIDE.md](EC2_PRODUCTION_GUIDE.md)** - Complete deployment guide
- **[WORKER_MANAGEMENT.md](../WORKER_MANAGEMENT.md)** - Worker architecture and management
- **[CLAUDE.md](../CLAUDE.md)** - Development guide
