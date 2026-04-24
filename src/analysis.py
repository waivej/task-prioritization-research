import pandas as pd


def build_views(dfs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    clients      = dfs['clients']
    invoices     = dfs['invoices']
    projects     = dfs['projects']
    projecttimes = dfs['projecttimes']

    name_lookup = clients[['id', 'client_name']]

    # invoices enriched with client name
    invoices_c = invoices.merge(
        name_lookup, left_on='userid', right_on='id', how='left', suffixes=('', '_c')
    ).drop(columns='id_c')

    # projects enriched with client name + linked invoice
    projects_c = projects.merge(
        name_lookup, left_on='userid', right_on='id', how='left', suffixes=('', '_c')
    ).drop(columns='id_c')
    projects_c = projects_c.merge(
        invoices[['id', 'total', 'status']].rename(
            columns={'id': 'inv_id', 'total': 'invoice_total', 'status': 'invoice_status'}
        ),
        left_on='invoiceid', right_on='inv_id', how='left'
    ).drop(columns='inv_id')

    # hours per project (valid rows only)
    times_valid = projecttimes[~projecttimes['orphaned']].copy()
    hours_pp = (
        times_valid.groupby('projectid')['duration_hours']
        .sum()
        .reset_index()
        .rename(columns={'duration_hours': 'total_hours'})
    )
    projects_h = projects_c.merge(
        hours_pp, left_on='id', right_on='projectid', how='left'
    ).drop(columns='projectid')
    projects_h['total_hours'] = projects_h['total_hours'].fillna(0).round(2)

    # paid invoices with revenue source tag
    paid = invoices_c[invoices_c['status'] == 'Paid'].copy()
    project_invoice_ids = set(projects['invoiceid'].dropna().astype(int))
    paid['source'] = paid['id'].apply(
        lambda x: 'Project' if x in project_invoice_ids else 'Hosting/Other'
    )

    return {
        'invoices_c':  invoices_c,
        'projects_h':  projects_h,
        'paid':        paid,
        'times_valid': times_valid,
    }


def revenue_by_year(paid: pd.DataFrame) -> pd.Series:
    return paid.groupby('year')['total'].sum()


def top_clients(paid: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return (
        paid.groupby('client_name')['total']
        .agg(lifetime_revenue='sum', invoice_count='count')
        .sort_values('lifetime_revenue', ascending=False)
        .head(n)
    )


def project_profitability(projects_h: pd.DataFrame) -> pd.DataFrame:
    df = projects_h[
        projects_h['invoice_total'].notna() &
        (projects_h['total_hours'] > 0) &
        (projects_h['invoice_status'] == 'Paid')
    ].copy()
    df['implied_rate'] = (df['invoice_total'] / df['total_hours']).round(2)
    return df


def aging_report(invoices_c: pd.DataFrame) -> pd.DataFrame:
    unpaid = invoices_c[invoices_c['status'] == 'Unpaid'].copy()
    unpaid['age_days'] = (pd.Timestamp.now() - unpaid['date']).dt.days
    bins   = [0, 30, 60, 90, 180, 365, float('inf')]
    labels = ['0-30', '31-60', '61-90', '91-180', '181-365', '365+']
    unpaid['bucket'] = pd.cut(unpaid['age_days'], bins=bins, labels=labels)
    return unpaid.groupby('bucket', observed=True)['total'].agg(amount='sum', invoices='count')
