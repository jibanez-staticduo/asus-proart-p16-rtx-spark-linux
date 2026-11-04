# P16 speaker routing and PipeWire setup

Target: ASUS ProArt P16 H7607BA with NVIDIA N1X. The kernel route correction
is independent of distribution and desktop. It adds four static DAPM routes
from the Speaker endpoint to CS42L43 AMP1/AMP2 output pins, only with exact
ASUS/H7607BA system and board DMI plus the NVDA6210 ACPI device. The base
MediaTek driver and its original GPL notices are included for comparison.

## Kernel route correction

First prepare the [kernel series](../kernel/README.md), then before compiling:

```sh
cd /path/to/prepared/linux-7.3
patch --batch --fuzz=0 -p1 < /path/to/this-fork/improvements/audio/route-h7607ba.patch
```

The expected unmodified driver SHA256 is
`1136655fcf885059e1f6c9e2184d9e6335c84f9fd9203e9adb10431fd3d0d2fc`.
The included `mt8901-acp-sdw-legacy-mach.c` is that base, not a second
replacement driver to install. Choose in-tree patching or a distro-managed
replacement; do not install two copies of the driver for the same kernel.
Keep SoundWire/CS42L43/CS35L56 support enabled as in `kernel/config.reference`.

The amplifiers also require the board's Cirrus firmware. Obtain it under its
OEM terms; no firmware binaries are distributed here. Upstream records the
[firmware extraction metadata](https://github.com/johannhartmann/asus-proart-p16-rtx-spark-linux/blob/93e0d2286888a69f31eca8f5b243005c9b293535/config/speaker-firmware.json)
and [extraction implementation](https://github.com/johannhartmann/asus-proart-p16-rtx-spark-linux/blob/93e0d2286888a69f31eca8f5b243005c9b293535/scripts/peripherals.py).
Inspect those versioned references and your distro's firmware process; the
old upstream system installer is not part of this guide.

Reboot into the installed kernel. Do not hot-unload/reload the live machine
or codec drivers: that was observed to leave CS35L56 calibration/control state
broken. The SoundWire platform also has fixes gated on a system BIOS reboot.

## Userspace speaker graph

The P16's physical outputs serve separate speaker bands; sending full-range
audio directly to every driver is not the intended configuration. The supplied
`p16-two-way.conf` is a public PipeWire example based on the local open graph:
3 kHz Linkwitz-Riley fourth-order crossover and a -1 dBFS limiter before the split.
It omits all OEM-derived convolution, EQ and compressor tuning. It is not
Dolby Atmos and will not reproduce the locally tuned frequency response.

Install PipeWire, WirePlumber, ALSA tools and the LSP LV2 plugin collection
using your distribution's packages. Discover the actual sink names and
channels with `wpctl status -n`, `pw-dump` and `aplay -l`; compare them to the
graph's targets before running it. Firmware/UCM changes can alter those names.
Do not paste unverified device mappings into a running speaker setup.

Run the graph manually as your desktop user:

```sh
pipewire -c /absolute/path/to/this-fork/improvements/audio/p16-two-way.conf
```

Keep that process running while using the virtual P16 sink. Use the desktop
sound settings to select it, begin at a very low volume, and verify the two
bands before normal playback. Stop it with Ctrl+C. This manual command does
not install a system service or change login configuration.

If the firmware/UCM has not configured the two speaker paths, inspect the
controls with `amixer -c mt8901soundwire contents`. On the tested mapping,
after starting the graph, selecting its virtual sink and lowering its volume:

```sh
amixer -c mt8901soundwire cset name='cs42l43 Speaker L Input 1' DP5RX1
amixer -c mt8901soundwire cset name='cs42l43 Speaker R Input 1' DP5RX2
amixer -c mt8901soundwire cset name='cs42l43 Speaker L Input 1 Volume' 32
amixer -c mt8901soundwire cset name='cs42l43 Speaker R Input 1 Volume' 32
amixer -c mt8901soundwire cset name='cs42l43 Speaker Digital Volume' 100,100
amixer -c mt8901soundwire cset name='Speaker Switch' on
amixer -c mt8901soundwire cset name='AMP1 Speaker Switch' on
amixer -c mt8901soundwire cset name='AMP2 Speaker Switch' on
amixer -c mt8901soundwire cset name='Headphone Switch' on
amixer -c mt8901soundwire cset name='cs42l43 Speaker Digital Switch' on,on
```

These are board routing values, not an acoustic tuning profile. The physical
Speaker/Headphones PCM levels must be equal and at unity; control listening
volume through the virtual sink. Confirm names/mapping first. Before stopping
the graph or using a plugged jack, mute the virtual sink and disable
`cs42l43 Speaker Digital Switch` (`off,off`), `Speaker Switch`,
`AMP1 Speaker Switch` and `AMP2 Speaker Switch` (`off`). Keep
`Headphone Switch` enabled for the jack and select its normal desktop output.

Do not select the raw internal band sinks as ordinary full-range outputs.
Use the desktop's normal headphone, Bluetooth or HDMI output when needed;
automatic jack switching is not installed by this example. The local complete
setup used a jack watcher, but the exported graph is a separate neutral
configuration that needs its own physical listening/headphone validation.

## Tests and validation scope

```sh
python3 -m unittest discover -s improvements/audio -p 'test_*.py' -v
```

The route patch and earlier local graph were physically tested after boot on
H7607BA BIOS 303. The neutral public graph has not had physical listening or
speaker thermal qualification. Source/syntax checks do not prove acoustic
safety, device mapping or output quality. See [the validation record](../VALIDATION.md).

## License and attribution

The base driver is GPL-2.0 (source SPDX), copyright 2026 MediaTek Inc.,
authors Trevor Wu and Weiyi Hsieh; the small route delta follows that license.
The new public graph, documentation and source tests are MIT, under the root
`licenses/MIT.txt`. LSP plugins are external dependencies under their own
licenses. ASUS/Cirrus firmware and OEM tuning retain their own terms; neither
firmware nor OEM-derived tuning is included.
