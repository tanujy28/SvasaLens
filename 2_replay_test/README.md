# 2. Replay test (`2_replay_test/`)

The 14 held-out nights are streamed into the Nano, and its output is compared with the same detector running on the laptop and with the technologist's labels.

- `ucddb_io.py`: reads the UCDDB recordings and event labels. Copy it next to the replay scripts.
- `v1/`: first version of the rule. `effort_replay/` goes on the Nano; `replay_ucddb.py` runs on the laptop; `chipref.py` is the laptop copy of the chip's detector.
- `v2/`: second version (skips the first 3 s of each event and removes slow drift). Same layout.
- `fit_rules.py`: builds the rules from the 11 training nights and scores them on the 14 held-out nights: the effort-only rule (threshold with the best balanced accuracy on the training nights), and the v1 and v2 rules (logistic regression with balanced class weights). Needs scikit-learn. It reproduces the three numbers in each rule.

See the [main README](../README.md) for setup.
