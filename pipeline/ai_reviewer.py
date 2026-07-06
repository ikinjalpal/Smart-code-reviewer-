"""
ai_reviewer.py
==============
Calls an LLM to do the part static analysis CAN'T do: judge design,
naming, edge-case handling, and style — things that require actual
understanding of intent, not just pattern matching.

KEY DESIGN DECISION (this is the whole point of the project):
We do NOT just dump raw code at the model and ask "review this."
We first run AST analysis, complexity analysis, and security
scanning, and feed those *results* into the prompt as context. This
means:
  1. The LLM doesn't waste its output re-discovering the mutable
     default argument bug — the prompt tells it that's already found,
     and asks it to focus on things NOT already caught.
  2. The LLM is less likely to hallucinate a complexity claim, because
     we've already told it the measured Big-O.
  3. Static findings + AI findings are complementary, not duplicated,
     which is the "why multi-stage, not just AI" argument for the README.
"""

import os
import json
import requests


class AIReviewer:
    """Sends an enriched, context-rich prompt to an LLM for qualitative review."""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.model = "claude-sonnet-4-6"

    def _build_prompt(self, code: str, static_results: dict) -> str:
        """
        Construct the enriched prompt. Kept as its own method (rather
        than inline in `review`) so it can be unit-tested and reused
        without making a network call.
        """
        return f"""You are a senior Google software engineer doing a code review.

CODE:
```python
{code}
```

STATIC ANALYSIS ALREADY FOUND:
Bugs detected: {json.dumps(static_results.get('bugs', []))}
Complexity: {json.dumps(static_results.get('complexity', {}))}
Security issues: {json.dumps(static_results.get('security', []))}

Given the above context, do NOT repeat what static analysis already found.
Instead, provide:
1. Exactly 3 specific improvements NOT already caught above.
2. Code style issues, evaluated against the Google Python Style Guide.
3. Edge cases the code doesn't handle.
4. A rewritten version of the most problematic function.

Respond ONLY with a JSON object using this exact shape, no prose outside the JSON:
{{
  "improvements": ["...", "...", "..."],
  "style_issues": ["..."],
  "edge_cases": ["..."],
  "rewritten_function": "..."
}}"""

    def review(self, code: str, static_results: dict) -> dict:
        """
        Build the enriched prompt, call the LLM, and parse the JSON
        response. Any failure (missing key, network error, bad JSON)
        degrades gracefully to a fallback dict rather than crashing
        the whole pipeline — one failed stage shouldn't take down the
        others.
        """
        if not self.api_key:
            return self._fallback("No ANTHROPIC_API_KEY configured in the environment.")

        prompt = self._build_prompt(code, static_results)

        try:
            response = requests.post(
                self.api_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            # The model's reply is a list of content blocks; we expect
            # a single text block containing our requested JSON.
            raw_text = "".join(
                block.get("text", "") for block in data.get("content", [])
                if block.get("type") == "text"
            )

            # Defensive cleanup in case the model wraps the JSON in
            # markdown code fences despite instructions not to.
            cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(cleaned)
            parsed["_source"] = "ai"
            return parsed

        except requests.exceptions.RequestException as e:
            return self._fallback(f"AI review API call failed: {e}")
        except (json.JSONDecodeError, KeyError) as e:
            return self._fallback(f"Could not parse AI response: {e}")

    def _fallback(self, reason: str) -> dict:
        """Uniform shape returned on any failure, so callers never have to branch."""
        return {
            "improvements": [],
            "style_issues": [],
            "edge_cases": [],
            "rewritten_function": None,
            "_source": "fallback",
            "_error": reason,
        }


# ----------------------------------------------------------------------
# Stage 5 self-test — verifies prompt construction and the no-API-key
# fallback path (we don't call the real network in an automated test).
# ----------------------------------------------------------------------
if __name__ == "__main__":
    reviewer = AIReviewer()

    sample_code = "def foo(x=[]):\n    x.append(1)\n    return x"
    sample_static = {
        "bugs": [{"type": "mutable_default_argument", "line": 1}],
        "complexity": {"time_complexity": "O(1)"},
        "security": [],
    }

    prompt = reviewer._build_prompt(sample_code, sample_static)
    print("=== Stage 5 self-test: prompt construction ===")
    checks = {
        "includes code": sample_code in prompt,
        "includes static bug context": "mutable_default_argument" in prompt,
        "requests JSON-only response": "Respond ONLY with a JSON object" in prompt,
    }
    all_pass = True
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}: {name}")
        all_pass = all_pass and passed

    print("\n=== Stage 5 self-test: graceful fallback (no API key case) ===")
    reviewer.api_key = None
    result = reviewer.review(sample_code, sample_static)
    fallback_ok = result["_source"] == "fallback" and result["rewritten_function"] is None
    print(f"  {'PASS' if fallback_ok else 'FAIL'}: fallback returned safely without crashing")
    all_pass = all_pass and fallback_ok

    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
