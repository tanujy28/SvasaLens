# 4. Live device test (`4_live_device_test/`)

The complete device makes every decision on the Nano. `live_log.py` only records what the Nano sends.

- `v1/`: live run 1 (`svasalens_live` sketch and `live_log.py`).
- `v2/`: live run 2 (`svasalens_live_v2` sketch and `live_log.py`).
- `dashboard/`: source code of the SvasaLens dashboard app (`SvasaLens.exe`), which shows the chest and belly signals and each of the chip's decisions, live or from a saved session. It only displays what the Nano sends; the decisions are made on the Nano. On Windows, run `build_exe.bat` to build `SvasaLens.exe` (uses PyInstaller and `SvasaLens.spec`; keep the `fonts` folder next to the script).

See the [main README](../README.md) for setup.
