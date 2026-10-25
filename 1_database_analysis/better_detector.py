import os, re, glob
import numpy as np
from scipy.signal import butter, sosfiltfilt, hilbert
from scipy.stats import rankdata
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

DATA_DIR = r'D:\files'
BAND = (0.1, 0.5)
NOISY_BELTS = {'ucddb009', 'ucddb025'}
PREV_HELDOUT = {'ucddb003', 'ucddb005', 'ucddb007', 'ucddb008', 'ucddb010', 'ucddb012',
                'ucddb015', 'ucddb017', 'ucddb020', 'ucddb021', 'ucddb023', 'ucddb024',
                'ucddb026', 'ucddb027'}
FEATS = ['amplitude', 'rhythm', 'paradox', 'local', 'snore_pre', 'gasp_post']

def read_edf(path, wanted):
    with open(path, 'rb') as f:
        f.read(8); f.read(80); f.read(80); f.read(8)
        start = f.read(8).decode('ascii', 'ignore').strip()
        f.read(8); f.read(44)
        nrec = int(f.read(8).decode('ascii', 'ignore').strip())
        rsec = float(f.read(8).decode('ascii', 'ignore').strip())
        ns = int(f.read(4).decode('ascii', 'ignore').strip())
        fld = lambda w: [f.read(w).decode('ascii', 'ignore').strip() for _ in range(ns)]
        labels = fld(16); fld(80); fld(8)
        pmin = [float(v) for v in fld(8)]; pmax = [float(v) for v in fld(8)]
        dmin = [float(v) for v in fld(8)]; dmax = [float(v) for v in fld(8)]
        fld(80)
        spr = [int(v) for v in fld(8)]
        f.read(32 * ns)
        raw = f.read()
    tot = sum(spr); bpr = tot * 2
    nrec = min(nrec, len(raw) // bpr)
    data = np.frombuffer(raw[:nrec * bpr], dtype='<i2').reshape(nrec, tot)
    off = np.cumsum([0] + spr)
    sig, rate = {}, {}
    for i, lab in enumerate(labels):
        key = 'Sound' if lab in ('Sound', 'Soud') else lab
        if key not in wanted:
            continue
        blk = data[:, off[i]:off[i + 1]].ravel().astype(float)
        span = (dmax[i] - dmin[i]) or 1.0
        sig[key] = (blk - dmin[i]) * ((pmax[i] - pmin[i]) / span) + pmin[i]
        rate[key] = spr[i] / rsec
    p = re.split(r'[.:]', start)
    h, m, s = (int(x) for x in p[:3]) if len(p) >= 3 else (0, 0, 0)
    return sig, rate, h * 3600 + m * 60 + s

EVENT_RE = re.compile(
    r'^\s*(\d{1,2}):(\d{2}):(\d{2})\s+([A-Z]+(?:-[A-Z])?)\s+(?:(PB|CS)\s+)?(\d+)')

def parse_events(path, start_s):
    out = []
    with open(path, errors='ignore') as fh:
        for line in fh:
            m = EVENT_RE.match(line)
            if m:
                h, mi, s, typ, _, d = m.groups()
                t = int(h) * 3600 + int(mi) * 60 + int(s) - start_s
                if t < 0:
                    t += 24 * 3600
                out.append((t, int(d), typ.upper()))
    return out

def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

def bp(x, fs, lo, hi, order=3):
    sos = butter(order, [lo, min(hi, 0.45 * fs)], btype='band', fs=fs, output='sos')
    return sosfiltfilt(sos, x)

def event_mask(n, fs, events, pad):
    m = np.zeros(n, dtype=bool)
    for t, d, _ in events:
        m[max(0, int((t - pad) * fs)):min(n, int((t + d + pad) * fs))] = True
    return m

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

def subject_features(sid, rec, evt_path):
    sig, rate, start_s = read_edf(rec, {'ribcage', 'abdo', 'Sound'})
    if 'ribcage' not in sig or 'abdo' not in sig:
        return []
    fs = rate['ribcage']
    events = parse_events(evt_path, start_s)
    n = min(len(sig['ribcage']), len(sig['abdo']))
    rib = bp(sig['ribcage'][:n], fs, *BAND)
    abd = bp(sig['abdo'][:n], fs, *BAND)
    rib_lp = bp(sig['ribcage'][:n], fs, 0.05, 0.7, order=2)
    abd_lp = bp(sig['abdo'][:n], fs, 0.05, 0.7, order=2)

    busy = event_mask(n, fs, events, 20)
    clean = ~busy
    w = int(30 * fs)

    def clean_rms(x):
        vals = [rms(x[i:i + w]) for i in range(0, n - w, w) if clean[i:i + w].all()]
        return float(np.median(vals)) if vals else (rms(x) or 1.0)

    base_rib, base_abd = clean_rms(rib), clean_rms(abd)

    ph = np.angle(hilbert(rib)) - np.angle(hilbert(abd))
    base_phase = np.angle(np.mean(np.exp(1j * ph[clean]))) if clean.any() else 0.0

    has_sound = 'Sound' in sig
    if has_sound:
        fs_s = rate['Sound']
        snd = np.abs(sig['Sound'])
        clean_s = ~event_mask(len(snd), fs_s, events, 20)
        base_snd = float(np.median(snd[clean_s])) if clean_s.any() else float(np.median(snd))
        base_snd = base_snd if base_snd > 0 else (float(np.mean(snd)) or 1.0)

    other = event_mask(n, fs, events, 0)
    rows = []
    for t, d, typ in events:
        if not typ.startswith('APNEA'):
            continue
        lab = typ.split('-')[-1]
        if lab not in ('O', 'C'):
            continue
        i0, i1 = int(t * fs), int((t + d) * fs)
        if i0 < int(40 * fs) or i1 + int(12 * fs) > n or i1 - i0 < int(4 * fs):
            continue

        r_n = rms(rib[i0:i1]) / base_rib
        a_n = rms(abd[i0:i1]) / base_abd
        amplitude = max(r_n, a_n)

        rhythm = max(rhythm_score(rib_lp[i0:i1], fs), rhythm_score(abd_lp[i0:i1], fs))

        dphi = np.angle(np.mean(np.exp(1j * (ph[i0:i1] - base_phase))))
        paradox = float(np.degrees(abs(dphi)))

        p0, p1 = i0 - int(35 * fs), i0 - int(5 * fs)
        keep = ~other[p0:p1]
        if keep.sum() >= int(10 * fs):
            lr = rms(rib[p0:p1][keep]) or base_rib
            la = rms(abd[p0:p1][keep]) or base_abd
        else:
            lr, la = base_rib, base_abd
        local = max(rms(rib[i0:i1]) / lr, rms(abd[i0:i1]) / la)

        if has_sound:
            j0, j1 = int(t * fs_s), int((t + d) * fs_s)
            pre = snd[max(0, j0 - int(10 * fs_s)):j0]
            post = snd[j1:j1 + int(10 * fs_s)]
            snore_pre = float(np.mean(pre)) / base_snd if len(pre) else 1.0
            gasp_post = float(np.max(post)) / base_snd if len(post) else 1.0
        else:
            snore_pre = gasp_post = 1.0

        rows.append(dict(subject=sid, label=lab, duration=d,
                         amplitude=amplitude, rhythm=rhythm, paradox=paradox,
                         local=local, snore_pre=snore_pre, gasp_post=gasp_post))
    return rows

def auc(score, y):
    ranks = rankdata(score)
    n1, n0 = y.sum(), len(y) - y.sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

def bal_acc(pred, y):
    return 0.5 * ((pred[y == 1] == 1).mean() + (pred[y == 0] == 0).mean())

def transform(X):
    X = X.copy()
    for j, f in enumerate(FEATS):
        if f in ('amplitude', 'local', 'snore_pre', 'gasp_post'):
            X[:, j] = np.log(np.clip(X[:, j], 1e-3, None))
    return X

def fit_logreg(X, y, l2=1.0, iters=50):
    n, p = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(p + 1)
    cw = np.where(y == 1, n / (2 * max(y.sum(), 1)), n / (2 * max((1 - y).sum(), 1)))
    R = l2 * np.eye(p + 1); R[0, 0] = 0
    for _ in range(iters):
        z = np.clip(Xb @ w, -30, 30)
        pr = 1 / (1 + np.exp(-z))
        g = Xb.T @ (cw * (pr - y)) + R @ w
        H = (Xb * (cw * pr * (1 - pr))[:, None]).T @ Xb + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w

def loso_single(x, y, who):
    pred = np.zeros(len(y), dtype=int)
    for s in np.unique(who):
        te, tr = who == s, who != s
        a = auc(x[tr], y[tr])
        sgn = 1.0 if a >= 0.5 else -1.0
        xt = sgn * x[tr]
        best_t, best_b = None, -1
        for t in np.unique(np.percentile(xt, np.linspace(1, 99, 99))):
            b = bal_acc((xt >= t).astype(int), y[tr])
            if b > best_b:
                best_t, best_b = t, b
        pred[te] = (sgn * x[te] >= best_t).astype(int)
    return pred

def loso_combined(X, y, who):
    prob = np.zeros(len(y))
    coefs = []
    for s in np.unique(who):
        te, tr = who == s, who != s
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        w = fit_logreg((X[tr] - mu) / sd, y[tr])
        z = np.clip(np.hstack([np.ones((te.sum(), 1)), (X[te] - mu) / sd]) @ w, -30, 30)
        prob[te] = 1 / (1 + np.exp(-z))
        coefs.append(w[1:])
    return prob, np.mean(coefs, axis=0), np.std(coefs, axis=0)

def boot_ci(fn, who, n=2000, seed=0):
    subs = np.unique(who)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = rng.choice(subs, len(subs), replace=True)
        idx = np.concatenate([np.where(who == s)[0] for s in pick])
        v = fn(idx)
        if not np.isnan(v):
            vals.append(v)
    if not vals:
        return np.nan, np.nan
    return np.percentile(vals, [2.5, 97.5])

def main():
    os.chdir(DATA_DIR)
    print('extracting features...\n')
    rows = []
    for rec in sorted(glob.glob('**/*.rec', recursive=True)):
        sid = os.path.basename(rec).replace('.rec', '')
        ev = glob.glob(f'**/{sid}_respevt.txt', recursive=True)
        if not ev:
            continue
        r = subject_features(sid, rec, ev[0])
        print(f'  {sid}: {len(r)} apneas')
        rows += r

    if not rows:
        print('no events extracted')
        return
    with open('better_features.csv', 'w') as fh:
        keys = list(rows[0])
        fh.write(','.join(keys) + '\n')
        for r in rows:
            fh.write(','.join(str(r[k]) for k in keys) + '\n')

    y = np.array([1 if r['label'] == 'O' else 0 for r in rows])
    who = np.array([r['subject'] for r in rows])
    Xraw = np.array([[r[f] for f in FEATS] for r in rows], float)
    X = transform(Xraw)

    out = []
    def say(s=''):
        print(s); out.append(s)

    say('\n' + '=' * 72)
    say(f'{len(rows)} apneas, {len(np.unique(who))} subjects   '
        f'obstructive {y.sum()}  central {len(y) - y.sum()}')
    say(f'always guessing central: accuracy {100 * (1 - y.mean()):.1f}%, balanced 50.0%')
    say('=' * 72)
    say('  every feature was predicted in advance to be HIGHER for obstructive.')
    say('  AUC below 0.5 means that prediction was wrong.\n')
    say(f'  {"feature":<12}{"AUC":>7}{"acc":>8}{"bal.acc":>9}   prediction')
    say('  ' + '-' * 58)

    results = {}
    for j, f in enumerate(FEATS):
        a = auc(X[:, j], y)
        pred = loso_single(X[:, j], y, who)
        acc, ba = (pred == y).mean(), bal_acc(pred, y)
        verdict = 'supported' if a >= 0.55 else ('wrong way round' if a < 0.45 else 'no signal')
        results[f] = (a, acc, ba, pred)
        say(f'  {f:<12}{a:>7.3f}{100 * acc:>7.1f}%{100 * ba:>8.1f}%   {verdict}')

    prob, cmean, csd = loso_combined(X, y, who)
    pred_c = (prob >= 0.5).astype(int)
    a_c, acc_c, ba_c = auc(prob, y), (pred_c == y).mean(), bal_acc(pred_c, y)
    say('  ' + '-' * 58)
    say(f'  {"COMBINED":<12}{a_c:>7.3f}{100 * acc_c:>7.1f}%{100 * ba_c:>8.1f}%')

    lo_a, hi_a = boot_ci(lambda i: auc(prob[i], y[i]), who)
    lo_b, hi_b = boot_ci(lambda i: bal_acc(pred_c[i], y[i]), who)
    say(f'\n  combined 95% CI:  AUC {lo_a:.3f}-{hi_a:.3f}   balanced acc {100*lo_b:.1f}-{100*hi_b:.1f}%')

    base = results['amplitude']
    amp = X[:, FEATS.index('amplitude')]
    say(f'\n  vs amplitude alone: AUC {base[0]:.3f} -> {a_c:.3f}, '
        f'balanced acc {100*base[2]:.1f}% -> {100*ba_c:.1f}%')
    d_lo, d_hi = boot_ci(lambda i: auc(prob[i], y[i]) - auc(amp[i], y[i]), who)
    b_lo, b_hi = boot_ci(lambda i: bal_acc(pred_c[i], y[i]) - bal_acc(base[3][i], y[i]), who)
    say(f'  improvement, 95% CI:  AUC {d_lo:+.3f} to {d_hi:+.3f}   '
        f'balanced acc {100*b_lo:+.1f} to {100*b_hi:+.1f} points')
    if d_lo > 0:
        say('  -> the combined model is reliably better than amplitude alone.')
    else:
        say('  -> the interval includes zero: you CANNOT claim the combined model is better.')

    say('\nWHAT THE COMBINED MODEL RELIES ON  (standardised weights, + = obstructive)')
    for f, m, s in sorted(zip(FEATS, cmean, csd), key=lambda z: -abs(z[1])):
        say(f'  {f:<12}{m:>+7.2f}  (+/- {s:.2f} across folds)')

    m = np.isin(who, list(PREV_HELDOUT))
    if m.any():
        say('\nON THE 14 PREVIOUSLY HELD-OUT SUBJECTS  (compare with the earlier 65%)')
        say(f'  amplitude alone: acc {100*(base[3][m]==y[m]).mean():.1f}%, '
            f'balanced {100*bal_acc(base[3][m], y[m]):.1f}%')
        say(f'  combined       : acc {100*(pred_c[m]==y[m]).mean():.1f}%, '
            f'balanced {100*bal_acc(pred_c[m], y[m]):.1f}%, AUC {auc(prob[m], y[m]):.3f}')

    m2 = ~np.isin(who, list(NOISY_BELTS))
    say('\nEXCLUDING THE TWO NOISY-BELT SUBJECTS')
    say(f'  combined: balanced {100*bal_acc(pred_c[m2], y[m2]):.1f}%, AUC {auc(prob[m2], y[m2]):.3f}')

    say('\nEXPLORATORY: features chosen after seeing the first results.')

    with open('better_results.txt', 'w') as fh:
        fh.write('\n'.join(out) + '\n')

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    names = FEATS + ['COMBINED']
    aucs = [results[f][0] for f in FEATS] + [a_c]
    cols = ['#888780'] * len(FEATS) + ['#378ADD']
    a1.barh(names[::-1], aucs[::-1], color=cols[::-1])
    a1.axvline(0.5, color='grey', ls=':', lw=1)
    a1.axvline(base[0], color='#D85A30', ls='--', lw=1, label='amplitude alone')
    a1.set_xlim(min(0.3, min(aucs) - 0.05), 1.0)
    a1.set_xlabel('AUC, leave-one-subject-out')
    a1.legend(fontsize=8)
    a1.set_title('Which features separate obstructive from central?')
    order = np.argsort(np.abs(cmean))
    a2.barh(np.array(FEATS)[order], cmean[order], xerr=csd[order],
            color=['#1D9E75' if v > 0 else '#D85A30' for v in cmean[order]])
    a2.axvline(0, color='grey', lw=0.8)
    a2.set_xlabel('weight (+ = obstructive)')
    a2.set_title('What the combined model relies on')
    fig.tight_layout()
    fig.savefig('better_plot.png', dpi=130)
    say('\nwrote better_results.txt, better_features.csv, better_plot.png')

if __name__ == '__main__':
    main()
