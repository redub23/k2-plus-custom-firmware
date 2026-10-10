# Remote monitoring (Mobileraker and OctoApp)

`bootstrap` installs the companions for both phone apps on a fresh install:

- **Mobileraker Companion** sends push notifications (progress, pauses,
  errors, filament runout) to the Mobileraker app.
- **OctoApp companion** links the printer to the OctoApp app for remote
  monitoring, notifications and the live camera.

On a printer that is already set up, install or reinstall them with:

```sh
bootstrap --companions          # install anything missing
bootstrap --companions replace  # reclone both and rebuild their Python envs
```

## What gets installed

| Item | Location |
| --- | --- |
| Mobileraker code / env | `/mnt/UDISK/mobileraker_companion`, `/mnt/UDISK/mobileraker-env` |
| Mobileraker config | `printer_data/config/mobileraker.conf` |
| OctoApp code / env | `/mnt/UDISK/octoapp`, `/mnt/UDISK/octoapp-env` |
| OctoApp data | `printer_data/octoapp-store` |
| Logs | `printer_data/logs` |
| Services | `mobileraker.service`, `octoapp.service` (systemd) |

Both appear in Fluidd/Mainsail's update manager. Mobileraker's entry lives in
`mobileraker-moonraker.conf`; `bootstrap --update configs` re-adds its include
to `moonraker.conf` after replacing that file. OctoApp manages its own entry.

## Linking the apps

Open the app on your phone (on the same network as the printer) and add the
printer at `http://<printer-ip>:7125`, then turn on notifications in the app.
For access away from home, use the app's own remote-access option or a VPN
such as Tailscale.

The Mobileraker snapshot uses the stock camera at
`http://<printer-ip>:8080/snapshot`; edit `snapshot_uri` in `mobileraker.conf`
if the printer's IP changes.

## Services

```sh
systemctl status mobileraker octoapp
systemctl restart mobileraker octoapp
```

Firmware swaps and reflashes replace the root filesystem, which removes the
service files; run `bootstrap --companions` again afterwards.
