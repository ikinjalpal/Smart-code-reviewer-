"""
ast_analyzer.py
================
Core static-analysis component. Uses Python's built-in `ast` module
(no third-party parsing library) to turn source code into a tree of
nodes, then walks that tree to find common bug patterns.

Why AST instead of regex/string matching?
A regex can't tell the difference between the word "except" inside a
string literal and an actual `except:` clause. The AST is the same
data structure the Python interpreter itself builds before running
your code, so we're reasoning about *structure*, not text.
"""

import ast


class ASTAnalyzer:
    """Parses Python source and detects a fixed set of bug patterns."""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse(self, code: str) -> ast.AST:
        """
        Convert a source-code string into an AST.

        ast.parse() raises SyntaxError on invalid Python. We catch that
        and re-raise a ValueError with a clearer message, because the
        caller (the Flask route) shouldn't have to know about `ast`
        internals to handle bad input.
        """
        try:
            # mode="exec" (the default) means "parse this as a full
            # module/script", as opposed to a single expression.
            return ast.parse(code)
        except SyntaxError as e:
            # e.lineno / e.msg are attributes SyntaxError gives us for
            # free — use them so the user knows *where* it broke.
            raise ValueError(f"Syntax error on line {e.lineno}: {e.msg}")

    # ------------------------------------------------------------------
    # Bug detection
    # ------------------------------------------------------------------
    def detect_bugs(self, tree: ast.AST) -> list[dict]:
        """
        Walk the tree once per bug category and collect findings.
        Each category is a small, focused method so it's easy to
        reason about (and to explain in an interview) in isolation.
        """
        bugs = []
        bugs.extend(self._detect_unused_variables(tree))
        bugs.extend(self._detect_infinite_loop_risk(tree))
        bugs.extend(self._detect_bare_except(tree))
        bugs.extend(self._detect_mutable_default_args(tree))
        bugs.extend(self._detect_missing_return(tree))
        return bugs

    def _detect_unused_variables(self, tree: ast.AST) -> list[dict]:
        """
        Detect variables that are assigned but never read, *within a
        single function scope*. We do this per-function so that a name
        reused across two different functions isn't a false positive.
        """
        bugs = []

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            assigned = {}  # name -> line number of assignment
            read = set()   # names that appear in a "load" (read) context

            for node in ast.walk(func):
                # A simple `x = 5` shows up as ast.Assign with Name
                # targets whose ctx is ast.Store.
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                            assigned[target.id] = node.lineno
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    read.add(node.id)

            for name, lineno in assigned.items():
                # Skip the conventional "I know I'm not using this" name.
                if name == "_" or name in read:
                    continue
                bugs.append({
                    "type": "unused_variable",
                    "line": lineno,
                    "message": f"Variable '{name}' is assigned but never read.",
                    "severity": "low",
                })
        return bugs

    def _detect_infinite_loop_risk(self, tree: ast.AST) -> list[dict]:
        """
        Flag `while True:` loops that contain no `break` statement
        anywhere in their body. Note: we must NOT count a `break` that
        belongs to a nested loop (that break wouldn't exit the outer
        while). So we walk the body but stop descending into nested
        loops when looking for a qualifying break.
        """
        bugs = []

        def is_while_true(node) -> bool:
            # `while True:` parses as While(test=Constant(value=True))
            return isinstance(node.test, ast.Constant) and node.test.value is True

        def has_break_in_own_scope(body_nodes) -> bool:
            for node in body_nodes:
                for child in ast.walk(node):
                    # Stop counting breaks that belong to a nested loop.
                    if isinstance(child, (ast.For, ast.While)) and child is not node:
                        continue
                    if isinstance(child, ast.Break):
                        return True
            return False

        for node in ast.walk(tree):
            if isinstance(node, ast.While) and is_while_true(node):
                # Directly scan node.body/orelse (not nested loops) for a break.
                found_break = False
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Break):
                        found_break = True
                        break
                if not found_break:
                    bugs.append({
                        "type": "infinite_loop_risk",
                        "line": node.lineno,
                        "message": "while True loop has no break statement — risk of infinite loop.",
                        "severity": "high",
                    })
        return bugs

    def _detect_bare_except(self, tree: ast.AST) -> list[dict]:
        """
        `except:` with no exception type silently swallows *everything*,
        including KeyboardInterrupt and SystemExit. ast.ExceptHandler
        has `.type is None` in exactly this case.
        """
        bugs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                bugs.append({
                    "type": "bare_except",
                    "line": node.lineno,
                    "message": "Bare 'except:' catches all exceptions, including system-exiting ones. Catch specific exception types instead.",
                    "severity": "medium",
                })
        return bugs

    def _detect_mutable_default_args(self, tree: ast.AST) -> list[dict]:
        """
        `def foo(x=[])` is a classic Python gotcha: the default list is
        created ONCE when the function is defined, not each call, so
        mutations leak across calls. We flag List/Dict/Set literals
        used as default values.
        """
        bugs = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for default in node.args.defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    bugs.append({
                        "type": "mutable_default_argument",
                        "line": node.lineno,
                        "message": f"Function '{node.name}' uses a mutable default argument. Use None and assign inside the function body instead.",
                        "severity": "high",
                    })
        return bugs

    def _detect_missing_return(self, tree: ast.AST) -> list[dict]:
        """
        Heuristic: if a function has at least one `return <value>`
        (a return that isn't bare `return` / implicit None) on SOME
        code path, but there also exists a path that falls off the end
        of the function without returning, that's often a bug (the
        caller expects a value on every path).

        We keep this heuristic simple and explicit: a function "looks
        non-void" if any return statement returns a non-None value.
        It "is missing a return" if the function's last top-level
        statement is not a return/raise AND not every branch of an
        if/elif/else at the end returns.
        """
        bugs = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            returns_with_value = [
                n for n in ast.walk(node)
                if isinstance(n, ast.Return) and n.value is not None
            ]
            if not returns_with_value:
                continue  # function never returns a value anywhere -> void function, not a bug

            if not node.body:
                continue

            last_stmt = node.body[-1]
            if self._statement_always_returns(last_stmt):
                continue

            bugs.append({
                "type": "missing_return",
                "line": node.lineno,
                "message": f"Function '{node.name}' returns a value on some paths but may fall through without returning on others.",
                "severity": "medium",
            })
        return bugs

    def _statement_always_returns(self, stmt) -> bool:
        """Helper: does this statement guarantee the function returns/raises?"""
        if isinstance(stmt, (ast.Return, ast.Raise)):
            return True
        if isinstance(stmt, ast.If):
            # Only "always returns" if there's an else AND both branches return.
            if not stmt.orelse:
                return False
            body_returns = bool(stmt.body) and self._statement_always_returns(stmt.body[-1])
            else_returns = bool(stmt.orelse) and self._statement_always_returns(stmt.orelse[-1])
            return body_returns and else_returns
        return False

    # ------------------------------------------------------------------
    # Function extraction (feeds the complexity analyzer)
    # ------------------------------------------------------------------
    def extract_functions(self, tree: ast.AST) -> list[dict]:
        """
        Pull out metadata for every function definition in the tree.
        This is consumed by ComplexityAnalyzer so it doesn't have to
        re-walk the tree from scratch.
        """
        functions = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            has_return = any(
                isinstance(n, ast.Return) and n.value is not None
                for n in ast.walk(node)
            )

            functions.append({
                "name": node.name,
                "line": node.lineno,
                "arg_count": len(node.args.args),
                "has_return": has_return,
                "nested_depth": self._max_block_depth(node),
            })
        return functions

    def _max_block_depth(self, node, current_depth: int = 0) -> int:
        """
        Recursive AST walk computing the deepest nesting of compound
        statements (if/for/while/with/try) inside a function. This is
        a small recursive-tree-traversal utility in its own right.
        """
        max_depth = current_depth
        compound_types = (ast.If, ast.For, ast.While, ast.With, ast.Try)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, compound_types):
                max_depth = max(max_depth, self._max_block_depth(child, current_depth + 1))
            else:
                max_depth = max(max_depth, self._max_block_depth(child, current_depth))
        return max_depth


# ----------------------------------------------------------------------
# Stage 2 self-test — run this file directly to confirm detection works
# before moving on to Stage 3.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    analyzer = ASTAnalyzer()

    test_code = """
def process(items, cache={}):
    while True:
        x = 1
        try:
            pass
        except:
            pass
    return cache
"""

    tree = analyzer.parse(test_code)
    bugs = analyzer.detect_bugs(tree)

    print("=== Stage 2 self-test ===")
    for bug in bugs:
        print(f"  [{bug['severity'].upper():6}] line {bug['line']:2} | {bug['type']:25} | {bug['message']}")

    found_types = {b["type"] for b in bugs}
    checks = {
        "mutable_default_argument": "mutable_default_argument" in found_types,
        "infinite_loop_risk": "infinite_loop_risk" in found_types,
        "bare_except": "bare_except" in found_types,
    }
    print("\n=== Checks ===")
    all_pass = True
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}: {name}")
        all_pass = all_pass and passed

    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")

    functions = analyzer.extract_functions(tree)
    print("\n=== extract_functions ===")
    print(functions)
