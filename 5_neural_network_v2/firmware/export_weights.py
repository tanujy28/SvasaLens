"""Turn the two trained SvasaLens networks into svweights.h for the XIAO firmware.

usage: python export_weights.py DET_MODEL.pt TYPING_MODEL.pt OUT_svweights.h

The header also carries a self-test: a made-up input and the answers PyTorch gives for it.
The firmware runs the same input at start-up and prints SELF-TEST PASS if the chip agrees."""
import sys, time
import numpy as np
import torch, torch.nn as nn


# ---- the two networks, exactly as trained (svdetnn.DetNet and the NN v2 Net)
class CausalConv(nn.Module):
    def __init__(s, cin, cout, k, d):
        super().__init__(); s.pad = (k - 1) * d; s.c = nn.Conv1d(cin, cout, k, dilation=d)
    def forward(s, x): return s.c(nn.functional.pad(x, (s.pad, 0)))


class DetNet(nn.Module):
    def __init__(s, cin, ch=16, k=9, dil=(1, 2, 4, 8, 16, 32, 64)):
        super().__init__()
        layers, c = [], cin
        for d in dil:
            layers += [CausalConv(c, ch, k, d), nn.ReLU()]; c = ch
        s.body = nn.Sequential(*layers); s.head = nn.Conv1d(ch, 1, 1)
    def forward(s, x): return s.head(s.body(x))[:, 0]          # logit for every sample


class TypeNet(nn.Module):
    def __init__(s, ch):
        super().__init__()
        s.f = nn.Sequential(nn.Conv1d(ch, 16, 7, padding=3), nn.ReLU(), nn.MaxPool1d(2),
                            nn.Conv1d(16, 32, 7, padding=3), nn.ReLU(), nn.MaxPool1d(2),
                            nn.Conv1d(32, 32, 5, padding=2), nn.ReLU(), nn.AdaptiveAvgPool1d(1))
        s.d = nn.Dropout(0.3); s.o = nn.Linear(32, 1)
    def forward(s, x): return s.o(s.d(s.f(x).squeeze(-1))).squeeze(-1)


def arr(name, a, fmt='%.9g'):
    a = np.asarray(a, np.float32)
    dims = ''.join(f'[{d}]' for d in a.shape)
    body = ', '.join(fmt % v for v in a.ravel())
    return f'static const float {name}{dims} = {{{body}}};\n'


def main(det_f, typ_f, out_f):
    ck = torch.load(det_f, map_location='cpu', weights_only=False)
    ct = torch.load(typ_f, map_location='cpu', weights_only=False)
    dch, alarm = list(ck['channels']), ck['alarm']
    tch, cut = list(ct['channels']), float(ct['cut'])
    det = DetNet(len(dch)); det.load_state_dict(ck['weights']); det.eval()
    typ = TypeNet(len(tch)); typ.load_state_dict(ct['weights']); typ.eval()
    w = {k: v.numpy() for k, v in ck['weights'].items()}
    t = {k: v.numpy() for k, v in ct['weights'].items()}

    # self-test input: 200 s of breathing-like waves with pauses, plus one typing window
    rng = np.random.default_rng(1)
    T = 200 * 8
    tt = np.arange(T) / 8
    xin = np.stack([np.sin(2 * np.pi * (0.25 + 0.03 * c) * tt) * (1 + 0.5 * np.sin(tt / 23 + c))
                    + 0.2 * rng.normal(size=T) for c in range(len(dch))]).astype(np.float32)
    xin[:, 60 * 8:80 * 8] *= 0.05                                             # a pause
    with torch.no_grad():
        logit = det(torch.tensor(xin[None]))[0].numpy()[7::8]                  # one per second
        win = rng.normal(size=(len(tch), 720)).astype(np.float32)
        win[-1] = 0; win[-1, 240:400] = 1                                     # last row: event marker
        score = float(typ(torch.tensor(win[None]))[0])

    L = [f'// SvasaLens network weights, made by export_weights.py on {time.strftime("%Y-%m-%d %H:%M")}\n',
         f'// detection: {det_f}\n// typing:    {typ_f}\n#pragma once\n\n',
         f'static const int DET_CIN = {len(dch)};\n',
         f'static const int DET_CH[{len(dch)}] = {{{", ".join(map(str, dch))}}};   // 0 thermistor, 1 chest, 2 belly\n',
         f'static const float ALARM_THR = {float(alarm["thr"]):.6f}f;\n',
         f'static const int ALARM_MIN_DUR = {int(alarm["min_dur"])};\n',
         f'static const int ALARM_LAG = {int(alarm["lag"])};\n',
         arr('DET_W0', w['body.0.c.weight']), arr('DET_B0', w['body.0.c.bias']),
         arr('DET_W', np.stack([w[f'body.{2 * i}.c.weight'] for i in range(1, 7)])),
         arr('DET_B', np.stack([w[f'body.{2 * i}.c.bias'] for i in range(1, 7)])),
         arr('DET_HW', w['head.weight'][0, :, 0]),
         f'static const float DET_HB = {float(w["head.bias"][0]):.9g}f;\n\n',
         f'static const int TYP_CIN = {len(tch)};\n',
         f'static const int TYP_CH[{len(tch)}] = {{{", ".join(map(str, tch))}}};   // 0 chest, 1 belly, 2 airflow, 3 event marker\n',
         f'static const float TYP_CUT = {cut:.9g}f;\n',
         arr('TYP_W1', t['f.0.weight']), arr('TYP_B1', t['f.0.bias']),
         arr('TYP_W2', t['f.3.weight']), arr('TYP_B2', t['f.3.bias']),
         arr('TYP_W3', t['f.6.weight']), arr('TYP_B3', t['f.6.bias']),
         arr('TYP_OW', t['o.weight'][0]), f'static const float TYP_OB = {float(t["o.bias"][0]):.9g}f;\n\n',
         '// self-test (input and PyTorch answers)\n',
         f'#define SELFTEST 1\nstatic const int ST_SEC = 200;\n',
         arr('ST_DET_IN', xin.T), arr('ST_DET_LOGIT', logit),
         arr('ST_TYP_IN', win), f'static const float ST_TYP_SCORE = {score:.9g}f;\n']
    open(out_f, 'w').write(''.join(L))
    print(f'wrote {out_f}: alarm {alarm}, detection channels {dch}, typing channels {tch}, cut {cut:.4f}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
