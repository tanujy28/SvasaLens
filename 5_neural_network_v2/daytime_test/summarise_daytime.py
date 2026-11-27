"""Headline numbers for the SvasaLens daytime test.

1. Copy the SD card files into one folder (the DEVx_Txxx_result.csv files are the ones used).
2. Fill in record_sheet.csv in the same folder (one row per person: which test file, comfort, etc.).
3. python summarise_daytime.py FOLDER      -> prints the results and writes FOLDER/summary.txt

Main measure (fixed before testing): the share of breath-holds the device FOUND and gave the RIGHT TYPE
(relaxed hold -> central, try -> obstructive). Holds marked 'not done properly' are left out, and the number
left out is reported. Ranges are 95% Wilson intervals."""
import csv, glob, math, os, re, sys
from collections import Counter


def wilson(k, n, z=1.96):
    if n == 0: return float('nan'), float('nan')
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, c - h), min(1, c + h)


def line(name, k, n):
    lo, hi = wilson(k, n)
    return f'{name:<44} {k:>4} of {n:<4} {100 * k / max(n, 1):5.1f}%   (95% range {100 * lo:.0f}-{100 * hi:.0f}%)'


def read_result(f):
    """One DEVx_Txxx_result.csv -> (test name, hold rows, summary dict)."""
    name = re.sub(r'_result\.csv$', '', os.path.basename(f))
    rows, summ = [], None
    lines = [l.strip() for l in open(f, encoding='utf-8', errors='ignore') if l.strip()]
    hdr = None
    for l in lines:
        if l.startswith('hold,'): hdr = l.split(','); continue
        if l.startswith('SUMMARY,'):
            p = l.split(',')
            summ = dict(holds=int(p[3]), found=int(p[4]), right=int(p[5]), false_alarms=int(p[6]),
                        normal_min=float(p[7]), stopped_early=p[8] == '1')
            continue
        if hdr and not l.startswith('#'):
            rows.append(dict(zip(hdr, l.split(','))))
    return name, rows, summ


def main(folder):
    files = sorted(glob.glob(os.path.join(folder, '**', '*_T*_result.csv'), recursive=True))
    if not files: sys.exit(f'no DEVx_Txxx_result.csv files in {folder}')
    sheet = {}
    sp = os.path.join(folder, 'record_sheet.csv')
    if os.path.exists(sp):
        for r in csv.DictReader(open(sp, encoding='utf-8-sig')):
            if r.get('test_file', '').strip(): sheet[r['test_file'].strip().upper()] = r
    tests = [read_result(f) for f in files]
    holds = [dict(h, test=name) for name, rows, _ in tests for h in rows]
    ok = [h for h in holds if h['done_properly'] == '1']
    found = [h for h in ok if h['found'] == '1']
    right = [h for h in ok if h['type_right'] == '1']
    L = [f'SvasaLens daytime simulated-apnea test: {len(tests)} people, {len(holds)} breath-holds '
         f'({len(holds) - len(ok)} left out as not done properly)', '']
    L.append(line('MAIN: found AND right type', len(right), len(ok)))
    L.append(line('found (any type)', len(found), len(ok)))
    L.append(line('right type, of the holds found', len(right), len(found)))
    for kind, want in (('HOLD', 'central'), ('TRY', 'obstructive')):
        o = [h for h in ok if h['kind'] == kind]
        L.append(line(f'{kind} (should be {want}): found and right', sum(h['type_right'] == '1' for h in o), len(o)))
    per, tot = Counter(), Counter()
    for h in ok: tot[h['test']] += 1; per[h['test']] += h['type_right'] == '1'
    L.append(line('people with at least 4 of their holds right', sum(per[t] >= 4 for t in tot), len(tot)))
    summ = [s for _, _, s in tests if s]
    fa = sum(s['false_alarms'] for s in summ); mins = sum(s['normal_min'] for s in summ)
    L.append(f'{"false alarms during normal breathing":<44} {fa:>4} in {mins / 60:.1f} h = {fa / max(mins / 60, 1e-9):.1f} per hour')
    L.append(f'{"stopped early":<44} {sum(s["stopped_early"] for s in summ):>4} of {len(summ)}')
    if sheet:
        com = [int(r['comfort_1to5']) for r in sheet.values() if r.get('comfort_1to5', '').strip().isdigit()]
        if com: L.append(f'{"comfort (1-5)":<44} average {sum(com) / len(com):.1f}; {sum(c >= 4 for c in com)} of {len(com)} gave 4 or 5')
        slip = [r for r in sheet.values() if r.get('slipped_or_fixed', '').strip().lower() not in ('', 'no', 'n', 'none', '-')]
        L.append(f'{"needed fixing / slipped":<44} {len(slip)} of {len(sheet)}')
        missing = [name for name, _, _ in tests if name.upper().lstrip('/') not in sheet]
        if missing: L.append(f'(not in record_sheet.csv: {", ".join(missing)})')
    k, n = len(right), len(ok); lo, hi = wilson(k, n)
    L += ['', f'Headline: On {len(tests)} people, SvasaLens found and correctly typed {k} of {n} simulated apneas '
              f'({100 * k / max(n, 1):.0f}%, 95% range {100 * lo:.0f}-{100 * hi:.0f}%). '
              'These are simulated apneas (awake people holding their breath), not real sleep apnea.']
    out = '\n'.join(L); print(out)
    open(os.path.join(folder, 'summary.txt'), 'w').write(out + '\n')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
