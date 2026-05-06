"""Cheap pre-flight: AST-parse the model's code so syntax errors don't waste a Blender round-trip.

Also runs lightweight semantic checks for common model mistakes.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass
class LintIssue:
    line: int
    col: int
    message: str
    level: str = "error"  # "error" | "warn"

    def format(self) -> str:
        prefix = "warning" if self.level == "warn" else "error"
        return f"{prefix}: line {self.line}, col {self.col}: {self.message}"


# Patterns that are always wrong in Blender 4+
_SEMANTIC_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"""nodes\s*\[\s*["']Principled BSDF["']\s*\]"""),
        'nodes["Principled BSDF"] is locale-dependent — look up by n.type == "BSDF_PRINCIPLED"',
        "warn",
    ),
    (
        re.compile(r"bpy\.ops\.export_scene\.obj\s*\("),
        "bpy.ops.export_scene.obj was removed in Blender 4.0 — use bpy.ops.wm.obj_export",
        "warn",
    ),
    (
        re.compile(r"bpy\.ops\.import_scene\.obj\s*\("),
        "bpy.ops.import_scene.obj was removed in Blender 4.0 — use bpy.ops.wm.obj_import",
        "warn",
    ),
    (
        re.compile(r"""light_add\s*\([^)]*type\s*=\s*["']HEMI["']"""),
        "'HEMI' light type was removed — use 'AREA' instead",
        "warn",
    ),
    (
        re.compile(r"\bmathutils\.(radians|degrees)\b"),
        "mathutils has no radians/degrees — use math.radians / math.degrees",
        "warn",
    ),
]


def lint_python(code: str) -> list[LintIssue]:
    """Return a list of issues. Empty list = no problems detected.

    Checks:
    1. Syntax (ast.parse)
    2. Missing `import bpy` when bpy is referenced
    3. Known-bad API patterns (removed Blender 4+ APIs)
    """
    issues: list[LintIssue] = []

    # --- syntax check ---
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
        return issues  # Can't do semantic checks if syntax is broken

    # --- missing import bpy ---
    if "bpy." in code or "bpy " in code:
        if not re.search(r"^\s*import\s+bpy\b", code, re.MULTILINE):
            issues.append(
                LintIssue(line=1, col=0, message="Missing `import bpy`", level="warn")
            )

    # --- semantic patterns ---
    for pattern, message, level in _SEMANTIC_PATTERNS:
        for match in pattern.finditer(code):
            # Calculate line number from match position
            line_no = code[:match.start()].count("\n") + 1
            issues.append(LintIssue(line=line_no, col=0, message=message, level=level))

    return issues
