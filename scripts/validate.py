#!/usr/bin/env python3
"""Dependency-free Phase 1 structural validation, not live security certification."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    for path in list((ROOT / "scripts").glob("*.py")) + list((ROOT / "tests").glob("*.py")):
        ast.parse(path.read_text(), filename=str(path))
    for path in (ROOT / "infra").rglob("*.json"):
        json.loads(path.read_text())
    versions = json.loads((ROOT / "infra/versions.json").read_text())
    if versions["deployment_enabled"] is not False:
        raise ValueError("Phase 1 must remain disabled until live deployment gates are implemented")
    required = ["context/PHASE-1-STATUS.md", "context/ARCHITECTURE.md", "context/NEXT-PHASE.md",
                "docs/INSTALL.md", "docs/DEVELOPMENT.md", "docs/UPDATE-ROLLBACK.md",
                "docs/adr/0001-staged-foundation.md", "security/THREAT-MODEL.md"]
    for name in required:
        if not (ROOT / name).read_text().strip():
            raise ValueError(f"missing handoff: {name}")
    print("PASS: Python syntax, JSON templates, disabled deployment, handoffs")


if __name__ == "__main__":
    main()
