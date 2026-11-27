"""SvasaLens V2 - 10-minute daytime simulated-apnea test (one person per run).

The laptop is plugged into the device by USB. This window tells the volunteer what to do, sends the
start of each breath-hold to the device (so it is marked in the device's own file), keeps a full copy
of the readings, and scores the device at the end. Results for everyone collect in the 'results' folder;
summarise_daytime.py turns them into the headline numbers.

    python svasalens_daytime_test.py              (finds the device's USB port by itself)
    python svasalens_daytime_test.py --port COM5
    python svasalens_daytime_test.py --simulate   (practice run with no device)

Test for each person (about 10 minutes including fitting):
    30 s   calibration: lie still, breathe normally
    2 min  normal breathing
    6 x    10 s breath-hold, then 50 s normal breathing
           3 RELAXED holds (no effort: like a central apnea) and 3 TRY holds (mouth closed, nose pinched,
           trying to breathe in: like an obstructive apnea), in a mixed order that is different for each person
Needs: pip install pyserial
"""
import argparse, csv, os, queue, random, sys, threading, time
from datetime import datetime
import tkinter as tk

# ---------------------------------------------------------------- settings (fixed before testing starts)
NORMAL_S = 120        # normal breathing before the first hold
HOLD_S = 10           # each breath-hold
REST_S = 50           # normal breathing after each hold (the typing network looks 60 s after the start)
LAST_REST_S = 60      # after the last hold
READY_S = 5           # 'get ready' warning before each hold
MATCH_AFTER_S = 5     # a device event counts for a hold if it overlaps the hold or the 5 s after it
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')

TEXT = {
    'calib': ('Lie still and breathe normally', 'स्थिर लेटें और सामान्य रूप से साँस लें'),
    'normal': ('Breathe normally', 'सामान्य रूप से साँस लें'),
    'HOLD': ('RELAXED HOLD: breathe out gently, then stop.\nStay relaxed. Do not push.',
             'साँस धीरे से छोड़ें, फिर रोकें।\nशरीर ढीला रखें, ज़ोर न लगाएँ।'),
    'TRY': ('TRY: close your mouth, pinch your nose,\nand keep trying to breathe in.',
            'मुँह बंद करें, नाक दबाएँ,\nऔर साँस लेने की कोशिश करते रहें।'),
    'readyHOLD': ('Get ready: RELAXED HOLD', 'तैयार हो जाएँ: आराम से साँस रोकना'),
    'readyTRY': ('Get ready: TRY', 'तैयार हो जाएँ: रुकी हुई साँस लेने की कोशिश'),
    'done': ('Finished. Thank you!', 'हो गया। धन्यवाद!'),
}


def hold_order(person):
    """3 RELAXED + 3 TRY in a mixed order, never 3 of the same kind in a row. Same person ID = same order."""
    rng = random.Random(person)
    while True:
        o = ['HOLD'] * 3 + ['TRY'] * 3
        rng.shuffle(o)
        if not any(o[i] == o[i + 1] == o[i + 2] for i in range(4)):
            return o


def timeline(order):
    """List of (start_s, end_s, phase) from the start of recording."""
    t, out = 0, [(0, NORMAL_S - READY_S, 'normal')]
    t = NORMAL_S - READY_S
    for k, kind in enumerate(order):
        out.append((t, t + READY_S, 'ready' + kind)); t += READY_S
        out.append((t, t + HOLD_S, kind)); t += HOLD_S
        rest = LAST_REST_S if k == len(order) - 1 else REST_S
        out.append((t, t + rest - (0 if k == len(order) - 1 else READY_S), 'normal'))
        t += rest - (0 if k == len(order) - 1 else READY_S)
    return out


# ---------------------------------------------------------------- scoring (the rule is fixed before testing)
def score(cues, events):
    """cues: list of dicts kind/start/end/valid. events: list of dicts start/end/type from the device.
    Each hold is matched to at most one device event and the other way round (largest overlap first).
    A match needs at least 1 s of overlap with the hold or the MATCH_AFTER_S seconds after it."""
    pairs = []
    for i, c in enumerate(cues):
        for j, e in enumerate(events):
            o = min(c['end'] + MATCH_AFTER_S, e['end']) - max(c['start'], e['start'])
            if o >= 1: pairs.append((o, i, j))
    pairs.sort(reverse=True)
    ci, ej = {}, set()
    for o, i, j in pairs:
        if i in ci or j in ej: continue
        ci[i] = j; ej.add(j)
    for i, c in enumerate(cues):
        e = events[ci[i]] if i in ci else None
        c['detected'] = e is not None
        c['device_type'] = e['type'] if e else ''
        want = 'CENTRAL' if c['kind'] == 'HOLD' else 'OBSTRUCTIVE'
        c['type_right'] = bool(e) and e['type'] == want
    false = [e for j, e in enumerate(events) if j not in ej]
    return cues, false


