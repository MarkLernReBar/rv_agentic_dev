# EC2 Production Deployment Guide

## Overview

This guide provides complete instructions for deploying the RV Agentic worker system to AWS EC2 for production use with:

- ✅ **Automatic startup** on system boot
- ✅ **Automatic restart** on worker crashes
- ✅ **Horizontal scaling** with multiple worker instances
- ✅ **Resilient operation** with lease-based task distribution
- ✅ **CloudWatch integration** for monitoring and alerts
- ✅ **Zero-downtime deploys** with rolling restarts

---

## Architecture

### Worker System

The system uses a **database-backed queue pattern** with:

1. **PostgreSQL as Queue** - `pm_pipeline` tables store runs and track progress
2. **Lease-Based Processing** - Workers claim tasks with time-limited leases to prevent double-processing
3. **Heartbeat System** - Workers send heartbeats; dead workers have leases released automatically
4. **Multi-Instance Scale** - Run N instances of each worker type for horizontal scaling

### Why This Works for EC2

- **Resilient**: Leases prevent lost work if workers crash
- **Scalable**: Add/remove worker instances without coordination
- **Simple**: No message queue infrastructure (SQS, RabbitMQ) needed
- **Cost-effective**: Uses existing PostgreSQL database
- **Event-responsive**: Workers poll frequently (every few seconds) for new tasks

---

## Prerequisites

### AWS Resources

1. **EC2 Instance** (recommended: t3.large or larger)
   - Ubuntu 22.04 LTS
   - 8GB+ RAM
   - 20GB+ disk
   - Security group allowing outbound HTTPS (443)

2. **IAM Role** (attach to EC2 instance)
   - `CloudWatchAgentServerPolicy` - For metrics/logs
   - `AmazonSSMManagedInstanceCore` - For Systems Manager access

3. **Environment Variables** (stored in EC2 Parameter Store or Secrets Manager)
   - `POSTGRES_URL` - Connection string to PostgreSQL database
   - `OPENAI_API_KEY` - OpenAI API key for GPT-5
   - `SUPABASE_SERVICE_KEY` - Supabase service role key
   - `HUBSPOT_PRIVATE_APP_TOKEN` - HubSpot API token
   - `N8N_MCP_SERVER_URL` - n8n MCP server URL (if using)

### Local Requirements

- SSH access to EC2 instance
- `rsync` installed locally
- AWS CLI configured (for Parameter Store access)

---

## Deployment Steps

### Step 1: Prepare EC2 Instance

```bash
# SSH into EC2 instance
ssh -i your-key.pem ubuntu@your-ec2-ip

# Update system
sudo apt-get update
sudo apt-get upgrade -y
```

### Step 2: Clone Repository

```bash
cd /home/ubuntu
git clone https://github.com/your-org/rv-agentic.git
cd rv-agentic
```

### Step 3: Create Environment File

```bash
# Create production environment file
cat > .env.production << 'EOF'
# Database
POSTGRES_URL=postgresql://user:pass@host:5432/dbname

# OpenAI
OPENAI_API_KEY=sk-...

# Supabase
SUPABASE_SERVICE_KEY=eyJ...
NEXT_PUBLIC_SUPABASE_URL=https://...

# HubSpot
HUBSPOT_PRIVATE_APP_TOKEN=pat-...

# n8n MCP (optional)
N8N_MCP_SERVER_URL=http://...

# Email notifications (optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@example.com
HEARTBEAT_MONITOR_ALERT_EMAIL=alerts@example.com

# Worker configuration
WORKER_POLL_INTERVAL=5
HEARTBEAT_MONITOR_INTERVAL=60
LEAD_LIST_OVERSAMPLE_FACTOR=2.0
EOF

# Secure the file
chmod 600 .env.production
```

**Alternative: Use AWS Parameter Store**

```bash
# Store secrets in Parameter Store
aws ssm put-parameter \
  --name "/rv-agentic/prod/postgres-url" \
  --value "postgresql://..." \
  --type "SecureString"

# Retrieve at runtime (add to systemd service)
Environment="POSTGRES_URL=$(aws ssm get-parameter --name /rv-agentic/prod/postgres-url --with-decryption --query Parameter.Value --output text)"
```

