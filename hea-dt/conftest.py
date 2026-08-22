"""
Ensures the project root is importable as top-level packages (contracts,
config, modules, orchestrator) regardless of how pytest is invoked —
`pytest`, `python -m pytest`, or from a different working directory.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
