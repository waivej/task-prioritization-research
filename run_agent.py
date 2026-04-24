"""
Entry point for running the Claude AI task prioritization agent.

Usage:
  # Step 1: submit the batch (run once, then quit)
  python run_agent.py submit

  # Step 2: poll for completion and collect results
  python run_agent.py collect <BATCH_ID>

  # Step 3: compare Claude's recommendations against other methods
  python run_agent.py compare

  # Or run all steps sequentially (waits synchronously — can take ~1 hour for 100 snapshots)
  python run_agent.py all
"""

import json
import os
import sys
from pathlib import Path

# load .env if present
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Configuration ──────────────────────────────────────────────────────────────
# Set TEST_MODE = False and N_WEEKS = 100 when ready for the full run.
TEST_MODE = False
N_WEEKS   = 3    # number of snapshots to process in test mode (ignored when TEST_MODE=False)
# ───────────────────────────────────────────────────────────────────────────────

from src.agent import (
    build_batch_requests,
    collect_and_save_results,
    load_saved_results,
    poll_batch,
    submit_batch,
)
from src.evaluate import revenue_collected
from src.load import load_all
from src.clean import clean

EVAL_DIR = Path('outputs')


def cmd_submit():
    n = N_WEEKS if TEST_MODE else 100
    if TEST_MODE:
        print(f"TEST MODE: processing {n} snapshots. Set TEST_MODE=False for the full 100-week run.")
    requests = build_batch_requests(n_weeks=n)
    batch_id = submit_batch(requests)
    print(f"\nBatch ID: {batch_id}")
    print(f"Run `python run_agent.py collect {batch_id}` when the batch finishes.")
    _save_batch_id(batch_id)


def cmd_collect(batch_id: str):
    poll_interval = 10 if TEST_MODE else 30
    poll_batch(batch_id, poll_interval=poll_interval)
    results = collect_and_save_results(batch_id)
    print(f"Collected {len(results)} snapshot results.")


def cmd_compare():
    """Compare Claude's recommendations to heuristics and actual worker."""
    results = load_saved_results()
    if not results:
        print("No agent results found. Run `python run_agent.py collect <BATCH_ID>` first.")
        return

    # load evaluation results from previous run for comparison
    eval_path = EVAL_DIR / 'evaluation_results.json'
    if not eval_path.exists():
        print(f"No {eval_path} found. Run `python run_evaluation.py` first.")
        return

    with open(eval_path) as f:
        eval_rows = json.load(f)
    eval_by_date = {r['snapshot_date']: r for r in eval_rows}

    # load data for revenue attribution
    print("Loading data for revenue attribution...")
    dfs = clean(load_all())

    agent_revenues = []
    comparison = []

    for snap_date, r in sorted(results.items()):
        ranked = r.get('ranked_tasks', [])
        avg_clients  = {t['client_id'] for t in ranked if t.get('within_avg_budget') and t.get('client_id')}
        upper_clients = {t['client_id'] for t in ranked if t.get('within_upper_budget') and t.get('client_id')}

        rev_avg   = revenue_collected(snap_date, avg_clients,   dfs, window_days=60)
        rev_upper = revenue_collected(snap_date, upper_clients, dfs, window_days=60)

        eval_row = eval_by_date.get(snap_date, {})
        row = {
            'snapshot_date':         snap_date,
            'agent_avg_revenue':     round(rev_avg, 2),
            'agent_upper_revenue':   round(rev_upper, 2),
            'actual_worker_revenue': eval_row.get('actual_revenue_60d'),
            'php_avg_revenue':       eval_row.get('rec_avg_revenue_60d'),
            'php_upper_revenue':     eval_row.get('rec_upper_revenue_60d'),
            'imp_avg_revenue':       eval_row.get('imp_avg_revenue_60d'),
            'imp_upper_revenue':     eval_row.get('imp_upper_revenue_60d'),
        }
        comparison.append(row)
        agent_revenues.append(rev_avg)

    # summary stats
    valid = [r for r in comparison if r['actual_worker_revenue'] is not None and r['actual_worker_revenue'] > 0]
    if valid:
        n          = len(valid)
        avg_actual = sum(r['actual_worker_revenue']        for r in valid) / n
        avg_agent  = sum(r['agent_avg_revenue']            for r in valid) / n
        avg_php    = sum(r['php_avg_revenue']    or 0      for r in valid) / n
        avg_imp    = sum(r['imp_avg_revenue']    or 0      for r in valid) / n

        def wins(a, b): return sum(1 for r in valid if (r[a] or 0) > (r[b] or 0))

        print(f"\n{'='*60}")
        print(f"METHOD COMPARISON — avg revenue/week, 60-day window")
        print(f"  Weeks with revenue > 0: {n}")
        print(f"{'='*60}")
        print(f"  {'Method':<30} {'Avg $/wk':>9}  {'vs actual':>9}  {'Weeks wins':>10}")
        print(f"  {'-'*30}  {'-'*9}  {'-'*9}  {'-'*10}")
        print(f"  {'Actual worker':<30} ${avg_actual:>8,.0f}  {'1.00×':>9}  {'—':>10}")
        print(f"  {'Claude agent (avg ~19h)':<30} ${avg_agent:>8,.0f}  {avg_agent/avg_actual:>8.2f}×  {wins('agent_avg_revenue','php_avg_revenue'):>6}/{n} vs PHP")
        print(f"  {'PHP formula (avg ~19h)':<30} ${avg_php:>8,.0f}  {avg_php/avg_actual:>8.2f}×  {wins('php_avg_revenue','agent_avg_revenue'):>6}/{n} vs agent")
        print(f"  {'Improved formula (avg)':<30} ${avg_imp:>8,.0f}  {avg_imp/avg_actual:>8.2f}×")
        print(f"{'='*60}")

    # save comparison CSV
    out_path = EVAL_DIR / 'agent_comparison.json'
    with open(out_path, 'w') as f:
        json.dump(comparison, f, indent=2)
    print(f"\nComparison saved to {out_path}")


def cmd_all():
    n = N_WEEKS if TEST_MODE else 100
    poll_interval = 10 if TEST_MODE else 60
    if TEST_MODE:
        print(f"TEST MODE: processing {n} snapshots. Set TEST_MODE=False for the full 100-week run.")
    requests = build_batch_requests(n_weeks=n)
    batch_id = submit_batch(requests)
    _save_batch_id(batch_id)
    print(f"Polling for completion (poll every {poll_interval}s)...")
    poll_batch(batch_id, poll_interval=poll_interval)
    collect_and_save_results(batch_id)
    cmd_compare()


def _save_batch_id(batch_id: str):
    path = EVAL_DIR / 'agent_batch_id.txt'
    path.parent.mkdir(exist_ok=True)
    path.write_text(batch_id)
    print(f"Batch ID saved to {path}")


if __name__ == '__main__':
    args = sys.argv[1:]

    if not args or args[0] == 'submit':
        cmd_submit()
    elif args[0] == 'collect':
        if len(args) < 2:
            # try loading saved batch id
            id_file = EVAL_DIR / 'agent_batch_id.txt'
            if id_file.exists():
                batch_id = id_file.read_text().strip()
                print(f"Using saved batch ID: {batch_id}")
            else:
                print("Usage: python run_agent.py collect <BATCH_ID>")
                sys.exit(1)
        else:
            batch_id = args[1]
        cmd_collect(batch_id)
    elif args[0] == 'compare':
        cmd_compare()
    elif args[0] == 'all':
        cmd_all()
    else:
        print(__doc__)
        sys.exit(1)
