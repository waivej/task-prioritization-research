"""
Produce anonymized snapshot files for public release.

Transformations applied:
  - client_name: removed entirely
  - client_id:   replaced with a stable hash-based pseudonym ("c-XXXX")
  - All monetary fields in client_metrics: multiplied by SCALE_FACTOR
  - score fields (priority_score, score_components): scaled where monetary
  - snapshot_date, task structure, age fields, hours, days_idle: unchanged

Output: outputs/snapshots_public/snapshot_YYYY-MM-DD.json
"""

import hashlib
import json
from pathlib import Path

SNAPSHOTS_DIR  = Path('outputs/snapshots')
OUTPUT_DIR     = Path('outputs/snapshots_public')
SCALE_FACTOR   = 2.7   # applied to all monetary fields; kept private

MONETARY_METRICS = {
    'lifetime_revenue',
    'unpaid_invoice_total',
    'income_600d',
    'hosting_revenue_600d',
    'hosting_cost_600d',
    'hourly_600d',
    'inv_due_soon',
}

MONETARY_SCORE_COMPONENTS = {
    'income_component',
    'inv_signal',
    'inv_due_soon',
    'income',
}


def hash_client(client_id: int) -> str:
    h = hashlib.sha256(str(client_id).encode()).hexdigest()[:6]
    return f"c-{h}"


def scale(v):
    return round(v * SCALE_FACTOR, 2) if isinstance(v, (int, float)) else v


def anonymize_task(task: dict) -> dict:
    out = {}
    for k, v in task.items():
        if k == 'client_name':
            continue
        elif k == 'client_id':
            out[k] = hash_client(v) if v else None
        elif k == 'client_metrics':
            out[k] = {
                mk: scale(mv) if mk in MONETARY_METRICS else mv
                for mk, mv in v.items()
            }
        elif k == 'priority_score':
            out[k] = scale(v)
        elif k == 'score_components':
            out[k] = {
                sk: scale(sv) if sk in MONETARY_SCORE_COMPONENTS else sv
                for sk, sv in v.items()
            }
        else:
            out[k] = v
    return out


def anonymize_snapshot(snap: dict) -> dict:
    return {
        **{k: v for k, v in snap.items() if k != 'open_tasks'},
        'open_tasks': [anonymize_task(t) for t in snap.get('open_tasks', [])],
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(SNAPSHOTS_DIR.glob('snapshot_*.json'))
    if not files:
        print(f"No snapshots found in {SNAPSHOTS_DIR}")
        return

    for path in files:
        with open(path) as f:
            snap = json.load(f)
        anon = anonymize_snapshot(snap)
        out_path = OUTPUT_DIR / path.name
        with open(out_path, 'w') as f:
            json.dump(anon, f, indent=2)

    print(f"Anonymized {len(files)} snapshots → {OUTPUT_DIR}/")
    print(f"Scale factor applied to monetary fields: {SCALE_FACTOR} (not disclosed in output)")


if __name__ == '__main__':
    main()
