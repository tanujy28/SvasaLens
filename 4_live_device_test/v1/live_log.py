import sys, time, datetime
import serial, serial.tools.list_ports

def choose_port():
    if len(sys.argv) > 1:
        return sys.argv[1]
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        sys.exit('No serial port. Is the Nano plugged in?')
    if len(ports) == 1:
        return ports[0].device
    for i, p in enumerate(ports):
        print(f'  [{i}] {p.device} {p.description}')
    return ports[int(input('Number of the Nano: '))].device

def main():
    port = choose_port()
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out = f'live_{stamp}.csv'
    try:
        ser = serial.Serial(port, 115200, timeout=3)
    except serial.SerialException as e:
        sys.exit(f'Could not open {port}: {e}\nClose the Arduino Serial Monitor first.')
    print(f'recording to {out}   (Ctrl+C stops, data so far is kept)\n')
    decisions, done = [], False
    with ser, open(out, 'w', newline='') as fh:
        try:
            while True:
                line = ser.readline().decode('ascii', 'ignore').strip()
                if not line:
                    continue
                fh.write(line + '\n')
                if line.startswith('#'):
                    print(line)
                elif line.startswith('D,'):
                    _, k, cue, eff, rh, z, dec = line.split(',')
                    decisions.append((int(k), cue, float(eff), float(rh), float(z), dec))
                    ok = (cue == 'HOLD') == (dec == 'CENTRAL')
                    print(f'  event {k}  asked {cue:4s}  chip says {dec:11s}  '
                          f'(effort {float(eff):.2f}, rhythm {float(rh):.2f})  {"match" if ok else "MISMATCH"}')
                elif line.startswith('DONE'):
                    print('\n' + line); done = True; break
        except KeyboardInterrupt:
            print('\nstopped early')
    L = [f'SvasaLens live session {stamp}', f'file: {out}', '',
         'event  asked  chip decision   effort  rhythm   match']
    for k, cue, eff, rh, z, dec in decisions:
        ok = (cue == 'HOLD') == (dec == 'CENTRAL')
        L.append(f'  {k}    {cue:4s}   {dec:11s}    {eff:5.2f}   {rh:5.2f}    {"yes" if ok else "NO"}')
    n = len(decisions); m = sum((c == 'HOLD') == (d == 'CENTRAL') for _, c, _, _, _, d in decisions)
    L += ['', f'{m} of {n} events matched the instruction (HOLD = central-like, TRY = obstructive-like).',
          'Rule inside the chip was fixed from UCDDB training nights before this session.',
          'One awake volunteer doing voluntary manoeuvres; event timing from the cue schedule.']
    text = '\n'.join(L)
    print('\n' + text)
    open(f'live_{stamp}_summary.txt', 'w').write(text + '\n')

if __name__ == '__main__':
    main()