### Step 4: Run Deployment Script

```bash
# Run automated deployment (installs dependencies, sets up systemd, starts workers)
sudo ./deployment/deploy_ec2.sh --workers 2

# Options:
#   --workers N     Number of worker instances per type (default: 2)
#   --skip-deps     Skip system dependencies installation
#   --dry-run       Show what would be done
```

The deployment script will:
1. Install system dependencies (Python 3.12, PostgreSQL client, etc.)
2. Set up Python virtual environment
3. Install Python dependencies
4. Install systemd service files
5. Enable and start all services
6. Verify workers are running

### Step 5: Verify Deployment

```bash
# Check all services
sudo systemctl status 'rv-agentic-*'

# Check worker heartbeats in database
cd /home/ubuntu/rv-agentic
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from rv_agentic.services import supabase_client
workers = supabase_client.get_active_workers()
print(f'Active workers: {len(workers)}')
"

# View logs
sudo journalctl -u rv-agentic-heartbeat -f
sudo journalctl -u 'rv-agentic-*' -f
```

### Step 6: Install CloudWatch Agent (Optional but Recommended)

```bash
# Download and install CloudWatch agent
wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
sudo dpkg -i -E ./amazon-cloudwatch-agent.deb

# Copy CloudWatch config
sudo cp /home/ubuntu/rv-agentic/deployment/cloudwatch_agent_config.json \
  /opt/aws/amazon-cloudwatch-agent/etc/config.json

# Start CloudWatch agent
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/config.json
```

---

## Operation

### Service Management

```bash
# Start all workers
sudo systemctl start 'rv-agentic-*'

# Stop all workers
sudo systemctl stop 'rv-agentic-*'

# Restart all workers (zero-downtime with multiple instances)
sudo systemctl restart 'rv-agentic-*'

# Check status
sudo systemctl status 'rv-agentic-*' --no-pager

# Enable auto-start on boot (already done by deploy script)
sudo systemctl enable 'rv-agentic-*'
```

### View Logs

```bash
# Real-time logs for all workers
sudo journalctl -u 'rv-agentic-*' -f

# Logs for specific worker
sudo journalctl -u rv-agentic-heartbeat -f
sudo journalctl -u rv-agentic-lead-list@1 -f

# Last 100 lines from all workers
sudo journalctl -u 'rv-agentic-*' -n 100 --no-pager

# Filter by error
sudo journalctl -u 'rv-agentic-*' -p err -n 50 --no-pager

# Export logs for analysis
sudo journalctl -u 'rv-agentic-*' --since "1 hour ago" > /tmp/worker-logs.txt
```

### Scaling Workers

**Scale Up (Add Worker Instance):**

```bash
# Start instance 3 of lead list runner
sudo systemctl enable rv-agentic-lead-list@3
sudo systemctl start rv-agentic-lead-list@3

# Verify
sudo systemctl status rv-agentic-lead-list@3
```

**Scale Down (Remove Worker Instance):**

```bash
# Stop instance 3
sudo systemctl stop rv-agentic-lead-list@3
sudo systemctl disable rv-agentic-lead-list@3
```

**Scale All Worker Types:**

```bash
# Add instance 4 to all worker types
for service in lead-list company-research contact-research; do
  sudo systemctl enable rv-agentic-${service}@4
  sudo systemctl start rv-agentic-${service}@4
done
```

### Code Deployments

**Rolling Deployment (Zero Downtime):**

```bash
cd /home/ubuntu/rv-agentic

# Pull latest code
git pull

# Install any new dependencies
.venv/bin/pip install -e .

# Restart workers one at a time
for i in 1 2; do
  for service in lead-list company-research contact-research; do
    echo "Restarting rv-agentic-${service}@${i}..."
    sudo systemctl restart rv-agentic-${service}@${i}
    sleep 10  # Wait for worker to start processing
  done
done

# Restart heartbeat monitor
sudo systemctl restart rv-agentic-heartbeat
```

**Emergency Restart (All at Once):**

```bash
# Stop all
sudo systemctl stop 'rv-agentic-*'

# Deploy code
git pull
.venv/bin/pip install -e .

# Start all
sudo systemctl start 'rv-agentic-*'
```

