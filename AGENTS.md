# Project ownership and working rules

Read `FORK.md` before changes. This is an independently maintained personal
project. Never submit upstream pull requests, configure upstream synchronization,
or automatically merge the original repository. Use this repository for future
work. Preserve its MIT license and original attribution.

Do not commit household identities, tracker mappings, precise location, real
temperature schedules, model/history databases, captured diagnostics, or secrets.
Use generic configurable code and synthetic test data. Runtime setup stays in HA.

Forecasting and the journal are observational only. No HVAC/light service calls
or other device-control automation belongs in either add-on without a separate
explicit owner request.

Keep shared forecast changes in `occupancy-forecast-edge/` and the corresponding
stable copy consistent. The existing promotion scripts only promote between
directories in this repository; they do not authorize synchronization upstream.
Do not blindly run historical deployment scripts containing another environment's
assumptions. Pin dependencies and run relevant tests, including native Pi CPU
smoke validation when a numerical dependency changes. Use LF for shell scripts.
