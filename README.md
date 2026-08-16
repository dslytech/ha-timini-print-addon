<p align="center">
  <img src="timini_print_server/logo.png" alt="TiMini Print Server logo" width="180">
</p>

# TiMini Print Server — Home Assistant Add-on

Runs [TiMini-Print](https://github.com/Dejniel/TiMini-Print)'s own
command-line client inside Home Assistant OS, wrapped in a tiny HTTP
server, so it's reachable from Home Assistant (or a plain browser) over
the network.

## Tested hardware

**Confirmed working** on a **Raspberry Pi 4** (`aarch64`) running Home
Assistant OS, printing to a **TD-11308** "cat printer" clone
(`[classic+ble]`, "Pocket Printer" family) - both via the Pi 4's
built-in Bluetooth adapter and via a dedicated USB BLE dongle (the
`ble_adapter` option lets you pick either).

The add-on's `arch:` list in `config.yaml` also includes `armv7` and
`amd64` (so the Supervisor will offer to build it on those
architectures too), but those haven't been tested against real
hardware - only the aarch64/Pi 4 path above has actually been run and
confirmed printing. If you try it on other hardware or with a
different printer model, please report back either way.

## Why this exists

The separate **Cat Printer Server** add-on (built earlier) wraps
[Cat-Printer](https://github.com/MaddoScientisto/Cat-Printer), which
uses the `bleak` Python library and had persistent connection
reliability problems with this printer family on Linux/BlueZ
(`br-connection-unknown`, `failed to discover services`, etc.), even
after extensive patching.

TiMini-Print is a **separate, independent implementation** that
explicitly lists **TD-11308** as supported (under "Pocket Printer and
clones"), and states it "models printer behavior to match the original
apps as closely as possible, down to the packet level." On the
hardware above, it turned out to be the more reliable option - see
"Tested hardware" above for exactly what's been confirmed.

## Installation

[![Open your Home Assistant instance and show the add-on store.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdslytech%2Fha-timini-print-addon)

Settings → Apps → + Install apps (bottom right) → "..." (top right) → Repositories → + Add (bottom right) → Paste repository URL

## HTTP API

- `GET /scan` → runs `timiniprint_command_line.py --scan`, returns
  `{"returncode", "stdout", "stderr"}` as JSON.
- `POST /print` with JSON body `{"text": "...", "printer": "optional
  name override", "text_columns": 20, "darkness": 3}` → runs
  `timiniprint_command_line.py --text "..."` with `--text-columns`
  and/or `--darkness` appended when given (both optional - omit either
  to use TiMini-Print's own native defaults). Returns
  `{"returncode", "stdout", "stderr"}` as JSON; HTTP 200 on success
  (CLI exit code 0), 500 otherwise.
- `POST /print_image` with JSON body `{"image_b64": "...", "filename":
  "photo.jpg", "printer": "optional name override", "darkness": 3}` →
  base64-decodes the image/PDF, saves it to a temp file (deleted
  afterwards), and runs `timiniprint_command_line.py <tempfile>` (with
  `--bluetooth <printer>` and/or `--darkness` if given). Supported
  extensions (from `filename`): `.png` `.jpg` `.jpeg` `.gif` `.bmp`
  `.pdf`. Same response shape as `/print`.

## Text size and print darkness: native controls, not a workaround

Earlier versions of this add-on (up to 1.5.x) rendered typed text into
an image ourselves (via Pillow) to get any control over its printed
size, working around what looked like TiMini-Print's own image
auto-crop (`converters/base.py`, `RasterConverter`) discarding
whatever size was requested - a short piece of text on a wide white
canvas got cropped to its own tight bounding box, then stretched to
fill the full paper width regardless of the font size it was rendered
at. A 1.5.0 fix worked around this with a non-croppable gray canvas,
confirmed correct by simulating their exact crop+resize algorithm.

As of **2.0.0**, none of that workaround is needed anymore. Digging
into TiMini-Print's actual CLI source (`app/cli.py`,
`printing/settings.py`) turned up the real, native, intended controls:

- `--text-columns N`: target characters per line for `--text` mode -
  fewer columns means bigger letters, more means smaller - this is the
  exact same "Characters per line" setting shown in the project's own
  Android app. Leave blank/omitted for TiMini-Print's own automatic
  default.
- `--darkness N` (1-5): print darkness, applies to both text and image
  jobs - matches the "Print darkness" slider in the Android app.

This add-on now shells out to those flags directly, with no Pillow
rendering, no image cropping tricks, and no canvas/DPI guessing
involved - it's exactly what the official Android app does under the
hood. Two settings mentioned in the Android app's UI - **rotate
print** and **paper feed amount** - exist in TiMini-Print's internal
`PrintSettings` dataclass but are **not** wired up to any CLI flag in
the version of the source seen so far, so they can't be exposed here
either; "number of copies" wasn't found anywhere in the CLI or
settings code at all. If a future TiMini-Print release adds CLI flags
for these, they can be added here too.

## Black & white preview with dithering and brightness adjustment

The image-printing card/UI shows a live black & white preview before
you print - this is exactly what gets sent to the printer, not just a
preview. It uses **Floyd-Steinberg dithering** (converting shades of
gray into black/white dot patterns) rather than a hard brightness
cutoff, so gradients and detail are preserved instead of being lost at
either extreme. The "Brightness adjustment" slider (-120 to +120,
default 0) shifts the whole image lighter or darker *before*
dithering, so the picture still looks like the picture - just overall
lighter or darker - rather than losing shadow or highlight detail the
way a simple threshold would. PDFs aren't processed this way (sent
unchanged, since a PDF can't be drawn to a canvas client-side) - only
single-page image files get this treatment.

Note this is a **different setting** from the native "Print darkness
(1-5)" field described above - the brightness slider here controls how
*your own image* gets converted to black/white pixels before sending;
the native 1-5 `--darkness` setting controls the *printer's own
thermal intensity* for whatever gets sent, and applies to text prints
too. You can adjust both independently for image prints.

## Troubleshooting

- **`BrokenPipeError` in the add-on log, or "Could not reach the
  add-on ... timed out" in Home Assistant, especially after clicking
  Scan/Print more than once in quick succession**: fixed as of add-on
  version 1.1.0. Earlier versions used Python's plain single-threaded
  `HTTPServer`, so a burst of requests would queue up strictly one
  after another - if an earlier request in that queue was still slow
  (a real BLE scan/print can take a while), later ones could time out
  client-side before the server ever got to them, then fail with
  `BrokenPipeError` when the server finally tried to respond to an
  already-abandoned connection. 1.1.0 switches to
  `ThreadingHTTPServer` (so connections are accepted and held
  immediately, no queuing pileup) combined with an internal lock that
  still serializes the actual Bluetooth operations one at a time
  (so a scan and a print can never clash on the single physical
  adapter) - tested with 5 simultaneous requests completing
  successfully with no errors.

- **Print intermittently fails with `org.bluez.Error.BREDR.ProfileUnavailable`
  or pairing errors mentioning "Operation already in progress"**: fixed
  as of add-on version 0.7.0. Earlier versions tried to power off the
  Pi's built-in adapter via `btmgmt -i 0`, which could silently fail
  ("Unable to open 0") depending on how controller indices are exposed
  inside the container - leaving the built-in adapter active and
  occasionally used by TiMini-Print instead of your dongle, triggering
  the same BR/EDR-vs-LE dual-mode connection issue the dongle was meant
  to avoid. 0.7.0 instead identifies and powers off every adapter
  *except* the one named in `ble_adapter` using `bluetoothctl` +
  `/sys/class/bluetooth`, which is more reliable in this environment.

- **Hungarian double-acute characters (ő/ű) print as empty boxes,
  other accented characters (á/é/í/ó/ú) print fine**: fixed as of
  add-on version 0.6.0 by installing the DejaVu font family (full
  Unicode coverage) - earlier versions had no system font installed at
  all, so text rendering fell back to a very limited placeholder font.
- **Accented characters (e.g. Hungarian á/é/ő/ű) print as garbled
  characters**: fixed as of add-on version 0.5.0 by forcing a UTF-8
  locale (`LANG=C.UTF-8`) and Python UTF-8 mode for the whole
  container - Alpine/musl containers can otherwise default to a
  locale with no UTF-8 awareness, corrupting non-ASCII text passed as
  a command-line argument. If you still see this on 0.5.0+, please
  report it with the exact characters that broke.

- **Build fails cloning TiMini-Print or installing requirements**:
  confirms your Pi4 has internet access during the add-on install step.
- **Scan finds nothing / print fails**: this add-on doesn't change
  TiMini-Print's own connection logic at all - troubleshoot it the same
  way you would running it directly over SSH (`cd /opt/timini-print &&
  python3 timiniprint_command_line.py --scan`), since the wrapper is
  just shelling out to exactly that.
- Compare results against the separate Cat Printer Server add-on's
  experience with the same physical printer and dongle - if one
  connects reliably and the other doesn't, that's useful signal about
  which underlying BLE approach actually suits your hardware.

## Credits

All of the actual printer-protocol and Bluetooth-connection work is
done by **[TiMini-Print](https://github.com/Dejniel/TiMini-Print)**
(Apache License 2.0) by [Dejniel](https://github.com/Dejniel) - this
add-on doesn't modify or include its source at all, it only
`git clone`s the unmodified project at Docker build time and wraps its
command-line client in a small HTTP server so Home Assistant can reach
it. All credit for printer/model support, the actual connection logic,
and text/image rendering belongs to that project - please direct
protocol-level bugs, feature requests, or thanks there. This repo is
not a fork of it; it's a separate, independent Home Assistant
packaging layer on top.

Also worth a mention: **[Cat-Printer](https://github.com/NaitLee/Cat-Printer)**
by NaitLee, and the [MaddoScientisto fork](https://github.com/MaddoScientisto/Cat-Printer)
of it with native Android support, which is where this whole project
started before TiMini-Print turned out to be the more reliable option
for this specific printer model.