---

## Monitoring and Alerting

### CloudWatch Metrics

With CloudWatch agent installed, you'll have:

**System Metrics:**
- CPU usage (idle, iowait)
- Memory usage
- Disk usage and I/O
- Network connections

**Custom Metrics (to add):**
Create custom metrics script:

```python
# /home/ubuntu/rv-agentic/scripts/emit_metrics.py
import boto3
from rv_agentic.services import supabase_client

cloudwatch = boto3.client('cloudwatch')

# Get worker stats
active = supabase_client.get_active_workers()
dead = supabase_client.get_dead_workers()

# Get run stats
runs = supabase_client.get_active_and_recent_runs(limit=100)
active_runs = [r for r in runs if r.get('status') == 'active']

# Emit metrics
cloudwatch.put_metric_data(
    Namespace='RVAgentic',
    MetricData=[
        {
            'MetricName': 'ActiveWorkers',
            'Value': len(active),
            'Unit': 'Count'
        },
        {
            'MetricName': 'DeadWorkers',
            'Value': len(dead),
            'Unit': 'Count'
        },
        {
            'MetricName': 'ActiveRuns',
            'Value': len(active_runs),
            'Unit': 'Count'
        },
    ]
)
```

Add to crontab:
```bash
*/5 * * * * cd /home/ubuntu/rv-agentic && .venv/bin/python scripts/emit_metrics.py
```

### CloudWatch Alarms

Create alarms for:

```bash
# Dead workers alarm
aws cloudwatch put-metric-alarm \
  --alarm-name rv-agentic-dead-workers \
  --alarm-description "Alert when workers are dead" \
  --metric-name DeadWorkers \
  --namespace RVAgentic \
  --statistic Average \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:region:account:topic

# High memory usage
aws cloudwatch put-metric-alarm \
  --alarm-name rv-agentic-high-memory \
  --metric-name MEMORY_USED \
  --namespace RVAgentic \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 3
```

### Email Alerts

Email alerts are automatically sent by the heartbeat monitor when workers die (if SMTP configured).

---

## Troubleshooting

### Workers Not Starting

```bash
# Check systemd service status
sudo systemctl status rv-agentic-lead-list@1 -l

# Check for errors in logs
sudo journalctl -u rv-agentic-lead-list@1 -n 50 --no-pager

# Common issues:
# 1. Missing .env.production file
# 2. Incorrect file permissions
# 3. Database connection issues
# 4. Missing Python dependencies

# Test worker manually
cd /home/ubuntu/rv-agentic
source .env.production
.venv/bin/python -m rv_agentic.workers.lead_list_runner
```

### Workers Keep Crashing

```bash
# Check resource usage
htop

# Check disk space
df -h

# Check memory
free -h

# View crash logs
sudo journalctl -u 'rv-agentic-*' -p err -n 100

# Common causes:
# - Out of memory (increase instance size)
# - OpenAI API rate limits (add exponential backoff)
# - Database connection pool exhaustion (tune connections)
# - Timeout issues (increase timeouts)
```

### No Progress on Runs

```bash
# Check if workers are active in database
cd /home/ubuntu/rv-agentic
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from rv_agentic.services import supabase_client
print('Active workers:', len(supabase_client.get_active_workers()))
print('Dead workers:', len(supabase_client.get_dead_workers()))
"

# Check for stuck leases
# (leases held by dead workers should be released by heartbeat monitor)

# Manually release all leases (emergency only)
psql $POSTGRES_URL -c "
UPDATE pm_pipeline.company_candidates
SET worker_id = NULL, worker_lease_until = NULL
WHERE worker_lease_until > NOW();
"
```

### High CPU/Memory Usage

```bash
# Identify problematic worker
sudo systemctl status 'rv-agentic-*' | grep -A 3 "Main PID"
ps aux | grep python | sort -nk 3  # Sort by CPU
ps aux | grep python | sort -nk 4  # Sort by memory

# Restart specific worker
sudo systemctl restart rv-agentic-lead-list@1

# Scale down if needed
sudo systemctl stop rv-agentic-lead-list@2
```

---

## Performance Tuning

### Worker Count Guidelines

