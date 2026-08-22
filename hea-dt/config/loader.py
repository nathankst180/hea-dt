"""
config/loader.py

Loads config/config.yaml into a typed Config object so every module reads
settings the same way (config.thresholds.min_altitude_m, not a raw dict
lookup scattered around the codebase). Nothing outside this file should
hardcode a threshold, path, or connection string — add it to config.yaml
and to the matching dataclass below instead.

Any section or key missing from the YAML file quietly falls back to the
default value defined here, so a partial config file is still valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SimConfig:
    """Simulator/autopilot connection settings (used by M1 and M11)."""

    backend: str = "ardupilot"
    """Which autopilot firmware the SITL instance is running: "ardupilot" or "px4"."""

    connection: str = "udp:127.0.0.1:14550"
    """MAVLink connection string, in pymavlink/MAVSDK format."""

    mission_file: str = "data/missions/default.plan"
    """Path to the mission plan file loaded at the start of a run."""


@dataclass
class LoopConfig:
    """The two loop rates described in the architecture doc."""

    fast_hz: float = 10.0
    """Rate (Hz) of the fast safety loop: M1/M2 ingest + hard-abort checks."""

    slow_hz: float = 0.5
    """Rate (Hz) of the slow prediction/decision loop: M3 through M9."""


@dataclass
class ModelsConfig:
    """Which model implementation each module should use. Lets you swap a
    module's algorithm without touching its code."""

    m2_validation: str = "isolation_forest"
    m3_twin_state: str = "ekf"
    m4_electrical: str = "lightgbm"
    m5_structural: str = "random_forest"
    m6_energy: str = "analytic"
    m7_uncertainty: str = "conformal"
    use_neural: bool = False
    """Global override: if False, no module may fall back to a neural-net
    variant even if it supports one — keeps the whole pipeline CPU-light."""


@dataclass
class ThresholdsConfig:
    """Safety-relevant numeric thresholds used by M9 (decision) and M10
    (safety arbitration)."""

    p_fail_abort: float = 0.6
    """M9: if predicted P(powertrain failure within horizon) exceeds this, decide ABORT."""

    structural_abort: float = 0.7
    """M9: if structural_anomaly_score exceeds this, decide ABORT."""

    energy_reserve_pct: float = 20.0
    """M6/M8: battery percentage that must always remain unspent, never planned into."""

    min_decision_confidence: float = 0.5
    """M10: below this fused confidence, escalate the decision to a human instead of auto-executing."""

    min_altitude_m: float = 10.0
    """M10: hard geofence floor — no autonomous command may take the aircraft below this."""

    gps_imu_disagreement_ms: float = 5.0
    """M2: if GPS-derived ground speed disagrees with the IMU-integrated
    speed estimate by more than this (m/s), flag it as a GPS sensor fault."""

    p_fail_horizon_s: float = 300.0
    """M4: the horizon N for "P(powertrain fault within N)" — how far ahead
    the failure-probability estimate looks. Default 300s = 5 minutes."""

    structural_imbalance_threshold: float = 0.5
    """M5: structural_anomaly_score (a 0-1 probability) above this sets imbalance_flag."""


@dataclass
class M6Config:
    """Physics constants for M6's power model — see modules/m6_energy/
    optimizer.py's docstring for the full derivation and the honest
    "simplified, no real aerodynamic test data" caveat. Defaults are
    order-of-magnitude-reasonable for a small (~1.5kg) quadrotor, not
    measured — swap in real thrust-stand numbers once available."""

    air_density_sea_level_kg_m3: float = 1.225
    disk_area_m2: float = 0.15
    """Combined rotor disk area (all rotors), m^2."""
    drag_area_m2: float = 0.02
    """Effective frontal drag area (Cd * A), m^2."""
    induced_power_ref_speed_ms: float = 8.0
    """v_ref in the induced-power falloff term — the forward speed at which
    induced power has meaningfully dropped due to translational lift."""
    battery_capacity_wh: float = 50.0
    """Nominal full-charge pack energy, watt-hours."""
    battery_resistance_ref_ohm: float = 0.2
    """Internal resistance at which the battery-loss efficiency derating bottoms out."""
    assumed_remaining_mission_s: float = 600.0
    """Placeholder remaining-mission duration used for the energy budget,
    until a real mission plan is wired in (M8/M9, Phase 3-4) — see the
    module docstring."""
    v_search_max_ms: float = 20.0
    """Upper bound of the speed search range for the optimizer."""
    alt_search_max_m: float = 150.0
    """Upper bound of the altitude search range for the optimizer."""


