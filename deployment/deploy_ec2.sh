#!/bin/bash
# deploy_ec2.sh - Production deployment script for EC2
#
# This script sets up the RV Agentic worker system on an EC2 instance
# with systemd for process management, auto-restart, and scaling.
#
# Usage:
#   ./deployment/deploy_ec2.sh [--workers N] [--skip-deps]
#
# Options:
#   --workers N     Number of worker instances per type (default: 2)
#   --skip-deps     Skip system dependencies installation
#   --dry-run       Show what would be done without doing it

set -e

# Configuration
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/ubuntu/rv-agentic}"
WORKERS_PER_TYPE="${WORKERS_PER_TYPE:-2}"
SKIP_DEPS=false
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --workers)
            WORKERS_PER_TYPE="$2"
            shift 2
            ;;
        --skip-deps)
            SKIP_DEPS=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Helper functions
log_info() {
    echo "[INFO] $1"
}

log_error() {
    echo "[ERROR] $1" >&2
}

run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] $@"
    else
        "$@"
    fi
}

# Pre-flight checks
log_info "===== RV Agentic EC2 Deployment ====="
log_info "User: $DEPLOY_USER"
log_info "Directory: $DEPLOY_DIR"
log_info "Workers per type: $WORKERS_PER_TYPE"
log_info "====================================="

