"""
Example version of clean.py for public release.

The hosting cost rules below are illustrative placeholders. In production,
each cost_fn reflects the actual wholesale cost of that service type.
Replace these values with your own before running against real data.

To use: rename this file to clean.py (or update imports accordingly).
"""

import pandas as pd


def clean(dfs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    dfs = {k: v.copy() for k, v in dfs.items()}

    clients = dfs['clients']
    invoices = dfs['invoices']
    projects = dfs['projects']
    projecttasks = dfs['projecttasks']
    projecttimes = dfs['projecttimes']

    # date columns
    for col in ['startdate', 'expdate']:
        clients[col] = pd.to_datetime(clients[col], errors='coerce')
    for col in ['date', 'duedate', 'datepaid']:
        invoices[col] = pd.to_datetime(invoices[col], errors='coerce')
    dfs['accounts']['date'] = pd.to_datetime(dfs['accounts']['date'], errors='coerce')
    for col in ['nextduedate', 'nextinvoicedate']:
        dfs['hosting'][col] = pd.to_datetime(dfs['hosting'][col], errors='coerce')
    for col in ['created', 'duedate', 'completed', 'lastmodified']:
        projects[col] = pd.to_datetime(projects[col], errors='coerce')
    for col in ['created', 'duedate', 'completeddate']:
        projecttasks[col] = pd.to_datetime(projecttasks[col], errors='coerce')

    # projecttimes: unix timestamps → datetime, add duration, flag orphans
    projecttimes['start'] = pd.to_datetime(projecttimes['start'], unit='s', errors='coerce')
    projecttimes['end']   = pd.to_datetime(projecttimes['end'],   unit='s', errors='coerce')
    projecttimes['duration_hours'] = (
        (projecttimes['end'] - projecttimes['start']).dt.total_seconds() / 3600
    )
    projecttimes['orphaned'] = (
        ~projecttimes['projectid'].isin(set(projects['id'])) |
        ~projecttimes['taskid'].isin(set(projecttasks['id']))
    )

    # projects: parse invoiceid, add duration
    projects['invoiceid'] = pd.to_numeric(
        projects['invoiceids'].replace('', pd.NA), errors='coerce'
    ).astype('Int64')
    projects['duration_days'] = (projects['completed'] - projects['created']).dt.days

    # invoices: derived columns
    invoices['days_to_pay'] = (invoices['datepaid'] - invoices['date']).dt.days
    invoices['year'] = invoices['date'].dt.year

    # status columns → categorical
    clients['status']  = clients['status'].astype('category')
    invoices['status'] = invoices['status'].astype('category')
    projects['status'] = projects['status'].astype('category')

    # client display name
    clients['client_name'] = clients['companyname'].where(
        clients['companyname'].str.strip().ne(''),
        clients['firstname'] + ' ' + clients['lastname']
    )

    # ── Invoice item classification ───────────────────────────────────────────
    # Each rule is (description_keyword, revenue_fn, cost_fn).
    # revenue_fn: how much of the invoice item amount counts as hosting revenue.
    # cost_fn:    your estimated wholesale cost for that service type.
    #
    # The cost values below are ILLUSTRATIVE PLACEHOLDERS — replace them with
    # your actual provider costs before using this for margin analysis.
    items = dfs['invoiceitems'].copy()
    items['hosting_revenue'] = 0.0
    items['hosting_cost']    = 0.0
    desc = items['description'].fillna('')

    rules = [
        # (keyword,                         revenue_fn,      cost_fn)
        ('Domain Renewal',                  lambda a: a,     lambda a: a * 0.85),   # ~85% passthrough
        ('Site In Development',             lambda a: a,     lambda a: 0.0),        # no direct cost
        ('Premium Managed Hosting',         lambda a: a,     lambda a: 50.0),       # illustrative flat rate
        ('Standard Hosting',                lambda a: a,     lambda a: 20.0),       # illustrative flat rate
        ('Basic Hosting',                   lambda a: a,     lambda a: 10.0),       # illustrative flat rate
        ('Free Website Hosting',            lambda a: a,     lambda a: 5.0),        # illustrative flat rate
        ('website forwarding',              lambda a: a,     lambda a: 2.0),        # illustrative flat rate
        ('Reseller Hosting',                lambda a: a,     lambda a: a),          # full passthrough
        ('Hosted Exchange',                 lambda a: a,     lambda a: 20.0),       # illustrative flat rate
        ('Cloud Server',                    lambda a: a,     lambda a: a),          # full passthrough
        ('Email Hosting',                   lambda a: a,     lambda a: 5.0),        # illustrative flat rate
        ('DNS Hosting',                     lambda a: a,     lambda a: 2.0),        # illustrative flat rate
    ]

    for keyword, rev_fn, cost_fn in rules:
        mask = desc.str.contains(keyword, case=False, na=False)
        items.loc[mask, 'hosting_revenue'] = items.loc[mask, 'amount'].apply(rev_fn)
        items.loc[mask, 'hosting_cost']    = items.loc[mask, 'amount'].apply(cost_fn)

    # DomainRegister / DomainTransfer: type field catches these even when
    # the description doesn't contain the word "domain"
    domain_type_mask = items['type'].isin({'DomainRegister', 'DomainTransfer'})
    items.loc[domain_type_mask, 'hosting_revenue'] = items.loc[domain_type_mask, 'amount']
    items.loc[domain_type_mask, 'hosting_cost']    = items.loc[domain_type_mask, 'amount'] * 0.85

    items['is_hosting'] = items['hosting_revenue'] > 0
    dfs['invoiceitems'] = items

    dfs.update({
        'clients': clients, 'invoices': invoices, 'projects': projects,
        'projecttasks': projecttasks, 'projecttimes': projecttimes,
    })
    return dfs
