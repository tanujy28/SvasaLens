import sys, time, datetime
import serial
import serial.tools.list_ports

BAUD = 115200

def choose_port():
    if len(sys.argv) > 1:
        return sys.argv[1]
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        sys.exit('No serial ports found. Is the Nano plugged in with a data cable?')
    if len(ports) == 1:
        print(f'using {ports[0].device}  ({ports[0].description})')
        return ports[0].device
    print('Several ports found:')
    for i, p in enumerate(ports):
        print(f'  [{i}] {p.device}  {p.description}')
    k = input('Type the number of the Nano (usually the one saying CH340): ')
    return ports[int(k)].device

def main():
    port = choose_port()
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out = f'servo_run_{stamp}.csv'
    try:
        ser = serial.Serial(port, BAUD, timeout=2)
    except serial.SerialException as e:
        sys.exit(f'Could not open {port}: {e}\n'
                 'If it says "Access is denied", close the Arduino Serial Monitor.')

    print(f'recording to {out}')
    print('the Nano restarts when the port opens, data begins after about 5 s')
    print('the full run takes about 10 minutes. do not touch or bump the table.\n')

    n, last_trial, done = 0, None, False
    waited = time.time()
    with ser, open(out, 'w', newline='') as fh:
        try:
            while True:
                raw = ser.readline()
                if not raw:
                    if n == 0 and time.time() - waited > 20:
                        print('...still no data. wrong sketch uploaded, or wrong port?')
                        waited = time.time()
                    continue
                line = raw.decode('ascii', 'ignore').strip()
                if not line:
                    continue
                if line.startswith('DONE'):
                    fh.write('#' + line + '\n')
                    print('\n' + line)
                    done = True
                    break
                fh.write(line + '\n')
                if line.startswith('#'):
                    print(line)
                    continue
                if line.startswith('trial'):
                    continue
                n += 1
                trial = line.split(',', 1)[0]
                if trial != last_trial:
                    print(f'  trial {trial} started   ({n} samples so far)')
                    last_trial = trial
                if n % 1000 == 0:
                    fh.flush()
        except KeyboardInterrupt:
            print('\nstopped early, partial data saved')

    print(f'\n{n} samples written to {out}')
    if done:
        print(f'next:  python analyse.py {out}')

if __name__ == '__main__':
    main()
