import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from pathlib import Path

OUTPUTS = Path(__file__).parent.parent / 'outputs'
USD = mticker.FuncFormatter(lambda x, _: f'${x:,.0f}')


def save(fig: plt.Figure, name: str) -> None:
    path = OUTPUTS / name
    fig.savefig(path, bbox_inches='tight', dpi=120)
    print(f'Saved {path}')
    plt.close(fig)


def plot_overview(paid: pd.DataFrame, times_valid: pd.DataFrame, invoices: pd.DataFrame) -> None:
    rev_by_year  = paid.groupby('year')['total'].sum()
    mix_by_year  = paid.groupby(['year', 'source'])['total'].sum().unstack(fill_value=0)
    hours_by_yr  = times_valid.groupby(times_valid['start'].dt.year)['duration_hours'].sum()
    inv_status   = invoices['status'].value_counts()

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle('Business Overview', fontsize=14, fontweight='bold')

    ax = axes[0, 0]
    ax.bar(rev_by_year.index, rev_by_year.values, color='steelblue')
    ax.yaxis.set_major_formatter(USD)
    ax.set_title('Paid Revenue by Year')
    ax.tick_params(axis='x', rotation=45)

    ax = axes[0, 1]
    mix_by_year.plot(kind='bar', stacked=True, ax=ax, color=['#2196F3', '#FF9800'])
    ax.yaxis.set_major_formatter(USD)
    ax.set_title('Revenue Mix: Project vs Hosting/Other')
    ax.tick_params(axis='x', rotation=45)

    ax = axes[1, 0]
    ax.bar(hours_by_yr.index, hours_by_yr.values, color='teal')
    ax.set_title('Hours Logged by Year')
    ax.set_ylabel('Hours')
    ax.tick_params(axis='x', rotation=45)

    ax = axes[1, 1]
    ax.pie(inv_status.values, labels=inv_status.index, autopct='%1.1f%%', startangle=90)
    ax.set_title('Invoice Status Breakdown')

    plt.tight_layout()
    save(fig, 'fig1_overview.png')


def plot_clients(paid: pd.DataFrame) -> None:
    top20 = paid.groupby('client_name')['total'].sum().sort_values(ascending=True).tail(20)
    all_c = paid.groupby('client_name')['total'].sum().sort_values(ascending=False)
    cumulative = all_c.cumsum() / all_c.sum() * 100
    n80 = (cumulative <= 80).sum() + 1

    fig, axes = plt.subplots(1, 2, figsize=(15, 7))
    fig.suptitle('Client Value', fontsize=14, fontweight='bold')

    ax = axes[0]
    ax.barh(top20.index, top20.values, color='steelblue')
    ax.xaxis.set_major_formatter(USD)
    ax.set_title('Top 20 Clients by Lifetime Revenue')

    ax = axes[1]
    ax.plot(range(1, len(cumulative) + 1), cumulative.values, color='steelblue')
    ax.axhline(80, color='red', linestyle='--', linewidth=0.8)
    ax.axvline(n80, color='orange', linestyle='--', linewidth=0.8, label=f'{n80} clients = 80% revenue')
    ax.set_title('Revenue Concentration')
    ax.set_xlabel('Clients ranked by revenue')
    ax.set_ylabel('Cumulative % of revenue')
    ax.legend(fontsize=8)

    plt.tight_layout()
    save(fig, 'fig2_clients.png')


def plot_projects(profitable: pd.DataFrame, task_velocity: pd.Series) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle('Project Profitability & Task Velocity', fontsize=14, fontweight='bold')

    ax = axes[0]
    ax.hist(profitable['implied_rate'], bins=30, color='#2196F3', edgecolor='white')
    ax.axvline(profitable['implied_rate'].median(), color='red', linestyle='--',
               label=f"Median ${profitable['implied_rate'].median():,.0f}/h")
    ax.set_title('Implied Hourly Rate')
    ax.set_xlabel('$/hour')
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.scatter(profitable['total_hours'], profitable['invoice_total'], alpha=0.4, s=20, color='teal')
    m, b = np.polyfit(profitable['total_hours'], profitable['invoice_total'], 1)
    x = np.linspace(0, profitable['total_hours'].max(), 100)
    ax.plot(x, m * x + b, 'r--', linewidth=1, label=f'${m:,.0f}/h trend')
    ax.set_title('Hours vs Invoice Total')
    ax.set_xlabel('Hours logged')
    ax.yaxis.set_major_formatter(USD)
    ax.legend(fontsize=8)

    ax = axes[2]
    ax.hist(task_velocity.clip(0, 90), bins=30, color='#FF9800', edgecolor='white')
    ax.axvline(task_velocity.median(), color='red', linestyle='--',
               label=f'Median {task_velocity.median():.0f} days')
    ax.set_title('Task Velocity (capped 90d)')
    ax.set_xlabel('Days')
    ax.legend(fontsize=8)

    plt.tight_layout()
    save(fig, 'fig3_projects.png')


