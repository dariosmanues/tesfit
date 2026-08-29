from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "services/geometry-api/app/main.py",
    ROOT / "services/geometry-api/app/recovery_solver.py",
    ROOT / "services/geometry-api/web/app.js",
    ROOT / "services/geometry-api/web/index.html",
    ROOT / "services/geometry-api/web/recovery-monitor.js",
]

for path in FILES:
    text = path.read_text(encoding="utf-8")
    if "2.5.13" not in text:
        raise RuntimeError(f"expected 2.5.13 marker not found in {path}")
    path.write_text(text.replace("2.5.13", "2.5.14"), encoding="utf-8")

# Version-specific regression assertions must follow the current product version.
for path in (ROOT / "services/geometry-api").glob("test_*.py"):
    text = path.read_text(encoding="utf-8")
    if "2.5.13" in text:
        path.write_text(text.replace("2.5.13", "2.5.14"), encoding="utf-8")

status = ROOT / "MILESTONE2_5_14_STATUS.md"
status.write_text(
    """# Development OS — Milestone 2.5.14\n\n"
    "## Structural Corridor Recovery\n\n"
    "M2.5.14 is the version identity for the structural corridor recovery solver introduced after M2.5.13.\n\n"
    "Hard acceptance remains **gross lot efficiency >= 70% of total land area** plus final geometry validation PASS.\n"
    "STANDARD lot dimensions remain sourced only from Geometry Settings; Adaptive lots remain residual-only.\n\n"
    "Structural mutation can vary corridor count and spacing, short-branch count and length, single/dual spine, "
    "double-loaded coverage, road termination, perimeter-assisted access, and block-depth combinations.\n\n"
    "Frontend title/version badges, cache-busting query strings, Recovery Solver labels, backend `/health` version, "
    "and version-specific regression assertions are synchronized to 2.5.14.\n"
    """,
    encoding="utf-8",
)

print("M2.5.14 version sync patch applied")
