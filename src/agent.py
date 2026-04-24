"""
Feed snapshot data into Claude to get AI-driven task prioritization recommendations.

Design:
  - Pre-filter each snapshot to the top N tasks by the improved heuristic score
    (prevents token bloat from 390 tasks; top 50 already covers the avg/upper budgets)
  - Use the Message Batches API (50% cost reduction) to process all 100 snapshots
  - Cache the stable system prompt across all batch requests
  - Parse Claude's JSON response and save per-snapshot ranked task lists
"""

import json
import math
import time
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

SNAPSHOTS_DIR = Path(__file__).parent.parent / 'outputs' / 'snapshots'
AGENT_DIR     = Path(__file__).parent.parent / 'outputs' / 'agent_results'
AVG_HOURS_PER_WEEK   = 19.0
UPPER_HOURS_PER_WEEK = 36.0
AVG_HOURS_PER_TASK   = 1.1
TOP_N_TASKS = 50   # pre-filter before sending to Claude

SYSTEM_PROMPT = """You are a task prioritization assistant for a small web hosting and development business.

BUSINESS CONTEXT
The owner manages hundreds of open tasks across ~200+ client accounts. Revenue comes from:
1. Recurring hosting fees (auto-billed, not dependent on task completion)
2. Hourly project work (web development, fixes, updates — requires completing tasks)

The owner works approximately 19–36 hours per week. The goal is to maximize revenue collected over the next 60 days.

CLIENT METRICS (provided per task)
- income_600d: total invoiced revenue (paid + unpaid) for this client in the trailing 600 days
- implied_hourly_rate: non-hosting revenue ÷ hours logged (capped at $300/hr to prevent outlier inflation)
- inv_due_soon: total invoice value due in the next 45 days — the single strongest signal (corr=0.766 with actual revenue collected)
- days_idle: days since any work was last logged for this client — lower = more active relationship
- hours_600d: hours of project work logged for this client in trailing 600 days
- task_age_days: how long this task has been open

RANKING GUIDANCE
Prioritize tasks where:
1. inv_due_soon is high (client is in an active billing cycle — they will pay soon)
2. days_idle is low (recent relationship momentum — client is engaged)
3. implied_hourly_rate is high (effective hourly value, max $300)
4. Multiple tasks for the same client can be batched efficiently
5. Very old tasks with zero client activity may be safely deprioritized

RESPONSE FORMAT
Return ONLY a valid JSON object with no markdown, no preamble, no explanation outside the JSON:
{
  "ranked_task_ids": [<task_id>, <task_id>, ...],
  "top_picks_reasoning": "<1-2 sentences on why the top 3-5 clients were chosen>"
}

Include ALL provided task IDs in ranked_task_ids (best to worst). Do not omit any."""


def _prefilter_score(task: dict) -> float:
    """Quick improved-formula score for pre-filtering before sending to Claude."""
    m   = task.get('client_metrics', {})
    age = task.get('task_age_days') or 0

    raw_rate  = m.get('implied_hourly_rate', 50)
    income    = m.get('income_600d', 0)
    inv_due   = m.get('inv_due_soon', 0)
    days_idle = m.get('days_idle', 9999)

    capped_rate  = max(50, min(raw_rate, 300))
    fresh_weight = 1000 if (capped_rate > 60 and age < 8) else 0
    php_base     = (12 * age) + (4 * capped_rate) + (0.003 * income) + fresh_weight
    inv_signal   = 7.0 * inv_due
    recency      = 500.0 * math.exp(-days_idle / 14.0)
    return php_base + inv_signal + recency


def _format_tasks_for_prompt(tasks: list[dict]) -> str:
    """Compact task list for the user message."""
    lines = []
    for t in tasks:
        m = t.get('client_metrics', {})
        client = t.get('client_name') or f"Client#{t.get('client_id')}"
        lines.append(
            f"  task_id={t['task_id']} | {client} | age={t.get('task_age_days',0)}d"
            f" | rate=${m.get('implied_hourly_rate',0)}/hr | income_600d=${m.get('income_600d',0):.0f}"
            f" | inv_due_soon=${m.get('inv_due_soon',0):.0f} | days_idle={m.get('days_idle',9999)}"
            f" | task=\"{str(t.get('task',''))[:80]}\""
        )
    return '\n'.join(lines)


