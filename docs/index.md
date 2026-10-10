# K2 Plus Custom Firmware

A complete alternative firmware stack for the Creality K2 Plus.

This is a standalone, telemetry-free fork of the original [K2 Plus Custom Firmware by Jacob10383](https://github.com/Jacob10383/k2-plus-custom-firmware).

It includes a custom Linux system and
[a Kalico fork](https://github.com/redub23/kalico), with
[ground-up implementations](https://github.com/KennethDoerflein/k2-plus-custom-firmware/tree/main/extras)
of CFS control, closed-loop motor control, power-loss recovery, and other
K2-specific systems originally engineered by the upstream project.

The stock firmware remains available after installation. You can switch
between stock and custom whenever you want without reinstalling either one.
Each keeps its own configuration and files.

To install it, follow the [installation guide](install.md).