# ---------------------------------------------------------------- device link
class Device(threading.Thread):
    def __init__(s, port):
        super().__init__(daemon=True)
        import serial
        s.ser = serial.Serial(port, 115200, timeout=0.2)
        s.q = queue.Queue()
    def run(s):
        buf = b''
        while True:
            try: buf += s.ser.read(512)
            except Exception as ex: s.q.put(f'# SERIAL ERROR {ex}'); return
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                s.q.put(line.decode(errors='ignore').strip())
    def send(s, c): s.ser.write(c.encode())


class FakeDevice(threading.Thread):
    """Practice mode: makes up readings and finds the holds (with a mistake now and then)."""
    def __init__(s):
        super().__init__(daemon=True); s.q = queue.Queue(); s.cmd = queue.Queue()
    def send(s, c): s.cmd.put(c)
    def run(s):
        import math
        rec, t, cue, cue_t, kind, n, rng = False, 0.0, None, 0, '', 0, random.Random(1)
        s.q.put('# SELF-TEST PASS (practice mode)')
        while True:
            try:
                c = s.cmd.get(timeout=0.125)
                if c == 's' and not rec:
                    s.q.put('# calibrating 30 s'); time.sleep(3); rec, t = True, 0.0
                    s.q.put('# recording. Hold the button 3 s (or type s) to stop.')
                elif c == 's' and rec:
                    s.q.put('# recorded ... '); s.q.put('# stopped and saved. Press the button for the next patient.'); rec = False
                elif c in 'ht' and rec:
                    cue, cue_t, kind = c, t, 'HOLD' if c == 'h' else 'TRY'; s.q.put(f'CUE,{t:.3f},{kind}')
                elif c == 'e' and rec:
                    s.q.put(f'CUE,{t:.3f},END')
                    if rng.random() < 0.85:
                        n += 1; typ = ('CENTRAL' if cue == 'h' else 'OBSTRUCTIVE') if rng.random() < 0.8 else ('OBSTRUCTIVE' if cue == 'h' else 'CENTRAL')
                        a = int(cue_t) + 2
                        s.q.put(f'EVENT,{n},{a},{a + 10},10,{typ},0.1,0.9,00:00:{a % 60:02d}')
                    cue = None
            except queue.Empty:
                pass
            if rec:
                for _ in range(1):
                    br = 0 if cue == 'h' else (0.3 if cue == 't' else 1)
                    v = br * math.sin(2 * math.pi * 0.25 * t)
                    pr = 0.9 if cue else 0.05
                    s.q.put(f'R,{t:.3f},{1500 + 40 * v:.1f},{5 * v:.2f},{6 * v * (-1 if cue == "t" else 1):.2f},0,0,1000,0,0,1000,{pr if t % 1 > 0.85 else -1:.3f},0,0')
                    t += 0.125


