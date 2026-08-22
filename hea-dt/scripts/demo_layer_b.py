"""
scripts/demo_layer_b.py

Demonstrates Layer B (M4-M7) responding differently to different injected
faults. For each of the 6 scenario types, generates one representative
flight by reusing data/generate_fault_data.py's simulate_flight() — the
SAME generator that built the training data, not mock random data — then
runs it through the REAL M1(replay-style)->M2->M3->M4->M5->M6->M7
pipeline via orchestrator.run_cycle(), printing the flow at the start,
middle, and end of each flight so the fault's progression (if any) is
visible, not just one snapshot.

Run (after M4/M5/M7 have been trained/calibrated — see README's Layer B section):
    python scripts/demo_layer_b.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.disable(logging.INFO)  # keep the printed flow readable

from config.loader import load_config  # noqa: E402
from contracts.schemas import Ack, RawState  # noqa: E402
from data.generate_fault_data import SCENARIOS, simulate_flight  # noqa: E402
from modules.m1_telemetry.base import TelemetrySource  # noqa: E402
from modules.m2_validation import SensorValidator  # noqa: E402
from modules.m3_twin_state import TwinStateEstimator  # noqa: E402
from modules.m5_structural_shm import StructuralHealthPredictor  # noqa: E402
from orchestrator.main import run_cycle  # noqa: E402


class _ListTelemetrySource(TelemetrySource):
    """Serves a pre-generated list of RawState in order, with no wall-clock
    pacing — a demo-only adapter so this script can reuse run_cycle()
    directly against data/generate_fault_data.py's synthetic flights,
    exactly the same pipeline code path the CLI/API use."""

    def __init__(self, rows: list[RawState]):
        super().__init__(ring_buffer_size=max(len(rows), 1))
        self._rows = rows
        self._index = 0

    def get_raw_state(self) -> RawState:
        row = self._rows[self._index]
        self._index = min(self._index + 1, len(self._rows) - 1)
        return self._record(row)

    def send_test_command(self, speed_ms: float) -> Ack:
        raise NotImplementedError("demo source: no command channel")


def main() -> None:
    config = load_config("config/config.yaml")
    duration_s, rate_hz = 15.0, 8.0

    for scenario in SCENARIOS:
        severity = "severe" if scenario != "healthy" else "mild"  # "mild" is unused for healthy, kept for API shape
        rows, _ = simulate_flight(scenario, severity, seed=abs(hash(scenario)) % 10_000, duration_s=duration_s, rate_hz=rate_hz)

        source = _ListTelemetrySource(rows)
        validator = SensorValidator(config)
        estimator = TwinStateEstimator(config)
        structural_predictor = StructuralHealthPredictor(config)

        label = "severe" if scenario != "healthy" else "n/a"
        print(f"\n\n########## SCENARIO: {scenario}  (severity={label}) ##########")

        n = len(rows)
        checkpoints = sorted({0, n // 2, n - 1})
        for i in range(n):
            run_cycle(config, source, validator, estimator, structural_predictor, cycle=i + 1, print_flow=(i in checkpoints))


if __name__ == "__main__":
    main()
