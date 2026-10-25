import os, sys, glob
import numpy as np
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception as _e:
    plt = None
    print('note: plotting unavailable (' + str(_e)[:60] + '), writing the summary only')

FS = 50.0
ACC_LSB, GYR_LSB = 16384.0, 131.0
SPOTS = {'A': 'top of breastbone (0x70 board)', 'B': 'upper belly (0x68 board)'}
NOISE_MG = {'A': 0.308, 'B': 0.244}
PREVIOUS_MID_STERNUM_MG = 0.93
OUT = 'results'

def _pad(x, n):
    n = min(n, len(x) - 1)
    return np.concatenate([2 * x[0] - x[n:0:-1], x, 2 * x[-1] - x[-2:-n - 2:-1]]), n

def bandpass(x, fs, lo, hi, order):
    x = np.asarray(x, float)
    xp, n = _pad(x, int(3 * fs / lo))
    f = np.fft.rfftfreq(len(xp), 1 / fs)
    with np.errstate(divide='ignore', invalid='ignore'):
        w = (f ** 2 - lo * hi) / (f * (hi - lo))
    gain = 1.0 / (1.0 + w ** (2 * order))
    gain[0] = 0.0
    y = np.fft.irfft(np.fft.rfft(xp) * gain, len(xp))
    return y[n:n + len(x)]

def downsample(x, fs_in, fs_out):
    x = np.asarray(x, float)
    xp, n = _pad(x, int(2 * fs_in))
    f = np.fft.rfftfreq(len(xp), 1 / fs_in)
    y = np.fft.irfft(np.fft.rfft(xp) * (f < 0.45 * fs_out), len(xp))[n:n + len(x)]
    t_in = np.arange(len(x)) / fs_in
    t_out = np.arange(int(len(x) * fs_out / fs_in)) / fs_out
    return np.interp(t_out, t_in, y)

def load(path):
    rows, meta = [], []
    for line in open(path):
        line = line.strip()
        if line.startswith('#'):
            meta.append(line[1:].strip()); continue
        p = line.split(',')
        if len(p) == 16 and p[0].lstrip('-').isdigit():
            rows.append([int(x) for x in p])
    if not rows:
        sys.exit('no two-sensor rows found. was two_sensor_test uploaded?')
    return meta, np.array(rows, float)

def rhythm(x):
    x = x - x.mean(); n = len(x)
    lo, hi = int(2 * FS), min(int(8 * FS), n // 2)
    var = np.dot(x, x) / n
    if var == 0: return 0.0
    ac = np.correlate(x, x, 'full')[n - 1:] / (n - np.arange(n)) / var
    return float(np.max(ac[lo:hi + 1]))

def breathing_trace(acc):
    g = acc.mean(0); gd = g / np.linalg.norm(g)
    R = acc - np.outer(acc @ gd, gd); R -= R.mean(0)
    u = np.linalg.svd(R, full_matrices=False)[2][0]; u -= (u @ gd) * gd; u /= np.linalg.norm(u)
    return 1000 * bandpass(acc @ u, FS, 0.1, 0.7, 2)

def clean(block):
    dead = np.all(block == 0, axis=1)
    frac = float(dead.mean())
    if 0 < frac < 1:
        idx = np.arange(len(block))
        block = block.copy()
        for j in range(block.shape[1]):
            block[dead, j] = np.interp(idx[dead], idx[~dead], block[~dead, j])
    return block, frac

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob('servo_run_*.csv'))[-1]
    meta, D = load(path)
    D = D[D[:, 1] == 0]
    t = (D[:, 2] - D[0, 2]) / 1000
    lines = [f'file: {path}', *[f'  {m}' for m in meta], f'recorded {t[-1]:.0f} s', '']
    tr, res = {}, {}
    for k, off in (('A', 4), ('B', 10)):
        block, frac = clean(D[:, off:off + 6])
        if frac > 0.2:
            lines += [f'SENSOR {k}: {SPOTS[k]}',
                      f'  NO USABLE DATA: it returned zeros for {100*frac:.0f}% of the run.',
                      '  It lost power or a data wire during recording. Redo this run.', '']
            continue
        acc = block[:, :3] / ACC_LSB
        gyr = block[:, 3:] / GYR_LSB
        s = breathing_trace(acc)
        edge = int(3 * FS); core = s[edge:-edge]
        mg = float(np.sqrt(np.mean(core ** 2)))
        rh = rhythm(core)
        motion = float(np.sqrt(np.mean(np.sum((gyr - np.median(gyr, 0)) ** 2, 1))))
        tr[k] = s; res[k] = (mg, rh, mg / NOISE_MG[k], motion)
        lines += [f'SENSOR {k}: {SPOTS[k]}',
                  f'  breathing strength: {mg:.2f} mg RMS',
                  f'  x its own noise floor: {mg / NOISE_MG[k]:.1f}',
                  f'  rhythm: {rh:.2f}   (normal breathing should be near 1)',
                  f'  body movement: {motion:.2f} deg/s',
                  f'  dropped-out samples filled in: {100*frac:.1f}%', '']
    if len(res) < 2:
        lines += ['Only one sensor has usable data, so the spots cannot be compared.']
        os.makedirs(OUT, exist_ok=True)
        text = '\n'.join(lines); print(text)
        open(os.path.join(OUT, 'placement_summary.txt'), 'w').write(text + '\n')
        return
    r = np.corrcoef(tr['A'], tr['B'])[0, 1]
    best = max(res, key=lambda k: res[k][2])
    lines += [f'yesterday, middle of breastbone: {PREVIOUS_MID_STERNUM_MG:.2f} mg', '',
              f'timing agreement between the two sensors: {abs(r):.2f}  (1 = rise and fall in step)',
              '  the sign is arbitrary here, so this cannot yet tell paradox apart from normal breathing', '',
              f'STRONGER SIGNAL: sensor {best}, {SPOTS[best]}',
              f'  {res[best][2]:.1f}x its noise floor, vs about 3.8x yesterday.']
    if max(v[1] for v in res.values()) < 0.5:
        lines += ['', '  Rhythm is low on both. Breathing is not coming through cleanly:',
                  '  check the sensors are pressed flat and the person lay completely still.']
    os.makedirs(OUT, exist_ok=True)
    text = '\n'.join(lines); print(text)
    open(os.path.join(OUT, 'placement_summary.txt'), 'w').write(text + '\n')
    if plt is None:
        return
    fig, ax = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    for a, k in zip(ax, 'AB'):
        a.plot(t, tr[k], lw=0.9)
        a.set_ylabel('mg'); a.set_title(f'{SPOTS[k]}: {res[k][0]:.2f} mg, rhythm {res[k][1]:.2f}', fontsize=10)
    ax[-1].set_xlabel('seconds')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'placement_plot.png'), dpi=130)
    print(f'\nwrote {OUT}/placement_summary.txt and {OUT}/placement_plot.png')

if __name__ == '__main__':
    main()
