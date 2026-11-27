# SvasaLens daytime test (10 minutes per person, no laptop)

The device runs the whole test by itself. It beeps the cues, marks every hold in its own file, scores itself, and saves the result on the SD card.

## Extra part: piezo beeper
- **15 mm piezo disc**, the bare brass disc with two wires (about ₹10–20). A 12 mm disc also fits. **A 20 mm disc does not fit.**
- Glue it flat under the lid, over the front half of the battery, away from the SD module.
- Wire it to **D1 and GND, with a 100 ohm resistor**, alongside the LED (the LED and beeper share D1). The box needs no changes.

## Before each person
1. Get the signed consent form. Check: 18 or older, no heart or lung problems, not pregnant.
2. Fit the chest and belly bands and the nose clip. The person lies on their back.
3. Fill in one row of the record sheet (`record_sheet.csv`, on paper or on your phone): person ID (P01, P02 … no names), age, sex.
4. Explain the beeps (show them this card):

| Sound | Meaning |
|---|---|
| 1 long beep | Get ready: **RELAXED HOLD** in 5 s |
| 2 short high beeps | Get ready: **TRY** in 5 s |
| 1 short very high beep | **Start now** (the LED stays on during the hold) |
| 1 long lower beep | **Breathe normally** |

- **RELAXED HOLD**: breathe out gently, stop, and stay relaxed. Don't push. (Like a central apnea.)
- **TRY**: close your mouth, pinch your nose, and keep trying to breathe in. (Like an obstructive apnea.)

## Running it
1. Switch on and wait for the slow LED flash (ready).
2. **Press the button twice quickly.** The LED blinks the test number (long blinks = tens, short blinks = units). **Write it on the record sheet** (e.g. DEV1_T007).
3. 30 s still with the LED steady (calibration), then 2 beeps, then 2 minutes of normal breathing.
4. 6 holds of 10 s, with 50 s of normal breathing after each (3 relaxed holds and 3 tries, in a mixed order that's different every test).
5. If a hold wasn't done properly (cough, laugh, breathing during the hold), **press the button once** during it or within 20 s after. Two beeps confirm it's left out of the score.
6. To stop at once (if anyone feels unwell): **hold the button for 3 s.**
7. The end: 3 rising beeps, then the LED blinks the number of holds it found with the right type (0–6). It stops and saves by itself.
8. Ask the person for comfort (1 = very uncomfortable, 5 = didn't notice it) and whether anything slipped. Write both on the record sheet.

The whole test takes about 8.5 minutes, plus fitting.

## Files on the SD card (for test 7 on device 1)
- `DEV1_T007.csv`: every reading. The `cue` column marks the holds (1 = relaxed hold, 2 = try).
- `DEV1_T007_events.csv`: everything the device detected.
- `DEV1_T007_result.csv`: each hold (found? right type?), false alarms, and a SUMMARY line.

## After everyone
Copy the SD card files and `record_sheet.csv` into one folder, then on any computer run:
`python summarise_daytime.py FOLDER`
It writes `summary.txt` with the headline:
> "On N people, SvasaLens found and correctly typed X of Y simulated apneas (Z%, 95% range A–B%)."

## Scoring rules (fixed before testing; don't change them halfway)
- A hold is **found** if the device marks an apnea that overlaps the hold, or the 5 s after it, by at least 1 s. Each hold can match at most one detection.
- **Right type**: relaxed hold → CENTRAL, try → OBSTRUCTIVE.
- **Main measure**: holds found AND given the right type, out of all holds done properly.
- Any other detection is a **false alarm**.
- Always report it as **simulated**: awake people holding their breath is not real sleep apnea.
