# SvasaLens code

Code for the paper on telling obstructive and central sleep apnea apart using breathing effort, with an Arduino Nano and two motion sensors.

Python scripts need Python 3 with numpy, scipy, matplotlib and pyserial (and scikit-learn for `fit_rules.py`). Arduino sketches (.ino) are opened in the Arduino IDE. Keep each .ino file in its own folder, together with its .h files. The database scripts expect the UCDDB files from PhysioNet (https://physionet.org/content/ucddb/1.0.0/) in the folder named at the top of each script.

## 1_database_analysis

- `better_detector.py`: measures the six features for each of the 559 apneas and tests them one by one and combined (logistic regression, leave-one-patient-out). It writes `better_features.csv`. Produces Table 1.
- `hypothesis_test.py`: reads `better_features.csv` and tests the hypothesis: median effort during obstructive apneas, the share at or above normal, and the one-sample Wilcoxon signed-rank test of each patient's median against 1.0.

## 2_replay_test

The 14 held-out nights are streamed into the Nano, and its output is compared with the same detector running on the laptop and with the technologist's labels.

- `ucddb_io.py`: reads the UCDDB recordings and event labels. Copy it next to the replay scripts.
- `v1/`: first version of the rule. `effort_replay/` goes on the Nano; `replay_ucddb.py` runs on the laptop; `chipref.py` is the laptop copy of the chip's detector.
- `v2/`: second version (skips the first 3 s of each event and removes slow drift). Same layout.
- `fit_rules.py`: builds the rules from the 11 training nights and scores them on the 14 held-out nights: the effort-only rule (threshold with the best balanced accuracy on the training nights), and the v1 and v2 rules (logistic regression with balanced class weights). Needs scikit-learn. It reproduces the three numbers in each rule.

## 3_sensor_tests

- `noise_test/`: sensor left still on a table (`still_test`), recorded with `capture.py`, analysed with `noise_floor.py`.
- `initial_chest_test/`: first test with one sensor at the centre of the sternum (`chest_test`, `capture_chest.py`, `chest_analysis.py`).
- `placement_test/`: both sensors on the volunteer, breathing quietly (`two_bus_test_v5`, `capture.py`, `placement_compare.py`).
- `hold_try_test/`: three breath-holds and three tries against a closed airway (`hold_try`, `capture_holdtry.py`, `holdtry_analysis.py`).

## 4_live_device_test

The complete device makes every decision on the Nano. `live_log.py` only records what the Nano sends.

- `v1/`: live run 1 (`svasalens_live` sketch and `live_log.py`).
- `v2/`: live run 2 (`svasalens_live_v2` sketch and `live_log.py`).
- `dashboard/`: source code of the SvasaLens dashboard app (`SvasaLens.exe`), which shows the chest and belly signals and each of the chip's decisions, live or from a saved session. It only displays what the Nano sends; the decisions are made on the Nano. On Windows, run `build_exe.bat` to build `SvasaLens.exe` (uses PyInstaller and `SvasaLens.spec`; keep the `fonts` folder next to the script).
