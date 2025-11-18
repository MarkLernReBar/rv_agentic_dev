#!/usr/bin/env python3
"""Simple test runner that loads .env.local and runs tests."""

import os
import sys
import subprocess
from pathlib import Path

# Load .env.local
env_file = Path(__file__).parent / ".env.local"
if env_file.exists():
    print("Loading .env.local...")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value
    print("✅ Environment loaded")
else:
    print("❌ .env.local not found")
    sys.exit(1)

# Check prerequisites
if not os.getenv("POSTGRES_URL"):
    print("❌ ERROR: POSTGRES_URL not set")
    sys.exit(1)

if not os.getenv("OPENAI_API_KEY"):
    print("❌ ERROR: OPENAI_API_KEY not set")
    sys.exit(1)

print(f"✅ POSTGRES_URL is set")
print(f"✅ OPENAI_API_KEY is set")
print()

# Step 1: Database integration test
print("="*80)
print("STEP 1: Database Integration Test")
print("="*80)
print()

result = subprocess.run([
    sys.executable, "-m", "pytest",
    "tests/integration/test_real_25_company_run.py::test_database_heartbeat_integration",
    "-v", "-s", "--tb=short"
], env=os.environ)

if result.returncode != 0:
    print("\n❌ Database integration test FAILED")
    sys.exit(1)

print("\n✅ Database integration test PASSED")
print()

# Step 2: Ask for confirmation
print("="*80)
print("STEP 2: Full 25-Company Test")
print("="*80)
print()
print("⚠️  WARNING: The next test will:")
print("   - Run real agents with OpenAI API")
print("   - Take 12-15 minutes")
print("   - Consume API credits (estimated $0.50-$1.00)")
print("   - Create test data in your database")
print()

response = input("Do you want to proceed? (yes/no): ")

if response.lower() != "yes":
    print("\nTest cancelled by user")
    sys.exit(0)

print("\nStarting full 25-company test...")
print()

# Step 3: Full test
result = subprocess.run([
    sys.executable, "-m", "pytest",
    "tests/integration/test_real_25_company_run.py::test_real_25_company_run_with_batching",
    "-v", "-s", "--tb=short"
], env=os.environ)

if result.returncode != 0:
    print("\n❌ Full test FAILED")
    sys.exit(1)

print("\n" + "="*80)
print("✅ ALL TESTS PASSED")
print("="*80)
print("\nYour system is production-ready! 🚀")
