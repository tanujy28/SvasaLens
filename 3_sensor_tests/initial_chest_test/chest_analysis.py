import os, sys, glob
import numpy as np
from scipy.signal import butter, sosfiltfilt, resample_poly
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ACC_LSB = 16384.0
FS_OUT = 8.0
BAND = (0.1, 0.5)
OUT_DIR = 'results'

EVENTS = [(35, 45, 'H'), (70, 80, 'E'), (105, 115, 'H'),
          (140, 150, 'E'), (175, 185, 'H'), (210, 220, 'E')]
NORMAL = [(10, 35), (45, 70), (80, 105), (115, 140), (150, 175), (185, 210), (220, 250)]
RECOVERY_SKIP = 10

UCDDB_OBS, UCDDB_CEN = 0.635, 0.406

def rhythm_score(x, fs):
    x = x - x.mean()
    n = len(x)
    lo, hi = int(2 * fs), min(int(8 * fs), n // 2)
    var = np.dot(x, x) / n
    if hi <= lo or var == 0:
        return 0.0
    raw = np.correlate(x, x, 'full')[n - 1:]
    ac = raw / (n - np.arange(n)) / var
    return float(np.max(ac[lo:hi + 1]))

def load(path):
    rows, meta = [], []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('trial'):
                continue
            if line.startswith('#'):
                meta.append(line[1:].strip())
                continue
            p = line.split(',')
            if len(p) == 10:
                try:
                    rows.append([int(x) for x in p])
                except ValueError:
                    pass
    if not rows:
        sys.exit('no data rows in ' + path)
    return meta, np.array(rows, dtype=float)

def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

def main():
    args = sys.argv[1:]
    offset = 0.0
    if args and args[-1].replace('.', '', 1).lstrip('-').isdigit():
        offset = float(args.pop())
    if args:
        path = args[0]
    else:
        runs = sorted(glob.glob('servo_run_*.csv'))
        if not runs:
            sys.exit('no servo_run_*.csv here. run capture.py first.')
        path = runs[-1]

    meta, D = load(path)
    t = (D[:, 2] - D[0, 2]) / 1000.0
    acc = D[:, 4:7] / ACC_LSB
    dur = t[-1]
    if dur < 200:
        print(f'warning: only {dur:.0f} s recorded, the schedule needs 250 s')

    g = acc.mean(0)
    gd = g / np.linalg.norm(g)
    R = acc - np.outer(acc @ gd, gd)
    R -= R.mean(0)
    _, _, Vt = np.linalg.svd(R, full_matrices=False)
    u = Vt[0] - (Vt[0] @ gd) * gd
    u /= np.linalg.norm(u)
    tilt = np.degrees(acc @ u)

    tilt8 = resample_poly(tilt, 2, 25)
    t8 = np.arange(len(tilt8)) / FS_OUT
    sos = butter(3, BAND, btype='band', fs=FS_OUT, output='sos')
    eff = sosfiltfilt(sos, tilt8)
    sos_w = butter(2, (0.05, 0.7), btype='band', fs=FS_OUT, output='sos')
    wide = sosfiltfilt(sos_w, tilt8)

    def seg(a, b, sig=None):
        sig = eff if sig is None else sig
        i0, i1 = int((a + offset) * FS_OUT), int((b + offset) * FS_OUT)
        i0, i1 = max(0, i0), min(len(sig), i1)
        return sig[i0:i1]

    base_parts = [rms(seg(a + RECOVERY_SKIP, b)) for a, b in NORMAL if b - a > RECOVERY_SKIP]
    base_parts = [x for x in base_parts if x > 0]
    if not base_parts:
        sys.exit('No breathing movement detected at all. Check the sensor is taped firmly '
                 'to the chest and the wires are connected, then record again.')
    baseline = float(np.median(base_parts))

    results = []
    for a, b, k in EVENTS:
        s = seg(a, b)
        if len(s) < 4 * FS_OUT:
            continue
        results.append((a, k, rms(s) / baseline, rhythm_score(seg(a, b, wide), FS_OUT)))

    H = [r for _, k, r, _ in results if k == 'H']
    E = [r for _, k, r, _ in results if k == 'E']
    HR = [q for _, k, _, q in results if k == 'H']
    ER = [q for _, k, _, q in results if k == 'E']
    norm_rh = [rhythm_score(seg(a + RECOVERY_SKIP, b, wide), FS_OUT)
               for a, b in NORMAL if b - a > RECOVERY_SKIP]

    lines = [f'file: {path}', *[f'  {m}' for m in meta],
             f'recorded {dur:.0f} s, schedule offset {offset:+.1f} s', '',
             'EACH EVENT          effort (1.0 = normal)   rhythm (0 = noise, 1 = perfectly regular)']
    for a, k, r, q in results:
        name = 'breath-hold     ' if k == 'H' else 'effort attempt  '
        lines.append(f'  {a:>4.0f} s  {name}  {r:>6.3f}               {q:>6.3f}')
    lines.append('')
    if H and E:
        lines += [
            f'  median breath-hold   : {np.median(H):.3f}   (like central apnea)',
            f'  median effort attempt: {np.median(E):.3f}   (like obstructive apnea)',
            '',
            'COMPARE WITH UCDDB (held-out patients, same measure)',
            f'  central   : {UCDDB_CEN:.3f}',
            f'  obstructive: {UCDDB_OBS:.3f}',
            '',
        ]
        lines += [
            f'  median rhythm, breath-hold   : {np.median(HR):.3f}',
            f'  median rhythm, effort attempt: {np.median(ER):.3f}',
            f'  median rhythm, normal breathing: {np.median(norm_rh):.3f}  (reference)',
            '',
        ]
        if min(ER) > max(HR):
            lines.append('  Rhythm: every effort attempt was more regular than every breath-hold.')
        else:
            lines.append('  Rhythm: some overlap between holds and attempts. Check the plot.')
        lines.append('')
        sep = min(E) > max(H)
        if sep:
            lines.append('  Every effort attempt scored above every breath-hold: the device')
            lines.append('  sees the difference cleanly on your chest.')
        elif np.median(E) > np.median(H):
            lines.append('  Efforts are higher on average but some overlap. Check the plot for')
            lines.append('  movement or a timing slip before judging.')
        else:
            lines.append('  No separation. Check the plot: timing offset, a loose sensor, or')
            lines.append('  movement are the usual causes. Re-run before concluding anything.')
        lines.append('')
        lines.append('  One person, awake, voluntary manoeuvres. This shows the sensor can')
        lines.append('  see the distinction; it is not a test of accuracy on real apneas.')

    os.makedirs(OUT_DIR, exist_ok=True)
    text = '\n'.join(lines)
    print(text)
    with open(os.path.join(OUT_DIR, 'chest_summary.txt'), 'w') as fh:
        fh.write(text + '\n')

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6),
                                 gridspec_kw={'height_ratios': [2.2, 1]})
    a1.plot(t8, eff, lw=0.9, color='#378ADD')
    for a, b, k in EVENTS:
        a1.axvspan(a + offset, b + offset, color='#D85A30' if k == 'H' else '#1D9E75',
                   alpha=0.2, lw=0)
        a1.text((a + b) / 2 + offset, a1.get_ylim()[1] * 0.85 if a1.get_ylim()[1] else 0,
                'hold' if k == 'H' else 'effort', ha='center', fontsize=8)
    a1.set_ylabel('chest tilt, breathing band (deg)')
    a1.set_xlabel('seconds')
    a1.set_title('Chest effort signal. Orange = breath-hold, green = effort attempt')

    xs = np.arange(len(results))
    cols = ['#D85A30' if k == 'H' else '#1D9E75' for _, k, _, _ in results]
    a2.bar(xs - 0.2, [r for _, _, r, _ in results], width=0.4, color=cols, label='effort')
    a2.bar(xs + 0.2, [q for _, _, _, q in results], width=0.4, color=cols, alpha=0.45,
           hatch='//', label='rhythm')
    a2.axhline(1.0, color='grey', lw=0.8, ls=':')
    a2.axhline(UCDDB_OBS, color='#1D9E75', lw=1, ls='--', label='UCDDB obstructive median')
    a2.axhline(UCDDB_CEN, color='#D85A30', lw=1, ls='--', label='UCDDB central median')
    a2.set_xticks(xs)
    a2.set_xticklabels([f'{"hold" if k == "H" else "effort"}\n{a:.0f}s' for a, k, _, _ in results],
                       fontsize=8)
    a2.set_ylabel('solid: effort   hatched: rhythm')
    a2.legend(fontsize=7, loc='upper right')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'chest_plot.png'), dpi=130)
    print(f'\nwrote {OUT_DIR}/chest_summary.txt and {OUT_DIR}/chest_plot.png')

if __name__ == '__main__':
    main()
