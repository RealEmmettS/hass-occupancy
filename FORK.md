# Independently maintained project

This repository is Emmett's own Occupancy Forecast project. It began as a fork
of `MartvanMale/hass-occupancy` at commit
`a6c84026a1f9f94985ea553262aed7cd4f9982c5` and is now maintained independently.

**Changes in this project will not be sent back upstream.** Do not open upstream
pull requests, automatically sync or merge the original repository, or replace
this installation with the original add-on. Future development and releases
belong to this repository. A future change to that policy requires an explicit
instruction from the owner.

The original MIT license and author attribution remain intact. Independence
does not erase the source project's contribution or license obligations.

## Current direction

- Run reliably on a 64-bit Raspberry Pi 4, including native CPU validation.
- Collect local, timestamped observations for a controlled occupancy trial.
- Preserve issued forecast snapshots and arrival estimates alongside later
  presence observations, instead of relying only on training scores.
- Keep forecast and journal code observational. Neither operates HVAC or other
  devices. Any future device-control work requires a separate owner request.
- Customize from measured results. Do not weaken publication gates or fabricate
  forecasts just to populate a new installation's charts.
- Keep resident identities, tracker mappings, household coordinates, actual
  schedules, logs, and credentials in private Home Assistant configuration and
  storage. Never commit them to this public repository.

## Initial independent release

Version 0.1.1 replaces the incompatible PyArrow 21.0.0 ARM wheel with 22.0.0.
The original wheel can execute ARMv8.1 instructions on the Pi 4's ARMv8.0 CPU,
causing an illegal-instruction crash. See Apache Arrow issue
<https://github.com/apache/arrow/issues/47229>.

Every image build performs a native NumPy/Pandas/PyArrow import, Parquet
round-trip, metadata read, and scikit-learn fit/predict check on the target CPU.
Shell files are forced to LF to preserve executable shebangs in Windows clones.
The separate Occupancy Test Journal add-on keeps sampled forecast revisions,
presence, and optional cooling observations, with 90-day retention by default.
