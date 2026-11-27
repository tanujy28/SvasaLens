# 1. Database analysis (`1_database_analysis/`)

- `better_detector.py`: measures the six features for each of the 559 apneas and tests them one by one and combined (logistic regression, leave-one-patient-out). It writes `better_features.csv`. Produces Table 1.
- `hypothesis_test.py`: reads `better_features.csv` and tests the hypothesis: median effort during obstructive apneas, the share at or above normal, and the one-sample Wilcoxon signed-rank test of each patient's median against 1.0.

See the [main README](../README.md) for setup.
