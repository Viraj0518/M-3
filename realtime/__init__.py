"""PALIMPSEST realtime plane — the LaserData->FalkorDB spine.

The EDGE TAP ([1]), the LASERDATA PLANE ([2]) and the SENSING GATE ([3]) of
plan/synthesis.json, plus the graph writer and the replay path that make the
REWIND A/B beat (GOAL victory condition 1) real.

TRANSPORT: the `laser-sdk` python package (v0.0.1), Log primitive ONLY. See
`realtime/laser_io.py` for the seam and the capabilities() gate. If laser-sdk is
absent this package still imports; only the live I/O raises an honest error.

SCOPE DISCIPLINE (plan/synthesis.json sponsor_integration.laserdata): we use
publish / consume / replay-from-offset and NOTHING else. `laser.graph()` and
`laser.memory()` are never called — FalkorDB owns EVER, LaserData owns NOW.
"""
