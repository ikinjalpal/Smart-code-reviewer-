"""
security.py
===========
Lightweight regex-based vulnerability scanner.

Why regex here, and not AST like the bug detector? Security patterns
like "hardcoded secret" or "uses eval()" are fundamentally about
*textual/lexical* patterns (a suspicious string literal, a suspicious
function name) rather than program *structure*. Regex is the right
tool for this job, and it's also how real tools like Bandit and
Semgrep's simple rules work under the hood — pattern matching over
source text/tokens, not full symbolic execution.

Trade-off to be upfront about (good interview talking point): regex
scanning has false positives/negatives. A string that happens to say
"password = get_from_vault()" isn't a hardcoded secret, but a naive
pattern could still catch it depending on exact regex. We keep the
patterns as specific as practical to reduce noise.
"""

import re


class SecurityScanner:
    """Scans source code text for common vulnerability patterns."""

    # Each key is a vulnerability category. Each value is a list of
    # regex patterns that, if matched, indicate that vulnerability.
    PATTERNS = {
        "hardcoded_secret": [
            r'password\s*=\s*["\'][^"\']+["\']',
            r'api_key\s*=\s*["\'][^"\']+["\']',
            r'secret\s*=\s*["\'][^"\']+["\']',
        ],
        "sql_injection": [
            r'execute\s*\(\s*["\'].*%s',
            r'execute\s*\(\s*f["\']',
            r'cursor\.execute.*format\(',
        ],
        "xss_risk": [
            r'innerHTML\s*=',
            r'document\.write\(',
        ],
        "unsafe_eval": [
            r'\beval\s*\(',
            r'\bexec\s*\(',
        ],
        "weak_crypto": [
            r'\bmd5\b',
            r'\bsha1\b',
        ],
    }

    # Human-readable severity and fix suggestion per category, so every
    # finding comes back with actionable guidance, not just a label.
    METADATA = {
        "hardcoded_secret": ("critical", "Load secrets from environment variables or a secrets manager, never hardcode them."),
        "sql_injection": ("critical", "Use parameterized queries (e.g. cursor.execute(query, (param,))) instead of string formatting."),
        "xss_risk": ("high", "Sanitize/escape untrusted input before inserting into the DOM, or use textContent instead of innerHTML."),
        "unsafe_eval": ("high", "Avoid eval()/exec() on any input that could be influenced by a user; use safer alternatives like ast.literal_eval for data."),
        "weak_crypto": ("medium", "Use a modern hash function (e.g. SHA-256 or better) or a dedicated password-hashing algorithm like bcrypt/argon2."),
    }

    def scan(self, code: str) -> list[dict]:
        """
        Run every pattern group against every line of `code`.
        We scan line-by-line (rather than the whole blob at once) so
        we can accurately report a line number for each finding.
        """
        findings = []
        lines = code.splitlines()

        for category, patterns in self.PATTERNS.items():
            severity, fix = self.METADATA[category]
            for compiled in (re.compile(p, re.IGNORECASE) for p in patterns):
                for line_number, line_text in enumerate(lines, start=1):
                    match = compiled.search(line_text)
                    if match:
                        findings.append({
                            "vulnerability": category,
                            "line": line_number,
                            "matched": match.group(0),
                            "severity": severity,
                            "fix": fix,
                        })

        # Sort by line number so results read top-to-bottom like the source.
        findings.sort(key=lambda f: f["line"])
        return findings


# ----------------------------------------------------------------------
# Stage 4 self-test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    scanner = SecurityScanner()

    test_code = """
def get_user(user_id):
    import sqlite3
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    password = "admin123"
    return cursor.fetchall()
"""

    findings = scanner.scan(test_code)

    print("=== Stage 4 self-test ===")
    for f in findings:
        print(f"  [{f['severity'].upper():8}] line {f['line']:2} | {f['vulnerability']:20} | matched: {f['matched']!r}")

    found_types = {f["vulnerability"] for f in findings}
    checks = {
        "sql_injection": "sql_injection" in found_types,
        "hardcoded_secret": "hardcoded_secret" in found_types,
    }
    print("\n=== Checks ===")
    all_pass = True
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}: {name}")
        all_pass = all_pass and passed
    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
