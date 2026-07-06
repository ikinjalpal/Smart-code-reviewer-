"""
complexity.py
=============
Estimates time complexity (Big-O) of a function purely from its AST
shape. This is a heuristic, not a formal proof — real complexity
analysis is undecidable in general — but the heuristics below cover
the patterns that show up constantly in interview-style code:
plain loops, nested loops, and recursion (which we treat specially
because a loop-counting heuristic alone can't see it).
"""

import ast


class ComplexityAnalyzer:
    """Classifies a function's time complexity from its AST."""

    def analyze(self, code: str, func_name: str) -> dict:
        """
        Parse `code`, locate the function named `func_name`, and
        classify its complexity.
        """
        tree = ast.parse(code)

        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                target = node
                break

        if target is None:
            return {
                "function": func_name,
                "time_complexity": "unknown",
                "loop_depth": 0,
                "has_recursion": False,
                "explanation": f"No function named '{func_name}' found in the given code.",
            }

        loop_depth = self._count_loop_depth(target)
        has_recursion = self._has_recursion(target)
        is_binary_search_shape = self._looks_like_binary_search(target)

        time_complexity, explanation = self._classify(
            loop_depth, has_recursion, is_binary_search_shape
        )

        return {
            "function": func_name,
            "time_complexity": time_complexity,
            "loop_depth": loop_depth,
            "has_recursion": has_recursion,
            "explanation": explanation,
        }

    # ------------------------------------------------------------------
    # The DSA component: recursive tree traversal counting max loop nesting
    # ------------------------------------------------------------------
    def _count_loop_depth(self, node, current_depth: int = 0) -> int:
        """
        Recursively walk the AST, incrementing depth every time we
        descend into a For/While loop. Returns the MAXIMUM depth
        reached anywhere under `node`.

        This mirrors a classic tree-DFS: at each node, recurse into
        every child, take the max of the children's results, and
        bubble that max back up.
        """
        max_depth = current_depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While)):
                # Entering a loop increases depth for everything inside it.
                child_depth = self._count_loop_depth(child, current_depth + 1)
            else:
                # Non-loop nodes don't add depth themselves, but we still
                # need to recurse into them to find loops further down
                # (e.g. a loop inside an `if` block inside a function).
                child_depth = self._count_loop_depth(child, current_depth)
            max_depth = max(max_depth, child_depth)
        return max_depth

    # ------------------------------------------------------------------
    # Recursion detection
    # ------------------------------------------------------------------
    def _has_recursion(self, func_node) -> bool:
        """
        A function is recursive (at least directly) if its body
        contains a Call node whose function name matches its own name.
        This won't catch mutual recursion (A calls B calls A), which
        is a known limitation we call out in the README.
        """
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == func_node.name:
                    return True
        return False

    # ------------------------------------------------------------------
    # Binary-search-shape heuristic
    # ------------------------------------------------------------------
    def _looks_like_binary_search(self, func_node) -> bool:
        """
        Binary search doesn't look like a "loop over n" or "nested
        loop" — it's a single while/for loop where the search space
        is *halved* each iteration (e.g. `mid = (low + high) // 2`
        followed by reassigning low or high based on mid). We detect
        this pattern by looking for floor-division by 2 combined with
        a loop, which is a strong, simple signal without needing full
        symbolic execution.
        """
        found_halving = False
        found_single_loop = False

        for node in ast.walk(func_node):
            if isinstance(node, (ast.For, ast.While)):
                found_single_loop = True
            # Look for `// 2` (floor division by the literal 2) anywhere,
            # which is the classic "find the midpoint" operation.
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.FloorDiv):
                if isinstance(node.right, ast.Constant) and node.right.value == 2:
                    found_halving = True

        return found_single_loop and found_halving

    # ------------------------------------------------------------------
    # Classification rules
    # ------------------------------------------------------------------
    def _classify(self, loop_depth: int, has_recursion: bool, is_binary_search: bool):
        if is_binary_search and loop_depth <= 1 and not has_recursion:
            return "O(log n)", "Single loop that halves the search space each iteration (binary-search pattern)."

        if has_recursion and loop_depth >= 1:
            return (
                "O(n log n) or worse — flagged for manual review",
                "Recursion combined with a loop detected. This pattern covers many complexities "
                "(e.g. merge sort is O(n log n), but this could also be exponential); "
                "a human reviewer should confirm the exact bound.",
            )

        if has_recursion:
            return (
                "O(?) recursive — flagged for manual review",
                "Function calls itself. Complexity depends on how the input shrinks per call "
                "(e.g. O(log n) for halving, O(2^n) for branching); static analysis alone can't "
                "determine this reliably.",
            )

        if loop_depth == 0:
            return "O(1)", "No loops or recursion detected — constant time."
        if loop_depth == 1:
            return "O(n)", "Single loop iterating over the input — linear time."
        if loop_depth == 2:
            return "O(n²)", "Two nested loops detected iterating over input size n."
        # loop_depth >= 3
        return f"O(n^{loop_depth})", f"{loop_depth} levels of nested loops detected."


# ----------------------------------------------------------------------
# Stage 3 self-test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    analyzer = ComplexityAnalyzer()

    linear_search_code = """
def linear_search(arr, target):
    for i in range(len(arr)):
        if arr[i] == target:
            return i
    return -1
"""

    bubble_sort_code = """
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr
"""

    binary_search_code = """
def binary_search(arr, target):
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

    tests = [
        ("linear_search", linear_search_code, "O(n)"),
        ("bubble_sort", bubble_sort_code, "O(n²)"),
        ("binary_search", binary_search_code, "O(log n)"),
    ]

    print("=== Stage 3 self-test ===")
    all_pass = True
    for func_name, code, expected in tests:
        result = analyzer.analyze(code, func_name)
        passed = result["time_complexity"] == expected
        all_pass = all_pass and passed
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {func_name:15} expected {expected:8} got {result['time_complexity']:8} | {result['explanation']}")

    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