# ---------------------------------------------------------------- the window
class App:
    def __init__(s, root, dev, port_name):
        s.root, s.dev, s.port_name = root, dev, port_name
        root.title('SvasaLens daytime test'); root.configure(bg='white')
        root.geometry('1100x700')
        s.big = tk.Label(root, text='', font=('Segoe UI', 34, 'bold'), bg='white', wraplength=1050, justify='center')
        s.hindi = tk.Label(root, text='', font=('Nirmala UI', 26), bg='white', fg='#555', wraplength=1050, justify='center')
        s.count = tk.Label(root, text='', font=('Segoe UI', 70, 'bold'), bg='white', fg='#1F6F78')
        s.info = tk.Label(root, text='', font=('Consolas', 13), bg='white', fg='#333', justify='left')
        s.form = tk.Frame(root, bg='white')
        s.big.pack(pady=(40, 10)); s.hindi.pack(); s.count.pack(pady=10); s.info.pack(pady=10); s.form.pack()
        root.bind('<Escape>', lambda e: s.abort()); root.bind('x', lambda e: s.mark_bad()); root.bind('X', lambda e: s.mark_bad())
        s.stage = 'form'; s.lines = []; s.cues = []; s.events = []; s.raw = []; s.selftest = ''
        s.sig = {'chest': [], 'belly': [], 'therm': []}
        s.ask_person()
        root.after(50, s.poll)

    # ---- before the test
    def ask_person(s):
        for w in s.form.winfo_children(): w.destroy()
        s.big.config(text='SvasaLens daytime test'); s.hindi.config(text=''); s.count.config(text='')
        s.info.config(text=f'Device: {s.port_name}\nFit the bands and nose clip first. Person lies on their back.')
        f = s.form; s.vars = {}
        for r, (lab, key) in enumerate([('Person ID (no names, e.g. P07)', 'pid'), ('Age', 'age'), ('Sex (M/F/other)', 'sex'), ('Device number', 'dev')]):
            tk.Label(f, text=lab, font=('Segoe UI', 13), bg='white').grid(row=r, column=0, sticky='e', padx=6, pady=4)
            v = tk.StringVar(value='1' if key == 'dev' else ''); tk.Entry(f, textvariable=v, font=('Segoe UI', 13), width=18).grid(row=r, column=1, pady=4)
            s.vars[key] = v
        s.consent = tk.BooleanVar()
        tk.Checkbutton(f, text='Signed consent form collected, and the person has no heart or lung problems and is not pregnant',
                       variable=s.consent, font=('Segoe UI', 12), bg='white', wraplength=700).grid(row=5, column=0, columnspan=2, pady=8)
        tk.Button(f, text='Start test', font=('Segoe UI', 16, 'bold'), bg='#1F6F78', fg='white', command=s.start).grid(row=6, column=0, columnspan=2, pady=10)
        s.err = tk.Label(f, text='', fg='#B00020', bg='white', font=('Segoe UI', 12)); s.err.grid(row=7, column=0, columnspan=2)

    def start(s):
        pid = s.vars['pid'].get().strip().upper()
        if not pid: s.err.config(text='Enter a person ID first'); return
        if not s.consent.get(): s.err.config(text='Tick the consent box first'); return
        os.makedirs(RESULTS, exist_ok=True)
        if os.path.exists(os.path.join(RESULTS, f'{pid}_cues.csv')): s.err.config(text=f'{pid} has already been tested. Use a new ID.'); return
        s.pid = pid; s.order = hold_order(pid); s.plan = timeline(s.order)
        for w in s.form.winfo_children(): w.destroy()
        s.t_start = None; s.stage = 'calib'; s.cur = None; s.bad = set()
        s.dev.send('v'); time.sleep(0.1); s.dev.send('s')
        s.show('calib'); s.count.config(text='30')
        s.calib_t = time.monotonic()

    # ---- during the test
    def show(s, key):
        en, hi = TEXT[key]; s.big.config(text=en); s.hindi.config(text=hi)
        s.big.config(fg='#B00020' if key in ('HOLD', 'TRY') else ('#C77700' if key.startswith('ready') else '#222'))

    def beep(s, f=1000, ms=150):
        try:
            import winsound; threading.Thread(target=winsound.Beep, args=(f, ms), daemon=True).start()
        except Exception:
            s.root.bell()

    def poll(s):
        try:
            while True:
                line = s.dev.q.get_nowait(); s.handle(line)
        except queue.Empty:
            pass
        s.tick()
        s.root.after(50, s.poll)

    def handle(s, line):
        s.lines.append(line)
        if line.startswith('# SELF-TEST'): s.selftest = line
        if line.startswith('R,'):
            p = line.split(',')
            s.raw.append(p[1:])
            if s.stage == 'run':
                s.dev_t = float(p[1])
                for k, i in (('therm', 2), ('chest', 3), ('belly', 4)):
                    s.sig[k].append(float(p[i])); s.sig[k] = s.sig[k][-240:]
        elif line.startswith('CUE,'):
            _, t, kind = line.split(',')
            if kind in ('HOLD', 'TRY'): s.cues.append({'kind': kind, 'start': float(t), 'end': None, 'valid': True})
            elif s.cues: s.cues[-1]['end'] = float(t)
        elif line.startswith('EVENT,'):
            p = line.split(',')
            s.events.append({'n': int(p[1]), 'start': float(p[2]), 'end': float(p[3]), 'type': p[5], 'score': p[6], 'peak': p[7]})
        elif line.startswith('# recording.') and s.stage == 'calib':
            s.stage = 'run'; s.t_start = time.monotonic(); s.cur = -1
        elif line.startswith('# stopped') and s.stage in ('stopping', 'abort'):
            s.finish()
        elif line.startswith('# chest sensor') and 'MISSING' in line:
            s.big.config(text='Sensor problem: ' + line[2:], fg='#B00020'); s.stage = 'form'

    def tick(s):
        if s.stage == 'calib':
            left = 30 - (time.monotonic() - s.calib_t); s.count.config(text=f'{max(0, left):.0f}')
            return
        if s.stage != 'run': return
        el = time.monotonic() - s.t_start
        i = next((k for k, (a, b, ph) in enumerate(s.plan) if a <= el < b), None)
        if i is None:
            s.stage = 'stopping'; s.dev.send('s'); s.show('done'); s.count.config(text=''); s.info.config(text='Saving...'); return
        a, b, ph = s.plan[i]
        if i != s.cur:
            prev = s.plan[s.cur][2] if s.cur >= 0 else ''
            if prev in ('HOLD', 'TRY'): s.dev.send('e'); s.beep(600, 400)
            if ph in ('HOLD', 'TRY'): s.dev.send('h' if ph == 'HOLD' else 't'); s.beep(1300, 200)
            if ph.startswith('ready'): s.beep(900, 120)
            s.cur = i; s.show(ph)
        s.count.config(text=f'{b - el:.0f}')
        n_done = sum(1 for (_, _, p) in s.plan[:i + 1] if p in ('HOLD', 'TRY'))
        s.info.config(text=f'Person {s.pid}   hold {n_done}/6   time {el / 60:4.1f} / {s.plan[-1][1] / 60:.1f} min\n'
                           f'{s.signal_check()}\nKeys: X = last hold not done properly   Esc = stop the test')

    def signal_check(s):
        """Breathing swing over the last 30 s, as a rough guide that every sensor is working."""
        out = []
        for k, lim in (('chest', 1.0), ('belly', 1.0), ('therm', 5.0)):
            v = s.sig[k]
            if len(v) < 80: out.append(f'{k}: ...'); continue
            sw = max(v) - min(v)
            out.append(f'{k}: {sw:6.1f} {"OK" if sw > lim else "WEAK - check"}')
        return '   '.join(out)

    def mark_bad(s):
        if s.stage == 'run' and s.cues:
            s.cues[-1]['valid'] = False
            s.info.config(text=s.info.cget('text') + f'\nHold {len(s.cues)} marked as not done properly')

    def abort(s):
        if s.stage in ('run', 'calib'):
            s.stage = 'abort'; s.dev.send('s'); s.big.config(text='Test stopped', fg='#B00020'); s.hindi.config(text='')

    # ---- after the test
    def finish(s):
        aborted = s.stage == 'abort'
        cues = [c for c in s.cues if c['end'] is not None]
        cues, false = score(cues, s.events)
        s.result = dict(cues=cues, false=false, aborted=aborted)
        s.save_files()
        ok = [c for c in cues if c['valid']]
        found = sum(c['detected'] for c in ok); right = sum(c['type_right'] for c in ok)
        lines = [f'{i + 1}. {c["kind"]:<5} {"found" if c["detected"] else "MISSED":<7} {c["device_type"]:<12} '
                 f'{"right" if c["type_right"] else ("wrong type" if c["detected"] else "")}{"" if c["valid"] else "   (not done properly)"}'
                 for i, c in enumerate(cues)]
        s.stage = 'feedback'
        s.big.config(text=f'{s.pid}: found {found}/{len(ok)}, right type {right}/{len(ok)}, false alarms {len(false)}', fg='#1F6F78')
        s.hindi.config(text=''); s.count.config(text='')
        s.info.config(text='\n'.join(lines) + ('\n\nTEST WAS STOPPED EARLY' if aborted else ''))
        f = s.form
        tk.Label(f, text='Comfort (1 = very uncomfortable, 5 = did not notice it)', font=('Segoe UI', 13), bg='white').grid(row=0, column=0, sticky='e')
        s.comfort = tk.StringVar(); tk.Entry(f, textvariable=s.comfort, width=6, font=('Segoe UI', 13)).grid(row=0, column=1, sticky='w')
        tk.Label(f, text='Did anything slip or need fixing?', font=('Segoe UI', 13), bg='white').grid(row=1, column=0, sticky='e')
        s.slip = tk.StringVar(); tk.Entry(f, textvariable=s.slip, width=40, font=('Segoe UI', 13)).grid(row=1, column=1)
        tk.Label(f, text='Other notes', font=('Segoe UI', 13), bg='white').grid(row=2, column=0, sticky='e')
        s.notes = tk.StringVar(); tk.Entry(f, textvariable=s.notes, width=40, font=('Segoe UI', 13)).grid(row=2, column=1)
        tk.Button(f, text='Save and next person', font=('Segoe UI', 15, 'bold'), bg='#1F6F78', fg='white', command=s.save_feedback).grid(row=3, column=0, columnspan=2, pady=10)

    def save_files(s):
        p = os.path.join(RESULTS, s.pid)
        with open(p + '_raw.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t_s', 'therm_mV', 'chest', 'belly', 'chest_x', 'chest_y', 'chest_z', 'belly_x', 'belly_y', 'belly_z', 'apnea_chance', 'err', 'cue']); w.writerows(s.raw)
        with open(p + '_device_log.txt', 'w', encoding='utf-8') as f: f.write('\n'.join(l for l in s.lines if not l.startswith('R,')))
        with open(p + '_cues.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['person', 'hold', 'kind', 'start_s', 'end_s', 'done_properly', 'found', 'device_type', 'type_right'])
            for i, c in enumerate(s.result['cues']):
                w.writerow([s.pid, i + 1, c['kind'], f'{c["start"]:.2f}', f'{c["end"]:.2f}', int(c['valid']), int(c['detected']), c['device_type'], int(c['type_right'])])
        with open(p + '_events.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['event', 'start_s', 'end_s', 'type', 'type_score', 'peak_chance', 'false_alarm'])
            fa = set(id(e) for e in s.result['false'])
            for e in s.events: w.writerow([e['n'], e['start'], e['end'], e['type'], e['score'], e['peak'], int(id(e) in fa)])

    def save_feedback(s):
        cues = s.result['cues']; held = sum((c['end'] - c['start']) + MATCH_AFTER_S for c in cues)
        total = float(s.raw[-1][0]) if s.raw else 0
        new = not os.path.exists(os.path.join(RESULTS, 'people.csv'))
        with open(os.path.join(RESULTS, 'people.csv'), 'a', newline='') as f:
            w = csv.writer(f)
            if new: w.writerow(['person', 'date', 'device', 'age', 'sex', 'order', 'stopped_early', 'holds', 'found', 'type_right',
                                'false_alarms', 'normal_breathing_min', 'comfort_1to5', 'slipped', 'notes', 'self_test'])
            ok = [c for c in cues if c['valid']]
            w.writerow([s.pid, datetime.now().strftime('%Y-%m-%d %H:%M'), s.vars['dev'].get(), s.vars['age'].get(), s.vars['sex'].get(),
                        ' '.join(s.order), int(s.result['aborted']), len(ok), sum(c['detected'] for c in ok), sum(c['type_right'] for c in ok),
                        len(s.result['false']), f'{max(0, total - held) / 60:.2f}', s.comfort.get(), s.slip.get(), s.notes.get(), s.selftest[2:]])
        s.cues, s.events, s.raw, s.lines = [], [], [], []
        s.sig = {'chest': [], 'belly': [], 'therm': []}
        s.stage = 'form'; s.ask_person()


def find_port():
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == 0x303A or 'ESP32' in (p.description or '') or 'USB JTAG' in (p.description or ''): return p.device
    if len(ports) == 1: return ports[0].device
    sys.exit('Could not find the device. Plug it in, or give the port, e.g. --port COM5. Ports seen: ' + ', '.join(p.device for p in ports))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--port'); ap.add_argument('--simulate', action='store_true')
    a = ap.parse_args()
    if a.simulate:
        RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results_practice')
        dev, name = FakeDevice(), 'practice mode (no device)'
    else:
        port = a.port or find_port(); dev, name = Device(port), port
    dev.start()
    root = tk.Tk(); App(root, dev, name); root.mainloop()