@dataclass
class M7Config:
    """Conformal-prediction and confidence-fusion parameters for M7. See
    modules/m7_uncertainty/quantifier.py's docstring for the fusion formula."""

    confidence_level: float = 0.9
    """MAPIE's target coverage probability for prediction intervals/sets."""

    deviation_scale: float = 3.0
    """Controls how fast decision_confidence decays as twin.deviation
    rises: deviation_factor = exp(-deviation / deviation_scale)."""


@dataclass
class AutonomyConfig:
    """How much autonomy the system is allowed to exercise."""

    mode: str = "human_in_the_loop"
    """"human_in_the_loop" (irreversible actions need explicit confirmation),
    "human_on_the_loop" (human can observe/override but isn't blocking), or
    "full_autonomous" (no human gate — use only once trust is established)."""


@dataclass
class PathsConfig:
    """Filesystem locations, so nothing is hardcoded in module code."""

    models_dir: str = "training/models"
    """Where trained model artifacts (M4/M5/M6 models, MAPIE calibrators) are saved/loaded."""

    data_dir: str = "data"
    """Where generated datasets and logs live."""

    log_db: str = "data/decision_log.sqlite"
    """SQLite file M12 appends the hash-chained decision log to."""


@dataclass
class MockConfig:
    """Fault injection for MockTelemetrySource (M1's --mock data source).
    Everything defaults to "off" so a plain mock run is fully nominal;
    tune these to demonstrate/test M2 and M3 noticing a problem."""

    seed: int = 42
    """Random seed, so a mock run is reproducible."""

    battery_decline_v_per_s: float = 0.0
    """Extra voltage sag per second of wall-clock run time, on top of the
    normal discharge curve — simulates a battery degrading faster than its
    internal-resistance model predicts (drives M3's deviation metric up)."""

    vibration_noise_std: float = 0.0
    """Extra Gaussian noise (m/s^2 std) added to every accel axis each
    cycle — simulates a developing vibration/structural problem."""

    glitch_prob: float = 0.0
    """Per-cycle probability (0-1) of a single-cycle sensor glitch, per
    glitch_field — tests M2's sensor-vs-component-fault disambiguation."""

    glitch_field: str = "gps"
    """Which channel glitches when glitch_prob fires: "gps", "battery_voltage", or "imu"."""


@dataclass
class Config:
    """Top-level config object — one field per section of config.yaml."""

    sim: SimConfig = field(default_factory=SimConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    mock: MockConfig = field(default_factory=MockConfig)
    m6: M6Config = field(default_factory=M6Config)
    m7: M7Config = field(default_factory=M7Config)


def load_config(path: str | Path = "config/config.yaml") -> Config:
    """Read config.yaml (if it exists) and return a fully-populated, typed
    Config object. Missing keys/sections fall back to the dataclass
    defaults above, so this never raises just because the YAML is
    incomplete — only if a value in the YAML has the wrong shape.
    """
    path = Path(path)
    raw: dict = {}
    if path.exists():
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}

    return Config(
        sim=SimConfig(**raw.get("sim", {})),
        loop=LoopConfig(**raw.get("loop", {})),
        models=ModelsConfig(**raw.get("models", {})),
        thresholds=ThresholdsConfig(**raw.get("thresholds", {})),
        autonomy=AutonomyConfig(**raw.get("autonomy", {})),
        paths=PathsConfig(**raw.get("paths", {})),
        mock=MockConfig(**raw.get("mock", {})),
        m6=M6Config(**raw.get("m6", {})),
        m7=M7Config(**raw.get("m7", {})),
    )
