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
- `GET /list-models` → runs `timiniprint_command_line.py
  --list-models`, returns `{"models": [{"key", "label"}, ...], "raw":
  {"returncode", "stdout", "stderr"}}` as JSON.
- `GET /licenses` → runs `timiniprint_command_line.py --licenses`,
  returns `{"returncode", "stdout", "stderr"}` as JSON.
- `POST /print` with JSON body `{"text": "...", "printer": "optional
  name override", "text_columns": 20, "darkness": 3, "printer_model":
  "a33", "hard_wrap": false, "copies": 1}` → runs `timiniprint_command_line.py
  --text "..."` with `--text-columns`, `--darkness`,
  `--printer-model`, and/or `--text-hard-wrap` (if `hard_wrap` is
  true) appended when given (all optional - omit any to use
  TiMini-Print's own native defaults). Returns `{"returncode",
  "stdout", "stderr"}` as JSON; HTTP 200 on success (CLI exit code 0),
  500 otherwise.
- `POST /print_image` with JSON body `{"image_b64": "...", "filename":
  "photo.jpg", "printer": "optional name override", "darkness": 3,
  "printer_model": "a33", "pdf_pages": "1,3-5", "page_gap": 5,
  "trim_side_margins": true, "trim_top_bottom_margins": true, "copies": 1}` →
  base64-decodes the image/PDF, saves it to a temp file (deleted
  afterwards), and runs `timiniprint_command_line.py <tempfile>` (with
  `--bluetooth <printer>`, `--darkness`, `--printer-model`,
  `--pdf-pages`, `--page-gap`, and/or `--no-trim-side-margins`/
  `--no-trim-top-bottom-margins` if `trim_side_margins`/
  `trim_top_bottom_margins` are `false` - both default to `true`,
  matching TiMini-Print's own default of trimming). Supported
  extensions (from `filename`): `.png` `.jpg` `.jpeg` `.gif` `.bmp`
  `.pdf`. Same response shape as `/print`.
- `POST /paper_motion` with JSON body `{"action": "feed", "printer":
  "optional name override", "printer_model": "a33"}` → runs
  `timiniprint_command_line.py --feed` (or `--retract` if `action` is
  `"retract"`) with `--bluetooth`/`--printer-model` appended when
  given. `action` is required and must be `"feed"` or `"retract"` -
  matches the "Feed"/"Retract" buttons in TiMini-Print's own GUI.
  Same response shape as `/print`.

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
hood. One setting mentioned in the Android app's UI - **rotate
print** - exists in TiMini-Print's internal `PrintSettings` dataclass
but is **not** wired up to any CLI flag in the version of the source
seen so far, so it can't be exposed here either; "number of copies"
wasn't found anywhere in the CLI or settings code at all (see "Copies,
and viewing TiMini-Print's own license text" below for how that's
worked around client-side instead). **Paper feed amount**, however,
*is* available in a different form: `--feed` and `--retract` (see
"Feed and retract paper" below) advance/retract the paper directly as
a standalone action - this isn't the same as the per-job "feed
padding" setting shown in the Android app (which still isn't
exposed), but covers the same practical need of moving the paper
without printing anything. If a future TiMini-Print release adds CLI
flags for rotation, they can be added here too.

## Feed and retract paper

The **Scan** card has **"Feed paper"** / **"Retract paper"** buttons
that run `timiniprint_command_line.py --feed` / `--retract` - these
just move the paper, no printing involved, matching the same-named
buttons in TiMini-Print's own GUI. Uses the printer/model selection
from the Scan card above (same as a print job would).

## Copies, and viewing TiMini-Print's own license text

Both print cards have a **Copies** field (1-20) - TiMini-Print's own
CLI has no `--copies` flag, so this just calls it that many times in
a row; if any copy fails, later ones are skipped rather than wasting
more paper. The small **"i"** button next to the page title (like the
info button on many real printers/appliances) fetches and shows
TiMini-Print's own license text (`--licenses` output) via a new
`GET /licenses` endpoint.

## PDF page selection, page gap, and margin trimming

The "Print image or PDF" card also has:

- **PDF pages** (e.g. `1,3-5`) - print only specific pages of a
  multi-page PDF instead of all of them. Ignored for plain images.
- **Gap between pages** (mm) - extra vertical spacing between PDF
  pages. Leave blank for TiMini-Print's own default (5mm).
- **Trim white side margins** / **Trim white top/bottom margins** -
  on by default (matching TiMini-Print's own default behavior).
  Untick either to print the image/PDF page at its original size
  without TiMini-Print's automatic white-margin cropping - see "Text
  size and print darkness" above for why this crop can matter (it's
  the same mechanism that made an early font-size approach for text
  printing behave unpredictably).

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

## Forcing an unrecognized printer's model

Some printers show up in a scan but aren't automatically recognized as
a specific known model (TiMini-Print's own GUI shows these tagged
"[manual model required]"). The **Scan** card has an **"Unsupported /
unrecognized device"** checkbox for this - checking it reveals a model
field with autocomplete, backed by a new `/list-models` endpoint
(wrapping `timiniprint_command_line.py --list-models`) so you can
search/pick the exact model key (e.g. `a33`) instead of guessing it.
This printer + model selection is shared by both the "Print text" and
"Print image/PDF" cards below, so you only need to set it once. It
passes `--printer-model <key>` to the CLI, which can let printing
succeed even when the automatic profile match doesn't work. Leave the
checkbox unticked for normal automatic detection (the default, and
what most printers need) - note the printer picker itself already
always shows every discovered device regardless of this checkbox
(TiMini-Print's own `--scan` doesn't filter), so this only affects
whether the model field is shown.

## Troubleshooting

- **`BrokenPipeError` traceback specifically around `/list-models`**:
  fixed as of add-on version 2.4.1. The `/list-models` call can be a
  little slower than others (144 known models to enumerate) - if the
  browser gave up waiting (e.g. a page refresh mid-request), the
  server would previously log a full Python traceback when it tried
  to respond anyway. 2.4.1 catches this specific case quietly - it's
  benign either way, nothing was actually broken.
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
- **Scan finds nothing, even for a device you can confirm is
  advertising (e.g. visible in your phone's own Bluetooth settings)**:
  if Home Assistant's own built-in "Bluetooth" integration is enabled,
  it actively holds/monitors the same physical adapter this add-on
  uses, which can make TiMini-Print's own `--scan` come back empty due
  to contention over the adapter - this add-on's own web UI has no
  workaround for that (it's a separate process from Home Assistant
  Core, with no way to read HA's own Bluetooth data). If you're also
  using the companion **HACS integration**, its Lovelace card has a
  second button - "Use Home Assistant's Bluetooth list" - that reads
  whatever Home Assistant's own Bluetooth integration has already
  (passively) discovered, without triggering a new scan at all, so it
  sidesteps this contention entirely. Pick a device from there (any
  Bluetooth device works, not just recognized printers - tick
  "Unsupported / unrecognized device" to force a model), even if this
  add-on's own web UI scan stays empty.
  The full request path in that case, for reference (this describes
  how it's designed to work end-to-end - the individual pieces have
  been tested, but not this exact combination all the way through
  repeatedly, so treat it as likely-correct rather than confirmed):
  the card puts the address you picked from HA's Bluetooth list into
  the printer field → pressing Print sends `printer: "<address>"` to
  the `timini_print.print_text`/`print_image` service → the HACS
  integration forwards that to this add-on over HTTP → the add-on
  appends `--bluetooth <address>` to the CLI call → the add-on then
  connects to that address itself, over its own Bluetooth adapter -
  entirely separately from however Home Assistant found that address
  in the first place. So while HA's Bluetooth list solves the
  *discovery* side of the adapter-contention problem, the actual
  *connection* still goes through this add-on's own Bluetooth stack,
  which may or may not be affected by the same contention - report
  back if you find it isn't reliable.
- **Scan finds nothing / print fails, and you're not using the HACS
  integration (or HA's own Bluetooth list didn't help either)**: this
  add-on doesn't change TiMini-Print's own connection logic at all -
  troubleshoot it the same way you would running it directly over SSH
  (`cd /opt/timini-print && python3 timiniprint_command_line.py
  --scan`), since the wrapper is just shelling out to exactly that.

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

* Developed with the assistance of Anthropic Claude.
