"""
modules/m6_energy/optimizer.py

M6 — Energy Modeling & Optimization (real implementation).

POWER MODEL — stated as a simplification, for the paper's limitations
section, not hidden: we have no real aerodynamic test data for this
airframe, so P(speed, altitude, wind, payload, battery_state) is built
from textbook actuator-disk / drag relationships with reasonable-but-not-
measured constants (config.m6), not a fitted model. It captures the right
SHAPE (power has an interior minimum at a nonzero cruise speed — the
classic reason cruising beats hovering) and the right DIRECTIONS (heavier
aircraft and degraded motors need more power; higher altitude's thinner
air needs less; battery wear increases electrical losses), but the
absolute numbers should not be taken as measured performance.

  P_hover(mass, alt, efficiency) = (mass * g)^1.5 / sqrt(2 * rho(alt) * A_disk) / efficiency

    Standard actuator-disk-theory hover power (thrust = weight at hover).
    `efficiency` is the mean of TwinParams.motor_thrust_efficiency — a
    degraded motor needs MORE power for the same thrust, so this
    directly reflects the "current, aged parameters from M3, not
    datasheet defaults" differentiator the project is built around.

  rho(alt) = rho_sea_level * exp(-alt / 8500)

    Standard exponential-atmosphere approximation for air density.

  P_induced(v_air) = P_hover / sqrt(1 + (v_air / v_ref)^2)

    Simplified translational-lift benefit: induced power drops as forward
    speed increases (real momentum theory gives a similar-shaped but more
    complex correction — this is the simplified version). KNOWN
    LIMITATION: this asymptotes toward 0 rather than a small nonzero
    floor, which is why P_parasite (below) is what actually produces an
    interior speed optimum rather than "faster is always better."

  P_parasite(v_air) = 0.5 * rho(alt) * drag_area_m2 * v_air^3

    Standard aerodynamic drag power (drag force ~ v^2, power = force * v).

  v_air = v_ground + |wind|

    Wind is treated as a worst-case headwind regardless of actual
    direction — conservative (never under-predicts required power) but
    not tailwind-optimistic. A full implementation would need the
    aircraft's heading relative to the wind vector, not just a scalar
    speed to optimize.

  P_elec = P_mech / battery_efficiency(R_batt)
  battery_efficiency(R) = clip(1 - R / R_ref, 0.5, 1.0)

    Battery internal-resistance losses derate mechanical-to-electrical
    efficiency. NOTE: because this is a uniform multiplicative derating
    (not speed-dependent), R_batt changes predicted energy magnitude but
    does NOT shift where the power-vs-speed minimum falls — that's
    physically correct, not a bug (see tests).

  predicted_energy_wh = P_elec(v_opt, alt_opt) * config.m6.assumed_remaining_mission_s / 3600

    STATED SIMPLIFICATION: there's no real mission-plan/remaining-distance
    contract wired in yet (that's M8/M9, Phase 3-4), so remaining flight
    time is a configurable placeholder constant, not derived from an
    actual route. Once a mission plan exists, replace this with
    remaining_distance / v_opt.

ALTITUDE'S EFFECT — worth stating precisely, since it's easy to get
backwards: thinner air at altitude cuts PARASITIC (drag) power, since drag
scales with air density, but it INCREASES hover/induced power, since
actuator-disk theory gives P_hover ~ 1/sqrt(rho) — accelerating enough air
mass for the same thrust takes more power when that air is thin. This is
the real reason helicopters have service ceilings: hovering gets harder,
not easier, at altitude. In this model the hover-power effect dominates
across the searched altitude range, so alt_opt consistently comes out
near the FLOOR (config.thresholds.min_altitude_m) rather than the
ceiling — flying low and dense-air is cheaper here. A fuller model would
also weigh altitude against things this one doesn't capture (wind shear,
airspace/regulatory constraints), which could push the optimum back up;
out of scope here.

SEARCH: grid over altitude (a WEAK effect over typical small-drone
altitude ranges relative to speed — see above) x
scipy.optimize.minimize_scalar (bounded) over speed for each altitude
candidate — combines both techniques the task allows ("grid search or
scipy.optimize").
"""

from __future__ import annotations

import logging
import time

import numpy as np
from scipy.optimize import minimize_scalar

from contracts.schemas import EnergyPlan, StateVector, TwinParams

