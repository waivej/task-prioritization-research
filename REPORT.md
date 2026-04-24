# Task Prioritization Research Report

**Business:** Small web hosting & development company  
**Data source:** WHMCS with mod_project module  
**Period analyzed:** 100 weeks (≈ May 2024 – April 2026)

> **Data availability:** The underlying WHMCS export and all snapshot files are proprietary business data and are not published. All code is published as-is and can be rerun against any compatible WHMCS export.

---

## Introduction

This project evaluates how different decision-making systems — including a production heuristic, an improved rule-based model, and a Claude AI agent — perform when prioritizing real-world work under uncertainty.

Using data from a small web hosting and development business, the WHMCS database was exported and converted into 100 weekly point-in-time snapshots. Each snapshot captures the exact state of open tasks and client context as it would have been known at that time, with no information leakage from future outcomes. For each snapshot, three methods were asked to rank which tasks to prioritize: the existing PHP heuristic used in production, an improved Python-based formula, and a Claude AI agent given the same structured input.

These rankings were evaluated against actual downstream revenue, measured as payments received within 60 days of each snapshot.

**Bottom line:** The original PHP heuristic contains a scoring bug that severely degrades performance. After correcting this issue, both the improved formula and the Claude agent substantially outperform the original system. However, approximately **52% of weekly revenue is driven by inbound, same-week work** (e.g., calls, quick fixes, reactive requests) that is not present in the ranked task set. When evaluation is restricted to schedulable work only, the Claude agent achieves **81% efficiency** relative to the actual worker, compared to **73%** for the corrected formula. (Efficiency is defined as revenue captured by the method divided by revenue captured by the actual worker over the same period.)

This suggests that while LLM-based prioritization can meaningfully improve decision quality on structured work, a large portion of real-world performance depends on reactive and relationship-driven inputs that are not captured in historical task data.

---

## 1. Data & Methodology

### Source Data

Seven tables were exported from the WHMCS MySQL database as semicolon-delimited, latin-1 encoded CSVs: clients, invoices, invoice items, payment accounts, hosting packages, projects, project tasks, and time log entries.

### Weekly Snapshots

Rather than analyzing data statically, 100 weekly snapshots were generated — one per week going back ~2 years. Each snapshot captures the exact state of open tasks and client metrics *as they would have appeared on the dashboard that day*, with no information from future weeks. Each task embeds point-in-time client metrics including trailing 600-day revenue, implied hourly rate, days since last work, and invoices due within 45 days.

### Revenue Attribution

For each snapshot, the "revenue collected" from a set of clients is the sum of invoices those clients paid in the **60 days following** the snapshot date. This window was chosen because it covers most typical payment cycles while keeping the evaluation window closed for all 100 weeks.

### Hosting vs. Project Revenue

Invoice line items were classified as hosting (pass-through cost) or project revenue using description-pattern matching, mirroring the PHP dashboard logic. The hourly rate used in scoring is `income − hosting_revenue`, isolating billable project work.

---

## 2. The PHP Formula and Its Bug

### The Formula

The existing dashboard scores each open task as:

```
score = (12 × age_days)
      + (4  × implied_hourly_rate)
      + (0.003 × income_600d)
      + fresh_weight   [+1,000 if rate > threshold AND task < 8 days old]
```

The intent is sound: prioritize old tasks, favor profitable clients, and surface new work from good clients quickly.

### The Rate Explosion Bug

`implied_hourly_rate` is computed as `income_600d / hours_600d`. For clients who have been recently billed but have no logged hours (typically hosting or pass-through accounts), this produces rates in the tens of thousands per hour. These clients dominate the ranked list and crowd out active project clients.

**Impact:** In the 100-week evaluation, the original PHP formula (average ~19h budget) captured less than **2% of what the actual worker collected** — effectively useless.

### The Fix

Three changes bring the formula in line with its intent:

1. **Cap implied rate** — prevents hosting-only clients from dominating
2. **Add invoice imminence** — `+7 × inv_due_soon` (invoices due within 45 days)
3. **Add recency bonus** — `+500 × exp(−days_idle / 14)` (exponential decay, 14-day half-life)

The invoice imminence signal had the **highest correlation (0.766) with 60-day revenue** of any metric in the dataset. Clients in an active billing cycle are far more likely to pay soon. The recency signal reflects relationship momentum: among weeks where clients paid, the median days-idle was 4 days; among non-paying clients it was 11 days.

The corrected PHP file (`src/heauristic_task_rank.php`) includes all three fixes and can be deployed directly to the dashboard.

---

## 3. Method Comparison

Four methods were compared across 99 weeks with measurable actual revenue:

| Method | Efficiency vs actual worker |
|---|---|
| **Actual worker** | **1.00×** |
| Claude AI agent | 0.59× |
| PHP formula (fixed) | 0.53× |
| Improved Python formula | 0.53× |
| PHP formula (original, buggy) | ~0.01× |

The Claude agent (Claude 3.5 Sonnet) was prompted with a structured JSON representation of each snapshot and asked to rank the top 3 tasks by expected revenue impact. The prompt was deterministic (temperature=0) to ensure consistent outputs across runs. Each snapshot was evaluated once. It modestly outperforms the fixed formula. The agent's advantage appears to come from its ability to reason over interacting signals (e.g., balancing recency, invoice timing, and task age) rather than applying fixed linear weights. However, this flexibility does not overcome the structural limitation that a large portion of revenue originates from unobserved inbound work.

---

## 4. The Inbound Work Gap

The actual worker substantially outperforms the algorithms (0.53×–0.59× efficiency). The reason is structural: **a large portion of each week's revenue comes from reactive, same-week work** — phone calls, quick fixes, and requests that arrive and get completed before any weekly snapshot could rank them.

### Measuring It

Tasks were classified as **inbound** if created and completed within 7 days (one snapshot window), vs. **pre-existing** tasks that appeared on the ranked list before the week started.

| Category | % of total revenue |
|---|---|
| Clients touched ONLY via inbound tasks | 52% |
| Clients touched ONLY via pre-existing tasks | 19% |
| Clients with BOTH types | 28% |
| **Schedulable total (pre-existing + both)** | **47%** |

**52% of actual revenue is inbound work no algorithm can pre-rank.**

### Algorithms vs. Schedulable Revenue Only

When evaluation is restricted to only the 47% of revenue that is schedulable, the picture improves significantly:

| Method | Efficiency vs schedulable revenue |
|---|---|
| Claude AI agent | **1.24×** |
| PHP formula (fixed) | **1.13×** |

The algorithms actually capture *more* than the pre-existing-task pool alone would suggest — probably because serving a pre-existing-task client often triggers same-week follow-on work from that client as well.

**The gap is not primarily an algorithm quality problem. It is a structural feature of the business model:** roughly half the week's revenue is reactive and relationship-driven.

---

## 5. Recommendations

### 5.1 Audit Heuristic Systems for Edge-Case Feature Behavior (High priority)

The results suggest that simple heuristic systems are highly sensitive to edge-case feature behavior, and even small fixes (e.g., capping implied rates) can dramatically change system performance. In this case, a single uncapped feature (`implied_hourly_rate`) caused the production formula to perform near zero. Three targeted changes bring it in line with its original intent:

```php
if ($hourly_rate > 300) $hourly_rate = 300;            // cap rate explosion
$inv_signal    = 7.0 * GetCustomerInvoiceDueSoon(...); // invoice imminence
$recency_bonus = 500 * exp(-$stats['idle'] / 14.0);    // recency decay
$score += $inv_signal + $recency_bonus;
```

This fix is estimated to increase the formula's efficiency from ~1% to ~53% of the actual worker's revenue on schedulable work.

### 5.2 Log Inbound Work as Tasks (Medium priority)

