from src.load import load_all
from src.clean import clean
from src.evaluate import evaluate, print_summary, save_results

if __name__ == '__main__':
    print('Loading and cleaning data...')
    dfs = clean(load_all())

    print('Running evaluation across 100 weeks (PHP + improved formula)...')
    results = evaluate(dfs, n_weeks=100)

    print_summary(results)
    save_results(results)
