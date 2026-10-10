# Copyright (C) 2026 redub23
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Read the nozzle load cell while extruding (PRTOUCH_FLOW_TEST).

First step toward automatic pressure-advance calibration: the K2's PRTouch
load cell sits in the hotend, so melt pressure from extrusion may show up in
its signal the same way it does on load-cell printers that tune pressure
advance from it. This command checks whether it does, and how cleanly.

At the wastebin it streams the load cell while the extruder runs a
slow / fast / slow square wave, then writes every sample to a CSV in the
logs folder and reports the mean and noise for each phase. A fast-phase mean
that clearly separates from the slow-phase mean means the signal is usable.

Only the pressure MCU is started. Step capture, which halts Z on a trigger,
stays off, and the trigger thresholds are set out of reach, so nothing here
can stop motion.
"""

import logging
import math
import os
import time

FILAMENT_AREA = math.pi * (1.75 / 2.0) ** 2
# CS1237 config register: bits 4-5 select the output rate.
SPS_BITS = {10: 0x00, 40: 0x10, 640: 0x20, 1280: 0x30}
NEVER_TRIGGER = 0x7FFFFFFF
BASELINE_S = 0.6
SETTLE_S = 0.6
POLL_S = 0.02
BUFFER_SAMPLES = 64


class PRTouchFlowMixin:
    """Mixed into PRTouchEndstopWrapper; uses its pressure-MCU commands."""

    def _register_flow_commands(self):
        self.gcode.register_command(
            'PRTOUCH_FLOW_TEST', self.cmd_PRTOUCH_FLOW_TEST,
            desc=self.cmd_PRTOUCH_FLOW_TEST_help)

    cmd_PRTOUCH_FLOW_TEST_help = (
        "Log the nozzle load cell during a slow/fast extrusion square wave "
        "at the wastebin (SLOW=, FAST= mm^3/s, HOLD= s, CYCLES=, SPS=)")

    # ------------------------------------------------------------------

    def cmd_PRTOUCH_FLOW_TEST(self, gcmd):
        slow = gcmd.get_float('SLOW', 2., above=0., maxval=40.)
        fast = gcmd.get_float('FAST', 12., above=0., maxval=40.)
        hold = gcmd.get_float('HOLD', 1.5, minval=.3, maxval=10.)
        cycles = gcmd.get_int('CYCLES', 3, minval=1, maxval=10)
        sps = gcmd.get_int('SPS', 640)
        if sps not in SPS_BITS:
            raise gcmd.error("SPS must be one of %s"
                             % ", ".join(str(s) for s in sorted(SPS_BITS)))
        if fast <= slow:
            raise gcmd.error("FAST must be greater than SLOW")
        if self._armed:
            raise gcmd.error("prtouch is armed for probing")
        self._require_flow_test_conditions(gcmd)

        toolhead = self.printer.lookup_object('toolhead')
        box = self.printer.lookup_object('box', None)
        if box is not None:
            box.move_to_wastebin()
        toolhead.wait_moves()

        samples = {}
        schedule = []
        notes = []
        started = False
        self.gcode.run_script_from_command(
            "SAVE_GCODE_STATE NAME=_prtouch_flow_test")
        try:
            self._start_flow_capture(sps)
            started = True
            self._poll_until(samples, notes,
                             self._estimated_print_time() + BASELINE_S)
            schedule = self._queue_square_wave(
                toolhead, slow, fast, hold, cycles)
            end_time = schedule[-1][1] + SETTLE_S
            self._poll_until(samples, notes, end_time)
        finally:
            if started:
                try:
                    self.stop_pres_cmd.send(
                        [self.pres_oid, self.pres_idle_swap_state])
                except Exception:
                    logging.exception("prtouch flow test: stop_pres failed")
            self.gcode.run_script_from_command(
                "RESTORE_GCODE_STATE NAME=_prtouch_flow_test MOVE=0")
        if box is not None:
            try:
                box.flush_clean_snap(fan_after=0.0)
            except Exception as exc:
                notes.append("wipe failed: %s" % (exc,))

        rows = self._label_samples(samples, schedule)
        path = self._write_flow_csv(rows, slow, fast, hold, sps)
        self._report_flow_summary(gcmd, rows, schedule, sps, path, notes)

    # ------------------------------------------------------------------

    def _require_flow_test_conditions(self, gcmd):
        eventtime = self.reactor.monotonic()
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None:
            state = print_stats.get_status(eventtime).get('state')
            if state == 'printing':
                raise gcmd.error("Pause or finish the print first")
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        heater = extruder.get_heater()
        temp, target = heater.get_temp(eventtime)
        if target < 170. or temp < target - 5.:
            raise gcmd.error(
                "Heat the nozzle to printing temperature first "
                "(e.g. M109 S220)")
        if 'xy' not in self.printer.lookup_object('toolhead').get_status(
                eventtime).get('homed_axes', ''):
            self.gcode.run_script_from_command("HOME_IF_NEEDED AXIS=XY")

    def _start_flow_capture(self, sps):
        cfg_regs = (self.pres_cfg_regs & 0x0F) | SPS_BITS[sps]
        acq_tick = max(1, int(round(self._pres_clock_freq / float(sps))))
        self._run_checked(
            'pres', self.start_pres_cmd,
            [self.pres_oid, cfg_regs, acq_tick, 0, 0, 0,
             NEVER_TRIGGER, NEVER_TRIGGER, 0, 0],
            self.pres_oid, 'start_prtouch_pres', 0x40)

    def _estimated_print_time(self):
        return self.pres_mcu.estimated_print_time(self.reactor.monotonic())

    def _queue_square_wave(self, toolhead, slow, fast, hold, cycles):
        """Queue extruder-only moves; return [(start, end, rate)] in print time."""
        self.gcode.run_script_from_command("M83")
        phases = []
        for _ in range(cycles):
            phases.append(slow)
            phases.append(fast)
        phases.append(slow)
        schedule = []
        start = toolhead.get_last_move_time()
        for rate in phases:
            feed_mm_s = rate / FILAMENT_AREA
            length = feed_mm_s * hold
            self.gcode.run_script_from_command(
                "G1 E%.4f F%.1f" % (length, feed_mm_s * 60.))
            end = toolhead.get_last_move_time()
            schedule.append((start, end, rate))
            start = end
        return schedule

    def _poll_until(self, samples, notes, end_print_time):
        while True:
            self._read_flow_buffer(samples, notes)
            now = self.reactor.monotonic()
            if self.pres_mcu.estimated_print_time(now) >= end_print_time:
                return
            self.reactor.pause(now + POLL_S)

    def _read_flow_buffer(self, samples, notes):
        from .prtouch import _decode_prtouch_frame
        index, buf_len, pages = 0, BUFFER_SAMPLES, 0
        while index < buf_len and pages < 16:
            try:
                page = self.read_pres_cmd.send(
                    [self.pres_oid, 1, 0, index, buf_len - index],
                    retry=False)
            except Exception as exc:
                if len(notes) < 5:
                    notes.append("read failed: %s" % (exc,))
                return
            pages += 1
            if index == 0:
                try:
                    buf_len = max(0, min(BUFFER_SAMPLES,
                                         int(page.get('buf_len', buf_len))))
                except (TypeError, ValueError):
                    pass
            ticks = _decode_prtouch_frame(page.get('ticks', b''), False)
            values = _decode_prtouch_frame(page.get('datas', b''), True)
            count = min(len(ticks), len(values))
            if not count:
                return
            for tick, value in zip(ticks[:count], values[:count]):
                clock = self.pres_mcu.clock32_to_clock64(tick & 0xFFFFFFFF)
                samples[clock] = value
            index += count

    def _label_samples(self, samples, schedule):
        rows = []
        if not samples:
            return rows
        first = min(samples)
        t0 = self.pres_mcu.clock_to_print_time(first)
        for clock in sorted(samples):
            print_time = self.pres_mcu.clock_to_print_time(clock)
            phase, rate = -1, 0.
            for idx, (start, end, phase_rate) in enumerate(schedule):
                if start <= print_time < end:
                    phase, rate = idx, phase_rate
                    break
            rows.append((print_time - t0, samples[clock], phase, rate,
                         print_time))
        return rows

    def _write_flow_csv(self, rows, slow, fast, hold, sps):
        log_file = self.printer.get_start_args().get('log_file')
        folder = os.path.dirname(log_file) if log_file else '/tmp'
        path = os.path.join(folder, time.strftime(
            'prtouch_flow_%Y%m%d_%H%M%S.csv'))
        try:
            with open(path, 'w') as out:
                out.write("# slow=%g fast=%g hold=%g sps=%d\n"
                          % (slow, fast, hold, sps))
                out.write("time_s,pressure,phase,rate_mm3s\n")
                for t, value, phase, rate, _abs in rows:
                    out.write("%.5f,%d,%d,%g\n" % (t, value, phase, rate))
        except OSError as exc:
            logging.warning("prtouch flow test: %s", exc)
            return None
        return path

    @staticmethod
    def _stats(values):
        if not values:
            return None, None
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
        return mean, math.sqrt(var)

    def _report_flow_summary(self, gcmd, rows, schedule, sps, path, notes):
        lines = []
        if not rows:
            lines.append("No load-cell samples were captured.")
        else:
            duration = rows[-1][0] - rows[0][0]
            gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
            lines.append(
                "Captured %d samples over %.2fs (%.0f/s, target %d/s, "
                "largest gap %.0fms)"
                % (len(rows), duration, len(rows) / max(duration, 1e-6),
                   sps, (max(gaps) if gaps else 0.) * 1000.))
            idle = [r[1] for r in rows if r[4] < schedule[0][0]]
            idle_mean, idle_sd = self._stats(idle)
            if idle_mean is not None:
                lines.append("idle: mean %.0f sd %.0f n=%d"
                             % (idle_mean, idle_sd, len(idle)))
            by_rate = {}
            for idx, (start, end, rate) in enumerate(schedule):
                # The first 40% of each phase is the response settling.
                cut = start + (end - start) * .4
                values = [r[1] for r in rows if r[2] == idx and r[4] >= cut]
                by_rate.setdefault(rate, []).extend(values)
                mean, sd = self._stats(values)
                if mean is not None:
                    lines.append("phase %d %.1f mm3/s: mean %.0f sd %.0f n=%d"
                                 % (idx, rate, mean, sd, len(values)))
            rates = sorted(by_rate)
            lo_mean, lo_sd = self._stats(by_rate[rates[0]])
            hi_mean, hi_sd = self._stats(by_rate[rates[-1]])
            if lo_mean is not None and hi_mean is not None:
                noise = max(lo_sd, hi_sd, 1.)
                lines.append(
                    "fast-slow difference %.0f = %.1fx the noise"
                    % (hi_mean - lo_mean, (hi_mean - lo_mean) / noise))
        if path:
            lines.append("Samples saved to %s" % path)
        lines.extend(notes)
        gcmd.respond_info("\n".join(lines))
