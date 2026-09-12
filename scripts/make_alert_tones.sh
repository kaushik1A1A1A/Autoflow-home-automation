#!/bin/sh
# =====================================================================
# Autoflow - tone alerts, fourth attempt
# =====================================================================
#
#   sh /home/<USER>/make_alert_tones.sh
#
# WHY THE LAST PAIR SOUNDED ALIKE
#
# power_cut and power_restored used the same three notes - G, C, E - one
# ascending, one descending. Direction alone is a weak signal: played a
# room away, both are just "three notes". Nothing about either said sad or
# happy, because G-C-E is neither.
#
# Now they are different chords, not the same chord reversed:
#
#   power_cut        D minor, falling      A4 - F4 - D4
#                    Minor third, downward, low register, slow decay.
#                    Minor and falling is about as close to "sad" as three
#                    notes get.
#
#   power_restored   C major, rising       C5 - E5 - G5
#                    Major third, upward, brighter register, quick.
#                    The standard "success" shape - it is what almost every
#                    device uses for a reason.
#
#   perimeter_vehicle   two quick taps, high, twice. Deliberately not a chord
#                    at all - it should not be mistaken for either power
#                    tone in the moment you hear it.
#
# LOUDNESS
#
# Matched with loudnorm rather than by ear. All three are normalised to the
# same perceived level, so none of them jumps out relative to the others.
# perimeter_vehicle sits 2 LU hotter on purpose - it is the one competing with
# outdoor noise.

set -e

A=/config/www/alerts
HOST=/home/<USER>/homeassistant/config/www/alerts

for f in perimeter_vehicle power_cut power_restored; do
    if [ -f "$HOST/$f.mp3" ] && [ ! -f "$HOST/$f.mp3.bak-speech" ]; then
        cp -p "$HOST/$f.mp3" "$HOST/$f.mp3.bak-speech"
        echo "  backed up $f.mp3"
    fi
done

# Soft struck tone - fundamental, quiet octave, trace of the twelfth. All
# whole multiples, so it reads as wood or glass rather than metal. The
# inharmonic partial that made the last set sound like a doorbell is gone.
tone() {
    printf "aevalsrc=exp(-%s*t)*(0.62*sin(2*PI*%s*t)+0.16*sin(2*PI*%s*t)+0.04*sin(2*PI*%s*t)):d=%s:s=44100" \
        "$2" "$1" "$(echo "$1 * 2" | bc)" "$(echo "$1 * 3" | bc)" "$3"
}

SIL="anullsrc=r=44100:cl=mono:d=0.09"

# --- perimeter_vehicle: two quick taps, twice. Not a chord ---------------
docker exec homeassistant ffmpeg -y -loglevel error \
  -f lavfi -i "$(tone 1046 11 0.20)" \
  -f lavfi -i "$(tone 1046 10 0.26)" \
  -f lavfi -i "$SIL" \
  -f lavfi -i "$(tone 1046 11 0.20)" \
  -f lavfi -i "$(tone 1046 8 0.40)" \
  -filter_complex "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1,\
lowpass=f=6000,loudnorm=I=-14:TP=-1.5:LRA=11[out]" \
  -map "[out]" -codec:a libmp3lame -q:a 3 "$A/perimeter_vehicle.mp3"
echo "  perimeter_vehicle.mp3   two quick taps, twice"

# --- power_cut: D minor falling, low and slow -------------------------
docker exec homeassistant ffmpeg -y -loglevel error \
  -f lavfi -i "$(tone 440 7 0.36)" \
  -f lavfi -i "$(tone 349 7 0.36)" \
  -f lavfi -i "$(tone 294 3.4 1.00)" \
  -filter_complex "[0:a][1:a][2:a]concat=n=3:v=0:a=1,\
lowpass=f=4200,loudnorm=I=-16:TP=-1.5:LRA=11[out]" \
  -map "[out]" -codec:a libmp3lame -q:a 3 "$A/power_cut.mp3"
echo "  power_cut.mp3        D minor falling"

# --- power_restored: C major rising, bright and quick -----------------
docker exec homeassistant ffmpeg -y -loglevel error \
  -f lavfi -i "$(tone 523 10 0.24)" \
  -f lavfi -i "$(tone 659 10 0.24)" \
  -f lavfi -i "$(tone 784 6 0.60)" \
  -filter_complex "[0:a][1:a][2:a]concat=n=3:v=0:a=1,\
lowpass=f=6500,loudnorm=I=-16:TP=-1.5:LRA=11[out]" \
  -map "[out]" -codec:a libmp3lame -q:a 3 "$A/power_restored.mp3"
echo "  power_restored.mp3   C major rising"

echo ""
echo "Listen in a browser:"
for f in perimeter_vehicle power_cut power_restored; do
    echo "  http://<HA_HOST>:8123/local/alerts/$f.mp3"
done
echo ""
echo "To put the spoken versions back:"
echo "  for f in perimeter_vehicle power_cut power_restored; do"
echo "    cp $HOST/\$f.mp3.bak-speech $HOST/\$f.mp3; done"
