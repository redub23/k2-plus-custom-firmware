# K2 Plus Custom Firmware (Security-Hardened Fork)

A security-hardened fork of [Jacob10383's K2 Plus Custom Firmware](https://github.com/Jacob10383/k2-plus-custom-firmware).

This fork removes embedded developer SSH keys, redirects all OTA updates to
self-hosted GitHub repositories, disables third-party telemetry, and adds
material-specific Klipper macro overrides.

> [!IMPORTANT]
> After installation is complete, log in to your printer via SSH and immediately
> run `passwd` to change the default `creality_2024` root password to something
> secure!

## Security Changes

- **SSH Backdoor Removed**: The installer now automatically deletes the embedded
  developer Ed25519 public key from `/etc/ssh/authorized_keys/root` after
  flashing the root filesystem.
- **Self-Hosted OTA**: All firmware downloads and bootstrap clones now point to
  this repository and its companion forks, not `firmware.jacobean.xyz`.
- **Telemetry Disabled**: Pseudonymous install telemetry to the original author's
  server has been disabled.
- **Local Firmware Flashing**: `install.py` can detect firmware binaries placed
  next to the script and use them instead of downloading, enabling fully offline
  installation.

### Verifying the SSH Backdoor is Removed

To verify that the developer SSH key has been successfully deleted from the printer:

```sh
cat /etc/ssh/authorized_keys/root
```

**Expected output:**
```
cat: can't open '/etc/ssh/authorized_keys/root': No such file or directory
```
If the file does not exist, the backdoor key is completely gone and cannot be used to access your printer.

## Upgrading from Jacob's Firmware

If you already have Jacob's custom firmware installed, you must swap back to the stock firmware before installing this fork.

1. SSH into the printer.
2. Run `swap` to switch back to the stock firmware environment. The printer will reboot.
3. SSH into the printer again (it will now be running stock firmware).
4. Run the installer for this fork:
   ```sh
   python3 -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/redub23/k2-plus-custom-firmware/main/install.py').read(), {'__name__':'__main__'})"
   ```
5. After the installer completes and reboots into the custom firmware, SSH in and run:
   ```sh
   /mnt/UDISK/bootstrap --replace
   ```
   *(Why `/mnt/UDISK/bootstrap`? The raw `rootfs.ext2` image initially contains Jacob's old script in `/usr/bin/bootstrap`. Running `/mnt/UDISK/bootstrap` executes this fork's hardened script and automatically copies itself over `/usr/bin/bootstrap`, permanently upgrading the command for all future runs. If you already flashed previously without this update, you can manually fetch it first via `python3 -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/KennethDoerflein/k2-plus-custom-firmware/main/bootstrap', '/usr/bin/bootstrap')" && chmod +x /usr/bin/bootstrap`)*

### Understanding `bootstrap --replace`

If you run `bootstrap` on a printer that already had Jacob's firmware installed, you will see this prompt:

```
bootstrap would overwrite existing managed paths.
  /mnt/UDISK/klipper
  /mnt/UDISK/moonraker
  /mnt/UDISK/klippy-env
  /mnt/UDISK/moonraker-env
  /mnt/UDISK/fluidd
  /mnt/UDISK/mainsail
  /mnt/UDISK/printer_data/config
rerun with --replace to delete and recreate them
```

#### Why this happens
The `/mnt/UDISK` partition is shared storage and persists across firmware installs. The existing directories still contain Jacob's original remotes, web UIs, and Python environments.

`bootstrap` refuses to overwrite these paths without your permission to prevent accidental data loss.

#### What `--replace` does
Running `bootstrap --replace`:
1. **Deletes and reclones** Klipper, Moonraker, Fluidd, and Mainsail from this fork's hardened repositories instead of Jacob's.
2. **Recreates** the Python virtual environments (`klippy-env`, `moonraker-env`) cleanly.
3. **Automatically backs up your configuration files**: Before replacing `/mnt/UDISK/printer_data/config`, `bootstrap` moves all existing configuration files into:
   ```
   /mnt/UDISK/printer_data/config/config_backups/bootstrap-replace-<timestamp>/
   ```
   Your custom macros, saved bed meshes, and probe offsets will remain safe in this backup directory so you can reference or restore them.

## Added in this fork

- **Mobileraker + OctoApp companions** for remote monitoring from the phone
  apps, installed by `bootstrap` (or `bootstrap --companions` on an existing
  install). See [docs/remote-monitoring.md](docs/remote-monitoring.md).
- **Cutter offset from the screen**: run the `CUTTER_OFFSET` macro (or
  `BOX_CUT_OFFSET`) for a dialog that nudges the cut position ±0.1/±0.5 mm.
- **Bucket purge**: `BOX_PRIME` purges, snaps and wipes at the waste bin. The
  `PURGE` macro wraps it so HelixScreen's Purge button uses the bucket, and
  `START_PRINT` runs it before the KAMP line.
- **Screen slot selection**: works with the HelixScreen change that routes
  remaps through `BOX_SET_ROUTING` (`MERGE=1` keeps earlier routes).

## Repository Layout

- `kernel.img` and `rootfs.ext2` — firmware images
- `install.py` — installation entrypoint
- `bootstrap` and `swap` — printer setup and firmware-slot commands
- `index` — release metadata
- `extras/` — Kalico extensions for K2-specific hardware and functionality
- `docs/` — user documentation

## Companion Repositories

- [Kalico (Klipper fork)](https://github.com/redub23/kalico)
- [Fluidd (Web UI)](https://github.com/KennethDoerflein/fluidd)

This is an independent project and is not affiliated with Creality.
