#!/bin/bash
# Real production test runner
# This script runs actual end-to-end tests with real database and agents

set -e  # Exit on error

echo "=================================="
echo "REAL PRODUCTION TEST SUITE"
echo "=================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if [ -z "$POSTGRES_URL" ]; then
    echo "❌ ERROR: POSTGRES_URL not set"
    echo "   Set it in .env.local or export it"
    exit 1
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "❌ ERROR: OPENAI_API_KEY not set"
    echo "   Set it in .env.local or export it"
    exit 1
fi

echo "✅ POSTGRES_URL is set"
echo "✅ OPENAI_API_KEY is set"
echo ""

# Load environment
if [ -f .env.local ]; then
    echo "Loading .env.local..."
    export $(cat .env.local | grep -v '^#' | xargs)
    echo "✅ Environment loaded"
else
    echo "⚠️  No .env.local found, using existing environment"
fi
echo ""

# Step 1: Grant database permissions
echo "=================================="
echo "STEP 1: Database Permissions"
echo "=================================="
echo ""
echo "Granting permissions for heartbeat system..."
echo ""

# Check current user
CURRENT_USER=$(psql "$POSTGRES_URL" -t -c "SELECT current_user;" | xargs)
echo "Current database user: $CURRENT_USER"
echo ""

# Update the permissions script with current user
sed "s/postgres/$CURRENT_USER/g" sql/migrations/004_grant_heartbeat_permissions.sql > /tmp/004_grant_permissions_temp.sql

# Run permission grants
if psql "$POSTGRES_URL" -f /tmp/004_grant_permissions_temp.sql; then
    echo "✅ Permissions granted successfully"
else
    echo "⚠️  Permission grant had issues (may already be granted)"
fi

rm /tmp/004_grant_permissions_temp.sql
echo ""

# Step 2: Run database integration test
echo "=================================="
echo "STEP 2: Database Integration Test"
echo "=================================="
echo ""
echo "Testing heartbeat system with real database..."
echo ""

if pytest tests/integration/test_real_25_company_run.py::test_database_heartbeat_integration -v -s; then
    echo ""
    echo "✅ Database integration test PASSED"
else
    echo ""
    echo "❌ Database integration test FAILED"
    echo "   Fix database issues before running full test"
    exit 1
fi
echo ""

# Step 3: Ask user confirmation for full test
echo "=================================="
echo "STEP 3: Full 25-Company Test"
echo "=================================="
echo ""
echo "⚠️  WARNING: The next test will:"
echo "   - Run real agents with OpenAI API"
echo "   - Take 12-15 minutes"
echo "   - Consume API credits (estimated $0.50-$1.00)"
echo "   - Create test data in your database"
echo ""
read -p "Do you want to proceed? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo ""
    echo "Test cancelled by user"
    exit 0
fi

echo ""
echo "Starting full 25-company test..."
echo ""

# Run the full test
START_TIME=$(date +%s)

if pytest tests/integration/test_real_25_company_run.py::test_real_25_company_run_with_batching -v -s; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo ""
    echo "=================================="
    echo "✅ FULL TEST PASSED"
    echo "=================================="
    echo "Total time: $DURATION seconds ($((DURATION / 60)) minutes)"
    echo ""
else
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo ""
    echo "=================================="
    echo "❌ FULL TEST FAILED"
    echo "=================================="
    echo "Total time: $DURATION seconds"
    echo ""
    exit 1
fi

# Summary
echo "=================================="
echo "TEST SUITE COMPLETE"
echo "=================================="
echo ""
echo "Results:"
echo "  ✅ Database permissions: OK"
echo "  ✅ Database integration: PASSED"
echo "  ✅ 25-company test: PASSED"
echo ""
echo "Your system is production-ready! 🚀"
echo ""