**Small Load (1-5 runs/day):**
- 1x Lead List Runner
- 1x Company Research Runner
- 1x Contact Research Runner
- Instance: t3.medium

**Medium Load (5-20 runs/day):**
- 2x Lead List Runner
- 2x Company Research Runner
- 2x Contact Research Runner
- Instance: t3.large

**High Load (20+ runs/day):**
- 3-4x Lead List Runner
- 3-4x Company Research Runner
- 3-4x Contact Research Runner
- Instance: t3.xlarge or t3.2xlarge

### Database Tuning

```sql
-- Add indexes for faster queries (if not already present)
CREATE INDEX CONCURRENTLY idx_company_candidates_run_worker
  ON pm_pipeline.company_candidates(run_id, worker_id, worker_lease_until);

CREATE INDEX CONCURRENTLY idx_runs_stage_status
  ON pm_pipeline.runs(stage, status) WHERE status = 'active';
```

### OpenAI API Rate Limits

If hitting rate limits:

1. **Add exponential backoff** in worker code
2. **Increase request spacing** with delays between API calls
3. **Request rate limit increase** from OpenAI for production use
4. **Use batch processing** where possible

---

## Cost Optimization

### Instance Right-Sizing

Monitor CloudWatch metrics for 1 week, then adjust:

- If CPU < 30% consistently: Downsize instance
- If Memory < 50% consistently: Downsize instance
- If CPU > 70% consistently: Upsize or scale workers

### Auto-Scaling (Advanced)

Use AWS Auto Scaling with custom metrics:

```bash
# Create Auto Scaling policy based on queue depth
aws autoscaling put-scaling-policy \
  --auto-scaling-group-name rv-agentic-workers \
  --policy-name scale-on-queue-depth \
  --policy-type TargetTrackingScaling \
  --target-tracking-configuration file://scaling-policy.json
```

---

## Security Best Practices

1. **Use IAM Roles** instead of hardcoded credentials
2. **Store secrets in Parameter Store/Secrets Manager**
3. **Enable CloudTrail** for audit logging
4. **Restrict security group** to only necessary outbound ports
5. **Enable VPC Flow Logs** for network monitoring
6. **Use Systems Manager Session Manager** instead of SSH
7. **Regularly update** system packages and dependencies
8. **Enable automated backups** of database

---

## Backup and Disaster Recovery

### Database Backups

```bash
# Manual backup
pg_dump $POSTGRES_URL > /tmp/rv-agentic-backup-$(date +%Y%m%d).sql

# Automated daily backups (add to cron)
0 2 * * * pg_dump $POSTGRES_URL | gzip > /backups/rv-agentic-$(date +\%Y\%m\%d).sql.gz
```

### Worker Recovery

Workers are stateless - all state is in the database. To recover:

1. Deploy new EC2 instance
2. Run deployment script
3. Workers will resume processing from database queue

---

## Appendix

### File Structure

```
/home/ubuntu/rv-agentic/
├── .env.production          # Environment variables
├── .venv/                   # Python virtual environment
├── src/                     # Application source code
├── deployment/
│   ├── deploy_ec2.sh       # Deployment automation
│   ├── cloudwatch_agent_config.json  # CloudWatch config
│   └── systemd/            # systemd service files
├── logs/                    # Local logs (if needed)
└── scripts/                 # Monitoring scripts
```

### Systemd Service Files

Located in `/etc/systemd/system/`:

- `rv-agentic-heartbeat.service` - Heartbeat monitor (singleton)
- `rv-agentic-lead-list@.service` - Lead list runner (multi-instance)
- `rv-agentic-company-research@.service` - Company research runner (multi-instance)
- `rv-agentic-contact-research@.service` - Contact research runner (multi-instance)

---

## Summary

**Deployment:** Run `sudo ./deployment/deploy_ec2.sh --workers 2`

**Management:** Use `systemctl` commands for all worker operations

**Monitoring:** CloudWatch metrics + journalctl logs + email alerts

**Scaling:** Add/remove worker instances with systemctl enable/disable

**Resilience:** Auto-restart, heartbeat monitoring, lease-based processing

**Event-Driven:** Workers poll database queue continuously (every few seconds)

The system is now production-ready for resilient, scalable, autonomous operation on EC2!
