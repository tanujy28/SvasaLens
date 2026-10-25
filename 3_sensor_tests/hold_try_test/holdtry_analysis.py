import os, sys, glob
import numpy as np
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception as _e:
    plt = None
    print('note: plotting unavailable (' + str(_e)[:60] + '), writing the summary only')

FS_IN, FS_OUT = 50.0, 8.0
ACC_LSB, GYR_LSB = 16384.0, 131.0
BAND = (0.1, 0.5)
OUT_DIR = 'results'
EVENTS = [(35, 45, 'H'), (70, 80, 'E'), (105, 115, 'H'),
          (140, 150, 'E'), (175, 185, 'H'), (210, 220, 'E')]
NORMAL = [(10, 35), (45, 70), (80, 105), (115, 140), (150, 175), (185, 210), (220, 250)]
RECOVERY_SKIP = 10
SCORE_SKIP = 3
MOVE_LIMIT = 3.0
MIN_COVER = 0.7
MOVE_HP = 1.0
SPOTS = {'A': 'top of breastbone (0x70 board)', 'B': 'upper belly (0x68 board)'}
UCDDB_OBS, UCDDB_CEN = 0.635, 0.406

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

def rhythm_score(x, fs):
    x = x - x.mean(); n = len(x)
    lo, hi = int(2 * fs), min(int(8 * fs), n // 2)
    var = np.dot(x, x) / n
    if hi <= lo or var == 0:
        return 0.0
    ac = np.correlate(x, x, 'full')[n - 1:] / (n - np.arange(n)) / var
    return float(np.max(ac[lo:hi + 1]))

def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

def load(path):
    rows, meta = [], []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith('trial'):
            continue
        if line.startswith('#'):
            meta.append(line[1:].strip()); continue
        p = line.split(',')
        if len(p) == 16:
            try: rows.append([int(x) for x in p])
            except ValueError: pass
    if not rows:
        sys.exit('no two-sensor rows in ' + path + '. was hold_try.ino uploaded?')
    return meta, np.array(rows, float)

def tilt_signal(acc):
    g = acc.mean(0); gd = g / np.linalg.norm(g)
    R = acc - np.outer(acc @ gd, gd); R -= R.mean(0)
    _, _, Vt = np.linalg.svd(R, full_matrices=False)
    u = Vt[0] - (Vt[0] @ gd) * gd; u /= np.linalg.norm(u)
    return np.degrees(acc @ u)

def clean(block):
    dead = np.all(block == 0, axis=1)
    frac = float(dead.mean())
    if 0 < frac < 1:
        idx = np.arange(len(block)); block = block.copy()
        for j in range(block.shape[1]):
            block[dead, j] = np.interp(idx[dead], idx[~dead], block[~dead, j])
    return block, frac

def analyse(acc, gyr, offset, cover):
    tilt8 = downsample(tilt_signal(acc), FS_IN, FS_OUT)
    eff = bandpass(tilt8, FS_OUT, BAND[0], BAND[1], 3)
    wide = bandpass(tilt8, FS_OUT, 0.05, 0.7, 2)
    gh = np.column_stack([bandpass(gyr[:, j], FS_IN, MOVE_HP, 20.0, 2) for j in range(3)])
    gmag = np.linalg.norm(gh, axis=1)
    move8 = downsample(gmag, FS_IN, FS_OUT)
    cov8 = np.interp(np.arange(len(move8)) / FS_OUT, np.arange(len(cover)) / FS_IN, cover)

    def seg(sig, a, b):
        i0, i1 = int((a + offset) * FS_OUT), int((b + offset) * FS_OUT)
        return sig[max(0, i0):min(len(sig), i1)]

    normals = [(a + RECOVERY_SKIP, b) for a, b in NORMAL if b - a > RECOVERY_SKIP]
    good = [(a, b) for a, b in normals if len(seg(cov8, a, b)) and seg(cov8, a, b).mean() >= MIN_COVER]
    normals = good or normals
    baseline = float(np.median([rms(seg(eff, a, b)) for a, b in normals]))
    move_rest = float(np.median([rms(seg(move8, a, b)) for a, b in normals]))
    norm_rh = float(np.median([rhythm_score(seg(wide, a, b), FS_OUT) for a, b in normals]))
    if baseline == 0:
        return None
    res = []
    for a, b, k in EVENTS:
        s0 = a + SCORE_SKIP
        e = seg(eff, s0, b)
        if len(e) < 4 * FS_OUT:
            continue
        mv = rms(seg(move8, s0, b)) / move_rest if move_rest else 0.0
        cv = float(seg(cov8, s0, b).mean())
        res.append(dict(t=a, kind=k, effort=rms(e) / baseline,
                        rhythm=rhythm_score(seg(wide, s0, b), FS_OUT),
                        move=mv, cover=cv, keep=(mv <= MOVE_LIMIT and cv >= MIN_COVER),
                        why='' if cv >= MIN_COVER else 'NO (gaps)' ))
    return dict(eff=eff, res=res, baseline=baseline, move_rest=move_rest, norm_rh=norm_rh)

def main():
    args = sys.argv[1:]
    offset = 0.0
    if args and args[-1].replace('.', '', 1).lstrip('-').isdigit():
        offset = float(args.pop())
    if args:
        path = args[0]
    else:
        runs = sorted(glob.glob('holdtry_*.csv'))
        if not runs:
            sys.exit('no holdtry_*.csv here. run capture_holdtry.py first.')
        path = runs[-1]
    meta, D = load(path)
    dur = (D[-1, 2] - D[0, 2]) / 1000.0
    L = [f'file: {path}', *[f'  {m}' for m in meta],
         f'recorded {dur:.0f} s, cue offset {offset:+.1f} s', '',
         'Rules fixed before recording: score last 7 s of each event;',
         f'exclude an event if movement (gyro above {MOVE_HP:.0f} Hz) > {MOVE_LIMIT:.0f}x normal breathing.', '']
    if dur < 240:
        L.append(f'WARNING: only {dur:.0f} s recorded, the schedule needs 250 s\n')

    t = (D[:, 2] - D[0, 2]) / 1000.0
    tg = np.arange(0, t[-1], 1 / FS_IN)
    near = np.abs(t[np.clip(np.searchsorted(t, tg), 0, len(t) - 1)] - tg)
    near = np.minimum(near, np.abs(t[np.clip(np.searchsorted(t, tg) - 1, 0, len(t) - 1)] - tg))
    cover = (near <= 1.5 / FS_IN).astype(float)
    L.append(f'real samples: {len(t)} of {len(tg)} expected ({100*cover.mean():.0f}% coverage)')
    L.append(f'Data-completeness rule (added after the first run, not based on results):')
    L.append(f'  an event or baseline window needs {100*MIN_COVER:.0f}% real samples to be scored.')
    L.append('')

    out = {}
    for key, c0 in (('A', 4), ('B', 10)):
        raw, frac = clean(D[:, c0:c0 + 6])
        block = np.column_stack([np.interp(tg, t, raw[:, j]) for j in range(6)])
        L.append(f'SENSOR {key}: {SPOTS[key]}')
        if frac > 0.2:
            out[key] = None
            L += [f'  NO USABLE DATA: zeros for {100*frac:.0f}% of the run (lost power or a wire).', '']
            continue
        r = analyse(block[:, :3] / ACC_LSB, block[:, 3:] / GYR_LSB, offset, cover)
        out[key] = r
        L.append(f'  dropped-out samples filled in: {100*frac:.1f}%')
        if r is None:
            L += ['  no breathing movement at all. loose sensor?', '']; continue
        L.append(f'  normal breathing: {r["baseline"]*1000:.1f} millideg RMS, rhythm {r["norm_rh"]:.2f}')
        L.append('  event            effort   rhythm   movement   real data   used?')
        for e in r['res']:
            nm = 'HOLD' if e['kind'] == 'H' else 'TRY '
            L.append(f'  {e["t"]:>4.0f} s  {nm}     {e["effort"]:>6.3f}   {e["rhythm"]:>6.3f}   '
                     f'{e["move"]:>5.1f}x     {100*e["cover"]:>5.0f}%     '
                     f'{"yes" if e["keep"] else (e["why"] or "NO (moved)")}')
        H = [e['effort'] for e in r['res'] if e['keep'] and e['kind'] == 'H']
        E = [e['effort'] for e in r['res'] if e['keep'] and e['kind'] == 'E']
        L.append(f'  scored: {len(H)} holds, {len(E)} tries')
        if H and E:
            L.append(f'  median effort  HOLD {np.median(H):.3f}   TRY {np.median(E):.3f}'
                     f'   (UCDDB central {UCDDB_CEN}, obstructive {UCDDB_OBS})')
            if min(E) > max(H):
                L.append('  RESULT: every scored TRY had more effort than every scored HOLD.')
            elif np.median(E) > np.median(H):
                L.append('  RESULT: tries higher on average, but they overlap with holds.')
            else:
                L.append('  RESULT: no separation.')
        else:
            L.append('  RESULT: not enough clean events to judge.')
        L.append('')
    L += ['One person, awake, voluntary manoeuvres. This tests whether the sensor',
          'sees effort. It is not a test of accuracy on real sleep apneas.']

    os.makedirs(OUT_DIR, exist_ok=True)
    text = '\n'.join(L)
    print(text)
    open(os.path.join(OUT_DIR, 'holdtry_summary.txt'), 'w').write(text + '\n')

    if plt is None:
        return
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    for ax, key in zip(axes, ('A', 'B')):
        r = out[key]
        if r is None:
            continue
        t8 = np.arange(len(r['eff'])) / FS_OUT
        gap = cover < 0.5
        edges = np.flatnonzero(np.diff(np.r_[0, gap.astype(int), 0]))
        for g0, g1 in zip(edges[::2], edges[1::2]):
            if (g1 - g0) / FS_IN >= 0.5:
                ax.axvspan(tg[g0], tg[min(g1, len(tg) - 1)], color='k', alpha=0.12, lw=0)
        ax.plot(t8, r['eff'] * 1000, lw=0.9, color='#2F7FD0' if key == 'A' else '#C98A12')
        for a, b, k in EVENTS:
            ax.axvspan(a + offset, b + offset, color='#D85A30' if k == 'H' else '#1D9E75',
                       alpha=0.18, lw=0)
            ax.axvspan(a + SCORE_SKIP + offset, b + offset, ymin=0, ymax=0.05,
                       color='k', alpha=0.5, lw=0)
        ax.set_ylabel(f'{key}: tilt (millideg)')
        ax.set_title(f'Sensor {key}, {SPOTS[key]}.  orange = HOLD, green = TRY, black bar = scored, grey = no data',
                     fontsize=9)
    axes[-1].set_xlabel('seconds')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'holdtry_plot.png'), dpi=130)
    print(f'\nwrote {OUT_DIR}/holdtry_summary.txt and {OUT_DIR}/holdtry_plot.png')

if __name__ == '__main__':
    main()
