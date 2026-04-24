"""
Replicates heauristic_task_rank.php scoring logic in Python.

Score = 12*age + 4*hourly_rate + 0.003*income + fresh_weight

Where (all computed over the trailing 600 days as of snapshot date):
  income       = sum of invoice totals (Paid or Unpaid) due between -600d and +45d
  hosting      = sum of hosting amounts for that client (proxy for invoice-item hosting costs)
  hourly       = income - hosting  (non-hosting revenue)
  hours        = hours logged on that client's projects
  hourly_rate  = floor(hourly / (hours + 0.001))  -- 50 if zero
  fresh_weight = 1000 if hourly_rate > 60 AND task age < 8 days, else 0
"""

import json
import math
import pandas as pd
from pathlib import Path

SNAPSHOTS_DIR = Path(__file__).parent.parent / 'outputs' / 'snapshots'

AVG_HOURS_PER_WEEK   = 19.0
UPPER_HOURS_PER_WEEK = 36.0
AVG_HOURS_PER_TASK   = 1.1


def _build_client_lookup(dfs: dict, snap_date: pd.Timestamp) -> dict:  # noqa: C901
    """
    Returns dict keyed by client_id with the stats the PHP computes:
      income, hosting, hourly, hours, hourly_rate
    All computed relative to snap_date over the trailing 600 days.
    """
    invoices     = dfs['invoices']
    projecttimes = dfs['projecttimes']
    projects     = dfs['projects']
    hosting_df   = dfs['hosting']

    window_start = snap_date - pd.Timedelta(days=600)
    window_end   = snap_date + pd.Timedelta(days=45)

    # income: invoice totals for Paid/Unpaid, duedate in window
    inv_mask = (
        invoices['duedate'].notna() &
        (invoices['duedate'] >= window_start) &
        (invoices['duedate'] <= window_end) &
        (invoices['status'].isin(['Paid', 'Unpaid']))
    )
    income_by_client = (
        invoices[inv_mask]
        .groupby('userid')['total']
        .sum()
        .rename('income')
    )

    # hosting revenue from invoice items (exact, mirrors PHP GetCustomerInvoiceHosting)
    items = dfs['invoiceitems']
    # join items → invoices to get userid and duedate
    items_inv = items.merge(
        invoices[['id', 'userid', 'duedate', 'status']].rename(columns={
            'id':     'invoiceid',
            'userid': 'inv_userid',
            'duedate':'inv_duedate',
            'status': 'inv_status',
        }),
        on='invoiceid', how='left'
    )
    items_window_mask = (
        items_inv['inv_duedate'].notna() &
        (items_inv['inv_duedate'] >= window_start) &
        (items_inv['inv_duedate'] <= window_end) &
        (items_inv['inv_status'].isin(['Paid', 'Unpaid']))
    )
    items_window = items_inv[items_window_mask]
    hosting_by_client = (
        items_window[items_window['is_hosting']]
        .groupby('inv_userid')['hosting_revenue']
        .sum()
        .rename('hosting')
    )

    # hours: projecttimes joined to projects for client, start in window
    times_mask = (
        projecttimes['start'].notna() &
        (projecttimes['start'] >= window_start) &
        (projecttimes['start'] <= snap_date) &
        (~projecttimes['orphaned'])
    )
    times = projecttimes[times_mask][['projectid', 'duration_hours']].copy()
    proj_user = projects[['id', 'userid']].rename(columns={'id': 'projectid', 'userid': 'client_id'})
    times = times.merge(proj_user, on='projectid', how='left')
    hours_by_client = (
        times.groupby('client_id')['duration_hours']
        .sum()
        .rename('hours')
    )

    # invoice due soon: amount on Paid/Unpaid invoices due within next 45 days
    due_soon_mask = (
        invoices['duedate'].notna() &
        (invoices['duedate'] >= snap_date) &
        (invoices['duedate'] <= snap_date + pd.Timedelta(days=45)) &
        (invoices['status'].isin(['Paid', 'Unpaid']))
    )
    inv_due_soon_by_client = (
        invoices[due_soon_mask]
        .groupby('userid')['total']
        .sum()
        .rename('inv_due_soon')
    )

    # days since last work: most recent projecttimes entry per client before snap
    times_all = projecttimes[
        projecttimes['start'].notna() &
        (projecttimes['start'] < snap_date) &
        (~projecttimes['orphaned'])
    ][['projectid', 'start']].copy()
    times_all = times_all.merge(proj_user, on='projectid', how='left')
    last_work = (
        times_all.groupby('client_id')['start']
        .max()
        .rename('last_work')
    )

    # combine
    stats = (
        pd.DataFrame(income_by_client)
        .join(hosting_by_client, how='outer')
        .join(hours_by_client.rename_axis('userid'), how='outer')
        .join(inv_due_soon_by_client, how='outer')
        .join(last_work.rename_axis('userid'), how='outer')
        .fillna({'income': 0, 'hosting': 0, 'hours': 0, 'inv_due_soon': 0})
    )

    result = {}
    for uid, row in stats.iterrows():
        income  = float(row['income'])
        hosting = float(row.get('hosting', 0))
        hours   = float(row.get('hours', 0))
        hourly  = income - hosting
        hourly_rate = math.floor(hourly / (hours + 0.001))
        if hourly_rate == 0:
            hourly_rate = 50   # same hack as PHP for IG tasks

        last = row.get('last_work')
        days_idle = (snap_date - last).days if pd.notna(last) else 9999

        result[int(uid)] = {
            'income':       round(income, 2),
            'hosting':      round(hosting, 2),
            'hourly':       round(hourly, 2),
            'hours':        round(hours, 2),
            'hourly_rate':  hourly_rate,
            'inv_due_soon': round(float(row.get('inv_due_soon', 0)), 2),
            'days_idle':    days_idle,
        }
    return result