Phone calls and email requests currently leave no trace unless the worker manually creates and closes a task. A one-click "log inbound call" button in the dashboard would:
- Improve the `days_idle` signal (better recency data)
- Give the algorithm visibility into actively communicating clients
- Enable future analysis of how much revenue follows inbound vs. scheduled work

### 5.3 Consider the Algorithm as a Floor, Not a Ceiling (Operational)

The ranked list works best as a **minimum floor of attention** — clients that should receive at least a touch each week based on billing cycle, relationship recency, and revenue potential. Inbound work will always take priority, but the ranked list prevents high-value scheduled work from slipping while reactive tasks dominate the day.

### 5.4 Optional: Use the Claude Agent for Weekly Planning

The Claude AI agent adds a modest improvement over the fixed formula (59% vs 53% efficiency), but at a small API cost per week. It is most useful for edge cases where multiple signals conflict — e.g., a long-idle high-value client with an imminent invoice and a stale task. It can be run as a weekly batch job against the most recent snapshot.

---

## 6. LLM Failure Modes

Observing the agent's recommendations across 100 snapshots revealed several recurring failure patterns:

- **Over-prioritizes high-revenue but stale clients.** Clients with strong historical billing but no recent activity tend to rank highly because lifetime revenue is a prominent signal in the prompt context. The agent occasionally surfaces clients who have not been active for months, where the recency signal should suppress them.
- **Underweights urgency for low-revenue clients.** A small client with an invoice due tomorrow and a quick task tends to be ranked below a large client with a distant due date. The agent applies revenue weighting more heavily than deadline imminence in ambiguous cases.
- **Struggles with long-tail, low-data clients.** Newer clients with few invoices and minimal time logged produce sparse, uncertain metrics (e.g., implied hourly rate defaults to a floor value). The agent has no way to distinguish a genuinely low-value client from a recently onboarded one, and tends to rank them conservatively.
- **Inconsistent handling of multi-task clients.** When a single client has several open tasks, the agent sometimes recommends multiple tasks from the same client rather than spreading capacity. This can be correct but can also reflect over-anchoring on a single strong revenue signal.
- **Cannot observe inbound context.** The most significant failure mode is structural: the agent sees only tasks that were open at snapshot time. Clients actively in communication, or about to call, are indistinguishable from dormant ones. The `days_idle` metric partially proxies for this, but the agent cannot act on information that isn't in the data.

---

## 7. File Index

| File | Purpose |
|---|---|
| `data/*.csv` | Raw WHMCS exports *(not published — proprietary business data)* |
| `src/load.py` | Load all CSVs into pandas DataFrames |
| `src/clean.py` | Parse dates, classify invoice items, derive columns *(not published — contains proprietary cost margins)* |
| `src/clean_example.py` | Drop-in template with illustrative cost values for public use |
| `src/snapshots.py` | Generate 100 weekly point-in-time JSON snapshots |
| `src/prioritize.py` | PHP heuristic + improved scoring functions |
| `src/evaluate.py` | Compare methods against actual worker outcomes |
| `src/agent.py` | Claude AI agent using the Batches API |
| `src/heauristic_task_rank.php` | Corrected PHP ranking code *(not published — contains proprietary cost margins)* |
| `src/heauristic_task_rank_example.php` | Public template with illustrative cost values |
| `generate_snapshots.py` | Regenerate all snapshots |
| `run_evaluation.py` | Run PHP + improved formula comparison |
| `run_agent.py` | Submit/collect/compare Claude agent batch |
| `analyze_sameday.py` | Inbound vs. schedulable revenue split |
| `anonymize_snapshots.py` | Produces anonymized snapshots from raw data |
| `outputs/snapshots/` | 100 weekly JSON snapshots *(not published)* |
| `outputs/evaluation_results.json` | Per-week comparison table |
| `outputs/agent_comparison.json` | Agent vs. all methods |
| `outputs/sameday_analysis.json` | Inbound/schedulable revenue breakdown |
