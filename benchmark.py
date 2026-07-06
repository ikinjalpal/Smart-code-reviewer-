"""
benchmark.py
============
Produces the concrete numbers referenced in the README and resume
bullets:
  1. Per-stage timing (how long AST/complexity/security/full pipeline take)
  2. Bug-detection accuracy against a fixed set of snippets with known bugs

Run with:  python3 benchmark.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.ast_analyzer import ASTAnalyzer
from pipeline.complexity import ComplexityAnalyzer
from pipeline.security import SecurityScanner
from pipeline.ai_reviewer import AIReviewer

ast_analyzer = ASTAnalyzer()
complexity_analyzer = ComplexityAnalyzer()
security_scanner = SecurityScanner()
ai_reviewer = AIReviewer()

SAMPLE_CODE = """
def get_user(user_id, cache={}):
    import sqlite3
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    password = "admin123"
    while True:
        pass
    return cursor.fetchall()
"""


def time_it(fn, *args, repeats: int = 200):
    """Run fn `repeats` times and return the average duration in milliseconds."""
    start = time.perf_counter()
    for _ in range(repeats):
        fn(*args)
    elapsed = time.perf_counter() - start
    return (elapsed / repeats) * 1000  # ms per call


def run_full_pipeline_no_ai(code: str) -> dict:
    tree = ast_analyzer.parse(code)
    bugs = ast_analyzer.detect_bugs(tree)
    functions = ast_analyzer.extract_functions(tree)
    complexity_results = [complexity_analyzer.analyze(code, f["name"]) for f in functions]
    security_findings = security_scanner.scan(code)
    return {"bugs": bugs, "complexity": complexity_results, "security": security_findings}


def run_full_pipeline_with_ai(code: str) -> dict:
    static_results = run_full_pipeline_no_ai(code)
    ai_result = ai_reviewer.review(code, static_results)
    static_results["ai_review"] = ai_result
    return static_results


# ----------------------------------------------------------------------
# 1. Stage timing
# ----------------------------------------------------------------------
def benchmark_stage_timing():
    print("=" * 62)
    print("1. PIPELINE STAGE TIMING (avg over 200 runs, single-threaded)")
    print("=" * 62)

    ast_ms = time_it(lambda c: ast_analyzer.detect_bugs(ast_analyzer.parse(c)), SAMPLE_CODE)
    complexity_ms = time_it(lambda c: complexity_analyzer.analyze(c, "get_user"), SAMPLE_CODE)
    security_ms = time_it(lambda c: security_scanner.scan(c), SAMPLE_CODE)
    full_no_ai_ms = time_it(run_full_pipeline_no_ai, SAMPLE_CODE)

    # The AI call is slow (network) and shouldn't run 200x in a benchmark
    # loop — one timed call is enough to report, with a graceful skip if
    # no API key is configured (so this benchmark still runs standalone).
    if ai_reviewer.api_key:
        start = time.perf_counter()
        run_full_pipeline_with_ai(SAMPLE_CODE)
        full_with_ai_ms = (time.perf_counter() - start) * 1000
        full_with_ai_label = f"{full_with_ai_ms:8.2f} ms"
    else:
        full_with_ai_label = "  skipped (no ANTHROPIC_API_KEY set)"

    rows = [
        ("AST analysis alone", f"{ast_ms:8.3f} ms"),
        ("Complexity analysis alone", f"{complexity_ms:8.3f} ms"),
        ("Security scan alone", f"{security_ms:8.3f} ms"),
        ("Full pipeline (no AI)", f"{full_no_ai_ms:8.3f} ms"),
        ("Full pipeline (with AI)", full_with_ai_label),
    ]
    for label, value in rows:
        print(f"  {label:<28} {value}")
    print()


# ----------------------------------------------------------------------
# 2. Bug detection accuracy
# ----------------------------------------------------------------------
# Each entry: (category, code snippet, does the snippet contain that bug?)
ACCURACY_CASES = [
    ("mutable_default_argument", "def foo(x=[]):\n    return x", True),
    ("mutable_default_argument", "def foo(x=None):\n    x = x or []\n    return x", False),
    ("infinite_loop_risk", "def foo():\n    while True:\n        pass", True),
    ("infinite_loop_risk", "def foo():\n    while True:\n        break", False),
    ("bare_except", "try:\n    pass\nexcept:\n    pass", True),
    ("bare_except", "try:\n    pass\nexcept ValueError:\n    pass", False),
    ("unused_variable", "def foo():\n    x = 1\n    return 2", True),
    ("unused_variable", "def foo():\n    x = 1\n    return x", False),
    ("sql_injection", 'cursor.execute(f"SELECT * FROM t WHERE id={x}")', True),
    ("sql_injection", 'cursor.execute("SELECT * FROM t WHERE id=?", (x,))', False),
]


def benchmark_accuracy():
    print("=" * 62)
    print("2. BUG DETECTION ACCURACY (10 snippets, known ground truth)")
    print("=" * 62)

    categories = {}  # category -> [correct, total]

    for category, code, should_detect in ACCURACY_CASES:
        categories.setdefault(category, [0, 0])
        categories[category][1] += 1

        if category == "sql_injection":
            findings = security_scanner.scan(code)
            detected = any(f["vulnerability"] == "sql_injection" for f in findings)
        else:
            tree = ast_analyzer.parse(code)
            bugs = ast_analyzer.detect_bugs(tree)
            detected = any(b["type"] == category for b in bugs)

        correct = (detected == should_detect)
        if correct:
            categories[category][0] += 1

    total_correct, total_cases = 0, 0
    for category, (correct, total) in categories.items():
        print(f"  {category:<28} {correct}/{total} correct")
        total_correct += correct
        total_cases += total

    print(f"\n  Overall: {total_correct}/{total_cases} correct ({100 * total_correct / total_cases:.0f}%)")
    print()


if __name__ == "__main__":
    benchmark_stage_timing()
    benchmark_accuracy()
