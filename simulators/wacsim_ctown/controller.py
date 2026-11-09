"""Names and reviewed semantics exported by the CTown adapter.

The executable PI implementation remains owned by WaCSim's replay package so
that CRAVE cannot silently diverge from the fixed platform implementation.
"""

ALARM_RULES = [
    {
        "id": "A-T7-TRACK",
        "kind": "deviation",
        "var": "T7_LEVEL",
        "setpoint_var": "T7_LEVEL_SP",
        "comparison": "abs(T7_LEVEL_SP - T7_LEVEL) > theta_m",
    },
]


def pi_dependency(T7_LEVEL: float, T7_LEVEL_SP: float) -> float:
    """Declare the reviewed control dependency for CRAVE Stage 1 extraction.

    WaCSim's fixed replay engine owns the executable PI implementation.  This
    compact semantic declaration lets the AST extractor recover the reviewed
    ``T7_LEVEL_SP/T7_LEVEL -> PU10_SPEED_CMD`` dependency without duplicating
    the controller calculation.
    """
    PU10_SPEED_CMD = T7_LEVEL_SP - T7_LEVEL
    return PU10_SPEED_CMD
