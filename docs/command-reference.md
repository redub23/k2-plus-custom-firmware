# Command Reference

Commands added or overridden by this firmware are listed here. Standard
Klipper and Cartographer commands remain in their own documentation.

## Maintenance macros

| Command | Behavior |
| --- | --- |
| `LUBRICATE_RAILS [ITERATIONS=1] [SPEED=500]` | Homes and runs the full rail-lubrication motion pattern. |
| `BELT_TENSION [AXES=XY]` | Automatically tensions X, Y, or both to the configured target. |
| `BEDPID` | Calibrates the bed PID at 100°C and saves the result. |
| `NOZZLE_PID` | Calibrates the nozzle PID at 230°C and saves the result. |
| `NOZZLE_PID_HIGH` | Calibrates the nozzle PID at 320°C and saves the result. |

## CFS and tool changes

| Command | Behavior |
| --- | --- |
| `T<n> [FLUSH=0\|1]` | Performs the full change to zero-based tool slot `<n>`. |
| `BOX_GO_TO_WASTEBIN` | Parks the printhead at the waste bin. |
| `BOX_PRIME [LENGTH=mm] [TEMP=C]` | Purges at the waste bin, then cools, snaps and wipes the nozzle exactly like a filament change. `START_PRINT` runs it at the end (length from `start_purge_length`). |
| `PURGE [LENGTH=mm] [PURGE_TEMP=C]` | Macro wrapper for `BOX_PRIME` used by the touchscreen's Purge button. Defaults to `fallback_purge_length`; heats the nozzle for the loaded filament if it is cold. Refused while printing (pause first). |
| `NOZZLE_CLEAN` | Runs the configured nozzle-cleaning sequence. |
| `BOX_LOAD [SLOT=0]` | Loads a physical zero-based CFS slot without the full tool-change policy. |
| `BOX_UNLOAD [MANUAL=0\|1]` | Fully unloads the active filament. |
| `SET_CANCEL_UNLOAD [ENABLE=0\|1]` | Enables or disables automatic CFS unload on print cancel. |
| `CANCEL_PRINT [UNLOAD=0\|1]` | Cancels current print and parks toolhead, unloading CFS filament if enabled (default `1`). |
| `BOX_CUT [FORCE=0\|1]` | Runs the cutter directly. |
| `BOX_CUT_OFFSET [OFFSET=mm] [ADJUST=mm] [RESET=1] [TEST=1]` | Trims the cutter X position by up to ±1.5 mm on top of the calibrated `cut_pos_x`. Negative pushes further into the cutter (firmer cut). With no parameters it opens a button dialog on the touchscreen and web UIs. The `CUTTER_OFFSET` macro wraps it for the macro list. Stored in the box state file; survives restarts and recalibration. |
| `PRTOUCH_FLOW_TEST [SLOW=2] [FAST=12] [HOLD=1] [CYCLES=3] [SPS=640] [PA=] [MOVE=1] [FILL=1] [PLACE=bin]` | Experimental. With the nozzle hot, extrudes a slow/fast square wave (mm³/s) at the waste bin while logging the nozzle load cell, then wipes. `MOVE=1` creeps the nozzle a fraction of a millimetre over the bin so pressure advance is active (Klipper ignores PA on extrude-only moves); `PA=` sets the value for the test. Reports how fast the pressure follows each rate change (t50/t90) and its overshoot, and saves every sample to `printer_data/logs/prtouch_flow_<time>.csv`. `SPS` is 640 or 1280; the slower ADC rates return garbage on this nozzle board. `PLACE=air` runs over the bed centre 40 mm up instead of at the bin, so nothing touches the extrudate (clear the bed afterwards). `FILL=1` tops up the CFS buffer first: once its slack is used up the buffer spring pulls on the toolhead and masks the pressure reading. CFS buffer and feed-encoder activity is logged to a matching `_cfs.csv`. A capture with too many off-cadence or implausible samples is reported as unreliable instead of scored. Groundwork for automatic pressure-advance calibration. |
| `PRTOUCH_PA_CALIBRATE [START=0.02] [STOP=0.12] [STEP=0.02] [PASSES=2] [CYCLES=3] [MATERIAL=] [SAVE=0]` | Pressure-advance calibration with the nozzle load cell. With the nozzle hot, runs `PRTOUCH_FLOW_TEST` over the bed centre (40 mm up) at each PA value, `PASSES` times, scores how far the pressure trails each slowdown, and reports the PA where that lag reaches zero (on PETG this matched a printed PA pattern within 0.005). About 30 mm of filament per run lands on the bed; clear it afterwards. `SAVE=1 MATERIAL=PETG` stores the result in `pa_calibration.json` next to `printer.cfg`. Per-value results go to `printer_data/logs/prtouch_pa_sweep_<time>.csv`. |
| `PA_APPLY [MATERIAL=] [FALLBACK=]` | Sets pressure advance from the calibration table for `MATERIAL`, or `FALLBACK` when that material has none. `START_PRINT` calls it unless the slicer passes `PA=`. Without arguments, lists the calibrated values. |
| `BOX_BUFFER_RETRACT` | Runs the CFS buffer retract phase. |
| `BOX_DEBUG [RAW=0\|1]` | Prints complete Box, topology, slot, sensor, and RFID diagnostics. |
| `BOX_RUNOUT_CHECK` | Handles a printhead-sensor runout event. |
| `PARSE_FLUSH_VOLUMES` | Reads the slicer's flush matrix from the loaded G-code file. |
| `BOX_SET_ROUTING T0=<slot> [T1=<slot> ...] [MERGE=1]` | Routes slicer tools to physical CFS slots for the current print. `MERGE=1` changes only the named tools (HelixScreen sends one per remapped tool). Cleared when the print ends. |
| `BOX_CLEAR_ROUTING` | Clears custom tool routing and restores 1:1 tool-to-slot mapping. |
| `_BOX_RESUME_CHECK [RETRY=0\|1]` | Validates or recovers Box state before resume. |
| `_FLUSH_CLEAN_SNAP [RETRACT=0\|1]` | Runs the internal flush, snap, and clean sequence. |

