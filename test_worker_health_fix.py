#!/usr/bin/env python3
"""Test script to verify Worker Health status logic fix."""

import sys
sys.path.insert(0, "src")

from rv_agentic.services import heartbeat

# Get worker health summary
health = heartbeat.get_worker_health_summary()

print("=== Worker Health Summary ===")
print(f"Total Active Workers: {health.get('total_active_workers', 0)}")
print(f"Total Dead Workers: {health.get('total_dead_workers', 0)}")
print(f"Health Status: {health.get('health_status', 'unknown')}")
print()

# Test the logic
total_active = health.get('total_active_workers', 0)
total_dead = health.get('total_dead_workers', 0)
status = health.get('health_status', 'unknown')

print("=== Status Logic Test ===")
if total_active > 0:
    expected = "healthy"
    print(f"✓ Active workers present ({total_active})")
    print(f"✓ Expected status: '{expected}'")
elif total_dead > 0:
    expected = "no_workers"
    print(f"✗ NO active workers, but {total_dead} dead worker(s)")
    print(f"✓ Expected status: '{expected}'")
else:
    expected = "unknown"
    print(f"⚪ No workers registered")
    print(f"✓ Expected status: '{expected}'")

print(f"\nActual status: '{status}'")

if status == expected:
    print("✅ Status logic is CORRECT!")
else:
    print(f"❌ Status logic is WRONG! Expected '{expected}' but got '{status}'")
    sys.exit(1)

# Display what users will see
print("\n=== User-Facing Display ===")
status_display = {
    "healthy": "✅ Healthy",
    "no_workers": "🔵 No Active Workers",
    "unknown": "⚪ Unknown"
}
display_text = status_display.get(status, f"⚪ {status.title()}")
print(f"System Health: {display_text}")

# Check if alarming "Degraded" will be shown
if "degraded" in status.lower() or "⚠️" in display_text:
    print("\n❌ FAIL: Alarming 'Degraded' status would be shown to users!")
    sys.exit(1)
else:
    print("\n✅ PASS: No alarming 'Degraded' status - users will not be freaked out")

print("\n=== Fix Verified Successfully ===")