def build_batch_requests(n_weeks: int = 100) -> list[Request]:
    """Load all snapshot JSONs, pre-filter top N tasks, build batch Request objects."""
    files = sorted(SNAPSHOTS_DIR.glob('snapshot_*.json'), reverse=True)[:n_weeks]
    requests = []

    for path in files:
        with open(path) as f:
            snap = json.load(f)

        # pre-filter to top N by improved score
        tasks = snap['open_tasks']
        tasks_scored = sorted(tasks, key=_prefilter_score, reverse=True)[:TOP_N_TASKS]

        snap_date  = snap['snapshot_date']
        avg_budget = int(AVG_HOURS_PER_WEEK / AVG_HOURS_PER_TASK)
        upper_budget = int(UPPER_HOURS_PER_WEEK / AVG_HOURS_PER_TASK)

        user_msg = (
            f"Snapshot date: {snap_date}\n"
            f"Total open tasks: {snap['open_task_count']} (showing top {len(tasks_scored)} by pre-filter score)\n"
            f"Weekly capacity: ~{avg_budget} tasks (avg) or ~{upper_budget} tasks (upper)\n\n"
            f"TASKS TO RANK:\n"
            f"{_format_tasks_for_prompt(tasks_scored)}"
        )

        requests.append(Request(
            custom_id=f"snap-{snap_date}",
            params=MessageCreateParamsNonStreaming(
                model="claude-haiku-4-5",
                max_tokens=2048,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
            )
        ))

    print(f"Built {len(requests)} batch requests (top {TOP_N_TASKS} tasks each)")
    return requests


def submit_batch(requests: list[Request]) -> str:
    """Submit batch to the API and return the batch ID."""
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch submitted: {batch.id}  status={batch.processing_status}")
    return batch.id


def poll_batch(batch_id: str, poll_interval: int = 30) -> None:
    """Poll until the batch completes."""
    client = anthropic.Anthropic()
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"  {batch.processing_status}  "
            f"processing={counts.processing}  succeeded={counts.succeeded}  "
            f"errored={counts.errored}"
        )
        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval)
    print("Batch complete.")


def collect_and_save_results(batch_id: str, snapshots_dir: Path = SNAPSHOTS_DIR) -> dict:
    """
    Retrieve batch results, parse Claude's ranked JSON, and save per-snapshot files.
    Returns a dict keyed by snapshot_date with ranked task lists.
    """
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()

    # build a lookup of pre-filtered task lists by snap date
    prefiltered: dict[str, list[dict]] = {}
    for path in snapshots_dir.glob('snapshot_*.json'):
        with open(path) as f:
            snap = json.load(f)
        tasks = snap['open_tasks']
        tasks_scored = sorted(tasks, key=_prefilter_score, reverse=True)[:TOP_N_TASKS]
        prefiltered[snap['snapshot_date']] = {t['task_id']: t for t in tasks_scored}

    results = {}
    errors  = 0

    for result in client.messages.batches.results(batch_id):
        snap_date = result.custom_id.replace('snap-', '')

        if result.result.type != "succeeded":
            print(f"  WARN {result.custom_id}: {result.result.type}")
            errors += 1
            continue

        raw_text = next(
            (b.text for b in result.result.message.content if b.type == "text"), ""
        )

        try:
            # strip markdown code fences if present
            text = raw_text.strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[-1]
                text = text.rsplit('```', 1)[0]
            parsed = json.loads(text)
            ranked_ids = parsed.get('ranked_task_ids', [])
            reasoning  = parsed.get('top_picks_reasoning', '')
        except json.JSONDecodeError:
            print(f"  WARN {snap_date}: could not parse JSON response")
            errors += 1
            continue

        # look up task objects in the right order
        task_map = prefiltered.get(snap_date, {})
        ranked_tasks = []
        cum_hours = 0.0
        for tid in ranked_ids:
            t = task_map.get(tid)
            if t is None:
                continue
            cum_hours += AVG_HOURS_PER_TASK
            ranked_tasks.append({
                **t,
                'rank':               len(ranked_tasks) + 1,
                'cumulative_hours':   round(cum_hours, 2),
                'within_avg_budget':  cum_hours <= AVG_HOURS_PER_WEEK,
                'within_upper_budget': cum_hours <= UPPER_HOURS_PER_WEEK,
            })

        out = {
            'snapshot_date': snap_date,
            'batch_id':      batch_id,
            'reasoning':     reasoning,
            'ranked_tasks':  ranked_tasks,
        }

        out_path = AGENT_DIR / f"agent_{snap_date}.json"
        with open(out_path, 'w') as f:
            json.dump(out, f, indent=2)

        results[snap_date] = out

    print(f"Saved {len(results)} result files to {AGENT_DIR}  ({errors} errors)")
    return results


def load_saved_results() -> dict:
    """Load previously saved agent result files (avoids re-polling batch API)."""
    results = {}
    for path in sorted(AGENT_DIR.glob('agent_*.json')):
        with open(path) as f:
            r = json.load(f)
        results[r['snapshot_date']] = r
    return results
