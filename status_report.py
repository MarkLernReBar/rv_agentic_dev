from rv_agentic.workers.utils import load_env_files
load_env_files()
from rv_agentic.services import supabase_client
import sys

def report(run_id: str):
    run = supabase_client.get_pm_run(run_id)
    if not run:
        print("Run not found")
        return
    company_gap = supabase_client.get_pm_company_gap(run_id) or {}
    contact_gap = supabase_client.get_contact_gap_summary(run_id) or {}
    print(f"Run {run_id}")
    print(f"  stage={run.get('stage')} status={run.get('status')}")
    print(f"  target_quantity={run.get('target_quantity')}")
    print(f"  companies_ready={company_gap.get('companies_ready')} gap={company_gap.get('companies_gap')}")
    print(f"  contacts_ready_min={contact_gap.get('contacts_min_ready_total')} gap_min={contact_gap.get('contacts_min_gap_total')}")
    print(f"  notes={run.get('notes')}")
    # Targeted contact gap for top-N companies (reduces oversample noise)
    try:
        target_qty = int(run.get("target_quantity") or 0)
    except Exception:
        target_qty = 0
    if target_qty > 0:
        targeted_gap = supabase_client.get_contact_gap_for_top_companies(run_id, target_qty)
        if targeted_gap:
            print(f"  [targeted_contacts] ready_companies={targeted_gap.get('ready_companies')} gap_total={targeted_gap.get('gap_total')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python status_report.py <run_id>")
        sys.exit(1)
    report(sys.argv[1])
