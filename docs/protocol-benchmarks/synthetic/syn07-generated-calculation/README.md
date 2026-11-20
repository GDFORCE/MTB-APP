# SYN07: a rule no handler covers

A synthetic protocol (`protocol.txt`) whose PK sample rule, "7 days (±1 day) after each injection that is given; no sample after a withheld injection", is not in the named-calculation registry. The analysis writes the calculation itself (`pattern_rules[].program`, see `backend/app/kit/calc.py`).

Try it in the app without a model provider: start the backend with `backend/start-backend-offline.cmd`, create a trial with protocol ID `SYN-GEN-PK`, and upload `protocol.txt` in Add Trial or the trial's visit schedule. The workflow review shows the generated calculation, its inputs, logic and test results; approval needs the study team's confirmation.

Tests: `backend/tests/test_kit_generated_calc.py`, `frontend/tests/kit-generated.test.ts`.
