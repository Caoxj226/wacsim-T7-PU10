"""CRAVE adapter for the reviewed WaCSim CTown T7 scenario.

The simulator itself is deliberately not copied here.  A pinned WaCSim
checkout is supplied at ``third_party/WaCSim`` when the integration image is
built.  This package only translates CRAVE tasks into that simulator's replay
contract.
"""

SYSTEM_ID = "wacsim_ctown"
