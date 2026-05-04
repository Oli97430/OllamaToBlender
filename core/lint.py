"""Cheap pre-flight: AST-parse the model's code so syntax errors don't waste a Blender round-trip."""
from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class LintIssue:
    line: int
    col: int
    message: str

    def format(self) -> str:
        return f"line {self.line}, col {self.col}: {self.message}"


def lint_python(code: str) -> list[LintIssue]:
    """Return a list of issues. Empty list = no syntax problems detected."""
    issues: list[LintIssue] = []
    try:
        ast.parse(code)
    except SyntaxError as exc:
        issues.append(
            LintIssue(
                line=exc.lineno or 0,
                col=exc.offset or 0,
                message=str(exc.msg or exc),
            )
        )
    return issues
