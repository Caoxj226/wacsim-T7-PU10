# WaCSim CTown CRAVE adapter

This directory contains only CRAVE's adapter for the reviewed CTown T7–PU10
scenario.  It does not contain a second copy of WaCSim or a second PI/oracle
implementation.

The final repository layout supplies the fixed platform through the
`third_party/WaCSim` Git submodule.  The adapter then reads the reviewed
scenario and `calibration.lock.yaml` from that submodule and maps CRAVE task
fields as follows:

| CRAVE field | WaCSim replay field |
| --- | --- |
| `point` | `InjectionSpec.point` |
| `amp` (signed) | `InjectionSpec.slope` per 300-second step |
| `duration` | `InjectionSpec.duration` |
| `t_start` | `InjectionSpec.start_step` |
| `role` | `driver` or `masker` |

Only the two reviewed injection points are accepted: `PU10_SPEED_CMD`
(driver) and `T7_LEVEL_SP` (masker).  The adapter accepts no attack, a driver
only, or driver plus masker.  It rejects every other role, range, start time,
and slope instead of silently altering the reviewed scenario.

The executor requires the Linux integration image.  Importing this package on
Windows is safe for static validation, but executing a replay on Windows is
 intentionally rejected because WaCSim's EPANET binding is Linux-native.
