import csv
import numpy as np
from scipy.stats import wilcoxon

rows = list(csv.DictReader(open('better_features.csv')))
obs = np.array([float(r['amplitude']) for r in rows if r['label'] == 'O'])
cen = np.array([float(r['amplitude']) for r in rows if r['label'] == 'C'])

q1, q3 = np.percentile(obs, [25, 75])
print(f'obstructive apneas: {len(obs)}')
print(f'median effort {np.median(obs):.2f} (IQR {q1:.2f}-{q3:.2f}), 1.0 = normal')
print(f'at or above normal: {(obs >= 1).sum()} of {len(obs)} ({100 * (obs >= 1).mean():.1f}%)')

per_patient = {}
for r in rows:
    if r['label'] == 'O':
        per_patient.setdefault(r['subject'], []).append(float(r['amplitude']))
med = np.array([np.median(v) for v in per_patient.values()])
stat, p = wilcoxon(med - 1.0)
print(f'patients with obstructive apneas: {len(med)}, median below normal: {(med < 1).sum()}')
print(f'one-sample Wilcoxon signed-rank test against 1.0: W = {stat:.0f}, p = {p:.2g}')
print(f'central apneas: {len(cen)}, median effort {np.median(cen):.2f}')
