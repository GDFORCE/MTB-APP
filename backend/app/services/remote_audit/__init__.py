"""Virtual Visit & Remote Audit module — pure-logic services.

Everything under this package is intentionally free of database/network I/O
so it can be unit-tested the same way app/domain/schedule/evaluator.py is:
callers (route handlers, workers) fetch whatever data a function needs and
pass it in as plain arguments.

See docs/remote-audit/ for the production-grade implementation plan (v1.3)
this package implements, phase by phase.
"""