# Check if running as root (needed for systemd)
if [ "$EUID" -ne 0 ] && [ "$DRY_RUN" = false ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Check if in correct directory
if [ ! -f "$(pwd)/deployment/deploy_ec2.sh" ]; then
    log_error "Script must be run from project root directory"
    exit 1
fi

# 1. Install system dependencies
if [ "$SKIP_DEPS" = false ]; then
    log_info "Installing system dependencies..."
    run_cmd apt-get update
    run_cmd apt-get install -y \
        python3.12 \
        python3.12-venv \
        python3-pip \
        postgresql-client \
        git \
        curl \
        jq \
        htop
fi

# 2. Set up application directory
log_info "Setting up application directory..."
if [ ! -d "$DEPLOY_DIR" ]; then
    run_cmd mkdir -p "$DEPLOY_DIR"
    run_cmd chown "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_DIR"
fi

# 3. Copy application files (if not already there)
if [ "$(pwd)" != "$DEPLOY_DIR" ]; then
    log_info "Copying application files to $DEPLOY_DIR..."
    run_cmd rsync -av \
        --exclude='.git' \
        --exclude='.venv' \
        --exclude='logs' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.env.local' \
        ./ "$DEPLOY_DIR/"
    run_cmd chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_DIR"
fi

# 4. Set up Python virtual environment
log_info "Setting up Python virtual environment..."
cd "$DEPLOY_DIR"

if [ ! -d ".venv" ]; then
    run_cmd su -c "python3.12 -m venv .venv" "$DEPLOY_USER"
fi

run_cmd su -c ".venv/bin/pip install --upgrade pip" "$DEPLOY_USER"
run_cmd su -c ".venv/bin/pip install -e ." "$DEPLOY_USER"

# 5. Check for environment file
log_info "Checking environment configuration..."
if [ ! -f ".env.production" ]; then
    log_error ".env.production file not found!"
    log_error "Please create .env.production with required variables:"
    log_error "  - POSTGRES_URL"
    log_error "  - OPENAI_API_KEY"
    log_error "  - SUPABASE_SERVICE_KEY"
    log_error "  - HUBSPOT_PRIVATE_APP_TOKEN"
    log_error "  - N8N_MCP_SERVER_URL"
    exit 1
fi

# Verify required environment variables
log_info "Verifying required environment variables..."
source .env.production
REQUIRED_VARS=(
    "POSTGRES_URL"
    "OPENAI_API_KEY"
    "SUPABASE_SERVICE_KEY"
    "HUBSPOT_PRIVATE_APP_TOKEN"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        log_error "Required environment variable $var is not set!"
        exit 1
    fi
done

log_info "✅ All required environment variables are set"

# 6. Install systemd service files
log_info "Installing systemd service files..."

# Copy service files
run_cmd cp deployment/systemd/*.service /etc/systemd/system/

# Update paths in service files (replace /home/ubuntu/rv-agentic if needed)
if [ "$DEPLOY_DIR" != "/home/ubuntu/rv-agentic" ]; then
    log_info "Updating service file paths to $DEPLOY_DIR..."
    run_cmd sed -i "s|/home/ubuntu/rv-agentic|$DEPLOY_DIR|g" /etc/systemd/system/rv-agentic-*.service
fi

if [ "$DEPLOY_USER" != "ubuntu" ]; then
    log_info "Updating service file user to $DEPLOY_USER..."
    run_cmd sed -i "s|User=ubuntu|User=$DEPLOY_USER|g" /etc/systemd/system/rv-agentic-*.service
    run_cmd sed -i "s|Group=ubuntu|Group=$DEPLOY_USER|g" /etc/systemd/system/rv-agentic-*.service
fi

# Reload systemd
run_cmd systemctl daemon-reload

# 7. Enable and start services
log_info "Enabling and starting services..."

# Start heartbeat monitor (singleton)
run_cmd systemctl enable rv-agentic-heartbeat.service
run_cmd systemctl restart rv-agentic-heartbeat.service

# Start worker instances
for i in $(seq 1 $WORKERS_PER_TYPE); do
    log_info "Starting worker instance $i/$WORKERS_PER_TYPE..."

    # Lead list runners
    run_cmd systemctl enable rv-agentic-lead-list@$i.service
    run_cmd systemctl restart rv-agentic-lead-list@$i.service

    # Company research runners
    run_cmd systemctl enable rv-agentic-company-research@$i.service
    run_cmd systemctl restart rv-agentic-company-research@$i.service

    # Contact research runners
    run_cmd systemctl enable rv-agentic-contact-research@$i.service
    run_cmd systemctl restart rv-agentic-contact-research@$i.service
done

# 8. Wait for services to start
log_info "Waiting for services to start..."
sleep 5

# 9. Check service status
log_info "Checking service status..."
systemctl status rv-agentic-heartbeat.service --no-pager || true

for i in $(seq 1 $WORKERS_PER_TYPE); do
    systemctl status rv-agentic-lead-list@$i.service --no-pager || true
    systemctl status rv-agentic-company-research@$i.service --no-pager || true
    systemctl status rv-agentic-contact-research@$i.service --no-pager || true
done

# 10. Verify workers in database
log_info "Verifying workers in database..."
su -c "$DEPLOY_DIR/.venv/bin/python -c \"
import sys
sys.path.insert(0, '$DEPLOY_DIR/src')
from rv_agentic.services import supabase_client

active = supabase_client.get_active_workers()
print(f'✅ {len(active)} active workers in database')
for w in active:
    print(f\"   - {w.get('worker_type', 'unknown')}: {w.get('worker_id', 'N/A')[:30]}...\")
\"" "$DEPLOY_USER"

# 11. Create monitoring cron job
log_info "Setting up monitoring cron job..."
CRON_CMD="*/5 * * * * cd $DEPLOY_DIR && .venv/bin/python -m rv_agentic.workers.health_check >> /var/log/rv-agentic-health.log 2>&1"
(crontab -u "$DEPLOY_USER" -l 2>/dev/null | grep -v "rv_agentic"; echo "$CRON_CMD") | crontab -u "$DEPLOY_USER" -

log_info ""
log_info "====================================="
log_info "✅ Deployment complete!"
log_info "====================================="
log_info ""
log_info "Services running:"
log_info "  - 1x Heartbeat Monitor"
log_info "  - ${WORKERS_PER_TYPE}x Lead List Runners"
log_info "  - ${WORKERS_PER_TYPE}x Company Research Runners"
log_info "  - ${WORKERS_PER_TYPE}x Contact Research Runners"
log_info ""
log_info "Management commands:"
log_info "  # View all services"
log_info "  systemctl list-units 'rv-agentic-*'"
log_info ""
log_info "  # Check status"
log_info "  systemctl status rv-agentic-heartbeat"
log_info "  systemctl status 'rv-agentic-*'"
log_info ""
log_info "  # View logs"
log_info "  journalctl -u rv-agentic-heartbeat -f"
log_info "  journalctl -u 'rv-agentic-*' -f"
log_info ""
log_info "  # Restart all workers"
log_info "  systemctl restart 'rv-agentic-*'"
log_info ""
log_info "  # Scale up (add instance 3)"
log_info "  systemctl enable rv-agentic-lead-list@3"
log_info "  systemctl start rv-agentic-lead-list@3"
log_info ""
log_info "  # Scale down (remove instance 3)"
log_info "  systemctl stop rv-agentic-lead-list@3"
log_info "  systemctl disable rv-agentic-lead-list@3"
log_info ""
log_info "Workers will now:"
log_info "  ✅ Start automatically on system boot"
log_info "  ✅ Restart automatically on failure"
log_info "  ✅ Process tasks as they're created"
log_info "  ✅ Scale horizontally with multiple instances"
log_info ""
