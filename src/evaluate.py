import json
import pandas as pd
import numpy as np
from pathlib import Path
from src.prioritize import build_ranked_snapshots, AVG_HOURS_PER_TASK

OUTPUTS = Path(__file__).parent.parent / 'outputs'


def _revenue_collected(invoices: pd.DataFrame, client_ids: set, after: pd.Timestamp, before: pd.Timestamp) -> float:
    """Sum of invoices paid by `client_ids` in the window (after, before]."""
    mask = (
        invoices['userid'].isin(client_ids) &
        invoices['datepaid'].notna() &
        (invoices['datepaid'] > after) &
        (invoices['datepaid'] <= before)
    )
    return float(invoices.loc[mask, 'total'].sum())


def revenue_collected(snap_date: str, client_ids: set, dfs: dict, window_days: int = 60) -> float:
    """Public helper: revenue paid by client_ids in the window_days following snap_date."""
    invoices = dfs['invoices']
    after    = pd.Timestamp(snap_date)
    before   = after + pd.Timedelta(days=window_days)
    return _revenue_collected(invoices, client_ids, after, before)


def evaluate(dfs: dict, n_weeks: int = 100) -> pd.DataFrame:
    invoices     = dfs['invoices'].copy()
    projecttasks = dfs['projecttasks'].copy()
    projects     = dfs['projects'].copy()

    # task → client_id lookup
    projects['invoiceid'] = pd.to_numeric(projects['invoiceids'].replace('', pd.NA), errors='coerce').astype('Int64')
    task_client = (
        projecttasks[['id', 'projectid', 'completeddate']]
        .merge(projects[['id', 'userid']], left_on='projectid', right_on='id', how='left', suffixes=('', '_proj'))
        .rename(columns={'userid': 'client_id'})
        [['id', 'client_id', 'completeddate']]
    )

    # ensure datetimes
    task_client['completeddate'] = pd.to_datetime(task_client['completeddate'], errors='coerce')
    invoices['datepaid'] = pd.to_datetime(invoices['datepaid'], errors='coerce')
    invoices['date']     = pd.to_datetime(invoices['date'], errors='coerce')

    ranked_snaps = build_ranked_snapshots(n_weeks=n_weeks)
    ranked_snaps.sort(key=lambda s: s['snapshot_date'])

    rows = []
    for i, snap in enumerate(ranked_snaps):
        snap_date  = pd.Timestamp(snap['snapshot_date'])
        # work window: from the previous snapshot date to this one
        prev_date  = pd.Timestamp(ranked_snaps[i - 1]['snapshot_date']) if i > 0 else snap_date - pd.Timedelta(weeks=1)
        # revenue window: tasks completed this week → revenue paid within avg_days_to_pay (capped 60d)
        rev_window_end = snap_date + pd.Timedelta(days=60)

        # ── ACTUAL work done this period ───────────────────────────────────────
        actual_completed = task_client[
            (task_client['completeddate'] > prev_date) &
            (task_client['completeddate'] <= snap_date)
        ]
        actual_client_ids  = set(actual_completed['client_id'].dropna().astype(int))
        actual_task_count  = len(actual_completed)
        actual_hours_est   = actual_task_count * AVG_HOURS_PER_TASK

        # ── RECOMMENDED tasks from the prior snapshot ──────────────────────────
        prior_snap = ranked_snaps[i - 1] if i > 0 else snap

        # PHP formula
        php_avg   = prior_snap['avg_budget_tasks']
        php_upper = prior_snap['upper_budget_tasks']
        php_avg_clients   = set(t['client_id'] for t in prior_snap['ranked_tasks'][:php_avg]   if t['client_id'])
        php_upper_clients = set(t['client_id'] for t in prior_snap['ranked_tasks'][:php_upper] if t['client_id'])

        # Improved formula
        imp_avg   = prior_snap['avg_budget_tasks_improved']
        imp_upper = prior_snap['upper_budget_tasks_improved']
        imp_avg_clients   = set(t['client_id'] for t in prior_snap['ranked_tasks_improved'][:imp_avg]   if t['client_id'])
        imp_upper_clients = set(t['client_id'] for t in prior_snap['ranked_tasks_improved'][:imp_upper] if t['client_id'])

        # ── REVENUE attribution ────────────────────────────────────────────────
        rev_actual    = _revenue_collected(invoices, actual_client_ids,  snap_date, rev_window_end)
        rev_php_avg   = _revenue_collected(invoices, php_avg_clients,    snap_date, rev_window_end)
        rev_php_upper = _revenue_collected(invoices, php_upper_clients,  snap_date, rev_window_end)
        rev_imp_avg   = _revenue_collected(invoices, imp_avg_clients,    snap_date, rev_window_end)
        rev_imp_upper = _revenue_collected(invoices, imp_upper_clients,  snap_date, rev_window_end)

        rows.append({
            'snapshot_date':           snap['snapshot_date'],
            'open_tasks':              snap['open_task_count'],

            # actual worker
            'actual_tasks_completed':  actual_task_count,
            'actual_hours_est':        round(actual_hours_est, 1),
            'actual_client_count':     len(actual_client_ids),
            'actual_revenue_60d':      round(rev_actual, 2),

            # PHP formula
            'rec_avg_task_count':      php_avg,
            'rec_avg_client_count':    len(php_avg_clients),
            'rec_avg_revenue_60d':     round(rev_php_avg, 2),
            'rec_upper_revenue_60d':   round(rev_php_upper, 2),
            'overlap_avg':             len(actual_client_ids & php_avg_clients),

            # Improved formula
            'imp_avg_revenue_60d':     round(rev_imp_avg, 2),
            'imp_upper_revenue_60d':   round(rev_imp_upper, 2),
            'imp_overlap_avg':         len(actual_client_ids & imp_avg_clients),
        })

    return pd.DataFrame(rows)


def print_summary(df: pd.DataFrame) -> None:
    valid = df[df['actual_revenue_60d'] > 0]
    n = len(valid)
    act = valid['actual_revenue_60d'].mean()

    def ratio(col): return valid[col].mean() / act if act else 0
    def wins(col):  return (valid[col] > valid['actual_revenue_60d']).sum()

    print('=' * 65)
    print('METHOD COMPARISON — avg revenue/week, 60-day window')
    print(f'  Weeks with revenue > 0:  {n}')
    print()
    print(f'  {"Method":<30} {"Avg $/wk":>10}  {"vs actual":>10}  {"Weeks wins":>10}')
    print(f'  {"-"*30}  {"-"*10}  {"-"*10}  {"-"*10}')
    print(f'  {"Actual worker":<30} ${act:>9,.0f}  {"1.00×":>10}  {"—":>10}')
    for label, col in [
        ('PHP formula (avg ~19h)',      'rec_avg_revenue_60d'),
        ('PHP formula (upper ~36h)',    'rec_upper_revenue_60d'),
        ('Improved formula (avg)',      'imp_avg_revenue_60d'),
        ('Improved formula (upper)',    'imp_upper_revenue_60d'),
    ]:
        if col in valid.columns:
            avg = valid[col].mean()
            print(f'  {label:<30} ${avg:>9,.0f}  {ratio(col):>9.2f}×  {wins(col):>9}/{n}')
    print('=' * 65)


def save_results(df: pd.DataFrame) -> None:
    csv_path = OUTPUTS / 'evaluation_results.csv'
    df.to_csv(csv_path, index=False)
    print(f'Saved {csv_path}')

    # also save per-week detail as JSON for agent use
    json_path = OUTPUTS / 'evaluation_results.json'
    df.to_json(json_path, orient='records', indent=2)
    print(f'Saved {json_path}')