logger = logging.getLogger("hea_dt.m6_energy")

G_M_S2 = 9.80665
ALTITUDE_SCALE_HEIGHT_M = 8500.0
ALT_GRID_STEPS = 6


def _air_density(alt_m: float, config) -> float:
    return config.m6.air_density_sea_level_kg_m3 * float(np.exp(-alt_m / ALTITUDE_SCALE_HEIGHT_M))


def _hover_power_w(mass_kg: float, alt_m: float, efficiency: float, config) -> float:
    efficiency = max(0.3, min(1.0, efficiency))
    rho = _air_density(alt_m, config)
    thrust_n = mass_kg * G_M_S2
    return (thrust_n**1.5) / np.sqrt(2.0 * rho * config.m6.disk_area_m2) / efficiency


def _induced_power_w(v_air_ms: float, hover_power_w: float, config) -> float:
    v_ref = config.m6.induced_power_ref_speed_ms
    return hover_power_w / np.sqrt(1.0 + (v_air_ms / v_ref) ** 2)


def _parasitic_power_w(v_air_ms: float, alt_m: float, config) -> float:
    rho = _air_density(alt_m, config)
    return 0.5 * rho * config.m6.drag_area_m2 * v_air_ms**3


def _battery_efficiency(r_batt_ohm: float, config) -> float:
    return max(0.5, min(1.0, 1.0 - r_batt_ohm / config.m6.battery_resistance_ref_ohm))


def electrical_power_w(v_ground_ms: float, alt_m: float, mass_kg: float, efficiency: float,
                        r_batt_ohm: float, wind_speed_ms: float, config) -> float:
    """Total electrical power (W) to sustain v_ground_ms at alt_m — the
    function the optimizer searches over. Exposed (not private) so tests
    can probe it directly."""
    v_air = max(0.0, v_ground_ms) + max(0.0, wind_speed_ms)
    hover_w = _hover_power_w(mass_kg, alt_m, efficiency, config)
    mech_w = _induced_power_w(v_air, hover_w, config) + _parasitic_power_w(v_air, alt_m, config)
    return mech_w / _battery_efficiency(r_batt_ohm, config)


def _wind_speed_ms(wind_ned) -> float:
    return float(np.hypot(wind_ned[0], wind_ned[1]))


def optimize(state: StateVector, twin: TwinParams, config) -> EnergyPlan:
    logger.info("M6 called")
    raw = state.raw

    mass_kg = twin.effective_mass_kg
    efficiency = (
        sum(twin.motor_thrust_efficiency) / len(twin.motor_thrust_efficiency)
        if twin.motor_thrust_efficiency
        else 1.0
    )
    r_batt_ohm = twin.battery_internal_resistance_ohm
    wind_speed = _wind_speed_ms(raw.wind_ned)

    alt_min = config.thresholds.min_altitude_m
    alt_max = max(alt_min + 1.0, config.m6.alt_search_max_m)
    alt_candidates = np.linspace(alt_min, alt_max, ALT_GRID_STEPS)

    best_power = float("inf")
    best_v, best_alt = 0.0, alt_min

    for alt_m in alt_candidates:
        result = minimize_scalar(
            lambda v: electrical_power_w(v, float(alt_m), mass_kg, efficiency, r_batt_ohm, wind_speed, config),
            bounds=(0.0, config.m6.v_search_max_ms),
            method="bounded",
        )
        if result.fun < best_power:
            best_power = float(result.fun)
            best_v = float(result.x)
            best_alt = float(alt_m)

    predicted_energy_wh = best_power * config.m6.assumed_remaining_mission_s / 3600.0

    remaining_pct = raw.battery_remaining_pct if raw.battery_remaining_pct is not None else 50.0
    usable_pct = max(0.0, remaining_pct - config.thresholds.energy_reserve_pct)
    available_energy_wh = config.m6.battery_capacity_wh * usable_pct / 100.0
    energy_margin_wh = available_energy_wh - predicted_energy_wh

    return EnergyPlan(
        timestamp=time.time(),
        v_opt_ms=best_v,
        alt_opt_m=best_alt,
        predicted_energy_wh=predicted_energy_wh,
        energy_margin_wh=energy_margin_wh,
        optimized_waypoints=[],  # no mission-plan contract wired in yet (M8/M9, Phase 3-4)
    )
