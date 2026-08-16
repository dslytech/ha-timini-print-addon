#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# Defensive: don't let any single non-critical command (e.g. a
# best-effort bluetoothctl call) silently kill this entire script if
# the base image's bashio environment runs under strict/errexit mode.
# The final `exec python3 wrapper.py` still replaces this process
# outright, so a genuine crash in the actual server is still visible
# as normal via its own exit code/log output.
set +e

# Ensure proper UTF-8 handling end-to-end (Alpine/musl containers often
# default to a "C" locale with no UTF-8 awareness, which can mangle
# accented characters - e.g. Hungarian á/é/ő/ű - when they're passed
# as command-line arguments to the TiMini-Print CLI subprocess).
# C.UTF-8 is built into musl and needs no locale-gen step.
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

PRINTER_NAME=$(bashio::config 'printer_name')
BLE_ADAPTER=$(bashio::config 'ble_adapter')

bashio::log.info "Starting TiMini Print wrapper"

# TiMini-Print's CLI does not expose a "use this specific hci adapter"
# flag, so instead of pinning in code (like the separate Cat Printer
# add-on does), we make sure only the intended adapter (typically a
# dedicated LE-only USB dongle, e.g. hci1) stays powered on - whatever
# BLE library TiMini-Print uses underneath will then have no other
# choice. This mirrors the reasoning in the Cat Printer add-on: the
# Pi4's built-in adapter can trigger BR/EDR-related connection errors
# with some cheap BLE printers.
#
# Uses bluetoothctl + sysfs (reliable in this environment) rather than
# btmgmt by raw index (which failed with "Unable to open 0" here,
# likely because container-visible controller indices don't line up
# with host hciN numbering the way btmgmt -i N expects).
if bashio::var.has_value "${BLE_ADAPTER}"; then
    powered_off_any=0
    for hci_path in /sys/class/bluetooth/hci*; do
        [ -d "$hci_path" ] || continue
        hci_name=$(basename "$hci_path")
        if [ "$hci_name" = "${BLE_ADAPTER}" ]; then
            continue
        fi
        addr=$(cat "$hci_path/address" 2>/dev/null)
        if [ -z "$addr" ]; then
            continue
        fi
        bashio::log.info "Powering off ${hci_name} (${addr}) so only ${BLE_ADAPTER} stays active..."
        printf 'select %s\npower off\n' "$addr" | timeout 8s bluetoothctl > /tmp/bluetoothctl-poweroff.log 2>&1 \
            && powered_off_any=1 \
            || bashio::log.warning "Failed to power off ${hci_name} (${addr}) - see /tmp/bluetoothctl-poweroff.log"
    done
    if [ "$powered_off_any" = "0" ]; then
        bashio::log.info "No other adapters found to power off (or ${BLE_ADAPTER} is the only one present)."
    fi
else
    bashio::log.info "No ble_adapter configured - leaving all adapters as-is."
fi

if bashio::var.has_value "${PRINTER_NAME}"; then
    export TIMINI_PRINTER_NAME="${PRINTER_NAME}"
    bashio::log.info "Default printer name configured: ${PRINTER_NAME}"
fi

# Register a "NoInputNoOutput" BlueZ pairing agent as the system default,
# running in the background for the lifetime of this add-on. Without a
# registered agent, a printer that shows up as "[unpaired]" in a scan
# can cause the pairing handshake to hang indefinitely (nothing headless
# is around to auto-confirm "Just Works" pairing) - this is a likely
# cause of print commands appearing to hang/time out with no error.
(echo -e "agent NoInputNoOutput\ndefault-agent\n"; sleep infinity) | bluetoothctl > /tmp/bluetoothctl-agent.log 2>&1 &
bashio::log.info "Registered a background NoInputNoOutput BlueZ pairing agent (pid $!)."

cd /opt/timini-print || exit 1
exec python3 wrapper.py
