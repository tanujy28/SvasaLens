# 5. Neural networks (SvasaLens V2)

Version 2 replaces the V1 two-feature rule with two small neural networks, adds a nasal thermistor for airflow, and moves from the Arduino Nano to a XIAO ESP32-S3.

- **Detection network:** finds when an apnea happens, using the thermistor plus chest and belly motion.
- **Typing network:** decides whether each apnea is obstructive or central, using breathing effort only.

## Results (hospital recordings)

| Test | Result |
|---|---|
| Typing, all 286 PSG-Audio patients (hospital never used in training) | 93.4% balanced accuracy, vs 86.6% for the V1 rule |
| Detection, 58 unseen PSG-Audio patients | 92.9% of apneas found, 5.2 false alarms per hour of sleep |
| Chip vs Python | 415 of 415 events identical |

The typing network was trained on 38,730 apneas from 666 PhysioNet 2018 patients. Patients were split into training, tuning and test sets, and decision rules were fixed before testing. These results come from hospital recordings; testing on the device itself is in progress.

## Folders

| Folder | Contents |
|---|---|
| `notebooks/` | Colab notebooks that prepare the datasets and train both networks |
| `firmware/` | Weight export scripts and firmware notes |
| `firmware/svasalens_v2/` | Arduino sketch for the XIAO ESP32-S3, with filters and both networks in C++ |
| `daytime_test/` | 10-minute simulated-apnea test for volunteers, and a script to summarise results |