def score_task(task: dict) -> tuple[float, dict]:
    """
    PHP formula — fully corrected version matching heauristic_task_rank.php.
    Fixes applied:
      1. Rate capped at $300/hr (prevents near-zero-hour clients dominating)
      2. +7.0 * inv_due_soon  (invoice imminence, corr=0.766 with 60d revenue)
      3. +500 * exp(-days_idle/14)  (recency bonus, 14-day half-life)
    """
    age  = task.get('task_age_days') or 0
    m    = task.get('client_metrics', {})

    income      = m.get('income_600d', 0)
    raw_rate    = m.get('implied_hourly_rate', 50) or 50
    hourly_rate = min(raw_rate, 300)
    inv_due     = m.get('inv_due_soon', 0)
    days_idle   = m.get('days_idle', 9999)

    fresh_weight  = 1000 if (hourly_rate > 60 and age < 8) else 0
    inv_signal    = 7.0 * inv_due
    recency_bonus = 500.0 * math.exp(-days_idle / 14.0)
    score = (12 * age) + (4 * hourly_rate) + (0.003 * income) + fresh_weight + inv_signal + recency_bonus

    return score, {
        'age_component':    round(12 * age, 2),
        'rate_component':   round(4 * hourly_rate, 2),
        'income_component': round(0.003 * income, 2),
        'fresh_weight':     fresh_weight,
        'inv_signal':       round(inv_signal, 2),
        'recency_bonus':    round(recency_bonus, 2),
        'hourly_rate':      hourly_rate,
        'raw_rate':         raw_rate,
        'inv_due_soon':     inv_due,
        'days_idle':        days_idle,
        'income':           income,
        'hours_600d':       m.get('hours_600d', 0),
    }


def score_task_original(task: dict) -> float:
    """
    Original Python heuristic (payment speed × revenue × age × unpaid penalty).
    Kept for comparison against the PHP formula.
    """
    m = task['client_metrics']
    avg_dtp       = m.get('avg_days_to_pay') or 50
    payment_speed = 1.0 / (1.0 + avg_dtp)
    lifetime      = m.get('lifetime_revenue') or 0
    revenue_weight = math.log1p(lifetime) / math.log1p(10_000)
    age            = task.get('task_age_days') or 0
    urgency        = math.log1p(age) / math.log1p(365)
    unpaid_total   = m.get('unpaid_invoice_total') or 0
    paid_count     = m.get('invoices_paid_count') or 1
    avg_invoice    = lifetime / paid_count if paid_count else 0
    unpaid_ratio   = unpaid_total / avg_invoice if avg_invoice > 0 else 0
    unpaid_penalty = max(0.0, 1.0 - min(unpaid_ratio * 0.5, 0.8))
    return payment_speed * revenue_weight * urgency * unpaid_penalty


