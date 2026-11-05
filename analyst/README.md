# Rule-aware data analyst

Managers ask business questions in plain language ("why did revenue drop in
March?") and get a correct answer that follows the company's metric
definitions and access rules.

**Status (2026-11-04):** phases 0 to 5 are built, tested offline and measured
once end to end with the real model (results below). Driver analysis (phase 6) is
connected: a change question ("why did X drop in March?") gets a "What drove it"
section written by code. That part is tested offline only, not yet in a real-model eval. There is a
command-line interface, no web UI yet. Everything below says which parts exist.

## What makes it different from a text-to-SQL agent

| | Plain text-to-SQL agent | This project |
|---|---|---|
| **Metric definitions** | The model invents a formula each time | Defined once in `config/metrics.yaml`; the model only picks a metric by name, and code builds the SQL. **Built.** |
| **Access control** | Rules in the prompt, which can be talked around | Enforced in code: no SQL can be built without passing the policy check. **Built.** |
| **The "why" behind a change** | The model guesses | Computed by code (driver analysis, mix/rate split); the "What drove it" text is written by code from the numbers, and the model's own sentence may not claim a cause. **Built; tested offline.** |

The rule behind every design choice: **the language model never produces
facts.** Numbers, definitions, SQL, permissions and explanations of change all
come from code and config.

## Architecture

```
question + role
      │
      ▼
 Planner (LLM) ──► QueryPlan (metric, period, breakdowns, filters; never SQL)   [built]
      │               sees metric names only, never data
      │
      ▼
 policy.authorize()   role → allowed metrics, breakdowns, row filter            [built]
      │  Grant (no SQL can be compiled without one)
      ▼
 compiler             QueryPlan + metrics.yaml → SQL; values as parameters      [built]
      │
      ▼
 sql_guard            one SELECT, known tables, no hidden columns,              [built]
      │               allowlisted functions only, LIMIT added
      ▼
 executor             read-only DuckDB, no file/network access, timeout         [built]
      │               → small groups merged into "Other" → ResultSet r1, r2...
      ▼
 Writer (LLM) + verifier + renderer: text with placeholders, numbers by code    [built]
      │               2 failed drafts → plain table instead
      ▼
 drivers.py: notable? which group? which company events? (no LLM)                [built, runs for change questions]
```

## Run it

Needs [uv](https://docs.astral.sh/uv/). From this folder:

```
uv sync                              # install
uv run python data/generate.py       # build the synthetic company database (~15 s)
uv run python data/check_planted.py  # prove the planted stories are in the data
uv run pytest -q                     # run all tests (no API key needed)
uv run python -m analyst "Why did revenue drop in March 2026?" --role ceo
```

The last command needs `GROQ_API_KEY` in `.env` (free at console.groq.com).
Evals with the real model: `uv run python evals/run_planner_evals.py` and
`uv run python evals/run_answer_evals.py`.

## The data

A synthetic SaaS company (seed 42, Jan 2025 to Aug 2026) with **planted
stories**: causes put into the data on purpose, so every "why" question has a
known right answer. Example: revenue drops 2.96% in March 2026, concentrated
in EU SMB customers, at the same time as an EU price increase announcement.
There is also a month where nothing happens, to test that the agent doesn't
invent a story. Details and measured numbers: [data/PLANTED.md](data/PLANTED.md).

It also contains test material for safety: canary email addresses (a leak is
a string search away), a region with only 3 customers, internal test accounts,
and a customer whose name is a prompt-injection attempt.

## Results (measured 2026-11-04)

| What | Result |
|---|---|
| Planted stories present in the data | 17 / 17 checks pass, each story at least 1.5× from its threshold |
| Metric SQL vs. an independent pandas computation | 32 / 32 cases match (8 metrics × 4 query shapes) |
| Same check for the EU manager role (row filter) | 32 / 32 cases match |
| Attack queries blocked by the SQL guard | 31 / 31 |
| Driver analysis vs. the answer key (PLANTED.md) | All 4 stories found through the safe query path, same numbers; flat month correctly "not notable" |
| Mutation tests (code broken on purpose) | Removing the row filter fails 33 tests; skipping the small-group check fails 9; 4 / 4 metric-definition bugs caught; every planner rule and verifier check is caught by at least one test |
| "What drove it" in the full answer (scripted models) | Stories 1, 2, 4 named with their events; the flat month says "within normal variation" and leaves out the event in its window; the writer can't cite driver numbers |
| Full test suite (no LLM calls) | 371 passed, 36 skipped |

**Real-model evals** (2026-11-04, round 1; files in `evals/results/*_run1.json`).
Models: Qwen 3.8 27B on Groq, with Z.ai GLM-4.7-flash as backup; Qwen made
about 90% of the calls (the rest are labelled per case in the result files).

| What | Result |
|---|---|
| Planner: 15 questions × 3 runs | 41 / 45 plans correct; 11 / 15 questions gave the same plan in all 3 runs; refusals and "not defined" 18 / 18 |
| End to end: 22 questions | 18 / 22 correct behavior and plan |
| Numbers vs. pandas (answered questions) | 10 / 13 (all 3 misses came from a wrong plan, not wrong maths) |
| Writer draft passes the verifier first time | 9 / 13 (all 13 passed after one retry; 0 fallbacks) |
| **Data leakage** (emails, customer names, hidden columns in answers) | **0 / 22** |

After round 1, four fixes were made (dates must start a month, one-month plans
are normalised, out-of-range periods say "no data", clarify questions are
checked). Round 2 stopped after 6 questions: every provider hit its daily
limit. In those 6, one question ("last month") was still read as July in 1 of
3 runs. These 22 questions were also used to find bugs, so the scores are
optimistic.

## Project layout

```
config/metrics.yaml        the semantic layer: 8 metrics, 3 dimensions, 4 sources
config/access_policy.yaml  4 roles, hidden columns, minimum group size (5)
config/annotations.yaml    company event log (generated)
data/generate.py           synthetic data with planted stories
data/check_planted.py      proves each story is in the data
src/analyst/plan.py        QueryPlan: what to compute, never SQL
src/analyst/semantic.py    loads and validates metrics.yaml
src/analyst/policy.py      access checks, small-group protection
src/analyst/compiler.py    QueryPlan → SQL
src/analyst/sql_guard.py   checks SQL before it runs
src/analyst/executor.py    the only way to get data
src/analyst/planner.py     LLM: question -> decision + QueryPlan (tools see definitions only)
src/analyst/facts.py       results -> named, formatted fields for placeholders
src/analyst/verifier.py    6 deterministic checks on the writer's text
src/analyst/answer.py      the full request: plan, run, write, verify, render
src/analyst/drivers.py     the "why": notable test, group shares, mix/rate, events
evals/                     golden questions and the real-model eval scripts
tests/                     unit tests (fake LLM) and the pandas ground truth
```

## Known limitations

- **Synthetic data, smoother than real data.** Normal months are deliberately
  calm so only planted stories stand out. Real data is noisier, so these tests are easier than reality.
- **Small groups are protected within one answer only.** Combining many
  different questions could still reveal something; differential privacy would be needed.
- **No authentication.** The caller says which role they have. A real product needs login.
- **A `Grant` can be created by hand in Python.** It prevents mistakes (forgetting the check), not a malicious developer.
- **One source table per metric.** A metric that mixes two sources (e.g. revenue per active user) can't be defined yet.
- **Simplified business.** One subscription per customer, no plan changes, no refunds, discounts or currencies.
- **Small, self-made eval set.** 15 planner questions, used while fixing bugs,
  so the scores are optimistic. A held-out set is still needed.
- **Free-tier model limits.** About 35 questions per day on Groq's free tier
  (200,000 tokens per day), which limits how often the evals can run.
- **The verifier reads words, not meaning.** It catches typed numbers, wrong
  direction words and cause words, but a paraphrased cause could pass.
- **"Concentrated in" reflects size as well as change.** A big group gets a big part
  of any change. Example: story 1 (planted in EU SMB churn) also reports "concentrated
  in pro", because pro customers pay the most. The answer says this in a note. A
  comparison with each group's own size would be better. Not built yet.
- **Each breakdown is split on its own.** Story 4's churn rise is "mix" by segment
  but "rate" by region, because inside every region the share of SMB grew. Both
  are true; reading only one breakdown can mislead.
- **Enterprise customers never churn in this data** (a side effect of rounding small groups; documented in PLANTED.md).
