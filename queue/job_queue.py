"""
job_queue.py
============
A small non-blocking job queue built on Python's built-in
`threading` and `queue` modules — no Redis, no Celery, no external
broker required. This is a deliberate scope decision: for a portfolio
project it demonstrates the *concept* of async job processing
(producer/consumer, worker pool, job status polling) without pulling
in infrastructure that would be overkill for a single-process demo.

Why this matters for system design:
A synchronous "/review" endpoint that runs the full pipeline (AST +
complexity + security + an LLM call) would block the request thread
for however long the LLM call takes — often several seconds. If two
users hit the endpoint at once, the second one waits behind the
first. By queueing the job and returning a job_id immediately, the
API stays responsive under concurrent load, and a fixed-size worker
pool bounds how much work happens at once (protecting the LLM API
from being hammered by unbounded concurrent calls).
"""

import queue
import threading
import uuid


class ReviewJobQueue:
    """Thread-pool-backed queue for running the review pipeline asynchronously."""

    def __init__(self, pipeline_fn, max_workers: int = 3):
        """
        pipeline_fn: a callable(code: str) -> dict that runs the full
        AST -> complexity -> security -> AI pipeline. Injected as a
        dependency so this queue class doesn't need to know anything
        about the pipeline internals — it only knows how to schedule
        and track work. This separation of concerns also makes the
        queue independently testable with a fake pipeline_fn.
        """
        self._pipeline_fn = pipeline_fn
        self.max_workers = max_workers

        # queue.Queue is thread-safe out of the box (internally uses a
        # lock + condition variable), so multiple worker threads can
        # safely pull from it without us writing our own locking.
        self._jobs: "queue.Queue[tuple[str, str]]" = queue.Queue()

        # Job results keyed by job_id. This dict IS shared mutable
        # state across threads, so every read/write to it goes through
        # self._lock to avoid race conditions.
        self._results: dict[str, dict] = {}
        self._lock = threading.Lock()

        # Spin up the fixed-size worker pool. `daemon=True` means these
        # threads won't prevent the process from exiting.
        self._workers = []
        for _ in range(max_workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._workers.append(t)

    def submit(self, code: str) -> str:
        """
        Enqueue a review job and return its id immediately — this
        method never blocks on the actual pipeline running.
        """
        job_id = str(uuid.uuid4())
        with self._lock:
            self._results[job_id] = {"status": "pending"}
        self._jobs.put((job_id, code))
        return job_id

    def get_result(self, job_id: str) -> dict:
        """
        Non-blocking status/result check.
        Returns {"status": "not_found"} if the id is unknown,
        {"status": "pending"} if still queued/processing, or the
        full result dict (with "status": "done") once finished.
        """
        with self._lock:
            result = self._results.get(job_id)
        if result is None:
            return {"status": "not_found"}
        return result

    def queue_depth(self) -> int:
        """Approximate number of jobs waiting to be picked up by a worker."""
        return self._jobs.qsize()

    def _worker(self):
        """
        Runs forever in a background thread: block on `.get()` until a
        job appears, run the pipeline, store the result. Because each
        worker loops independently, up to `max_workers` jobs run truly
        concurrently.
        """
        while True:
            job_id, code = self._jobs.get()  # blocks until a job is available
            try:
                result = self._pipeline_fn(code)
                result["status"] = "done"
            except Exception as e:
                # A crashed pipeline shouldn't kill the worker thread —
                # report the error as the job's result instead.
                result = {"status": "error", "error": str(e)}

            with self._lock:
                self._results[job_id] = result
            self._jobs.task_done()


# ----------------------------------------------------------------------
# Stage 6 self-test — uses a fake pipeline_fn (with a small sleep to
# simulate work) to confirm submit/get_result and concurrency behave.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import time

    def fake_pipeline(code: str) -> dict:
        time.sleep(0.2)  # simulate work (e.g. an LLM call)
        return {"echo": code}

    print("=== Stage 6 self-test ===")

    q = ReviewJobQueue(pipeline_fn=fake_pipeline, max_workers=3)

    # Check unknown job_id.
    unknown = q.get_result("does-not-exist")
    print(f"  {'PASS' if unknown['status'] == 'not_found' else 'FAIL'}: unknown job_id returns not_found")

    # Submit 3 jobs concurrently and confirm submit() returns instantly.
    start = time.time()
    job_ids = [q.submit(f"code_{i}") for i in range(3)]
    submit_elapsed = time.time() - start
    print(f"  {'PASS' if submit_elapsed < 0.05 else 'FAIL'}: submit() returned immediately ({submit_elapsed*1000:.1f}ms for 3 jobs)")

    # Immediately after submitting, at least one should still be pending.
    immediate_status = q.get_result(job_ids[0])["status"]
    print(f"  {'PASS' if immediate_status == 'pending' else 'FAIL'}: job is 'pending' right after submit (got '{immediate_status}')")

    # Wait for all 3 to finish. With 3 workers and 0.2s each, this
    # should take ~0.2s total (parallel), not ~0.6s (serial).
    wait_start = time.time()
    while any(q.get_result(jid)["status"] == "pending" for jid in job_ids):
        time.sleep(0.01)
    wait_elapsed = time.time() - wait_start
    print(f"  {'PASS' if wait_elapsed < 0.5 else 'FAIL'}: 3 jobs with 3 workers finished in {wait_elapsed:.2f}s (parallel, not serial)")

    final_results = [q.get_result(jid) for jid in job_ids]
    all_done = all(r["status"] == "done" for r in final_results)
    print(f"  {'PASS' if all_done else 'FAIL'}: all jobs report status 'done'")
