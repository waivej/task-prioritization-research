# Task Prioritization Research

Evaluates how different decision-making systems — a production PHP heuristic, an improved rule-based formula, and a Claude AI agent — perform when prioritizing real-world work under uncertainty.

See [report.md](report.md) for the full write-up and findings.

## Overview

Weekly point-in-time snapshots of open tasks and client metrics were generated from a WHMCS export. Three methods were asked to rank which tasks to prioritize each week, then evaluated against actual downstream revenue (payments received within 60 days).

## Setup

```bash
pip install -r requirements.txt
```

Add your Anthropic API key to a `.env` file:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# Generate weekly snapshots from your WHMCS data export
python generate_snapshots.py

# Compare PHP + improved formula against actual worker outcomes
python run_evaluation.py

# Run the Claude AI agent across all snapshots (submit → collect → compare)
python run_agent.py submit
python run_agent.py collect
python run_agent.py compare

# Analyze inbound vs schedulable revenue split
python analyze_sameday.py
```

## Data

The underlying WHMCS export is proprietary and not published. To run this code against your own data, export the following tables from WHMCS as semicolon-delimited CSVs into `data/`:

| File | WHMCS table |
|---|---|
| `clients.csv` | `tblclients` |
| `invoices.csv` | `tblinvoices` |
| `invoiceitems.csv` | `tblinvoiceitems` |
| `accounts.csv` | `tblaccounts` |
| `hosting.csv` | `tblhosting` |
| `projects.csv` | `mod_project` |
| `projecttasks.csv` | `mod_projecttasks` |
| `projecttimes.csv` | `mod_projecttimes` |

## Cost Classification

`src/clean_example.py` and `src/heauristic_task_rank_example.php` contain illustrative placeholder values for hosting cost classification. Copy and rename to `clean.py` / `heauristic_task_rank.php` and replace with your own service catalog and provider costs before running.

## Project Structure

```
├── src/
│   ├── load.py                          # Read CSVs into DataFrames
│   ├── clean_example.py                 # Cost classification template (rename to clean.py)
│   ├── snapshots.py                     # Generate weekly point-in-time snapshots
│   ├── prioritize.py                    # PHP heuristic + improved scoring
│   ├── evaluate.py                      # Compare methods against actual outcomes
│   ├── agent.py                         # Claude AI agent via Batches API
│   ├── analysis.py                      # Joined views and analysis helpers
│   ├── report.py                        # Charts and printed summary
│   └── heauristic_task_rank_example.php # PHP scoring template (rename to deploy)
├── generate_snapshots.py
├── run_evaluation.py
├── run_agent.py
├── analyze_sameday.py
├── anonymize_snapshots.py
├── main.py
├── report.md
└── requirements.txt
```
