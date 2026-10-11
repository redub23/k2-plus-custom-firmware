# Copyright (C) 2026 redub23
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Read the nozzle load cell while extruding (PRTOUCH_FLOW_TEST).

Groundwork for automatic pressure-advance calibration. The K2's PRTouch load
cell sits in the hotend and reads melt pressure: extruding faster raises the
reading, and the reading lags the extruder the way pressure advance assumes.

At the wastebin the command runs a slow / fast extrusion square wave while
streaming the load cell, writes every sample to a CSV in the logs folder and
reports, for every rate change, how quickly the pressure followed (t50/t90)
and how far it overshot. Running it at several PA= values shows which one
makes the pressure follow the commanded flow most cleanly.

Klipper applies pressure advance only to moves with X/Y motion, so by default
the nozzle creeps back and forth by a fraction of a millimetre over the bin
while extruding (MOVE=0 extrudes in place, with no pressure advance).

Only the pressure MCU is started. Step capture, which halts Z on a trigger,
stays off, and the trigger thresholds are out of reach, so nothing here can
stop motion.
"""

import logging
import math
import os
import time

from . import motion_limits

FILAMENT_AREA = math.pi * (1.75 / 2.0) ** 2
# CS1237 config register: bits 4-5 select the output rate. The nozzle
# firmware samples on its own fast cadence, so the 10 and 40 SPS settings
# return mostly unfinished conversions; only the fast rates read cleanly.
SPS_BITS = {640: 0x20, 1280: 0x30}
DEFAULT_SPS = 640
OUTLIER_WINDOW = 9      # samples in the rolling median
OUTLIER_FLOOR = 20000   # counts; well above idle noise (~450 sd)
OUTLIER_MADS = 8.0
MAX_REJECT_FRACTION = 0.1
MIN_CADENCE_FRACTION = 0.6
CFS_POLL_S = 0.1        # CFS buffer/encoder sampling period
CFS_TIMEOUT = 0.3  # sample gaps that match the ADC rate
NEVER_TRIGGER = 0x7FFFFFFF
BASELINE_S = 0.8
SETTLE_S = 0.8
POLL_S = 0.015
MAX_PAGE_REQUEST = 64
WIGGLE_SPEED = 0.5      # mm/s of X travel while extruding
STEADY_FRACTION = 0.4   # last part of a phase used as its steady level
AIR_Z = 40.             # mm above the bed for PLACE=air
PRIME_MM = 1.5          # undoes the previous wipe's snap retract


class PRTouchFlowMixin:
    """Mixed into PRTouchEndstopWrapper; uses its pressure-MCU commands."""

    def _register_flow_commands(self):
        self._flow_capture = None
        self.gcode.register_command(
            'PRTOUCH_FLOW_TEST', self.cmd_PRTOUCH_FLOW_TEST,
            desc=self.cmd_PRTOUCH_FLOW_TEST_help)

    cmd_PRTOUCH_FLOW_TEST_help = (
        "Log the nozzle load cell during a slow/fast extrusion square wave "
        "at the wastebin (SLOW=, FAST= mm^3/s, HOLD= s, CYCLES=, SPS=640|1280, PA=, FILL=, PLACE=bin|air, "
        "MOVE=0|1)")

    # ------------------------------------------------------------------

    def cmd_PRTOUCH_FLOW_TEST(self, gcmd):
        slow = gcmd.get_float('SLOW', 2., above=0., maxval=40.)
        fast = gcmd.get_float('FAST', 12., above=0., maxval=40.)
        hold = gcmd.get_float('HOLD', 1.0, minval=.3, maxval=10.)
        cycles = gcmd.get_int('CYCLES', 3, minval=1, maxval=10)
        sps = gcmd.get_int('SPS', DEFAULT_SPS)
        move = bool(gcmd.get_int('MOVE', 1, minval=0, maxval=1))
        fill = bool(gcmd.get_int('FILL', 1, minval=0, maxval=1))
        place = gcmd.get('PLACE', 'bin').lower()
        if place not in ('bin', 'air'):
            raise gcmd.error("PLACE must be bin or air")
        pa = gcmd.get_float('PA', None, minval=0., maxval=1.)
        if sps not in SPS_BITS:
            raise gcmd.error("SPS must be one of %s"
                             % ", ".join(str(s) for s in sorted(SPS_BITS)))
        if fast <= slow:
            raise gcmd.error("FAST must be greater than SLOW")
        if self._armed or self._flow_capture is not None:
            raise gcmd.error("prtouch is busy")
        self._require_flow_test_conditions(gcmd)

        toolhead = self.printer.lookup_object('toolhead')
        extruder = toolhead.get_extruder()
        box = self.printer.lookup_object('box', None)
        if place == 'air':
            self._move_to_free_air(toolhead)
        elif box is not None:
            box.move_to_wastebin()
        toolhead.wait_moves()
        fill_note = None
        if box is not None and fill:
            # The buffer spring pulls on the toolhead once its slack is
            # used up, which masks the nozzle pressure; start with it full.
            try:
                fed = box.fill_buffer()
                fill_note = ("CFS buffer topped up"
                             + ("" if fed is None else " (%.1f mm fed)" % fed))
            except Exception as exc:
                fill_note = "CFS buffer top-up skipped: %s" % (exc,)
        old_pa = self._current_pressure_advance(extruder)
        used_pa = old_pa if pa is None else pa

        capture = {'samples': {}, 'notes': [], 'busy': False,
                   'buf_len_max': 0, 'page_max': 0, 'cfs': [],
                   'cfs_busy': False,
                   'cfs_driver': self._flow_cfs_driver(box)}
        if fill_note:
            capture['notes'].append(fill_note)
        schedule = []
        timer = cfs_timer = None
        started = False
        self.gcode.run_script_from_command(
            "SAVE_GCODE_STATE NAME=_prtouch_flow_test")
        motion_limits.save_motion_limits(
            self.printer, self.gcode, "_prtouch_flow_limits",
            include_gcode=True)
        try:
            if pa is not None:
                self.gcode.run_script_from_command(
                    "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % (pa,))
            self._start_flow_capture(sps)
            started = True
            self._flow_capture = capture
            timer = self.reactor.register_timer(
                self._flow_poll_timer, self.reactor.NOW)
            if capture['cfs_driver'] is not None:
                cfs_timer = self.reactor.register_timer(
                    self._flow_cfs_timer, self.reactor.NOW)
            self.gcode.run_script_from_command("M83")
            self.gcode.run_script_from_command(
                "G1 E%.2f F%.1f" % (PRIME_MM, slow / FILAMENT_AREA * 60.))
            toolhead.wait_moves()
            self._wait_print_time(self._estimated_print_time() + BASELINE_S)
            schedule = self._queue_square_wave(
                toolhead, slow, fast, hold, cycles, move)
            self._wait_print_time(schedule[-1][1] + SETTLE_S)
        finally:
            if timer is not None:
                self.reactor.unregister_timer(timer)
            if cfs_timer is not None:
                self.reactor.unregister_timer(cfs_timer)
            self._flow_capture = None
            if started:
                try:
                    self.stop_pres_cmd.send(
                        [self.pres_oid, self.pres_idle_swap_state])
                except Exception:
                    logging.exception("prtouch flow test: stop_pres failed")
            if pa is not None and old_pa is not None:
                self.gcode.run_script_from_command(
                    "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % (old_pa,))
            self.gcode.run_script_from_command(
                "RESTORE_GCODE_STATE NAME=_prtouch_flow_test MOVE=0")
            motion_limits.restore_motion_limits(
                self.gcode, "_prtouch_flow_limits", include_gcode=True,
                move=0)
        if box is not None:
            try:
                box.move_to_wastebin()
                box.flush_clean_snap(fan_after=0.0)
            except Exception as exc:
                capture['notes'].append("wipe failed: %s" % (exc,))

        rows = self._label_samples(capture['samples'], schedule)
        rows, capture['rejected'] = self.reject_outliers(rows)
        path = self._write_flow_csv(
            rows, slow, fast, hold, sps, used_pa, move)
        if path and capture['cfs'] and rows:
            self._write_cfs_csv(path, capture['cfs'], rows[0][4])
        self._report_flow_summary(
            gcmd, rows, schedule, sps, used_pa, move, path, capture)

    # ------------------------------------------------------------------

    def _require_flow_test_conditions(self, gcmd):
        eventtime = self.reactor.monotonic()
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None:
            state = print_stats.get_status(eventtime).get('state')
            if state == 'printing':
                raise gcmd.error("Pause or finish the print first")
        toolhead = self.printer.lookup_object('toolhead')
        temp, target = toolhead.get_extruder().get_heater().get_temp(eventtime)
        if target < 170. or temp < target - 5.:
            raise gcmd.error(
                "Heat the nozzle to printing temperature first "
                "(e.g. M109 S220)")
        homed = toolhead.get_status(eventtime).get('homed_axes', '')
        if 'x' not in homed or 'y' not in homed:
            self.gcode.run_script_from_command("HOME_IF_NEEDED AXIS=XY")

    def _move_to_free_air(self, toolhead):
        """Park over the bed centre, high enough that nothing touches the
        extrudate, so the load cell sees only the extrusion itself."""
        status = toolhead.get_status(self.reactor.monotonic())
        if 'z' not in status.get('homed_axes', ''):
            self.gcode.run_script_from_command("G28")
            status = toolhead.get_status(self.reactor.monotonic())
        lo, hi = status['axis_minimum'], status['axis_maximum']
        z = min(hi[2] - 5., max(toolhead.get_position()[2], AIR_Z))
        self.gcode.run_script_from_command("G90")
        self.gcode.run_script_from_command("G1 Z%.2f F600" % (z,))
        self.gcode.run_script_from_command(
            "G1 X%.2f Y%.2f F6000" % ((lo[0] + hi[0]) / 2.,
                                      (lo[1] + hi[1]) / 2.))

    @staticmethod
    def _current_pressure_advance(extruder):
        stepper = getattr(extruder, 'extruder_stepper', None)
        return getattr(stepper, 'pressure_advance', None)

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

    def _wait_print_time(self, end_print_time):
        while self._estimated_print_time() < end_print_time:
            self.reactor.pause(self.reactor.monotonic() + 0.05)

    def _queue_square_wave(self, toolhead, slow, fast, hold, cycles, move):
        """Queue the extrusion phases; return [(start, end, rate)]."""
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G91")
        rates = []
        for _ in range(cycles):
            rates += [slow, fast]
        rates.append(slow)
        schedule = []
        start = toolhead.get_last_move_time()
        direction = 1.
        for rate in rates:
            feed = rate / FILAMENT_AREA            # mm/s of filament
            length = feed * hold
            if move:
                # Constant X speed; the rate change is an E/X ratio change,
                # which is what pressure advance responds to while printing.
                self.gcode.run_script_from_command(
                    "G1 X%.4f E%.4f F%.2f"
                    % (direction * WIGGLE_SPEED * hold, length,
                       WIGGLE_SPEED * 60.))
                direction = -direction
            else:
                self.gcode.run_script_from_command(
                    "G1 E%.4f F%.1f" % (length, feed * 60.))
            end = toolhead.get_last_move_time()
            schedule.append((start, end, rate))
            start = end
        self.gcode.run_script_from_command("G90")
        return schedule

    # ------------------------------------------------------------------
    # Capture: a reactor timer keeps reading while the G-code greenlet is
    # blocked queueing moves.

    def _flow_poll_timer(self, eventtime):
        capture = self._flow_capture
        if capture is None:
            return self.reactor.NEVER
        if not capture['busy']:
            capture['busy'] = True
            try:
                self._read_flow_buffer(capture)
            finally:
                capture['busy'] = False
        return self.reactor.monotonic() + POLL_S

    @staticmethod
    def _flow_cfs_driver(box):
        """Driver for the CFS box feeding the toolhead, if any."""
        if box is None:
            return None
        drivers = getattr(box, 'drivers', None) or {}
        snap = getattr(box, 'snapshot', None)
        address = getattr(snap, 'path_box', None)
        if address not in drivers:
            address = min(drivers) if drivers else None
        return drivers.get(address)

    def _flow_cfs_timer(self, eventtime):
        """Log the CFS buffer state and feed encoder while capturing.

        The buffer spring pulls on the toolhead and the CFS refills it in
        bursts, so its activity is logged next to the load cell.
        """
        capture = self._flow_capture
        if capture is None:
            return self.reactor.NEVER
        if not capture['cfs_busy']:
            capture['cfs_busy'] = True
            driver = capture['cfs_driver']
            try:
                print_time = self._estimated_print_time()
                buf = enc = None
                try:
                    reply = driver.query_buffer(timeout=CFS_TIMEOUT)
                    buf = None if reply is None else reply.value
                except Exception:
                    pass
                try:
                    reply = driver.query_encoder(timeout=CFS_TIMEOUT)
                    enc = None if reply is None else reply.value
                except Exception:
                    pass
                capture['cfs'].append((print_time, buf, enc))
            finally:
                capture['cfs_busy'] = False
        return self.reactor.monotonic() + CFS_POLL_S

    def _read_flow_buffer(self, capture):
        from .prtouch import _decode_prtouch_frame
        samples, notes = capture['samples'], capture['notes']
        index, buf_len, pages = 0, MAX_PAGE_REQUEST, 0
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
                    buf_len = max(0, min(MAX_PAGE_REQUEST,
                                         int(page.get('buf_len', buf_len))))
                except (TypeError, ValueError):
                    pass
                capture['buf_len_max'] = max(capture['buf_len_max'], buf_len)
            ticks = _decode_prtouch_frame(page.get('ticks', b''), False)
            values = _decode_prtouch_frame(page.get('datas', b''), True)
            count = min(len(ticks), len(values))
            if not count:
                return
            capture['page_max'] = max(capture['page_max'], count)
            for tick, value in zip(ticks[:count], values[:count]):
                if value == 0:
                    continue  # an empty slot, not a reading
                clock = self.pres_mcu.clock32_to_clock64(tick & 0xFFFFFFFF)
                samples[clock] = value
            index += count

    # ------------------------------------------------------------------
    # Output

    def _label_samples(self, samples, schedule):
        rows = []
        if not samples:
            return rows
        t0 = self.pres_mcu.clock_to_print_time(min(samples))
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

    def _write_flow_csv(self, rows, slow, fast, hold, sps, pa, move):
        log_file = self.printer.get_start_args().get('log_file')
        folder = os.path.dirname(log_file) if log_file else '/tmp'
        path = os.path.join(folder, time.strftime(
            'prtouch_flow_%Y%m%d_%H%M%S.csv'))
        try:
            with open(path, 'w') as out:
                out.write("# slow=%g fast=%g hold=%g sps=%d pa=%s move=%d\n"
                          % (slow, fast, hold, sps,
                             'unknown' if pa is None else '%.4f' % pa,
                             int(move)))
                out.write("time_s,pressure,phase,rate_mm3s,ok\n")
                for t, value, phase, rate, _abs, ok in rows:
                    out.write("%.5f,%d,%d,%g,%d\n"
                              % (t, value, phase, rate, int(ok)))
        except OSError as exc:
            logging.warning("prtouch flow test: %s", exc)
            return None
        return path

    @classmethod
    def phase_levels(cls, rows, schedule):
        """Steady level of each phase relative to the idle baseline."""
        good = [r for r in rows if len(r) < 6 or r[5]]
        if not schedule:
            return None, []
        idle, _ = cls._stats([r[1] for r in good if r[4] < schedule[0][0]])
        if idle is None:
            return None, []
        levels = []
        for start, end, rate in schedule:
            cut = end - (end - start) * STEADY_FRACTION
            mean, _ = cls._stats([r[1] for r in good if cut <= r[4] < end])
            levels.append((rate, None if mean is None else mean - idle))
        return idle, levels

    @staticmethod
    def cadence_fraction(rows, sps):
        """Share of sample-to-sample gaps within 30% of 1/sps.

        A healthy capture is mostly back-to-back conversions; garbage reads
        arrive at whatever cadence the firmware happened to poll.
        """
        gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
        if not gaps:
            return 0.
        period = 1. / sps
        return sum(1 for g in gaps
                   if 0.7 * period <= g <= 1.3 * period) / float(len(gaps))

    @staticmethod
    def _write_cfs_csv(path, cfs, t0):
        cfs_path = path[:-4] + "_cfs.csv"
        try:
            with open(cfs_path, 'w') as out:
                out.write("time_s,buffer_state,encoder_mm\n")
                for print_time, buf, enc in cfs:
                    out.write("%.3f,%s,%s\n" % (
                        print_time - t0, '' if buf is None else buf,
                        '' if enc is None else '%.2f' % enc))
        except OSError as exc:
            logging.warning("prtouch flow test: %s", exc)

    @staticmethod
    def cfs_activity(cfs, schedule):
        """Per phase: buffer state changes and encoder travel (mm)."""
        out = []
        for start, end, rate in schedule:
            inside = [c for c in cfs if start <= c[0] < end]
            states = [c[1] for c in inside if c[1] is not None]
            changes = sum(1 for a, b in zip(states, states[1:]) if a != b)
            encs = [c[2] for c in inside if c[2] is not None]
            travel = (max(encs) - min(encs)) if len(encs) > 1 else None
            out.append((rate, changes, travel))
        return out

    @staticmethod
    def reject_outliers(rows):
        """Mark samples far from their neighbours' median as bad.

        Returns (rows with an ok flag appended, number rejected). A torn
        read of the live buffer shows up as isolated wild values; real
        pressure changes move the median with them, so they are kept.
        """
        values = [r[1] for r in rows]
        half = OUTLIER_WINDOW // 2
        flagged, rejected = [], 0
        for idx, row in enumerate(rows):
            window = sorted(values[max(0, idx - half):idx + half + 1])
            median = window[len(window) // 2]
            deviations = sorted(abs(v - median) for v in window)
            mad = deviations[len(deviations) // 2]
            limit = max(OUTLIER_FLOOR, OUTLIER_MADS * mad)
            ok = abs(row[1] - median) <= limit
            rejected += not ok
            flagged.append(tuple(row) + (ok,))
        return flagged, rejected

    @staticmethod
    def _stats(values):
        if not values:
            return None, None
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
        return mean, math.sqrt(var)

    @classmethod
    def step_metrics(cls, rows, schedule):
        """Per rate change: steady levels, t50/t90 and overshoot.

        Each phase's steady level is the mean of its last STEADY_FRACTION.
        The response inside a phase is normalised from the previous level
        (0) to this one (1); overshoot is how far past 1 it goes.
        """
        rows = [r for r in rows if len(r) < 6 or r[5]]
        levels = []
        for start, end, _rate in schedule:
            cut = end - (end - start) * STEADY_FRACTION
            mean, sd = cls._stats(
                [r[1] for r in rows if cut <= r[4] < end])
            levels.append((mean, sd))
        metrics = []
        for idx in range(1, len(schedule)):
            (y0, _), (y1, sd1) = levels[idx - 1], levels[idx]
            start, end, rate = schedule[idx]
            if y0 is None or y1 is None or y1 == y0:
                continue
            points = [((r[4] - start), (r[1] - y0) / (y1 - y0))
                      for r in rows if start <= r[4] < end]
            if not points:
                continue
            t50 = next((t for t, n in points if n >= .5), None)
            t90 = next((t for t, n in points if n >= .9), None)
            peak = max(n for _, n in points)
            metrics.append({
                'phase': idx,
                'rising': rate > schedule[idx - 1][2],
                'step': y1 - y0,
                't50': t50, 't90': t90,
                'overshoot': max(0., peak - 1.),
                'noise': sd1,
            })
        return metrics

    def _report_flow_summary(self, gcmd, rows, schedule, sps, pa, move,
                             path, capture):
        lines = ["PA %s, %s"
                 % ('unknown' if pa is None else '%.4f' % pa,
                    'moving (PA active)' if move
                    else 'extruding in place (PA inactive)')]
        if not rows:
            lines.append("No load-cell samples were captured.")
        else:
            duration = rows[-1][0] - rows[0][0]
            gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
            expected = duration * sps
            lines.append(
                "Captured %d of ~%d samples over %.1fs (%d/s ADC, "
                "largest gap %.0fms, buffer %d, page %d)"
                % (len(rows), expected, duration, sps,
                   (max(gaps) if gaps else 0.) * 1000.,
                   capture['buf_len_max'], capture['page_max']))
            rejected = capture.get('rejected', 0)
            if rejected:
                lines.append("Rejected %d implausible samples (%.0f%%)"
                             % (rejected, 100. * rejected / len(rows)))
            cadence = self.cadence_fraction(rows, sps)
            if (rejected > MAX_REJECT_FRACTION * len(rows)
                    or cadence < MIN_CADENCE_FRACTION):
                lines.append("Capture unreliable (%.0f%% of samples at the "
                             "ADC rate): metrics skipped."
                             % (cadence * 100.))
                metrics = []
            else:
                metrics = self.step_metrics(rows, schedule)
            for kind, rising in (("slow->fast", True), ("fast->slow", False)):
                group = [m for m in metrics if m['rising'] == rising]
                if not group:
                    continue
                def avg(key):
                    vals = [m[key] for m in group if m[key] is not None]
                    return sum(vals) / len(vals) if vals else None
                t50, t90 = avg('t50'), avg('t90')
                lines.append(
                    "%s: step %.0f, t50 %s, t90 %s, overshoot %.0f%% (n=%d)"
                    % (kind, avg('step'),
                       '-' if t50 is None else '%.0fms' % (t50 * 1000.),
                       '-' if t90 is None else '%.0fms' % (t90 * 1000.),
                       avg('overshoot') * 100., len(group)))
            idle, levels = self.phase_levels(rows, schedule)
            if levels:
                lines.append("Levels above idle: " + "  ".join(
                    "%g:%s" % (rate, '-' if lvl is None else '%+.0f' % lvl)
                    for rate, lvl in levels))
            noise = [m['noise'] for m in metrics if m['noise']]
            if metrics and noise:
                step = sum(abs(m['step']) for m in metrics) / len(metrics)
                lines.append("signal/noise %.0fx"
                             % (step / (sum(noise) / len(noise))))
        if schedule:
            used = sum((end - start) * rate for start, end, rate
                       in schedule) / FILAMENT_AREA
            lines.append("Filament used: %.1f mm" % used)
        if capture['cfs']:
            polled = [c for c in capture['cfs'] if c[1] is not None]
            states = sorted(set(c[1] for c in polled))
            lines.append("CFS: %d polls, buffer states seen %s" % (
                len(capture['cfs']), states or '-'))
            lines.append("CFS per phase (changes/feed mm): " + "  ".join(
                "%g:%d/%s" % (rate, changes,
                              '-' if travel is None else '%.1f' % travel)
                for rate, changes, travel
                in self.cfs_activity(capture['cfs'], schedule)))
        elif capture['cfs_driver'] is None:
            lines.append("CFS: no box driver found, buffer not logged")
        if path:
            lines.append("Samples saved to %s" % path)
        lines.extend(capture['notes'])
        gcmd.respond_info("\n".join(lines))
