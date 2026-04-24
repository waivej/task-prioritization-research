from pathlib import Path
from src.load import load_all
from src.clean import clean
from src.snapshots import generate

SNAPSHOTS_DIR = Path('outputs/snapshots')

if __name__ == '__main__':
    # Clear stale snapshots so no old-window files linger
    if SNAPSHOTS_DIR.exists():
        removed = list(SNAPSHOTS_DIR.glob('snapshot_*.json'))
        for f in removed:
            f.unlink()
        print(f'Cleared {len(removed)} existing snapshots.')

    print('Loading and cleaning data...')
    dfs = clean(load_all())
    # start_weeks_ago=10 ensures the most recent snapshot is 10 weeks old,
    # so every snapshot's 60-day payment window has already closed.
    print('Generating 100 weekly snapshots (starting 10 weeks ago)...')
    generate(dfs, n_weeks=100, start_weeks_ago=10)
