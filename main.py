from src.load import load_all
from src.clean import clean
from src.analysis import build_views, project_profitability
from src.report import plot_overview, plot_clients, plot_projects, plot_hosting, print_summary


def main():
    dfs   = clean(load_all())
    views = build_views(dfs)

    print_summary(dfs, views)

    plot_overview(views['paid'], views['times_valid'], dfs['invoices'])
    plot_clients(views['paid'])

    done = dfs['projecttasks'][dfs['projecttasks']['completed'] == 1].copy()
    done['velocity_days'] = (done['completeddate'] - done['created']).dt.days

    plot_projects(project_profitability(views['projects_h']), done['velocity_days'].dropna())
    plot_hosting(views['invoices_c'], dfs['hosting'])


if __name__ == '__main__':
    main()
