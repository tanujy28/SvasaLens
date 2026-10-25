import os, sys, glob
import numpy as np
from scipy.signal import butter, sosfiltfilt
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

FS = 100.0
ACC_LSB = 16384.0
GYR_LSB = 131.0
BAND = (0.1, 0.5)
DATASHEET_UG_RTHZ = 400.0
OUT_DIR = 'results'

def load(path):
    rows, meta = [], []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('trial'):
                continue
            if line.startswith('#'):
                meta.append(line[1:].strip())
                continue
            p = line.split(',')
            if len(p) == 10:
                try:
                    rows.append([int(x) for x in p])
                except ValueError:
                    pass
    if not rows:
        sys.exit('no data rows in ' + path)
    return meta, np.array(rows, dtype=float)

def rms(x):
    return float(np.sqrt(np.mean(np.square(x))))

def bandpass(x):
    sos = butter(2, BAND, btype='band', fs=FS, output='sos')
    return sosfiltfilt(sos, x)

def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        runs = sorted(glob.glob('servo_run_*.csv'))
        if not runs:
            sys.exit('no servo_run_*.csv here. run capture.py first.')
        path = runs[-1]

    meta, D = load(path)
    D = D[D[:, 1] == 0]
    if len(D) < 30 * FS:
        sys.exit(f'only {len(D)/FS:.0f} s of data. the run may have stopped early.')

    acc = D[:, 4:7] / ACC_LSB
    gyr = D[:, 7:10] / GYR_LSB
    t_ms = D[:, 2]

    g = acc.mean(0)
    gnorm = np.linalg.norm(g)
    gd = g / gnorm
    e1 = np.cross(gd, [1, 0, 0])
    if np.linalg.norm(e1) < 0.5:
        e1 = np.cross(gd, [0, 1, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(gd, e1)

    edge = int(3 * FS)
    a1 = bandpass(acc @ e1)[edge:-edge]
    a2 = bandpass(acc @ e2)[edge:-edge]
    tilt1 = np.degrees(a1)
    tilt2 = np.degrees(a2)

    noise_mg = 1000 * np.sqrt((rms(a1) ** 2 + rms(a2) ** 2) / 2)
    noise_deg = np.sqrt((rms(tilt1) ** 2 + rms(tilt2) ** 2) / 2)
    worst_deg = max(np.max(np.abs(tilt1)), np.max(np.abs(tilt2)))

    bw = BAND[1] - BAND[0]
    ds_mg = DATASHEET_UG_RTHZ * np.sqrt(bw) / 1000
    ds_deg = np.degrees(ds_mg / 1000)

    broadband_mg = 1000 * np.mean([rms(acc[:, i] - acc[:, i].mean()) for i in range(3)])
    gyro_dps = np.mean([rms(gyr[:, i] - gyr[:, i].mean()) for i in range(3)])
    dt = np.diff(t_ms)

    lines = [
        f'file: {path}',
        *[f'  {m}' for m in meta],
        '',
        f'recorded: {len(D)/FS:.0f} s, median interval {np.median(dt):.1f} ms (target 10)',
        f'mean |g|: {gnorm:.3f}  (should be about 1.00)',
        '',
        'NOISE IN THE BREATHING BAND (0.1 to 0.5 Hz)',
        f'  measured : {noise_mg:.3f} mg RMS   =  {noise_deg:.4f} deg RMS',
        f'  datasheet: {ds_mg:.3f} mg RMS   =  {ds_deg:.4f} deg RMS',
        f'  ratio measured/datasheet: {noise_mg/ds_mg:.1f}x',
        f'  largest excursion: {worst_deg:.4f} deg',
        '',
        'BROADBAND (unfiltered, for reference)',
        f'  accelerometer: {broadband_mg:.2f} mg RMS per axis',
        f'  gyroscope    : {gyro_dps:.3f} deg/s RMS per axis',
        '',
        'INTERPRETATION',
    ]
    if noise_deg < 0.1:
        lines.append(f'  Noise in the breathing band is {noise_deg:.3f} deg. Breathing tilts')
        lines.append('  the chest by a few degrees, so the sensor has ample margin.')
    elif noise_deg < 0.5:
        lines.append('  Noise is higher than the datasheet but still well below a few degrees.')
        lines.append('  Usable. Note it in the logbook.')
    else:
        lines.append('  Noise is high. Check the table was still, the wires are firm,')
        lines.append('  and consider trying the second MPU.')
    if noise_mg / ds_mg > 3:
        lines.append('  Measured noise is well above the datasheet figure. A clone chip or')
        lines.append('  vibration from the table are the usual causes.')

    os.makedirs(OUT_DIR, exist_ok=True)
    text = '\n'.join(lines)
    print(text)
    with open(os.path.join(OUT_DIR, 'noise_summary.txt'), 'w') as fh:
        fh.write(text + '\n')

    tt = np.arange(len(tilt1)) / FS
    fig, ax = plt.subplots(figsize=(9, 3))
    ax.plot(tt, tilt1, lw=0.8, label='tilt direction 1')
    ax.plot(tt, tilt2, lw=0.8, label='tilt direction 2')
    ax.set_xlabel('seconds')
    ax.set_ylabel('deg')
    ax.set_title(f'MPU-6050 held still, breathing band only: {noise_deg:.4f} deg RMS')
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'noise_floor.png'), dpi=130)
    print(f'\nwrote {OUT_DIR}/noise_summary.txt and {OUT_DIR}/noise_floor.png')

if __name__ == '__main__':
    main()
