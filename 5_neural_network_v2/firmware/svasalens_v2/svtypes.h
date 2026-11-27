// Types used by functions in svasalens_v2.ino. They live here, not in the .ino, because the Arduino IDE
// adds its own list of the sketch's functions near the top of the .ino, above any type defined in it.
#pragma once
struct Axis { float s[3], c[6], u[3]; };   // breathing direction of one pod (sums for calibration, then the direction)
