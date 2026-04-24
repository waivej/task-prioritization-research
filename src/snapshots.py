import json
import pandas as pd
from pathlib import Path

OUTPUTS = Path(__file__).parent.parent / 'outputs' / 'snapshots'


def _client_metrics_at(invoices: pd.DataFrame, items_inv: pd.DataFrame,
                        date: pd.Timestamp, window_days: int = 600) -> dict:
    """
    Returns per-client metrics known as of `date`.

    Adds invoice-item-derived fields that mirror the PHP heuristic:
      - hosting_revenue_600d  (GetCustomerInvoiceHosting)
      - hosting_cost_600d     (GetCustomerInvoiceCost)
      - income_600d           (GetCustomerInvoiceTotal)
      - hourly_600d           = income - hosting_revenue
      - hours_600d            (computed separately, passed in via dfs)
    """
    window_start = date - pd.Timedelta(days=window_days)

    # ── payment history (all time up to date) ───────────────────────────────
    known = invoices[invoices['date'] <= date].copy()
    paid  = known[known['datepaid'].notna() & (known['datepaid'] <= date)]
    paid_stats = (
        paid.groupby('userid')
        .agg(
            lifetime_revenue=('total', 'sum'),
            invoices_paid_count=('total', 'count'),
            avg_days_to_pay=('days_to_pay', 'mean'),
            median_days_to_pay=('days_to_pay', 'median'),
        )
    )

    unpaid_mask = (
        known['status'].isin(['Unpaid', 'Paid']) &
        (known['datepaid'].isna() | (known['datepaid'] > date))
    )
    unpaid_stats = (
        known[unpaid_mask].groupby('userid')
        .agg(
            unpaid_invoice_count=('total', 'count'),
            unpaid_invoice_total=('total', 'sum'),
        )
    )

    # ── 600-day income window (mirrors PHP GetCustomerInvoiceTotal) ──────────
    window_mask = (
        invoices['duedate'].notna() &
        (invoices['duedate'] >= window_start) &
        (invoices['duedate'] <= date + pd.Timedelta(days=45)) &
        (invoices['status'].isin(['Paid', 'Unpaid']))
    )
    income_600 = (
        invoices[window_mask]
        .groupby('userid')['total']
        .sum()
        .rename('income_600d')
    )

    # ── invoice items: hosting revenue + cost in the same window ────────────
    items_w = items_inv[
        items_inv['inv_duedate'].notna() &
        (items_inv['inv_duedate'] >= window_start) &
        (items_inv['inv_duedate'] <= date + pd.Timedelta(days=45)) &
        (items_inv['inv_status'].isin(['Paid', 'Unpaid']))
    ]
    hosting_rev_600 = (
        items_w[items_w['is_hosting']]
        .groupby('inv_userid')['hosting_revenue']
        .sum()
        .rename('hosting_revenue_600d')
    )
    hosting_cost_600 = (
        items_w[items_w['is_hosting']]
        .groupby('inv_userid')['hosting_cost']
        .sum()
        .rename('hosting_cost_600d')
    )

    # ── invoice due soon (next 45 days) ─────────────────────────────────────
    due_soon = (
        invoices[
            invoices['duedate'].notna() &
            (invoices['duedate'] >= date) &
            (invoices['duedate'] <= date + pd.Timedelta(days=45)) &
            (invoices['status'].isin(['Paid', 'Unpaid']))
        ]
        .groupby('userid')['total']
        .sum()
        .rename('inv_due_soon')
    )

    # ── combine ──────────────────────────────────────────────────────────────
    metrics = (
        paid_stats
        .join(unpaid_stats,       how='outer')
        .join(income_600,         how='outer')
        .join(hosting_rev_600,    how='outer')
        .join(hosting_cost_600,   how='outer')
        .join(due_soon,           how='outer')
        .fillna(0)
    )

    metrics['has_unpaid_invoices'] = metrics['unpaid_invoice_count'] > 0
    metrics['hourly_600d'] = (metrics['income_600d'] - metrics['hosting_revenue_600d']).clip(lower=0)

    for col in ['lifetime_revenue', 'avg_days_to_pay', 'median_days_to_pay',
                'unpaid_invoice_total', 'income_600d', 'hosting_revenue_600d',
                'hosting_cost_600d', 'hourly_600d', 'inv_due_soon']:
        metrics[col] = metrics[col].round(2)

    no_paid = metrics['invoices_paid_count'] == 0
    metrics.loc[no_paid, 'avg_days_to_pay']    = None
    metrics.loc[no_paid, 'median_days_to_pay'] = None

    return metrics.to_dict(orient='index')


