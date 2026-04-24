"""
Analyze same-day (inbound) tasks vs pre-existing tasks to explain
why the actual worker outperforms all algorithmic methods.

A "same-day task" is one created and completed on the same date.
These are inbound/reactive jobs that no ranking algorithm could schedule
in advance — the worker does them because a client calls or messages.
"""

import json
import pandas as pd
from pathlib import Path
from src.load import load_all
from src.clean import clean

OUTPUTS = Path('outputs')


def main():
    print("Loading data...")
    dfs = clean(load_all())

    invoices     = dfs['invoices'].copy()
    projecttasks = dfs['projecttasks'].copy()
    projects     = dfs['projects'].copy()

    # task → client_id
    task_client = (
        projecttasks[['id', 'projectid', 'created', 'completeddate']]
        .merge(projects[['id', 'userid']], left_on='projectid', right_on='id', how='left', suffixes=('', '_proj'))
        .rename(columns={'userid': 'client_id'})
        [['id', 'projectid', 'client_id', 'created', 'completeddate']]
    )
    task_client['completeddate'] = pd.to_datetime(task_client['completeddate'], errors='coerce')
    task_client['created']       = pd.to_datetime(task_client['created'],       errors='coerce')
    invoices['datepaid']         = pd.to_datetime(invoices['datepaid'],         errors='coerce')

    completed = task_client[task_client['completeddate'].notna()].copy()

    # inbound = created and completed within 7 days (one snapshot window)
    completed['age_at_completion'] = (completed['completeddate'] - completed['created']).dt.days
    completed['is_inbound']  = completed['age_at_completion'] <= 7
    completed['is_sameday']  = completed['age_at_completion'] == 0

    total = len(completed)
    inbound_count = completed['is_inbound'].sum()
    sameday_count = completed['is_sameday'].sum()
    print(f"\nCompleted tasks:       {total:,}")
    print(f"Same-day (0 days):     {sameday_count:,}  ({sameday_count/total*100:.1f}%)")
    print(f"Inbound within 7 days: {inbound_count:,}  ({inbound_count/total*100:.1f}%)")
    print(f"Pre-existing (>7d):    {total - inbound_count:,}  ({(total-inbound_count)/total*100:.1f}%)")

    # ── Load evaluation snapshots to align with the 100-week window ──────────
    eval_path = OUTPUTS / 'evaluation_results.json'
    with open(eval_path) as f:
        eval_rows = json.load(f)

    snap_dates = sorted(r['snapshot_date'] for r in eval_rows)
    if not snap_dates:
        print("No evaluation results found.")
        return

    window_start = pd.Timestamp(snap_dates[0])
    window_end   = pd.Timestamp(snap_dates[-1]) + pd.Timedelta(days=60)
    n_weeks      = len(snap_dates)

    in_window = completed[
        (completed['completeddate'] >= window_start) &
        (completed['completeddate'] <= window_end)
    ]

    inbound_w = in_window['is_inbound'].sum()
    sameday_w = in_window['is_sameday'].sum()
    total_w   = len(in_window)
    print(f"\nWithin evaluation window ({snap_dates[0]} → {snap_dates[-1]}):")
    print(f"  Completed tasks:       {total_w:,}")
    print(f"  Same-day:              {sameday_w:,}  ({sameday_w/total_w*100:.1f}%)")
    print(f"  Inbound within 7 days: {inbound_w:,}  ({inbound_w/total_w*100:.1f}%)")

    # ── Weekly revenue split ──────────────────────────────────────────────────
    rows = []
    for i, snap in enumerate(eval_rows):
        snap_date = pd.Timestamp(snap['snapshot_date'])
        prev_date = pd.Timestamp(eval_rows[i-1]['snapshot_date']) if i > 0 else snap_date - pd.Timedelta(weeks=1)
        rev_end   = snap_date + pd.Timedelta(days=60)

        week_tasks = in_window[
            (in_window['completeddate'] > prev_date) &
            (in_window['completeddate'] <= snap_date)
        ]

        sameday_clients    = set(week_tasks[week_tasks['is_inbound']  ]['client_id'].dropna().astype(int))
        preexist_clients   = set(week_tasks[~week_tasks['is_inbound'] ]['client_id'].dropna().astype(int))
        all_clients        = sameday_clients | preexist_clients
        only_sameday       = sameday_clients - preexist_clients
        only_preexist      = preexist_clients - sameday_clients
        both               = sameday_clients & preexist_clients

        def rev(cids):
            if not cids:
                return 0.0
            mask = (
                invoices['userid'].isin(cids) &
                invoices['datepaid'].notna() &
                (invoices['datepaid'] > snap_date) &
                (invoices['datepaid'] <= rev_end)
            )
            return float(invoices.loc[mask, 'total'].sum())

        rows.append({
            'snapshot_date':        snap['snapshot_date'],
            'actual_revenue_60d':   snap['actual_revenue_60d'],
            'n_sameday_tasks':      week_tasks['is_inbound'].sum(),
            'n_preexist_tasks':     (~week_tasks['is_inbound']).sum(),
            'n_all_clients':        len(all_clients),
            'n_only_sameday':       len(only_sameday),
            'n_only_preexist':      len(only_preexist),
            'n_both':               len(both),
            'rev_only_sameday':     rev(only_sameday),
            'rev_only_preexist':    rev(only_preexist),
            'rev_both':             rev(both),
            'rev_addressable':      rev(only_preexist) + rev(both),  # schedulable revenue
        })

    df = pd.DataFrame(rows)
    active = df[df['actual_revenue_60d'] > 0]
    n = len(active)

    print(f"\n{'='*65}")
    print(f"INBOUND (≤7 days) vs PRE-EXISTING TASK REVENUE SPLIT")
    print(f"  Weeks with revenue > 0: {n}  (out of {n_weeks})")
    print(f"{'='*65}")

    avg_actual     = active['actual_revenue_60d'].mean()
    avg_sameday    = active['rev_only_sameday'].mean()
    avg_preexist   = active['rev_only_preexist'].mean()
    avg_both       = active['rev_both'].mean()
    avg_addressable = active['rev_addressable'].mean()

    print(f"  {'Category':<40} {'Avg $/wk':>9}  {'% of total':>10}")
    print(f"  {'-'*40}  {'-'*9}  {'-'*10}")
    print(f"  {'Total actual worker':<40} ${avg_actual:>8,.0f}  {'100%':>10}")
    print(f"  {'   Clients with ONLY inbound tasks (≤7d)':<40} ${avg_sameday:>8,.0f}  {avg_sameday/avg_actual*100:>9.1f}%")
    print(f"  {'   Clients with ONLY pre-existing tasks':<40} ${avg_preexist:>8,.0f}  {avg_preexist/avg_actual*100:>9.1f}%")
    print(f"  {'   Clients with BOTH types':<40} ${avg_both:>8,.0f}  {avg_both/avg_actual*100:>9.1f}%")
    print(f"  {'Schedulable (pre-existing + both)':<40} ${avg_addressable:>8,.0f}  {avg_addressable/avg_actual*100:>9.1f}%")
    print(f"  {'Inbound-only (unreachable by algorithm)':<40} ${avg_sameday:>8,.0f}  {avg_sameday/avg_actual*100:>9.1f}%")
    print(f"{'='*65}")

    # ── Load agent comparison for context ────────────────────────────────────
    agent_path = OUTPUTS / 'agent_comparison.json'
    if agent_path.exists():
        with open(agent_path) as f:
            agent_rows = json.load(f)
        agent_by_date = {r['snapshot_date']: r for r in agent_rows}

        algo_revs = {}
        for snap in eval_rows:
            d = snap['snapshot_date']
            ar = agent_by_date.get(d, {})
            algo_revs[d] = {
                'agent':   ar.get('agent_avg_revenue', 0) or 0,
                'php':     ar.get('php_avg_revenue',   0) or 0,
                'imp':     ar.get('imp_avg_revenue',   0) or 0,
            }

        valid_dates = [r['snapshot_date'] for r in active.to_dict('records')]
        n_v = len(valid_dates)
        if n_v:
            avg_agent = sum(algo_revs.get(d, {}).get('agent', 0) for d in valid_dates) / n_v
            avg_php   = sum(algo_revs.get(d, {}).get('php',   0) for d in valid_dates) / n_v
            avg_imp   = sum(algo_revs.get(d, {}).get('imp',   0) for d in valid_dates) / n_v

            print(f"\nALGORITHM PERFORMANCE vs ADDRESSABLE REVENUE (${avg_addressable:,.0f}/wk)")
            print(f"{'='*65}")
            print(f"  {'Method':<35} {'Avg $/wk':>9}  {'vs total':>9}  {'vs schedulable':>14}")
            print(f"  {'-'*35}  {'-'*9}  {'-'*9}  {'-'*14}")
            print(f"  {'Actual worker (total)':<35} ${avg_actual:>8,.0f}  {'1.00×':>9}  {avg_actual/avg_addressable:>12.2f}×")
            print(f"  {'Actual worker (schedulable only)':<35} ${avg_addressable:>8,.0f}  {avg_addressable/avg_actual:>8.2f}×  {'1.00×':>14}")
            print(f"  {'Claude agent':<35} ${avg_agent:>8,.0f}  {avg_agent/avg_actual:>8.2f}×  {avg_agent/avg_addressable:>12.2f}×")
            print(f"  {'PHP formula (fixed)':<35} ${avg_php:>8,.0f}  {avg_php/avg_actual:>8.2f}×  {avg_php/avg_addressable:>12.2f}×")
            print(f"  {'Improved formula':<35} ${avg_imp:>8,.0f}  {avg_imp/avg_actual:>8.2f}×  {avg_imp/avg_addressable:>12.2f}×")
            print(f"{'='*65}")
            print(f"\nKey insight: {avg_sameday/avg_actual*100:.0f}% of actual revenue (${avg_sameday:,.0f}/wk)")
            print(f"comes from inbound-only clients — no algorithm can schedule these.")
            print(f"Against schedulable revenue, Claude agent reaches {avg_agent/avg_addressable:.0%} efficiency.")

    # save
    out = OUTPUTS / 'sameday_analysis.json'
    df.to_json(out, orient='records', indent=2)
    print(f"\nDetailed weekly breakdown saved to {out}")


if __name__ == '__main__':
    main()
