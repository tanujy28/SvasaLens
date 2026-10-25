import sys, time, datetime, threading
import serial
import serial.tools.list_ports

try:
    import winsound
    def _tone(f, ms):
        winsound.Beep(f, ms)
except ImportError:
    def _tone(f, ms):
        print('\a', end='', flush=True)
        time.sleep(ms / 1000)

PATTERNS = {1: [(880, 150)], 2: [(1200, 180), (1200, 180)], 3: [(500, 400)], 0: []}

def beep(kind):
    def run():
        for f, ms in PATTERNS[kind]:
            _tone(f, ms)
            time.sleep(0.08)
    threading.Thread(target=run, daemon=True).start()

CUES = [(0, 'Lie still. Breathe normally.', 0)]
for i, (a, kind) in enumerate([(35, 'H'), (70, 'E'), (105, 'H'),
                               (140, 'E'), (175, 'H'), (210, 'E')]):
    if kind == 'H':
        CUES += [(a - 3, 'GET READY: breathe out gently...', 1),
                 (a, '>>>  HOLD   just stop, relaxed, no pushing', 2)]
    else:
        CUES += [(a - 3, 'GET READY: breathe out, close mouth, pinch nose...', 1),
                 (a, '>>>  TRY   small gentle attempts to breathe in, one every ~3 s', 2)]
    CUES.append((a + 10, '     BREATHE normally', 3))
CUES.append((247, 'Nearly done. Stay still until it says DONE.', 3))

def choose_port():
    if len(sys.argv) > 1:
        return sys.argv[1]
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        sys.exit('No serial ports found. Is the Nano plugged in with a data cable?')
    if len(ports) == 1:
        print(f'using {ports[0].device}  ({ports[0].description})')
        return ports[0].device
    for i, p in enumerate(ports):
        print(f'  [{i}] {p.device}  {p.description}')
    return ports[int(input('Number of the Nano (the CH340 one): '))].device

def main():
    port = choose_port()
    out = f'holdtry_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv'
    try:
        ser = serial.Serial(port, 115200, timeout=2)
    except serial.SerialException as e:
        sys.exit(f'Could not open {port}: {e}\n'
                 'If it says "Access is denied", close the Arduino Serial Monitor.')
    try:
        ser.set_buffer_size(rx_size=65536)
    except Exception:
        pass

    print(f'\nrecording to {out}')
    print('the Nano restarts, then the first cue appears in about 5 s.')
    print('Ctrl+C stops at any time.\n')

    n, t0, fired, done = 0, None, 0, False
    zeros = {'A': 0, 'B': 0}
    with ser, open(out, 'w', newline='') as fh:
        try:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode('ascii', 'ignore').strip()
                if not line:
                    continue
                if line.startswith('DONE'):
                    fh.write('#' + line + '\n')
                    done = True
                    break
                fh.write(line + '\n')
                if line.startswith('#') or line.startswith('trial'):
                    continue
                parts = line.split(',')
                if len(parts) != 16:
                    continue
                n += 1
                for _k, _c in (('A', 4), ('B', 10)):
                    if all(v == '0' for v in parts[_c:_c + 6]):
                        zeros[_k] += 1
                        if zeros[_k] in (1, 50, 500):
                            print(f'  WARNING: sensor {_k} sending zeros ({zeros[_k]} so far). wire or power loose?')
                t_ms = int(parts[2])
                if t0 is None:
                    t0 = t_ms
                el = (t_ms - t0) / 1000.0
                while fired < len(CUES) and el >= CUES[fired][0]:
                    s, text, kind = CUES[fired]
                    print(f'  [{s:>4.0f} s]  {text}')
                    beep(kind)
                    fired += 1
                if n % 1000 == 0:
                    fh.flush()
        except KeyboardInterrupt:
            print('\nstopped early. partial data saved.')

    print(f'\n{n} samples written to {out}')
    if done:
        print(f'next:  python holdtry_analysis.py {out}')

if __name__ == '__main__':
    main()
