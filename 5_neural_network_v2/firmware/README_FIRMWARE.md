# SvasaLens V2 firmware (XIAO ESP32S3)

The device finds apneas itself with the two-hospital detection network, tells each one's type with the NN v2 typing network, and saves everything to the SD card, with one numbered file per patient.

## Folder

| File | What it is |
|---|---|
| `svasalens_v2/svasalens_v2.ino` | The sketch: sensors, thermistor, button, LED, SD card |
| `svasalens_v2/svcore.h` | Filters and both networks (checked against the Python code: 415 out of 415 events identical) |
| `svasalens_v2/svweights.h` | The trained weights for both networks (about 430 KB) |
| `SvasaLens_Export_Weights.ipynb` | Colab notebook that makes `svweights.h` from your trained networks on Drive |
| `export_weights.py` | The same export as a plain script |

## Step 1: svweights.h (only after retraining)

`svweights.h` already holds the trained weights. To make a new one after retraining the networks:

1. Open `SvasaLens_Export_Weights.ipynb` in Colab. No GPU is needed.
2. Runtime > **Run all**, and allow Drive access.
3. `svweights.h` downloads (about 430 KB). A copy is also saved in My Drive/SvasaLens_Firmware.
4. Put it in the `svasalens_v2` folder, replacing the old one.

## Step 2: Arduino IDE settings (same as before)

- Board: **XIAO_ESP32S3**
- Tools > **USB CDC On Boot: Enabled**
- If an upload fails: hold **B**, plug in USB, release, then upload again.

## Wiring

| Part | Part pin | XIAO pin |
|---|---|---|
| Chest pod (GY-521) | VCC / GND | **3V3** / GND |
| | SDA / SCL | **D4 / D5** |
| Belly pod (GY-521) | VCC / GND | **3V3** / GND |
| | SDA / SCL | **D6 / D7** |
| Thermistor | one leg | **3V3** |
| | other leg | **A0**, and a 10K resistor from A0 to GND |
| LED | long leg | **D1** through a 220 ohm resistor |
| | short leg | GND |
| Piezo disc 15 mm (for the daytime test beeps) | red / black | **D1** through a 100 ohm resistor / GND (alongside the LED) |
| Push button | one side / other side | **D2** / GND |
| SD module (3.3 V mini) | VCC / GND | 3V3 / GND |
| | SCK / MISO / MOSI / CS | **D8 / D9 / D10 / D3** |
| Battery | + (through the on/off switch) / - | BAT+ / BAT- pads under the XIAO |

**The sensors now go to 3V3, not 5V.** On battery the XIAO's 5V pin has no power.

## Using it (for the nurse)

| Do | LED |
|---|---|
| Switch on | One short flash every 2 s = ready |
| Put the bands and nose clip on the patient, then **press the button once** (pressing it **twice** starts the 10-minute daytime test instead; see `daytime_test/README_DAYTIME_TEST.md`) | Blinks the session number (long blinks = tens, short blinks = units). **Write this number next to the patient's name.** |
| Patient lies still and breathes normally for 30 s | Steady light = calibrating |
| Sleep | One tiny flash every 10 s = recording |
| Morning: **hold the button for 3 s** | 3 long blinks = saved. Then switch off. |
| Fast blinking at any time | Problem: a sensor is missing, there's no SD card, or the self-test failed |

## Files on the SD card

- `DEV1_S007.csv`: every reading, 8 per second. Thermistor (mV), chest and belly breathing movement, the raw x/y/z of both pods, and the chance of apnea once per second.
- `DEV1_S007_events.csv`: one row per apnea found, with start, end, length, OBSTRUCTIVE or CENTRAL, and a summary line at the end (hours, apneas per hour).

The second device: set `DEVICE_ID = 2` at the top of the sketch, so its files are named `DEV2_...`.

## Bench test (without the SD card, thermistor or cords)

1. Wire the two pods to the new pins (D4/D5 and D6/D7), powered from 3V3.
2. Upload, then open the Serial Monitor at 115200.
3. Check you see `SELF-TEST PASS`. That means the networks on the chip match PyTorch.
4. Check you see `chest sensor 0x68` (or 0x70/0x71) and `belly sensor 0x68`.
5. Type **s** and press Enter (or **d** for the daytime test). Keep still for 30 s, then breathe. You get one line per second: `time, thermistor, chest, belly, chance`. The chest and belly numbers should swing up and down with each breath.
6. Type **s** again to stop.

The thermistor and apnea-chance numbers mean nothing until the thermistor is connected.

## Known limits (to check later)

- **Not compiled for the XIAO yet.** I couldn't install the Arduino compiler here, so I checked the code by compiling it on a laptop against stand-in Arduino functions. If the Arduino IDE shows an error, send me the text.
- **Thermistor direction (`THERM_SIGN`).** This is set so breathing out raises the voltage. Whether the hospital's thermistor used the same direction isn't known. Once we have a few real nights, we can test both settings on the saved data.
- **Turning over in bed.** The breathing direction is set during the 30 s calibration. The raw x/y/z are saved, so the signal can be worked out again afterwards if the patient turns over.
- **Typing each apnea takes a fraction of a second.** The device catches up on the readings straight after, so nothing is lost.