def generate(dfs: dict, n_weeks: int = 100, start_weeks_ago: int = 10) -> None:
    """
    Generate n_weeks weekly snapshots ending start_weeks_ago weeks in the past.

    start_weeks_ago=10 ensures every snapshot's 60-day (~8.6-week) payment
    collection window has already closed before we evaluate it.
    """
    OUTPUTS.mkdir(parents=True, exist_ok=True)

    clients      = dfs['clients']
    invoices     = dfs['invoices'].copy()
    items        = dfs['invoiceitems'].copy()
    projects     = dfs['projects']
    projecttasks = dfs['projecttasks'].copy()
    projecttimes = dfs['projecttimes'].copy()

    # pre-join items → invoices; invoiceitems has its own userid+duedate columns,
    # so rename all invoice fields to avoid collisions
    items_inv = items.merge(
        invoices[['id', 'userid', 'duedate', 'status']].rename(columns={
            'id':     'invoiceid',
            'userid': 'inv_userid',
            'duedate':'inv_duedate',
            'status': 'inv_status',
        }),
        on='invoiceid', how='left'
    )

    # pre-join tasks → project → client
    proj_client = projects[['id', 'userid']].rename(columns={'id': 'project_id', 'userid': 'client_id'})
    tasks = projecttasks.merge(proj_client, left_on='projectid', right_on='project_id', how='left')
    tasks = tasks.merge(
        clients[['id', 'client_name']].rename(columns={'id': 'cid'}),
        left_on='client_id', right_on='cid', how='left'
    ).drop(columns='cid')

    # pre-join projecttimes → projects for hours-per-client lookup
    times_proj = projecttimes[~projecttimes['orphaned']].merge(
        projects[['id', 'userid']].rename(columns={'id': 'projectid_p', 'userid': 'client_id'}),
        left_on='projectid', right_on='projectid_p', how='left'
    )

    invoices = invoices[invoices['date'].notna()].copy()
    invoices['days_to_pay'] = (invoices['datepaid'] - invoices['date']).dt.days

    start = pd.Timestamp.now().normalize() - pd.Timedelta(weeks=start_weeks_ago)
    weeks = [start - pd.Timedelta(weeks=i) for i in range(n_weeks)]

    for week_index, snap_date in enumerate(weeks):
        snap_str    = snap_date.strftime('%Y-%m-%d')
        window_start = snap_date - pd.Timedelta(days=600)

        # open tasks at this snapshot date
        open_mask = (
            (tasks['created'] <= snap_date) &
            ((tasks['completed'] == 0) |
             tasks['completeddate'].isna() |
             (tasks['completeddate'] > snap_date))
        )
        open_tasks = tasks[open_mask].copy()

        # hours per client in trailing 600 days
        hours_mask = (
            times_proj['start'].notna() &
            (times_proj['start'] >= window_start) &
            (times_proj['start'] <= snap_date)
        )
        hours_by_client = (
            times_proj[hours_mask]
            .groupby('client_id')['duration_hours']
            .sum()
        )

        # days since last work per client
        recent_mask = (
            times_proj['start'].notna() &
            (times_proj['start'] < snap_date)
        )
        last_work_by_client = (
            times_proj[recent_mask]
            .groupby('client_id')['start']
            .max()
        )

        # client metrics
        metrics = _client_metrics_at(invoices, items_inv, snap_date)

        records = []
        for _, row in open_tasks.iterrows():
            client_id = int(row['client_id']) if pd.notna(row['client_id']) else None
            m = metrics.get(client_id, {})

            hours_600d = float(hours_by_client.get(client_id, 0))
            last_work  = last_work_by_client.get(client_id)
            days_idle  = int((snap_date - last_work).days) if pd.notna(last_work) else 9999

            hourly_600d = float(m.get('hourly_600d', 0))
            import math
            raw_rate = math.floor(hourly_600d / (hours_600d + 0.001))
            if raw_rate == 0:
                raw_rate = 50

            records.append({
                'task_id':       int(row['id']),
                'project_id':    int(row['project_id']) if pd.notna(row['project_id']) else None,
                'task':          row['task'] if pd.notna(row['task']) else '',
                'created':       row['created'].strftime('%Y-%m-%d') if pd.notna(row['created']) else None,
                'task_age_days': int((snap_date - row['created']).days) if pd.notna(row['created']) else None,
                'billed':        int(row['billed']) if pd.notna(row['billed']) else 0,
                'client_id':     client_id,
                'client_name':   row['client_name'] if pd.notna(row['client_name']) else None,
                'client_metrics': {
                    # payment history
                    'lifetime_revenue':       m.get('lifetime_revenue', 0),
                    'invoices_paid_count':    int(m.get('invoices_paid_count', 0)),
                    'avg_days_to_pay':        m.get('avg_days_to_pay'),
                    'median_days_to_pay':     m.get('median_days_to_pay'),
                    'has_unpaid_invoices':    bool(m.get('has_unpaid_invoices', False)),
                    'unpaid_invoice_count':   int(m.get('unpaid_invoice_count', 0)),
                    'unpaid_invoice_total':   m.get('unpaid_invoice_total', 0),
                    # PHP heuristic inputs (600-day window)
                    'income_600d':            m.get('income_600d', 0),
                    'hosting_revenue_600d':   m.get('hosting_revenue_600d', 0),
                    'hosting_cost_600d':      m.get('hosting_cost_600d', 0),
                    'hourly_600d':            m.get('hourly_600d', 0),
                    'hours_600d':             round(hours_600d, 2),
                    'implied_hourly_rate':    raw_rate,
                    # recency
                    'days_idle':              days_idle,
                    'inv_due_soon':           m.get('inv_due_soon', 0),
                },
            })

        snapshot = {
            'snapshot_date':   snap_str,
            'week_index':      week_index,
            'open_task_count': len(records),
            'open_tasks':      records,
        }

        out_path = OUTPUTS / f'snapshot_{snap_str}.json'
        with open(out_path, 'w') as f:
            json.dump(snapshot, f, indent=2)

        if week_index % 10 == 0:
            print(f'  [{week_index+1:3d}/100]  {snap_str}  open_tasks={len(records)}')

    print(f'Done. {n_weeks} snapshots written to {OUTPUTS}')
