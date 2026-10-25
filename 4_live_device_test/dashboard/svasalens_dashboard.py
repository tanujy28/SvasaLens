import argparse, collections, datetime, os, sys, threading, time, queue

import matplotlib
if not any(a.startswith('--snapshot') for a in sys.argv):
    matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

WINDOW_S = 60
FS = 8
GAUGE_MAX = 3.0

PAPER, INK, MUTED = '#ffffff', '#1f2933', '#243b53'
RULE, GRID_MAJ, GRID_MIN = '#cbd2d9', '#e4e7eb', '#f1f3f5'
TEAL, BRICK = '#1f4e79', '#9b2c2c'
CHEST, BELLY = '#1f4e79', '#4f6577'

def _res(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'fonts', name)

def _font(name, size):
    from matplotlib.font_manager import FontProperties
    path = _res(name)
    if os.path.exists(path):
        return FontProperties(fname=path, size=size)
    return FontProperties(family='DejaVu Sans', size=size)

def F_TITLE(sz):  return _font('SourceSans3-Semibold.ttf', sz)
def F_SERIF(sz):  return _font('SourceSans3-Regular.ttf', sz)
def F_BODY(sz):   return _font('SourceSans3-Regular.ttf', sz)
def F_BOLD(sz):   return _font('SourceSans3-Semibold.ttf', sz)
def F_MONO(sz):   return _font('SourceSans3-Regular.ttf', sz)
def F_MONOB(sz):  return _font('SourceSans3-Semibold.ttf', sz)

def serial_lines(port, out_path, q, stop, state):
    import serial, serial.tools.list_ports
    if port is None:
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            state['error'] = 'No device found. Plug in SvasaLens and restart.'
            return
        nano = [p for p in ports if any(s in (p.description or '') for s in ('CH340', 'USB', 'Arduino', 'Serial'))]
        port = (nano or ports)[0].device
    try:
        ser = serial.Serial(port, 115200, timeout=1)
    except Exception as e:
        state['error'] = f'Could not open {port}. Close the Arduino Serial Monitor.'
        return
    state['port'] = port
    with ser, open(out_path, 'w', newline='') as fh:
        while not stop.is_set():
            try:
                line = ser.readline().decode('ascii', 'ignore').strip()
            except Exception:
                state['error'] = 'Device disconnected.'
                return
            if line:
                fh.write(line + '\n'); fh.flush()
                q.put(line)

def file_lines(path, speed, q, stop, state=None):
    with open(path) as fh:
        lines = [l.strip() for l in fh if l.strip()]
    for line in lines:
        if stop.is_set():
            return
        q.put(line)
        if line[0].isdigit():
            time.sleep(1.0 / FS / speed)

def effort_words(e):
    if e < 0.35:
        return 'chest and belly almost still'
    if e < 0.7:
        return 'chest and belly still moving a little'
    return 'chest and belly still moving strongly'

def rhythm_words(r):
    if r < 0.2:
        return 'no regular breathing rhythm'
    if r < 0.5:
        return 'some breathing rhythm'
    return 'a clear breathing rhythm'

def scale_bar(ax, y, label, value, lo, hi, ticks, fmt, note):
    x0, x1 = 0.0, 1.0
    v = (min(max(value, lo), hi) - lo) / (hi - lo)
    ax.text(x0, y + 0.075, label, fontproperties=F_MONO(9.5), color=MUTED, va='bottom')
    ax.text(x1, y + 0.075, fmt.format(value), fontproperties=F_MONOB(12), color=INK, va='bottom', ha='right')
    ax.plot([x0, x1], [y, y], color=RULE, lw=1.2, solid_capstyle='butt')
    ax.plot([x0, x0 + (x1 - x0) * v], [y, y], color=INK, lw=2.4, solid_capstyle='butt')
    for t in ticks:
        xt = x0 + (x1 - x0) * (t - lo) / (hi - lo)
        ax.plot([xt, xt], [y - 0.012, y + 0.012], color=MUTED, lw=0.8)
        ax.text(xt, y - 0.025, f'{t:g}', fontproperties=F_MONO(8), color=MUTED, ha='center', va='top')
    ax.plot([x0 + (x1 - x0) * v], [y], marker='v', ms=8, color=INK, mec=INK)
    ax.text(x0, y - 0.075, note, fontproperties=F_BODY(11), color=INK, va='top')