## RFID and slot metadata

| Command | Behavior |
| --- | --- |
| `RFID_READER_READ` | Reads the most recent record from the external RFID reader. |
| `_BOX_SLOT_SET SLOT= MATERIAL= COLOR=#RRGGBB [BRAND=] [NAME=] [SPOOLMAN_ID=]` | Saves a CFS or external-slot profile. |
| `_BOX_SLOT_CLEAR SLOT=` | Clears a CFS or external-slot profile. |
| `_BOX_MATERIAL_SET MATERIAL= TARGET_TEMP=170..350` | Saves a material target temperature. |
| `_BOX_SET_RUNOUT_SWAP [ENABLE=0\|1]` | Enables or disables automatic matching-slot runout swaps. |
| `_BOX_SET_UNLOAD_AFTER_PRINT [ENABLE=0\|1]` | Persistently enables or disables unloading after a completed print. |
| `_BOX_SET_RFID_INSERT_READING [ENABLE=0\|1]` | Enables or disables reads when a spool is inserted. |
| `_BOX_SET_RFID_STARTUP_READING [ENABLE=0\|1]` | Enables or disables RFID reads during CFS startup. |
| `_BOX_RFID_MAP_SET CODE= MATERIAL= BRAND= NAME= [TARGET_TEMP=]` | Saves an unknown-tag mapping. |
| `_BOX_RFID_MAP_DELETE CODE=` | Deletes an RFID mapping. |

## CFS Dryer

| Command | Behavior |
| --- | --- |
| `BOX_SET_DRY_MODE [BOX=1] [CH=0\|1\|2] [SLOT=] [TARGET_TEMP=30..75] [TOTAL_TIME=1..1440] [HOURS=]` | Configures and starts chamber drying. Channels: `0`=Left (slots 0-1), `1`=Right (slots 2-3), `2`=Both (default). |
| `BOX_GET_DRY_MODE [BOX=1]` | Displays live dryer telemetry, AC power interlock status, temperatures, and remaining time. |
| `BOX_PAUSE_DRY [BOX=1] [CH=0\|1\|2]` | Pauses active heating for the specified chamber(s). |
| `CONTINUE_PAUSE_DRY [BOX=1]` | Resumes paused chamber drying using prior setpoints or remaining time. |
| `BOX_SET_AUTO_DRY_MODE [ENABLE=0\|1]` | Enables or disables automatic drying after print completion. |
| `BOX_SET_AUTO_HUMIDITY_MODE [ENABLE=0\|1] [THRESHOLD=5..80]` | Enables or disables automatic humidity maintenance (default threshold 25% RH). |

## Probe commands

| Command | Behavior |
| --- | --- |
| `PRTOUCH_HOME [TRAVEL_SPEED=] [Z_HOP=] [PRINT_TEMP=]` | Runs a multi-sample PRTouch Z home; optional print temperature applies thermal compensation. |
| `PRTOUCH_SCAN_CALIBRATE [MODEL=default]` | Uses PRTouch to establish nozzle Z=0 for the Cartographer scan model. |
| `PRTOUCH_AXIS_TWIST_COMPENSATION [AXIS=X\|Y] [USE_TOUCH_BOUNDARIES=0\|1] [SAMPLE_COUNT=] [START=] [END=] [LINE=]` | Uses Cartographer scans and PRTouch contacts to calibrate axis twist. Run `SAVE_CONFIG`, then regenerate the bed mesh. |
| `PRTOUCH_SCRUB` | Homes if needed, detects the rear tab, and wipes the nozzle across it. |

