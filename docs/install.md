# Install

Unload any filament from the printhead before starting.

SSH into the printer and run:

```sh
python3 -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/redub23/k2-plus-custom-firmware/main/install.py').read(), {'__name__':'__main__'})"
```

Power cycle the printer when instructed.

If custom firmware was installed before, the installer asks whether to keep
your setup or start over. See [Updates & Recovery](updates-recovery.md#update-creality-firmware).

## First boot

After the printer starts again, connect it to the network:

- Ethernet: nothing else is required.
- Wi-Fi: if Creality firmware was connected to Wi-Fi, the printer joins the
  same network. Otherwise, use the printer screen only far enough to join the
  network.

Then SSH into the printer as `root` (password `creality_2024`) and run:

```sh
/mnt/UDISK/bootstrap
```
*(Note: On initial flash, `/usr/bin/bootstrap` contains the prebuilt script from the rootfs image. Running `/mnt/UDISK/bootstrap` executes your seeded version and automatically updates `/usr/bin/bootstrap`, so for all future runs you can simply type `bootstrap`.)*

Change the password with `passwd` if you so choose.

When bootstrap finishes, Fluidd is available on port `4408` and Mainsail on
port `4409` at the printer's IP address.

The default configuration expects the 5.8 mm Y-endstop spacer and the
Cartographer mount used by K2 Improvements.

## Before your first print

1. [Calibrate the printer](calibration.md). Follow the path that applies to
   your probe mode.
2. [Set up OrcaSlicer](setup.md) after calibration.
3. Read [CFS and RFID](cfs.md) to find the Fluidd CFS widget, its controls,
   and RFID setup.
