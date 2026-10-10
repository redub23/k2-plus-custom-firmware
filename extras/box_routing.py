# Copyright (C) 2026 Kenneth Doerflein
# This file may be distributed under the terms of the GNU GPLv3 license.
"""CFS Tool Routing Mixin for runtime slicer tool mapping."""

import logging


class BoxRoutingMixin:
    """Provides runtime tool routing (BOX_SET_ROUTING, BOX_CLEAR_ROUTING)."""

    def _init_routing(self):
        # Tool routing table: maps slicer tool index -> physical slot index.
        # Default is 1:1 (T0->Slot 0, T1->Slot 1, etc.).
        # Updated via BOX_SET_ROUTING and cleared via BOX_CLEAR_ROUTING.
        self.tool_routing = {}
        for event in (
                "print_stats:complete_printing",
                "print_stats:error_printing",
                "print_stats:cancelled_printing"):
            self.printer.register_event_handler(
                event, self._reset_tool_routing)

    def _routing_commands(self):
        return (
            ("BOX_SET_ROUTING", self.cmd_set_routing,
             "Set slicer-tool-to-physical-slot routing (e.g. BOX_SET_ROUTING T0=2 T1=0; "
             "MERGE=1 keeps routes not named)"),
            ("BOX_CLEAR_ROUTING", self.cmd_clear_routing,
             "Reset tool routing to 1:1 default"),
        )

    def _routing_status(self):
        return {
            str(tool): slot
            for tool, slot in sorted(self.tool_routing.items())
        }

    def _reset_tool_routing(self, *args):
        """Clear custom tool routing back to 1:1 default.
        Called automatically on print complete/cancel/error so that prints
        started directly from the physical screen always use default 1:1 mapping.
        """
        if self.tool_routing:
            self.tool_routing = {}
            logging.info("box: tool routing cleared (print event)")

    def cmd_set_routing(self, gcmd):
        """Handle BOX_SET_ROUTING T0=2 T1=0 ...

        Maps slicer tool numbers to physical CFS slots at runtime without
        modifying G-code files. Persists until print ends or BOX_CLEAR_ROUTING
        is called. MERGE=1 updates only the named tools, so a screen can send
        one remap per tool without each command erasing the previous ones.
        """
        params = gcmd.get_command_parameters()
        merge = bool(gcmd.get_int("MERGE", 0, minval=0, maxval=1))
        new_routing = {}
        for key, val in params.items():
            if not key.startswith("T"):
                continue
            try:
                slicer_tool = int(key[1:])
                physical_slot = int(val)
            except (ValueError, IndexError):
                raise gcmd.error(
                    "[BOX]: BOX_SET_ROUTING parameter %s=%s is invalid; "
                    "use T<n>=<slot> (e.g. T0=2)" % (key, val))
            if not self.is_valid_slot(physical_slot):
                raise gcmd.error(
                    "[BOX]: BOX_SET_ROUTING T%d=%d: slot %d is not an "
                    "online CFS slot" % (slicer_tool, physical_slot,
                                          physical_slot))
            new_routing[slicer_tool] = physical_slot

            cmd_name = "T%d" % slicer_tool
            is_registered = (
                self.gcode.is_command_registered(cmd_name)
                if hasattr(self.gcode, "is_command_registered")
                else cmd_name in self.gcode.ready_gcode_handlers
            )
            if not is_registered:
                self.gcode.register_command(
                    cmd_name,
                    lambda gcmd, st=slicer_tool: self.change_engine.select_tool(
                        gcmd, st),
                    desc="Select tool T%d (routed)" % slicer_tool,
                )
            self.registered_tools.add(slicer_tool)

        if not new_routing:
            raise gcmd.error(
                "[BOX]: BOX_SET_ROUTING requires at least one "
                "T<n>=<slot> parameter")

        if merge:
            merged = dict(self.tool_routing)
            merged.update(new_routing)
            new_routing = merged
        self.tool_routing = new_routing
        mapping_str = " ".join("T%d->Slot%d" % (k, v)
                               for k, v in sorted(new_routing.items()))
        self._info(gcmd, "Tool routing set: %s" % mapping_str)

    def cmd_clear_routing(self, gcmd):
        """Handle BOX_CLEAR_ROUTING - resets all tool routing to 1:1 default."""
        self.tool_routing = {}
        self._info(gcmd, "Tool routing cleared; using 1:1 slot mapping")