def score_task_improved(task: dict) -> tuple[float, dict]:
    """
    Improved formula using client_metrics embedded in the snapshot task dict.
    score = php_base(rate capped $300) + 7*inv_due_soon + 500*exp(-days_idle/14)
    """
    age = task.get('task_age_days') or 0
    m   = task.get('client_metrics', {})

    income    = m.get('income_600d', 0)
    raw_rate  = m.get('implied_hourly_rate', 50) or 50
    inv_due   = m.get('inv_due_soon', 0)
    days_idle = m.get('days_idle', 9999)

    capped_rate  = max(50, min(raw_rate, 300))
    fresh_weight = 1000 if capped_rate > 60 and age < 8 else 0
    php_base     = (12 * age) + (4 * capped_rate) + (0.003 * income) + fresh_weight
    inv_signal   = 7.0 * inv_due
    recency_bonus = 500.0 * math.exp(-days_idle / 14.0)
    score = php_base + inv_signal + recency_bonus

    return score, {
        'php_base':      round(php_base, 2),
        'inv_signal':    round(inv_signal, 2),
        'recency_bonus': round(recency_bonus, 2),
        'capped_rate':   capped_rate,
        'raw_rate':      raw_rate,
        'inv_due_soon':  inv_due,
        'days_idle':     days_idle,
        'income':        income,
        'fresh_weight':  fresh_weight,
    }


def rank_snapshot(snapshot: dict, scorer=None) -> list[dict]:
    """
    Rank all open tasks in a snapshot using the given scorer function.
    scorer defaults to score_task (PHP formula).
    Client metrics are read from each task's embedded client_metrics dict.
    """
    if scorer is None:
        scorer = score_task

    scored = []
    for t in snapshot['open_tasks']:
        s, components = scorer(t)
        scored.append({
            **t,
            'priority_score':   round(s, 2),
            'score_components': components,
        })

    scored.sort(key=lambda x: x['priority_score'], reverse=True)

    cum_hours = 0.0
    for t in scored:
        cum_hours += AVG_HOURS_PER_TASK
        t['cumulative_hours']     = round(cum_hours, 2)
        t['within_avg_budget']    = cum_hours <= AVG_HOURS_PER_WEEK
        t['within_upper_budget']  = cum_hours <= UPPER_HOURS_PER_WEEK

    return scored


def build_ranked_snapshots(n_weeks: int = 100) -> list[dict]:
    """
    Load all snapshot JSONs, rank using both PHP and improved formulas.
    Returns list of dicts with ranked_tasks (PHP) and ranked_tasks_improved.
    """
    files = sorted(SNAPSHOTS_DIR.glob('snapshot_*.json'), reverse=True)[:n_weeks]
    results = []

    for i, path in enumerate(files):
        with open(path) as f:
            snap = json.load(f)

        ranked_php      = rank_snapshot(snap, scorer=score_task)
        ranked_improved = rank_snapshot(snap, scorer=score_task_improved)

        results.append({
            'snapshot_date':               snap['snapshot_date'],
            'week_index':                  snap['week_index'],
            'open_task_count':             snap['open_task_count'],
            'ranked_tasks':                ranked_php,
            'ranked_tasks_improved':       ranked_improved,
            'avg_budget_tasks':            sum(1 for t in ranked_php      if t['within_avg_budget']),
            'upper_budget_tasks':          sum(1 for t in ranked_php      if t['within_upper_budget']),
            'avg_budget_tasks_improved':   sum(1 for t in ranked_improved if t['within_avg_budget']),
            'upper_budget_tasks_improved': sum(1 for t in ranked_improved if t['within_upper_budget']),
        })
        if i % 10 == 0:
            print(f'  Ranked snapshot {i+1}/{len(files)}  ({snap["snapshot_date"]})')

    return results
