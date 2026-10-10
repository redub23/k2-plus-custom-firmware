# Copyright (C) 2026 redub23
# This file may be distributed under the terms of the GNU GPLv3 license.
"""User trim for the filament cutter position (BOX_CUT_OFFSET).

CALIBRATE_CUT_POS writes [box] cut_pos_x, the X position where the toolhead
fully actuates the cutter lever. Wear, a different nozzle/extruder, or a cutter
that only scores the filament can call for a small nudge on top of that value.
The nudge is stored in the Box state file (filament_box.json), so it survives
restarts and a later CALIBRATE_CUT_POS, and needs no SAVE_CONFIG.

Negative offsets push the toolhead further into the lever (a firmer cut);
positive offsets stop short of it.

Without parameters the command opens a Klipper action prompt with nudge
buttons, which HelixScreen, Fluidd and Mainsail all render, so the offset can
be adjusted from the printer's touchscreen.
"""

CUT_OFFSET_SETTING = "cut_offset_x"
CUT_OFFSET_LIMIT = 1.5
CUT_OFFSET_STEPS = (-0.5, -0.1, 0.1, 0.5)


class BoxCutOffsetMixin:
    """Adds BOX_CUT_OFFSET and applies the stored trim to every cut."""

    def _cut_offset_commands(self):
        return (
            ("BOX_CUT_OFFSET", self.cmd_cut_offset,
             "Adjust the cutter X offset (OFFSET=, ADJUST=, RESET=1, TEST=1); "
             "no parameters opens an on-screen adjuster"),
        )

    # ------------------------------------------------------------------
    # Values
    # ------------------------------------------------------------------

    def get_cut_offset(self):
        try:
            value = float(self.store.setting(CUT_OFFSET_SETTING, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return max(-CUT_OFFSET_LIMIT, min(CUT_OFFSET_LIMIT, value))

    def effective_cut_x(self):
        if self.cut_x is None:
            return None
        return self.cut_x + self.get_cut_offset()

    def _cut_offset_status(self):
        return {
            "cut_pos_x": self.cut_x,
            "offset_x": self.get_cut_offset(),
            "effective_x": self.effective_cut_x(),
            "limit": CUT_OFFSET_LIMIT,
        }

    def _set_cut_offset(self, value):
        value = round(float(value), 2)
        if abs(value) > CUT_OFFSET_LIMIT + 1e-9:
            raise ValueError(
                "offset %.2f mm is outside +/-%.1f mm; re-run CALIBRATE_CUT_POS "
                "if the cutter needs a larger correction"
                % (value, CUT_OFFSET_LIMIT))
        if value == 0.0:
            self.store.clear_settings(CUT_OFFSET_SETTING)
        else:
            self.store.set_setting(CUT_OFFSET_SETTING, value)
        return value

    # ------------------------------------------------------------------
    # G-code
    # ------------------------------------------------------------------

    def cmd_cut_offset(self, gcmd):
        params = gcmd.get_command_parameters()
        if gcmd.get_int("CLOSE", 0):
            self.gcode.respond_info("action:prompt_end")
            return
        try:
            if gcmd.get_int("RESET", 0):
                self._set_cut_offset(0.0)
            elif "OFFSET" in params:
                self._set_cut_offset(gcmd.get_float("OFFSET"))
            elif "ADJUST" in params:
                self._set_cut_offset(
                    self.get_cut_offset() + gcmd.get_float("ADJUST"))
        except ValueError as exc:
            raise gcmd.error("[BOX]: BOX_CUT_OFFSET: %s" % exc)

        if gcmd.get_int("TEST", 0):
            self._test_cut(gcmd)

        status = self._cut_offset_status()
        if status["cut_pos_x"] is None:
            summary = ("Cutter offset %+.2f mm (cut_pos_x not calibrated yet; "
                       "run CALIBRATE_CUT_POS)" % status["offset_x"])
        else:
            summary = ("Cutter offset %+.2f mm: calibrated X %.2f, cutting at "
                       "X %.2f" % (status["offset_x"], status["cut_pos_x"],
                                   status["effective_x"]))
        self._info(gcmd, summary)

        # A bare call, or a press of one of the prompt's own buttons, (re)draws
        # the on-screen adjuster. Scripted calls with values stay quiet.
        if not params or gcmd.get_int("PROMPT", 0):
            self._show_cut_offset_prompt(status)

    def _test_cut(self, gcmd):
        change_engine = self.change_engine
        if change_engine._is_print_active() or change_engine._is_print_paused():
            raise gcmd.error(
                "[BOX]: Test cuts are not allowed while a print is running")
        detected, _error = self.get_filament_sensor_state()
        if detected:
            raise gcmd.error(
                "[BOX]: Unload the filament (BOX_UNLOAD) before a test cut")
        try:
            self.cut_filament(force=True)
        except Exception as exc:
            raise gcmd.error("[BOX]: Test cut failed: %s" % exc)

    def _show_cut_offset_prompt(self, status):
        lines = ["action:prompt_begin Cutter Offset"]
        if status["cut_pos_x"] is None:
            lines.append(
                "action:prompt_text Cutter not calibrated: run "
                "CALIBRATE_CUT_POS first.")
        else:
            lines.append(
                "action:prompt_text Offset %+.2f mm (cutting at X %.2f, "
                "calibrated %.2f)" % (
                    status["offset_x"], status["effective_x"],
                    status["cut_pos_x"]))
        lines.append(
            "action:prompt_text Minus pushes further into the cutter (firmer "
            "cut). Plus stops shorter. Saved immediately.")
        lines.append("action:prompt_button_group_start")
        for step in CUT_OFFSET_STEPS:
            color = "warning" if step < 0 else "info"
            lines.append(
                "action:prompt_button %+.1f|BOX_CUT_OFFSET ADJUST=%.1f PROMPT=1|%s"
                % (step, step, color))
        lines.append("action:prompt_button_group_end")
        lines.append(
            "action:prompt_button Test cut (no filament loaded)|"
            "BOX_CUT_OFFSET TEST=1 PROMPT=1|secondary")
        lines.append(
            "action:prompt_footer_button Reset|BOX_CUT_OFFSET RESET=1 PROMPT=1|error")
        lines.append(
            "action:prompt_footer_button Done|BOX_CUT_OFFSET CLOSE=1|primary")
        lines.append("action:prompt_show")
        for line in lines:
            self.gcode.respond_info(line, log=False)
