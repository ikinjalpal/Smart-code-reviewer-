# Smart Code Review Assistant

![tests](https://github.com/YOUR_USERNAME/smart-code-reviewer/actions/workflows/tests.yml/badge.svg)

**Live demo:** _add your Render URL here once deployed, e.g. https://smart-code-reviewer.onrender.com_

A multi-stage code analysis pipeline that combines static analysis
(AST parsing, complexity estimation, security scanning) with an
LLM-based review — where the LLM is given the static findings as
context instead of just raw code.

## Problem

Manual code review is slow, inconsistent, and misses security
vulnerabilities. Existing tools (linters) catch syntax errors but not
logic bugs, algorithmic complexity issues, or design-level feedback —
and pointing an LLM at raw code alone tends to hallucinate complexity
claims and duplicate what a linter would have caught for free.

## Solution & Architecture

```
                    ┌─────────────────────┐
   source code ───▶ │   1. AST Analyzer    │──┐
                    │  parse + detect bugs │  │
                    └─────────────────────┘  │
                                              │
                    ┌─────────────────────┐  │
                    │ 2. Complexity        │  │   static
                    │   Analyzer (Big-O)   │──┤   findings
                    └─────────────────────┘  │
                                              │
                    ┌─────────────────────┐  │
                    │ 3. Security Scanner  │──┘
                    │   (regex patterns)   │
                    └──────────┬───────────┘
                               │ enriched context
                               ▼
                    ┌─────────────────────┐
                    │ 4. AI Reviewer       │──▶ final review
                    │  (LLM + static ctx)  │
                    └─────────────────────┘

  Concurrency layer: a threading.Queue-backed worker pool (3 workers)
  lets /review accept jobs without blocking; /review/sync runs the
  same pipeline function synchronously for the demo UI.
```

## Why Multi-Stage (not just AI)?

This is the key engineering decision behind the whole project.

**Static analysis catches what AI hallucinates about.** Ask an LLM
"what's the time complexity of this function?" and it will confidently
answer even when it's wrong, because it's pattern-matching on how the
code *looks*, not mechanically counting loop nesting. The
`ComplexityAnalyzer` in this project does an actual recursive AST walk
to count loop depth — it's slower to write, but it's not guessing.

**AI catches what static analysis can't reason about.** A regex or an
AST walk can tell you a variable is unused; it can't tell you a
function's *name* is misleading, that an edge case (empty list, `None`
input) isn't handled, or that a Google style guide convention was
violated. That requires actual understanding of intent.

**They're complementary, not duplicated — by construction.** The AI
reviewer's prompt in `pipeline/ai_reviewer.py` explicitly includes the
static analysis results and instructs the model *not* to repeat them,
asking instead for exactly what static analysis structurally cannot
provide (design feedback, style, edge cases, a rewrite). This is the
"enriched context" idea: the AI's output budget is spent on genuinely
new information, not restating a bug the AST analyzer already found
for free in under a millisecond.

## Benchmark Results

Measured by running `benchmark.py` on this machine (Python 3.12,
single-threaded, averaged over 200 runs per stage):

```
1. PIPELINE STAGE TIMING (avg over 200 runs, single-threaded)
  AST analysis alone              0.479 ms
  Complexity analysis alone       0.270 ms
  Security scan alone             0.077 ms
  Full pipeline (no AI)           0.957 ms
  Full pipeline (with AI)         skipped (no ANTHROPIC_API_KEY set)

2. BUG DETECTION ACCURACY (10 snippets, known ground truth)
  mutable_default_argument     2/2 correct
  infinite_loop_risk           2/2 correct
  bare_except                  2/2 correct
  unused_variable              2/2 correct
  sql_injection                2/2 correct

  Overall: 10/10 correct (100%)
```

With an `ANTHROPIC_API_KEY` set, re-run `benchmark.py` to get the
"full pipeline (with AI)" number — it will be dominated by network
latency to the LLM API (typically 1-3 seconds), which is exactly why
the async job queue (Stage 6) exists instead of making every caller
block on that.

## Bug Detection Examples

**1. Mutable default argument**
```python
# Input:
def get_user(user_id, cache={}):
    ...

# Caught:
{"type": "mutable_default_argument", "line": 1, "severity": "high",
 "message": "Function 'get_user' uses a mutable default argument. Use None
             and assign inside the function body instead."}
```

