"""CRAVE-visible CTown trace contract.

This module intentionally contains no hydraulic calculation.  The fixed
WaCSim replay engine is the sole owner of EPANET stepping, the PI calculation,
the injection ramp, and the alarm/hazard oracle.
"""

from __future__ import annotations

from .controller import ALARM_RULES


HAZARD_RULES = [
    {
        "id": "H-T7-LOW",
        "kind": "threshold",
        "var": "T7_LEVEL",
        "comparison": "T7_LEVEL <= 0.5",
        "threshold_m": 0.5,
    },
]


REQUIRED_TRACE_KEYS = (
    "t_step",
    "time_seconds",
    "T7_LEVEL",
    "T7_LEVEL_SP",
    "PU10_SPEED_CMD",
    "PU10_FLOW",
    "J317_PRESSURE",
    "alarm_rule_ids",
    "hazard_rule_ids",
)


def trace_contract() -> dict:
    """Return the immutable semantic boundary CRAVE consumes from WaCSim."""
    return {
        "process_variable": "T7_LEVEL",
        "setpoint_variable": "T7_LEVEL_SP",
        "driver_variable": "PU10_SPEED_CMD",
        "observations": ("PU10_FLOW", "J317_PRESSURE"),
        "alarm_rules": ALARM_RULES,
        "hazard_rules": HAZARD_RULES,
        "required_trace_keys": REQUIRED_TRACE_KEYS,
        "score_start_step": 24,
        "step_seconds": 300,
    }


def hydraulic_dependency(PU10_SPEED_CMD: float) -> float:
    """Declare the reviewed PU10-command-to-T7 physical dependency.

    The actual hydraulic transition remains in WaCSim EPANET.  This statement
    is an explicit semantic edge for CRAVE Stage 1 source extraction only.
    """
    T7_LEVEL = PU10_SPEED_CMD
    return T7_LEVEL