def plot_hosting(invoices_c: pd.DataFrame, hosting: pd.DataFrame) -> None:
    unpaid = invoices_c[invoices_c['status'] == 'Unpaid'].copy()
    unpaid['age_days'] = (pd.Timestamp.now() - unpaid['date']).dt.days
    bins   = [0, 30, 60, 90, 180, 365, float('inf')]
    labels = ['0-30', '31-60', '61-90', '91-180', '181-365', '365+']
    unpaid['bucket'] = pd.cut(unpaid['age_days'], bins=bins, labels=labels)
    aging = unpaid.groupby('bucket', observed=True)['total'].sum()

    hosting['renewal_month'] = hosting['nextduedate'].dt.to_period('M')
    renewals = hosting.groupby('renewal_month')['amount'].sum().head(24)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle('Hosting & Receivables', fontsize=14, fontweight='bold')

    ax = axes[0]
    ax.bar(aging.index.astype(str), aging.values,
           color=['#4CAF50', '#8BC34A', '#FF9800', '#FF5722', '#F44336', '#B71C1C'])
    ax.yaxis.set_major_formatter(USD)
    ax.set_title('Unpaid Invoice Aging')
    ax.set_xlabel('Days overdue')

    ax = axes[1]
    ax.bar(range(len(renewals)), renewals.values, color='steelblue')
    ax.set_xticks(range(len(renewals)))
    ax.set_xticklabels([str(p) for p in renewals.index], rotation=45, ha='right', fontsize=7)
    ax.yaxis.set_major_formatter(USD)
    ax.set_title('Hosting Renewal Pipeline (next 24 months)')

    plt.tight_layout()
    save(fig, 'fig4_hosting.png')


def print_summary(dfs: dict, views: dict) -> None:
    paid        = views['paid']
    projects_h  = views['projects_h']
    times_valid = views['times_valid']
    invoices_c  = views['invoices_c']
    clients     = dfs['clients']
    hosting     = dfs['hosting']

    from src.analysis import project_profitability
    profitable  = project_profitability(projects_h)
    unpaid_amt  = invoices_c[invoices_c['status'] == 'Unpaid']['total'].sum()
    rev_by_year = paid.groupby('year')['total'].sum()
    all_c       = paid.groupby('client_name')['total'].sum().sort_values(ascending=False)
    n80         = (all_c.cumsum() / all_c.sum() <= 0.80).sum() + 1

    done = dfs['projecttasks'][dfs['projecttasks']['completed'] == 1].copy()
    done['velocity_days'] = (done['completeddate'] - done['created']).dt.days

    print('=' * 45)
    print(f"  Total lifetime revenue:    ${paid['total'].sum():>12,.2f}")
    print(f"  Hosting ARR estimate:      ${hosting['amount'].sum():>12,.2f}")
    print(f"  Outstanding (unpaid):      ${unpaid_amt:>12,.2f}")
    print(f"  Active clients:            {(clients['status'] == 'Active').sum():>12,}")
    print(f"  Peak revenue year:         {rev_by_year.idxmax()} (${rev_by_year.max():,.0f})")
    print(f"  Clients = 80% revenue:     {n80:>12,}")
    print(f"  Median implied rate:       ${profitable['implied_rate'].median():>11,.2f}/h")
    print(f"  Total billable hours:      {times_valid[times_valid['donotbill']==0]['duration_hours'].sum():>12,.1f}")
    print(f"  Median days to pay:        {paid['days_to_pay'].dropna().median():>12.0f}")
    print(f"  Median task velocity:      {done['velocity_days'].dropna().median():>11.0f} days")
    print('=' * 45)
