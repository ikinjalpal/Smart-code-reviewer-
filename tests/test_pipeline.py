"""
test_pipeline.py
=================
Unit tests for each pipeline stage in isolation, plus one integration
test that runs the full app-level pipeline function. Run with:

    python3 -m pytest tests/test_pipeline.py -v

or, without pytest installed, run this file directly with:

    python3 tests/test_pipeline.py
"""

import os
import sys

# Make the project root importable regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.ast_analyzer import ASTAnalyzer
from pipeline.complexity import ComplexityAnalyzer
from pipeline.security import SecurityScanner
from pipeline.ai_reviewer import AIReviewer


# ----------------------------------------------------------------------
# ASTAnalyzer
# ----------------------------------------------------------------------
def test_ast_detects_mutable_default_argument():
    analyzer = ASTAnalyzer()
    tree = analyzer.parse("def foo(x=[]):\n    return x")
    bugs = analyzer.detect_bugs(tree)
    assert any(b["type"] == "mutable_default_argument" for b in bugs)


def test_ast_detects_infinite_loop_risk():
    analyzer = ASTAnalyzer()
    tree = analyzer.parse("def foo():\n    while True:\n        pass")
    bugs = analyzer.detect_bugs(tree)
    assert any(b["type"] == "infinite_loop_risk" for b in bugs)


def test_ast_does_not_flag_while_true_with_break():
    analyzer = ASTAnalyzer()
    tree = analyzer.parse("def foo():\n    while True:\n        break")
    bugs = analyzer.detect_bugs(tree)
    assert not any(b["type"] == "infinite_loop_risk" for b in bugs)


def test_ast_detects_bare_except():
    analyzer = ASTAnalyzer()
    tree = analyzer.parse("try:\n    pass\nexcept:\n    pass")
    bugs = analyzer.detect_bugs(tree)
    assert any(b["type"] == "bare_except" for b in bugs)


def test_ast_detects_unused_variable():
    analyzer = ASTAnalyzer()
    tree = analyzer.parse("def foo():\n    x = 1\n    return 2")
    bugs = analyzer.detect_bugs(tree)
    assert any(b["type"] == "unused_variable" for b in bugs)


def test_ast_raises_clear_error_on_syntax_error():
    analyzer = ASTAnalyzer()
    try:
        analyzer.parse("def foo(:\n    pass")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "line" in str(e)


def test_ast_extract_functions_metadata():
    analyzer = ASTAnalyzer()
    tree = analyzer.parse("def foo(a, b):\n    return a + b")
    functions = analyzer.extract_functions(tree)
    assert len(functions) == 1
    assert functions[0]["name"] == "foo"
    assert functions[0]["arg_count"] == 2
    assert functions[0]["has_return"] is True


# ----------------------------------------------------------------------
# ComplexityAnalyzer
# ----------------------------------------------------------------------
def test_complexity_constant_time():
    analyzer = ComplexityAnalyzer()
    result = analyzer.analyze("def foo(x):\n    return x + 1", "foo")
    assert result["time_complexity"] == "O(1)"


def test_complexity_linear():
    analyzer = ComplexityAnalyzer()
    code = "def foo(arr):\n    for x in arr:\n        print(x)"
    result = analyzer.analyze(code, "foo")
    assert result["time_complexity"] == "O(n)"


def test_complexity_quadratic():
    analyzer = ComplexityAnalyzer()
    code = "def foo(arr):\n    for x in arr:\n        for y in arr:\n            print(x, y)"
    result = analyzer.analyze(code, "foo")
    assert result["time_complexity"] == "O(n\u00b2)"


def test_complexity_logarithmic_binary_search():
    analyzer = ComplexityAnalyzer()
    code = """
def foo(arr, target):
    low, high = 0, len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
"""
    result = analyzer.analyze(code, "foo")
    assert result["time_complexity"] == "O(log n)"


def test_complexity_recursion_flagged():
    analyzer = ComplexityAnalyzer()
    code = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)"
    result = analyzer.analyze(code, "fact")
    assert result["has_recursion"] is True


def test_complexity_unknown_function_name():
    analyzer = ComplexityAnalyzer()
    result = analyzer.analyze("def foo():\n    pass", "does_not_exist")
    assert result["time_complexity"] == "unknown"


# ----------------------------------------------------------------------
# SecurityScanner
# ----------------------------------------------------------------------
def test_security_detects_sql_injection():
    scanner = SecurityScanner()
    findings = scanner.scan('cursor.execute(f"SELECT * FROM t WHERE id={x}")')
    assert any(f["vulnerability"] == "sql_injection" for f in findings)


def test_security_detects_hardcoded_secret():
    scanner = SecurityScanner()
    findings = scanner.scan('password = "hunter2"')
    assert any(f["vulnerability"] == "hardcoded_secret" for f in findings)


def test_security_detects_unsafe_eval():
    scanner = SecurityScanner()
    findings = scanner.scan("eval(user_input)")
    assert any(f["vulnerability"] == "unsafe_eval" for f in findings)


def test_security_detects_weak_crypto():
    scanner = SecurityScanner()
    findings = scanner.scan("import hashlib\nhashlib.md5(data)")
    assert any(f["vulnerability"] == "weak_crypto" for f in findings)


def test_security_clean_code_has_no_findings():
    scanner = SecurityScanner()
    findings = scanner.scan("def add(a, b):\n    return a + b")
    assert findings == []


# ----------------------------------------------------------------------
# AIReviewer
# ----------------------------------------------------------------------
def test_ai_reviewer_prompt_includes_static_context():
    reviewer = AIReviewer()
    prompt = reviewer._build_prompt("def foo(): pass", {"bugs": [{"type": "x"}], "complexity": {}, "security": []})
    assert "STATIC ANALYSIS ALREADY FOUND" in prompt
    assert '"type": "x"' in prompt


def test_ai_reviewer_falls_back_gracefully_without_api_key():
    reviewer = AIReviewer()
    reviewer.api_key = None
    result = reviewer.review("def foo(): pass", {"bugs": [], "complexity": {}, "security": []})
    assert result["_source"] == "fallback"
    assert result["rewritten_function"] is None


# ----------------------------------------------------------------------
# Simple manual test runner (works without pytest installed)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    test_functions = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = 0, 0
    for test_fn in test_functions:
        try:
            test_fn()
            print(f"  PASS: {test_fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test_fn.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test_fn.__name__} — {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(test_functions)} tests")
    sys.exit(1 if failed else 0)
