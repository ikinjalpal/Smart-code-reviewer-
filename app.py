"""
app.py
======
Flask API that ties the whole pipeline together:
  AST analysis -> complexity analysis -> security scan -> AI review

Two ways to run a review:
  - POST /review        -> async: queues the job, returns a job_id immediately
  - POST /review/sync    -> sync: runs the full pipeline and waits for the result
                             (used by the demo UI so it can show a result in
                             one round trip without polling)

NOTE ON IMPORTS: the `queue/` folder is deliberately imported by adding
it directly to sys.path (rather than `from queue.job_queue import ...`)
because a package literally named "queue" collides with Python's
built-in `queue` module inside job_queue.py itself. See job_queue.py's
own `import queue` statement — if our package were also on the import
path as "queue", Python would shadow the stdlib module. Adding the
folder itself to sys.path and importing the file as a top-level
module (`job_queue`) sidesteps that collision entirely.
"""

import os
import sys
import time

from flask import Flask, request, jsonify, render_template

# --- Make local packages importable -----------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "queue"))  # see note above
sys.path.insert(0, BASE_DIR)

from pipeline.ast_analyzer import ASTAnalyzer
from pipeline.complexity import ComplexityAnalyzer
from pipeline.security import SecurityScanner
from pipeline.ai_reviewer import AIReviewer
from job_queue import ReviewJobQueue

app = Flask(__name__)

# Single shared instances of each analyzer — they're stateless, so
# it's safe (and cheaper) to reuse them across requests/threads
# instead of constructing new ones per call.
ast_analyzer = ASTAnalyzer()
complexity_analyzer = ComplexityAnalyzer()
security_scanner = SecurityScanner()
ai_reviewer = AIReviewer()

MAX_WORKERS = 3


def run_full_pipeline(code: str) -> dict:
    """
    The single source of truth for "what does a full review consist
    of". Both the sync endpoint and the async queue worker call this
    exact function, so the two code paths can never drift apart.
    """
    # --- Stage: AST analysis (bugs + function metadata) ---
    try:
        tree = ast_analyzer.parse(code)
    except ValueError as e:
        # Invalid syntax means none of the later stages can run
        # meaningfully, so we return early with a clear error.
        return {"error": str(e)}

    bugs = ast_analyzer.detect_bugs(tree)
    functions = ast_analyzer.extract_functions(tree)

    # --- Stage: complexity analysis (per function found) ---
    complexity_results = []
    for func in functions:
        complexity_results.append(
            complexity_analyzer.analyze(code, func["name"])
        )

    # --- Stage: security scan ---
    security_findings = security_scanner.scan(code)

    static_results = {
        "bugs": bugs,
        "complexity": complexity_results,
        "security": security_findings,
    }

    # --- Stage: AI review (uses the above as enriched context) ---
    ai_result = ai_reviewer.review(code, static_results)

    return {
        "bugs": bugs,
        "functions": functions,
        "complexity": complexity_results,
        "security": security_findings,
        "ai_review": ai_result,
    }


# The async job queue is wired to run the exact same pipeline function.
job_queue = ReviewJobQueue(pipeline_fn=run_full_pipeline, max_workers=MAX_WORKERS)


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.route("/")
def index():
    """Serve the demo UI."""
    return render_template("index.html")


@app.route("/review", methods=["POST"])
def review_async():
    """
    Queue a review job and return immediately. Client should poll
    GET /result/<job_id> for the outcome.
    """
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")

    if not code.strip():
        return jsonify({"error": "Field 'code' is required and cannot be empty."}), 400

    job_id = job_queue.submit(code)
    return jsonify({"job_id": job_id, "status": "queued"})


@app.route("/result/<job_id>", methods=["GET"])
def result(job_id):
    """Poll for a queued job's status/result."""
    result_data = job_queue.get_result(job_id)
    if result_data["status"] == "not_found":
        return jsonify(result_data), 404
    return jsonify(result_data)


@app.route("/review/sync", methods=["POST"])
def review_sync():
    """
    Run the full pipeline synchronously and return the complete
    result in one response. Used by the demo UI for simplicity
    (no polling loop needed client-side).
    """
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")

    if not code.strip():
        return jsonify({"error": "Field 'code' is required and cannot be empty."}), 400

    start = time.time()
    result_data = run_full_pipeline(code)
    result_data["elapsed_ms"] = round((time.time() - start) * 1000, 2)
    return jsonify(result_data)


@app.route("/health", methods=["GET"])
def health():
    """Basic liveness + queue-depth check, useful for monitoring/demo purposes."""
    return jsonify({
        "status": "ok",
        "queue_depth": job_queue.queue_depth(),
        "workers": MAX_WORKERS,
    })


if __name__ == "__main__":
    # PORT is read from the environment because hosting platforms like
    # Render/Heroku assign a port dynamically and pass it in — the app
    # must listen on whatever port the platform gives it, not a
    # hardcoded one. Locally, it falls back to 5000.
    #
    # debug=False in a "production-ish" demo; threaded=True lets Flask's
    # dev server handle multiple simultaneous requests (needed since we
    # now have genuinely concurrent async job processing to demonstrate).
    #
    # NOTE: this app.run() path is only used for local development.
    # In production (e.g. on Render), gunicorn imports `app` directly
    # and never executes this block — see Procfile.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
