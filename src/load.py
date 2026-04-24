import pandas as pd
from pathlib import Path

DATA = Path(__file__).parent.parent / 'data'
LOAD = dict(sep=';', encoding='latin1')


def load_all() -> dict[str, pd.DataFrame]:
    tables = {
        'clients':      'clients.csv',
        'invoices':     'invoices.csv',
        'accounts':     'accounts.csv',
        'hosting':      'hosting.csv',
        'projects':     'projects.csv',
        'projecttasks': 'projecttasks.csv',
        'projecttimes': 'projecttimes.csv',
    }
    dfs = {name: pd.read_csv(DATA / filename, **LOAD) for name, filename in tables.items()}
    dfs['invoiceitems'] = pd.read_csv(DATA / 'invoiceitems.csv', **LOAD)
    return dfs
