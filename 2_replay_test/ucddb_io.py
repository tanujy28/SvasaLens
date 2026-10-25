import re
import numpy as np
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

