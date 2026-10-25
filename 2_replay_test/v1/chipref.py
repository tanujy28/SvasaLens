import numpy as np
from math import tan, pi, sqrt
FS = 8
f32 = np.float32
GUARD_S, TAU_S, WARM_S = 20, 300.0, 60
RH_MAX = 100
LAG_LO, LAG_HI = 8, 32

def _lp(fc):
    K = tan(pi*fc/FS); n = 1/(1+sqrt(2)*K+K*K); b0 = K*K*n
    return (b0, 2*b0, b0, 2*(K*K-1)*n, (1-sqrt(2)*K+K*K)*n)
def _hp(fc):
    K = tan(pi*fc/FS); n = 1/(1+sqrt(2)*K+K*K)
    return (n, -2*n, n, 2*(K*K-1)*n, (1-sqrt(2)*K+K*K)*n)
COEF = {'HP1': _hp(0.1), 'LP1': _lp(0.5), 'HP2': _hp(0.05), 'LP2': _lp(0.7)}
C32 = {k: tuple(f32(c) for c in v) for k, v in COEF.items()}

def bq(c, z, i, x):
    b0, b1, b2, a1, a2 = c
    y = f32(f32(b0*x) + z[i])
    z[i] = f32(f32(f32(b1*x) - f32(a1*y)) + z[i+1])
    z[i+1] = f32(f32(b2*x) - f32(a2*y))
    return y

class Ch:
    def __init__(s):
        s.z = [f32(0)]*8; s.sec = f32(0)
        s.ring = [f32(0)]*GUARD_S; s.ok = [False]*GUARD_S
        s.base = f32(0); s.nb = 0; s.evs = f32(0); s.buf = []
    def step(s, x):
        x = f32(x)
        a = bq(C32['LP1'], s.z, 2, bq(C32['HP1'], s.z, 0, x))
        w = bq(C32['LP2'], s.z, 6, bq(C32['HP2'], s.z, 4, x))
        return a, w

def rhythm(buf):
    n = len(buf)
    hi = min(LAG_HI, n // 2)
    if hi <= LAG_LO: return f32(0)
    m = f32(0)
    for v in buf: m = f32(m + v)
    m = f32(m / f32(n))
    x = [f32(v - m) for v in buf]
    var = f32(0)
    for v in x: var = f32(var + f32(v*v))
    var = f32(var / f32(n))
    if var <= 0: return f32(0)
    best = f32(-9)
    for L in range(LAG_LO, hi + 1):
        acc = f32(0)
        for i in range(n - L): acc = f32(acc + f32(x[i]*x[i+L]))
        c = f32(f32(acc / f32(n - L)) / var)
        if c > best: best = c
    return best

class Detector:
    def __init__(s):
        s.ch = [Ch(), Ch()]; s.k = 0; s.since = 10**6; s.inev = False
        s.n = 0; s.pos = 0; s.b0 = [f32(0), f32(0)]; s.valid = False
    def feed(s, rib, abd, ev):
        out = None
        ys = [s.ch[0].step(rib), s.ch[1].step(abd)]
        if ev and not s.inev:
            s.inev = True; s.n = 0; s.b0 = [c.base for c in s.ch]
            s.valid = s.ch[0].nb >= WARM_S and s.ch[1].nb >= WARM_S
            for c in s.ch:
                c.evs = f32(0); c.buf = []; c.ok = [False]*GUARD_S
        if s.inev and ev:
            for c, (a, w) in zip(s.ch, ys):
                c.evs = f32(c.evs + f32(a*a))
                if s.n % 2 == 0 and len(c.buf) < RH_MAX: c.buf.append(w)
            s.n += 1
        if s.inev and not ev:
            s.inev = False; s.since = 0
            if s.valid and s.n > 0:
                amp = f32(0); rh = f32(-9)
                for c, b in zip(s.ch, s.b0):
                    r = f32(np.sqrt(f32(f32(c.evs / f32(s.n)) / b))) if b > 0 else f32(0)
                    if r > amp: amp = r
                    q = rhythm(c.buf)
                    if q > rh: rh = q
                out = (float(amp), float(rh), s.n)
        for c, (a, w) in zip(s.ch, ys): c.sec = f32(c.sec + f32(a*a))
        s.k += 1
        if s.k % FS == 0:
            clean = (not s.inev) and s.since >= GUARD_S
            for c in s.ch:
                old, ok = c.ring[s.pos], c.ok[s.pos]
                if ok:
                    if c.nb < WARM_S:
                        c.nb += 1
                        c.base = f32(c.base + f32(f32(old - c.base) / f32(c.nb)))
                    else:
                        if c.nb < 30000: c.nb += 1
                        c.base = f32(c.base + f32(f32(old - c.base) / f32(TAU_S)))
                c.ring[s.pos] = f32(c.sec / f32(FS)); c.ok[s.pos] = clean
                c.sec = f32(0)
            s.pos = (s.pos + 1) % GUARD_S
            if not s.inev: s.since += 1
        return out

def prepare(rib, abd, ev):
    n = len(rib)
    s = max(np.abs(rib).max(), np.abs(abd).max()) / 30000.0 or 1.0
    ri = np.round(rib / s).astype(np.int16); ai = np.round(abd / s).astype(np.int16)
    flag = np.zeros(n, np.uint8)
    for t, d, ty in ev: flag[max(0, int(t*FS)):min(n, int((t+d)*FS))] = 1
    return ri, ai, flag

def scored_events(ev, n):
    out = []
    for t, d, ty in ev:
        if ty not in ('APNEA-O', 'APNEA-C'): continue
        i0, i1 = int(t*FS), int((t+d)*FS)
        if i0 < 40*FS or i1 + 12*FS > n or i1 - i0 < 4*FS: continue
        out.append((i0, i1, ty[-1]))
    return out

def match(results, scored):
    res = sorted(results)
    rows = []
    for i0, i1, lab in scored:
        cand = [r for r in res if r[0] >= i1 - 1 and r[0] - r[3] <= i0]
        rows.append((i0, i1, lab, cand[0] if cand else None))
    return rows
