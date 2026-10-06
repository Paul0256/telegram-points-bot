"""The core must stay platform-independent: no messaging libraries, no adapter imports."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "pointsbot" / "core"
FORBIDDEN = ("aiogram", "telegram", "discord", "slack_sdk", "slack_bolt", "twilio", "pointsbot.adapters")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve against the file's package
                package = ".".join(("pointsbot", *path.relative_to(CORE.parent).parent.parts))
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                names.add(f"{base}.{node.module}" if node.module else base)
            else:
                names.add(node.module or "")
    return names


def test_core_sources_import_no_platform_code():
    offenders = {
        f"{path.relative_to(CORE.parent)}: {name}"
        for path in CORE.rglob("*.py")
        for name in imported_modules(path)
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN)
    }
    assert not offenders, offenders


def test_importing_the_core_does_not_load_aiogram():
    code = "import sys, pointsbot.core, pointsbot.core.storage.gsheets; print('aiogram' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=CORE.parents[1])
    assert out.stdout.strip() == "False"