## Motor control

| Command | Behavior |
| --- | --- |
| `CALIBRATE_CUT_POS` | Homes XY if needed, measures the cutter hit, and saves `cut_pos_x`. |
| `MOTOR_CALIBRATE AXIS=XYZZ1 [DETAIL=raw]` | Calibrates one or more kinematic motors after a positioning confirmation pass. |
| `MOTOR_CALIBRATE AXIS=E STAGE=encoder\|offset\|1\|2 [DETAIL=raw]` | Runs the selected extruder calibration stage. |
| `MOTOR_STATUS [VERBOSE=1]` | Reports motor startup, fault, calibration, and runtime state. |
| `MOTOR_CLEAR_ERROR` | Queries and attempts to clear active motor faults and warnings. |
| `MOTOR_QUERY_FAULTS` | Queries live fault, warning, and status codes for every motor. |
| `MOTOR_CFG_OVERRIDE_STATUS [AXIS=XYZZ1E] [DETAIL=raw]` | Compares active config overrides with current board values. |
| `MOTOR_RETRY_STARTUP` | Re-runs motor-control startup. |
| `MOTOR_READ_PARAM PARAM=<config-key>` | Reads a motor parameter's runtime and flash values. |
| `MOTOR_READ_ALL_PIN_IO` | Reads raw motor pin-I/O state. |
| `MOTOR_FLASH_PARAM PARAM=<config-key> [VALUE=] [COMMIT=1]` | Reads or changes a motor parameter; `COMMIT=1` writes motor-board flash. |
| `REQUIRE_EXTRUDER_CLEAR` | Aborts the running G-code block if the extruder fault remains latched. |

## Belt tension modules

Normal belt maintenance should use `BELT_TENSION`. These commands operate the
individual tension modules directly.

| Command | Behavior |
| --- | --- |
| `BELT_TENSION_STATUS [AXIS=X\|Y]` | Reports live position, ADC, calibration, and tension state. |
| `BELT_TENSION_SET AXIS=X\|Y [TENSION=]` | Settles and adjusts one belt to a target tension. |
| `BELT_TENSION_CALIBRATE AXIS=X\|Y POINT=LOW\|HIGH` | Captures the two strain-gauge calibration points. |
| `BELT_TENSION_MOVE_TO AXIS=X\|Y POSITION=` | Moves a tensioner to a raw stored position. |
| `BELT_TENSION_MOVE_BY AXIS=X\|Y DISTANCE= [UPDATE_POSITION=0\|1]` | Performs a raw relative tensioner move. |
| `BELT_TENSION_ZERO AXIS=X\|Y` | Overwrites the module's stored position with zero. |

## Power-loss recovery

| Command | Behavior |
| --- | --- |
| `PLR_STATUS` | Reports checkpoint and recovery state. |
| `PLR_RECOVER CONFIRM=1` | Validates and resumes the saved print checkpoint. |
| `PLR_DISCARD` | Deletes the saved recovery checkpoint. |

## Hardware and runtime utilities

| Command | Behavior |
| --- | --- |
| `SERIAL_STATUS` | Reports shared RS-485 transport counters and errors. |
| `FAN_FEEDBACK_STATUS` | Reports live fan tachometer feedback. |
| `SET_TEMPERATURE_FAN_MANUAL_SPEED TEMPERATURE_FAN=<name> SPEED=0..1` | Sets a manual floor under the temperature fan's automatic speed. |
| `SET_LED_IDLE_MANAGER [ENABLE=0\|1]` | Enables or disables automatic LED idle control for this session. |
| `LED_IDLE_MANAGER_ON` | Enables LED idle control for this session. |
| `LED_IDLE_MANAGER_OFF` | Disables LED idle control for this session. |
| `LED_IDLE_MANAGER_STATUS` | Reports LED idle-manager state. |
| `EXTENDED_ZONE_TRANSFORM_STATUS` | Reports rear-zone transform and Y-envelope state. |
| `SAVE_MOTION_LIMITS [NAME=default] [INCLUDE_GCODE=0\|1]` | Saves current motion limits and optional G-code state. |
| `RESTORE_MOTION_LIMITS [NAME=default] [INCLUDE_GCODE=0\|1] [MOVE=0\|1]` | Restores a saved motion-limit snapshot. |
| `ZDOWN` | Starts the blocking MCU-assisted Z alignment and rise. |
| `ZDOWN_FORCE_STOP` | Aborts an active Z alignment. |
| `SKIP_SOAK` | Skips the active heat soak countdown during `START_PRINT` and immediately continues the print start sequence. |
| `HEAT_SOAK [SOAK_TIME=min]` | Performs an interruptible heat soak countdown with periodic LCD and console status updates. |
