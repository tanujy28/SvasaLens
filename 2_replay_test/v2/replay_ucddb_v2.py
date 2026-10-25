import sys, os, time, glob, struct, math
import numpy as np
import serial, serial.tools.list_ports
from ucddb_io import read_edf, parse_events
from chipref_v2 import Detector, prepare, scored_events, match, FS

HELDOUT = ['ucddb003', 'ucddb005', 'ucddb007', 'ucddb008', 'ucddb010', 'ucddb012',
           'ucddb015', 'ucddb017', 'ucddb020', 'ucddb021', 'ucddb023', 'ucddb024',
           'ucddb026', 'ucddb027']
RULE = (1.0999081134796143, 2.603668451309204, 5.435161113739014)
LAPTOP_FULL_MODEL = 'v1 chip on the same 14 nights: balanced 74.6%, AUC 0.856 (laptop 6-feature model: 77.3%, AUC 0.872)'
BLOCK = 32

def zscore(e, r):
    return RULE[0] + RULE[1] * math.log(max(e, 1e-3)) + RULE[2] * r

def hexf(h):
    return struct.unpack('>f', bytes.fromhex(h))[0]

def auc(s, l):
    s, l = np.asarray(s, float), np.asarray(l, bool)
    order = np.argsort(s, kind='mergesort'); r = np.empty(len(s)); r[order] = np.arange(1, len(s) + 1)
    for v in np.unique(s):
        m = s == v
        if m.sum() > 1: r[m] = r[m].mean()
    n1 = l.sum(); n0 = len(l) - n1
    return float((r[l].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

def bal(pred, l):
    pred, l = np.asarray(pred, bool), np.asarray(l, bool)
    return 0.5 * (pred[l].mean() + (~pred[~l]).mean())

def open_nano():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if args:
        port = args[0]
    else:
        ports = list(serial.tools.list_ports.comports())
        if not ports: sys.exit('No serial port. Is the Nano plugged in?')
        port = ports[0].device if len(ports) == 1 else \
            ports[int(input('\n'.join(f'  [{i}] {p.device} {p.description}' for i, p in enumerate(ports)) + '\nNumber of the Nano: '))].device
    try:
        ser = serial.Serial(port, 250000, timeout=5)
    except serial.SerialException as e:
        sys.exit(f'Could not open {port}: {e}\nClose the Arduino Serial Monitor and try again.')
    t0 = time.time()
    while time.time() - t0 < 8:
        line = ser.readline().decode('ascii', 'ignore').strip()
        if line.startswith('READY'):
            print(f'{port}: {line}')
            if 'v2' not in line: sys.exit('This is the OLD replay sketch. Upload effort_replay_v2.ino first.')
            return ser
    sys.exit('The Nano did not say READY. Is effort_replay_v2.ino uploaded?')

def run_night(ser, ri, ai, fl):
    ser.write(b'S')
    if ser.readline().decode().strip() != 'OK': sys.exit('Nano did not reply OK')
    res, n = [], len(ri)
    t0 = time.time()
    for s in range(0, n, BLOCK):
        m = min(BLOCK, n - s)
        payload = b''.join(struct.pack('<hhB', int(ri[i]), int(ai[i]), int(fl[i])) for i in range(s, s + m))
        ser.write(b'B' + bytes([m]) + payload)
        while True:
            line = ser.readline().decode('ascii', 'ignore').strip()
            if not line: sys.exit(f'Nano stopped answering at sample {s}. Replug and retry.')
            if line == 'K': break
            if line.startswith('ERR'): sys.exit('Nano: ' + line)
            if line.startswith('E,'):
                _, idx, he, hr, cnt, dec = line.split(',')
                res.append((int(idx) - 1, he.lower().zfill(8), hr.lower().zfill(8), int(cnt), dec))
        if s % (BLOCK * 400) == 0:
            done = (s + m) / n
            el = time.time() - t0
            print(f'\r   {100*done:5.1f}%  ({el:.0f} s)', end='', flush=True)
    print(f'\r   100.0%  ({time.time()-t0:.0f} s)')
    return res

def main():
    here = os.getcwd()
    nights = sorted(os.path.basename(p)[:8] for p in glob.glob('ucddb*.rec'))
    if not nights: sys.exit('No ucddb*.rec files here. Run this from D:\\files')
    if '--all' in sys.argv: todo = nights
    elif '--quick' in sys.argv: todo = ['ucddb003']
    else: todo = [s for s in HELDOUT if s in nights]
    ser = open_nano()
    rows, n_ev, n_exact, n_agree = [], 0, 0, 0
    t_start = time.time()
    for k, sid in enumerate(todo):
        print(f'[{k+1}/{len(todo)}] {sid}')
        sig, rate, st = read_edf(f'{sid}.rec', {'ribcage', 'abdo'})
        ev = parse_events(f'{sid}_respevt.txt', st)
        n = min(len(sig['ribcage']), len(sig['abdo']))
        ri, ai, fl = prepare(sig['ribcage'][:n], sig['abdo'][:n], ev)
        det, ref = Detector(), {}
        for i in range(n):
            o = det.feed(int(ri[i]), int(ai[i]), bool(fl[i]))
            if o: ref[i] = (struct.pack('>f', o[0]).hex(), struct.pack('>f', o[1]).hex(), o[2])
        chip = run_night(ser, ri, ai, fl)
        for idx, he, hr, cnt, dec in chip:
            n_ev += 1
            r = ref.get(idx)
            if r and r[0] == he and r[1] == hr and r[2] == cnt: n_exact += 1
            if r and (zscore(hexf(r[0]), hexf(r[1])) > 0) == (dec == 'OBSTRUCTIVE'): n_agree += 1
        results = [(idx, hexf(he), hexf(hr), cnt) for idx, he, hr, cnt, _ in chip]
        decs = {idx: dec for idx, _, _, _, dec in chip}
        for i0, i1, lab, m in match(results, scored_events(ev, n)):
            if m is None: continue
            rows.append((sid, i0 / FS, (i1 - i0) / FS, lab, m[1], m[2], decs[m[0]]))
        print(f'   chip events {len(chip)}, laptop events {len(ref)}')
    ser.close()

    S = np.array([r[0] for r in rows]); Y = np.array([r[3] == 'O' for r in rows])
    E = np.array([r[4] for r in rows]); R = np.array([r[5] for r in rows])
    P = np.array([r[6] == 'OBSTRUCTIVE' for r in rows])
    Z = np.array([zscore(e, r) for e, r in zip(E, R)])
    L = [f'SvasaLens replay test, detector v2  ({time.strftime("%Y-%m-%d %H:%M")}, {(time.time()-t_start)/60:.0f} min)',
         f'nights: {", ".join(todo)}', '',
         'DOES THE CHIP COMPUTE THE SAME NUMBERS AS THE LAPTOP?',
         f'  events computed by the chip: {n_ev}',
         f'  bit-for-bit identical to the laptop: {n_exact} ({100*n_exact/max(n_ev,1):.1f}%)',
         f'  same OBSTRUCTIVE/CENTRAL decision:   {n_agree} ({100*n_agree/max(n_ev,1):.1f}%)', '',
         'HOW WELL DOES THE CHIP CLASSIFY REAL APNEAS?  (doctor labels)',
         f'  scored apneas: {len(Y)}  (obstructive {Y.sum()}, central {(~Y).sum()})']
    if Y.any() and (~Y).any():
        L += [f'  accuracy {100*(P == Y).mean():.1f}%   balanced accuracy {100*bal(P, Y):.1f}%   AUC {auc(Z, Y):.3f}',
              f'  obstructive caught {100*P[Y].mean():.1f}%   central caught {100*(~P[~Y]).mean():.1f}%']
        subs = np.unique(S)
        if len(subs) > 3:
            rng = np.random.default_rng(0); bs = []
            for _ in range(2000):
                pick = rng.choice(subs, len(subs))
                idx = np.concatenate([np.flatnonzero(S == s) for s in pick])
                if Y[idx].all() or (~Y[idx]).all(): continue
                bs.append(bal(P[idx], Y[idx]))
            lo, hi = np.percentile(bs, [2.5, 97.5])
            L.append(f'  balanced accuracy 95% CI (resampling nights): {100*lo:.1f}% to {100*hi:.1f}%')
        L += ['', f'  for comparison, {LAPTOP_FULL_MODEL}']
    L += ['', 'Rule inside the chip (fixed from the 11 training nights before this test):',
          f'  z = {RULE[0]:.4f} + {RULE[1]:.4f} x ln(effort) + {RULE[2]:.4f} x rhythm ; z > 0 means OBSTRUCTIVE',
          '', 'The event start/stop comes from the recorded airflow labels, standing in',
          'for the airflow channel. The chip decides the TYPE from effort and rhythm.']
    text = '\n'.join(L); print('\n' + text)
    open('replay_v2_results.txt', 'w').write(text + '\n')
    with open('replay_v2_events.csv', 'w') as fh:
        fh.write('night,start_s,duration_s,label,chip_effort,chip_rhythm,chip_decision\n')
        for r in rows: fh.write(f'{r[0]},{r[1]:.1f},{r[2]:.1f},{r[3]},{r[4]:.5f},{r[5]:.5f},{r[6]}\n')
    print('\nwrote replay_v2_results.txt and replay_v2_events.csv')

if __name__ == '__main__':
    main()
