import os, sys, importlib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, balanced_accuracy_score

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, 'v1'), os.path.join(HERE, 'v2')]
from ucddb_io import read_edf, parse_events

DATA = sys.argv[1] if len(sys.argv) > 1 else r'D:\files'
HELDOUT = {'ucddb003', 'ucddb005', 'ucddb007', 'ucddb008', 'ucddb010', 'ucddb012',
           'ucddb015', 'ucddb017', 'ucddb020', 'ucddb021', 'ucddb023', 'ucddb024',
           'ucddb026', 'ucddb027'}

def chip_features(mod, sid):
    sig, rate, st = read_edf(os.path.join(DATA, f'{sid}.rec'), {'ribcage', 'abdo'})
    ev = parse_events(os.path.join(DATA, f'{sid}_respevt.txt'), st)
    n = min(len(sig['ribcage']), len(sig['abdo']))
    ri, ai, fl = mod.prepare(sig['ribcage'][:n], sig['abdo'][:n], ev)
    det, res = mod.Detector(), []
    for i in range(n):
        o = det.feed(int(ri[i]), int(ai[i]), bool(fl[i]))
        if o:
            res.append((i, o[0], o[1], o[2]))
    return [(lab == 'O', m[1], m[2]) for i0, i1, lab, m in mod.match(res, mod.scored_events(ev, n))
            if m is not None]

def table(mod, nights):
    tr, te = [], []
    for sid in nights:
        rows = chip_features(mod, sid)
        (te if sid in HELDOUT else tr).extend(rows)
        print(f'   {sid}: {len(rows)} apneas', flush=True)
    split = lambda rows: tuple(np.array(c, float) for c in zip(*rows))
    return split(tr), split(te)

def fit_rule(lnE, R, Y):
    X = np.c_[lnE, R]
    mu, sd = X.mean(0), X.std(0)
    m = LogisticRegression(class_weight='balanced', tol=1e-10, max_iter=100000).fit((X - mu) / sd, Y)
    w = m.coef_[0] / sd
    return m.intercept_[0] - (w * mu).sum(), w[0], w[1]

def main():
    nights = sorted(f[:8] for f in os.listdir(DATA) if f.startswith('ucddb') and f.endswith('.rec'))
    if len(nights) != 25:
        sys.exit(f'Expected 25 UCDDB nights in {DATA}, found {len(nights)}.')
    L = [f'training nights: {", ".join(s for s in nights if s not in HELDOUT)}',
         f'held-out nights: {", ".join(sorted(HELDOUT))}', '']
    for ver, name in ((1, 'chipref'), (2, 'chipref_v2')):
        print(f'version {ver}: running the chip detector over all nights...')
        (y, e, r), (Y, E, R) = table(importlib.import_module(name), nights)
        y, Y = y.astype(bool), Y.astype(bool)
        L.append(f'VERSION {ver}  (training apneas {len(y)}, held-out apneas {len(Y)}: '
                 f'{int(Y.sum())} obstructive, {int((~Y).sum())} central)')
        if ver == 1:
            thr = max(np.unique(e), key=lambda t: balanced_accuracy_score(y, e >= t))
            L += ['  effort-only rule: obstructive if effort >= %.4f' % thr,
                  '    held-out: balanced accuracy %.1f%%   AUC %.3f'
                  % (100 * balanced_accuracy_score(Y, E >= thr), roc_auc_score(Y, E))]
        c0, c1, c2 = fit_rule(np.log(np.maximum(e, 1e-3)), r, y)
        Z = c0 + c1 * np.log(np.maximum(E, 1e-3)) + c2 * R
        L += [f'  rule: z = {c0:.4f} + {c1:.4f} x ln(effort) + {c2:.4f} x rhythm ; z > 0 means obstructive',
              '    held-out: balanced accuracy %.1f%%   AUC %.3f   obstructive caught %.1f%%   central caught %.1f%%'
              % (100 * balanced_accuracy_score(Y, Z > 0), roc_auc_score(Y, Z),
                 100 * (Z > 0)[Y].mean(), 100 * (Z <= 0)[~Y].mean()),
              '    rhythm alone, held-out AUC %.3f' % roc_auc_score(Y, R), '']
    text = '\n'.join(L)
    print('\n' + text)
    open('fit_rules_results.txt', 'w').write(text + '\n')

if __name__ == '__main__':
    main()