class Screen:
    def __init__(self, q, state):
        self.q, self.state = q, state
        n = WINDOW_S * FS
        self.t, self.a, self.b, self.f = (collections.deque(maxlen=n) for _ in range(4))
        self.mean = [None, None]
        self.events = []
        self.t0 = None
        self.in_event = False
        self.started = 0
        self.live = 0.0
        self.done = False
        self.ev_marks = []

        plt.rcParams.update({'axes.edgecolor': RULE, 'xtick.color': MUTED, 'ytick.color': MUTED})
        self.fig = plt.figure(figsize=(14, 7.8), facecolor=PAPER)
        try:
            self.fig.canvas.manager.set_window_title('SvasaLens')
        except Exception:
            pass
        fig = self.fig

        fig.text(0.035, 0.915, 'SvasaLens', fontproperties=F_TITLE(30), color=INK)
        fig.text(0.215, 0.927, 'breathing effort monitor', fontproperties=F_SERIF(14), color=MUTED,
                 style='italic')
        self.clock = fig.text(0.965, 0.935, '', fontproperties=F_MONO(11), color=MUTED, ha='right')
        self.status = fig.text(0.965, 0.905, '', fontproperties=F_BOLD(13), color=INK, ha='right')
        self.dot = fig.text(0.0, 0.905, '■', fontsize=12, color=TEAL, ha='right')
        fig.add_artist(plt.Line2D([0.035, 0.965], [0.885, 0.885], color=INK, lw=1.4))

        self.axA = fig.add_axes([0.035, 0.595, 0.585, 0.245])
        self.axB = fig.add_axes([0.035, 0.31, 0.585, 0.245], sharex=self.axA)
        for ax, name, col in ((self.axA, 'CHEST', CHEST), (self.axB, 'BELLY', BELLY)):
            ax.set_facecolor(PAPER)
            for s in ax.spines.values():
                s.set_color(RULE); s.set_lw(0.8)
            ax.tick_params(length=0, labelsize=8)
            ax.set_yticks([])
            ax.set_title(name, loc='left', fontproperties=F_MONOB(10), color=col, pad=3)
        plt.setp(self.axA.get_xticklabels(), visible=False)
        for lbl in self.axB.get_xticklabels():
            lbl.set_fontproperties(F_MONO(8))
        self.axB.set_xlabel('time (s)', fontproperties=F_MONO(9), color=MUTED, labelpad=2)
        self.lineA, = self.axA.plot([], [], color=CHEST, lw=1.5, zorder=3)
        self.lineB, = self.axB.plot([], [], color=BELLY, lw=1.5, zorder=3)
        self.dyn = []

        fig.add_artist(plt.Line2D([0.655, 0.655], [0.30, 0.845], color=RULE, lw=0.8))
        self.card = fig.add_axes([0.675, 0.30, 0.29, 0.55]); self.card.axis('off')

        fig.add_artist(plt.Line2D([0.035, 0.965], [0.245, 0.245], color=INK, lw=1.0))
        self.hist = fig.add_axes([0.035, 0.03, 0.585, 0.20]); self.hist.axis('off')
        self.gauge = fig.add_axes([0.675, 0.03, 0.29, 0.20]); self.gauge.axis('off')

    def feed(self, line):
        if line.startswith('D,'):
            _, k, asked, e, r, z, dec = line.split(',')
            self.events.append((int(k), asked, float(e), float(r), float(z), dec))
        elif line.startswith('DONE'):
            self.done = True
        elif line[0].isdigit():
            p = line.split(',')
            if len(p) < 5:
                return
            t = int(p[0]) / 1000.0
            if self.t0 is None:
                self.t0 = t
            x = [float(p[1]), float(p[2])]
            flag = int(p[3]); self.live = float(p[4])
            for i in range(2):
                self.mean[i] = x[i] if self.mean[i] is None else self.mean[i] + (x[i] - self.mean[i]) / 40
                x[i] -= self.mean[i]
            if flag and not self.in_event:
                self.started += 1
                self.ev_marks.append((t - self.t0, self.started))
            self.in_event = bool(flag)
            self.t.append(t - self.t0); self.a.append(x[0]); self.b.append(x[1]); self.f.append(flag)

    def draw_signals(self):
        for d in self.dyn:
            d.remove()
        self.dyn = []
        if not self.t:
            return
        t = list(self.t)
        lo, hi = t[-1] - WINDOW_S, t[-1] + 1
        self.lineA.set_data(t, list(self.a)); self.lineB.set_data(t, list(self.b))
        self.axA.set_xlim(lo, hi)
        self.axB.set_xticks(range(int(lo // 10 + 1) * 10, int(hi) + 1, 10))
        for lbl in self.axB.get_xticklabels():
            lbl.set_fontproperties(F_MONO(8))
        for ax, ys in ((self.axA, self.a), (self.axB, self.b)):
            m = max(3.0, max(abs(v) for v in ys) * 1.2)
            ax.set_ylim(-m, m)
            for s in range(int(lo), int(hi) + 1):
                major = s % 5 == 0
                self.dyn.append(ax.axvline(s, color=GRID_MAJ if major else GRID_MIN,
                                           lw=0.9 if major else 0.6, zorder=0))
            for yy in (-m / 2, 0, m / 2):
                self.dyn.append(ax.axhline(yy, color=GRID_MIN, lw=0.6, zorder=0))
        f, start = list(self.f), None
        bands = []
        for i, v in enumerate(f + [0]):
            if v and start is None:
                start = t[i]
            if not v and start is not None:
                bands.append((start, t[i - 1])); start = None
        for s0, s1 in bands:
            for ax in (self.axA, self.axB):
                self.dyn.append(ax.axvspan(s0, s1, fc='none', ec=BRICK, hatch='////', lw=0, alpha=0.25, zorder=1))
            no = next((n for ts, n in self.ev_marks if abs(ts - s0) < 0.3), None)
            asked = 'hold' if no and no % 2 == 1 else 'try'
            if no:
                self.dyn.append(self.axA.text(s0 + 0.3, self.axA.get_ylim()[1] * 0.97, f'event {no} · {asked}',
                                              fontproperties=F_MONO(8.5), color=BRICK, va='top', zorder=6))

    def draw_status(self):
        if self.state.get('error'):
            s, c = self.state['error'], BRICK
        elif self.done:
            s, c = 'Session finished', INK
        elif not self.t:
            s, c = 'Waiting for the device', MUTED
        elif self.in_event:
            s, c = f'Event {self.started} in progress', BRICK
        elif self.started == 0:
            s, c = 'Learning normal breathing', TEAL
        else:
            s, c = 'Normal breathing', TEAL
        self.status.set_text(s)
        r = self.fig.canvas.get_renderer() if hasattr(self.fig.canvas, 'get_renderer') else None
        try:
            bb = self.status.get_window_extent(renderer=r).transformed(self.fig.transFigure.inverted())
            self.dot.set_x(bb.x0 - 0.006)
        except Exception:
            self.dot.set_x(0.80)
        self.dot.set_color(c)
        tt = self.t[-1] if self.t else 0
        src = self.state.get('port', 'replay' if self.state.get('replay') else '')
        self.clock.set_text(f'{src}   t = {int(tt // 60):02d}:{int(tt % 60):02d}')

    def draw_card(self):
        ax = self.card; ax.clear(); ax.axis('off'); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.text(0, 1.0, "THE CHIP'S DECISION", fontproperties=F_MONO(9.5), color=MUTED, va='top')
        if not self.events:
            ax.text(0, 0.86, 'Waiting for the\nfirst event…', fontproperties=F_SERIF(22), color=MUTED, va='top')
            return
        k, asked, e, r, z, dec = self.events[-1]
        col = BRICK if dec == 'OBSTRUCTIVE' else TEAL
        ax.text(0, 0.93, f'Event {k}, asked to {asked.lower()}', fontproperties=F_BODY(11.5), color=MUTED, va='top')
        ax.text(0, 0.86, dec.capitalize() + '.', fontproperties=F_TITLE(34), color=col, va='top')
        ax.plot([0, 0.12], [0.665, 0.665], color=col, lw=3)
        scale_bar(ax, 0.50, 'effort  (× normal breathing)', e, 0, 3, [0, 1, 2, 3], '{:.2f}', effort_words(e))
        scale_bar(ax, 0.22, 'rhythm  (0 = none, 1 = perfectly regular)', max(r, 0), 0, 1, [0, 0.5, 1],
                  '{:.2f}', rhythm_words(r))
        ax.text(0, 0.0, f'z = 1.10 + 2.60·ln({e:.2f}) + 5.44·{r:.2f} = {z:+.2f}',
                fontproperties=F_MONO(9.5), color=MUTED, va='bottom')

    def draw_history(self):
        ax = self.hist; ax.clear(); ax.axis('off'); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.text(0, 0.98, 'Event log', fontproperties=F_SERIF(15), color=INK, va='top')
        cols = [0.0, 0.10, 0.25, 0.42, 0.59, 0.74]
        heads = ['#', 'asked', 'effort', 'rhythm', 'z', 'chip says']
        for c, h in zip(cols, heads):
            ax.text(c, 0.74, h, fontproperties=F_MONO(9), color=MUTED, va='top')
        ax.plot([0, 1], [0.63, 0.63], color=RULE, lw=0.8)
        if not self.events:
            ax.text(0, 0.52, 'no events yet', fontproperties=F_MONO(10), color=MUTED, va='top')
            return
        rows = self.events[-6:]
        for i, (k, asked, e, r, z, dec) in enumerate(rows):
            y = 0.585 - i * 0.098
            col = BRICK if dec == 'OBSTRUCTIVE' else TEAL
            for c, v in zip(cols[:5], [f'{k}', asked.lower(), f'{e:.2f}', f'{r:.2f}', f'{z:+.2f}']):
                ax.text(c, y, v, fontproperties=F_MONO(10), color=INK, va='top')
            ax.text(cols[5], y, dec.lower(), fontproperties=F_MONOB(10), color=col, va='top')
            ax.plot([0, 1], [y - 0.082, y - 0.082], color=GRID_MAJ, lw=0.6)

    def draw_gauge(self):
        ax = self.gauge; ax.clear(); ax.axis('off'); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.text(0, 0.98, 'Breathing effort, now', fontproperties=F_SERIF(15), color=INK, va='top')
        v = max(0.0, min(self.live, GAUGE_MAX))
        y = 0.42
        ax.plot([0, 1], [y, y], color=RULE, lw=6, solid_capstyle='butt')
        ax.plot([0, v / GAUGE_MAX], [y, y], color=BRICK if self.live > 1.6 else TEAL, lw=6, solid_capstyle='butt')
        for tk in [0, 0.5, 1, 1.5, 2, 2.5, 3]:
            xt = tk / GAUGE_MAX
            ax.plot([xt, xt], [y - 0.07, y - 0.12], color=MUTED, lw=0.8)
            if tk == int(tk):
                ax.text(xt, y - 0.15, f'{tk:g}', fontproperties=F_MONO(8.5), color=MUTED, ha='center', va='top')
        ax.plot([1 / GAUGE_MAX] * 2, [y - 0.06, y + 0.13], color=INK, lw=1.2)
        ax.text(1 / GAUGE_MAX, y + 0.15, 'normal', fontproperties=F_MONO(8.5), color=INK, ha='center',
                va='bottom')
        ax.text(1, 0.70, f'{self.live:.2f}×', fontproperties=F_MONOB(18), color=INK, ha='right', va='bottom')

    def update(self, _=None):
        while True:
            try:
                self.feed(self.q.get_nowait())
            except queue.Empty:
                break
        self.draw_signals(); self.draw_status(); self.draw_card(); self.draw_history(); self.draw_gauge()
        return []

def main():
    ap = argparse.ArgumentParser(description='SvasaLens dashboard')
    ap.add_argument('port', nargs='?')
    ap.add_argument('--file', help='replay a saved session CSV instead of the live device')
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--snapshot', help='(testing) save one PNG after the file is read, no window')
    a = ap.parse_args()
    if a.port and a.port.lower().endswith('.csv'):
        a.file, a.port = a.port, None

    q, stop, state = queue.Queue(), threading.Event(), {'replay': bool(a.file)}
    if a.snapshot:
        matplotlib.use('Agg')
        file_lines(a.file, 1e9, q, stop)
        scr = Screen(q, state); scr.update(); scr.fig.savefig(a.snapshot, dpi=110, facecolor=PAPER)
        return
    if a.file:
        src = threading.Thread(target=file_lines, args=(a.file, a.speed, q, stop, state), daemon=True)
    else:
        out = 'live_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv'
        src = threading.Thread(target=serial_lines, args=(a.port, out, q, stop, state), daemon=True)
    src.start()
    scr = Screen(q, state)
    anim = FuncAnimation(scr.fig, scr.update, interval=150, cache_frame_data=False)
    plt.show()
    stop.set()

def _report_crash():
    import traceback
    msg = traceback.format_exc()
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    path = os.path.join(here, 'SvasaLens_error.txt')
    try:
        with open(path, 'w') as fh:
            fh.write(msg)
    except Exception:
        pass
    try:
        import tkinter, tkinter.messagebox
        root = tkinter.Tk(); root.withdraw()
        tkinter.messagebox.showerror('SvasaLens could not start', msg[-1500:] + f'\n\nSaved to {path}')
    except Exception:
        pass

if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            _report_crash()
    except Exception:
        _report_crash()