**2. SQL injection via f-string**
```python
# Input:
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# Caught:
{"vulnerability": "sql_injection", "line": 1, "severity": "critical",
 "matched": "execute(f\"",
 "fix": "Use parameterized queries (e.g. cursor.execute(query, (param,)))
         instead of string formatting."}
```

**3. Infinite loop risk**
```python
# Input:
def foo():
    while True:
        pass

# Caught:
{"type": "infinite_loop_risk", "line": 2, "severity": "high",
 "message": "while True loop has no break statement — risk of infinite loop."}
```

## API Documentation

**`POST /review`** — queue an async review job
```bash
curl -X POST http://localhost:5000/review \
  -H "Content-Type: application/json" \
  -d '{"code": "def foo(x=[]):\n    return x"}'
# -> {"job_id": "a04f4026-...", "status": "queued"}
```

**`GET /result/<job_id>`** — poll for the result
```bash
curl http://localhost:5000/result/a04f4026-...
# -> {"status": "pending"}                (still running)
# -> {"status": "done", "bugs": [...], "complexity": [...], ...}
# -> 404 {"status": "not_found"}          (unknown job_id)
```

**`POST /review/sync`** — run the full pipeline and wait for the result
```bash
curl -X POST http://localhost:5000/review/sync \
  -H "Content-Type: application/json" \
  -d '{"code": "def foo(x=[]):\n    return x"}'
# -> {"bugs": [...], "functions": [...], "complexity": [...],
#     "security": [...], "ai_review": {...}, "elapsed_ms": 1.93}
```

**`GET /health`** — liveness + queue status
```bash
curl http://localhost:5000/health
# -> {"status": "ok", "queue_depth": 0, "workers": 3}
```

**`GET /`** — serves the demo UI (two-pane code editor + live results).

## Running it locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here   # optional — AI stage degrades
                                          # gracefully without it
python3 app.py
# visit http://localhost:5000
```

Run tests: `python3 -m pytest tests/test_pipeline.py -v`
Run benchmarks: `python3 benchmark.py`

## Deploying (free, no terminal needed)

This repo is ready to deploy on [Render](https://render.com) directly
from the GitHub web UI:

1. Push this repo to your own GitHub account.
2. On Render, click **New** → **Web Service** → connect your GitHub
   account → select this repo.
3. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
4. (Optional) Add an environment variable `ANTHROPIC_API_KEY` if you
   want the AI review stage to run live instead of falling back.
5. Click **Create Web Service**. Render gives you a live URL
   (e.g. `https://smart-code-reviewer.onrender.com`) once the build
   finishes — put that URL in the badge section at the top of this
   README.

The `Procfile` and the `PORT` environment variable handling in
`app.py` exist specifically to support this — Render assigns a port
dynamically at runtime rather than using a fixed one, and expects a
production WSGI server (`gunicorn`) rather than Flask's built-in
development server.

## What I'd build next

- **Support for JavaScript and Java** — extend the AST layer with a
  language-specific parser per language (Python's `ast` module is
  Python-only; JS/Java would need `esprima`/`javalang` or similar),
  behind the same `detect_bugs()` interface.
- **GitHub PR integration via webhooks** — trigger a review
  automatically on `pull_request` events and post findings as a PR
  comment instead of requiring someone to paste code into the UI.
- **Fine-tuned model on code review datasets** — the current AI stage
  uses a general-purpose model; a model fine-tuned specifically on
  accepted/rejected review comments could give more calibrated,
  less generic feedback.
- **VSCode extension** — run the same pipeline on save and surface
  findings as inline diagnostics, closing the loop between "written"
  and "reviewed" to seconds instead of a PR cycle.

## Known limitations (worth knowing before an interview)

- The complexity analyzer's recursion detection only catches *direct*
  recursion (a function calling itself by name), not mutual recursion
  (A calls B calls A).
- The `missing_return` bug check is a heuristic based on whether the
  last statement in a function guarantees a return — it can miss more
  complex control-flow shapes (loops with returns inside, `match`
  statements, etc.).
- The security scanner is regex-based, so it can both miss obfuscated
  vulnerabilities and produce false positives on code that merely
  contains matching text (e.g. a variable named `password` that holds
  a reference, not a literal secret).
