"""Cross-module compatibility checks, run after every upstream merge.

Upstream and our fork edit the same modules, and a merge that keeps one side
of a file can leave the other side calling something that no longer exists.
Each of those has crashed the printer at boot before. These checks catch the
mismatch statically, with the standard library only (no Klipper needed):

    python -m unittest discover -s tests -v
"""

import ast
import dataclasses
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRAS = ROOT / "extras"
sys.path.insert(0, str(EXTRAS))

import box_protocol  # noqa: E402


def parse(name):
    path = EXTRAS / name
    return ast.parse(path.read_text(), filename=str(path))


def chain(node):
    """Dotted name of an attribute chain, e.g. self.change_engine.change."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def accessed(tree, prefix):
    """Attribute names read on `prefix` (a dotted name such as self.box)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and chain(node.value) == prefix:
            found.add(node.attr)
    return found


def class_members(tree, class_name):
    """Methods, class attributes and self.<x> assignments of a class."""
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == class_name:
            break
    else:
        raise AssertionError("class %s not found" % class_name)
    members = set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(node.name)
        elif isinstance(node, ast.Attribute) and chain(node.value) == "self":
            if isinstance(node.ctx, ast.Store):
                members.add(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            members.add(node.id)
    for base in cls.bases:
        base_name = getattr(base, "id", getattr(base, "attr", None))
        if base_name:
            for extra_file in EXTRAS.glob("*.py"):
                sub_tree = parse(extra_file.name)
                for sub in ast.walk(sub_tree):
                    if isinstance(sub, ast.ClassDef) and sub.name == base_name:
                        for node in ast.walk(sub):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                members.add(node.name)
                            elif isinstance(node, ast.Attribute) and chain(node.value) == "self":
                                if isinstance(node.ctx, ast.Store):
                                    members.add(node.attr)
    return members


def function_source(tree, source, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("function %s not found" % name)


class AutoAddressContract(unittest.TestCase):
    """box_addr.py consumes what box_protocol.py decodes (the `mode` crash)."""

    def test_reply_fields_cover_what_box_addr_reads(self):
        read = accessed(parse("box_addr.py"), "reply")
        fields = {f.name for f in dataclasses.fields(
            box_protocol.AutoAddressReply)}
        self.assertLessEqual(
            read, fields,
            "box_addr.py reads AutoAddressReply attributes that "
            "box_protocol.py does not define: %s" % sorted(read - fields))

    def test_client_methods_cover_what_box_addr_calls(self):
        called = accessed(parse("box_addr.py"), "client")
        methods = class_members(parse("box_protocol.py"), "AutoAddressClient")
        self.assertLessEqual(called, methods, sorted(called - methods))


class BoxInterfaces(unittest.TestCase):
    """box.py and box_change.py call into each other."""

    def test_box_uses_only_existing_change_engine_members(self):
        used = accessed(parse("box.py"), "self.change_engine")
        members = class_members(parse("box_change.py"), "BoxChangeEngine")
        self.assertLessEqual(used, members, sorted(used - members))

    def test_change_engine_uses_only_existing_box_members(self):
        used = accessed(parse("box_change.py"), "self.box")
        members = class_members(parse("box.py"), "Box")
        self.assertLessEqual(used, members, sorted(used - members))

    def test_tool_commands_go_through_select_tool(self):
        """T<n> must use select_tool or BOX_PRINT_START maps are ignored."""
        source_box = (EXTRAS / "box.py").read_text()
        tree_box = ast.parse(source_box)
        body = function_source(tree_box, source_box, "_register_tools")
        self.assertIn("select_tool", body)
        self.assertNotIn("change_engine.change(", body)

        routing_path = EXTRAS / "box_routing.py"
        source_routing = (routing_path if routing_path.exists() else EXTRAS / "box.py").read_text()
        tree_routing = ast.parse(source_routing)
        body = function_source(tree_routing, source_routing, "cmd_set_routing")
        self.assertIn("select_tool", body)
        self.assertNotIn("change_engine.change(", body)


class CommandRegistration(unittest.TestCase):
    """Runs the real registration code, which static checks cannot cover."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.remove, str(ROOT))
        import extras.box as box_module
        self.box_module = box_module

        class FakeGcode:
            def __init__(self):
                self.handlers = {}

            def register_command(self, name, handler, desc=None):
                self.handlers[name] = handler

        engine = type("Engine", (), {
            "parse_flush_volumes": lambda *a: None,
            "capture_pause": lambda *a: None,
            "prepare_resume": lambda *a: None,
            "complete_pause_resume": lambda *a: None})()
        box = object.__new__(box_module.Box)
        box.gcode = FakeGcode()
        box.change_engine = engine
        box._register_commands()
        self.handlers = box.gcode.handlers

    def test_mixin_commands_are_registered(self):
        for name in ("BOX_SET_ROUTING", "BOX_CLEAR_ROUTING",
                     "BOX_SET_DRY_MODE", "BOX_GET_DRY_MODE", "BOX_PAUSE_DRY",
                     "CONTINUE_PAUSE_DRY", "BOX_SET_AUTO_DRY_MODE",
                     "BOX_SET_AUTO_HUMIDITY_MODE", "BOX_CUT_OFFSET",
                     "BOX_PRIME"):
            self.assertIn(name, self.handlers)

    def test_widget_commands_are_guarded(self):
        for name in self.box_module.SAFE_WIDGET_COMMANDS:
            self.assertIn(name, self.handlers, name)
            self.assertEqual(self.handlers[name].__name__, "guarded", name)


class CutOffset(unittest.TestCase):
    """BOX_CUT_OFFSET stores a clamped trim and the cut uses it."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.remove, str(ROOT))
        import extras.box as box_module

        class Store:
            def __init__(self):
                self.data = {"runtime": {}}

            def setting(self, name, default=None):
                return self.data["runtime"].get(name, default)

            def set_setting(self, name, value):
                self.data["runtime"][name] = value

            def clear_settings(self, *names):
                for name in names:
                    self.data["runtime"].pop(name, None)

        class Gcode:
            def __init__(self):
                self.lines = []

            def respond_info(self, msg, log=True):
                self.lines.append(msg)

        class Error(Exception):
            pass

        class Cmd:
            def __init__(self, gcode, **params):
                self.params = {k.upper(): str(v) for k, v in params.items()}
                self.gcode = gcode

            def get_command_parameters(self):
                return dict(self.params)

            def get_int(self, name, default=None):
                return int(self.params.get(name, default))

            def get_float(self, name, default=None):
                return float(self.params.get(name, default))

            def error(self, msg):
                return Error(msg)

            def respond_info(self, msg, log=True):
                self.gcode.respond_info(msg)

        self.Cmd, self.Error = Cmd, Error
        box = object.__new__(box_module.Box)
        box.store = Store()
        box.gcode = Gcode()
        box.cut_x = -8.0
        self.box = box

    def run_cmd(self, **params):
        self.box.cmd_cut_offset(self.Cmd(self.box.gcode, **params))

    def test_adjust_set_reset(self):
        self.run_cmd(ADJUST=-0.1)
        self.run_cmd(ADJUST=-0.1)
        self.assertAlmostEqual(self.box.get_cut_offset(), -0.2)
        self.assertAlmostEqual(self.box.effective_cut_x(), -8.2)
        self.run_cmd(OFFSET=0.5)
        self.assertAlmostEqual(self.box.effective_cut_x(), -7.5)
        self.run_cmd(RESET=1)
        self.assertEqual(self.box.get_cut_offset(), 0.0)
        self.assertNotIn("cut_offset_x", self.box.store.data["runtime"])

    def test_out_of_range_rejected(self):
        with self.assertRaises(self.Error):
            self.run_cmd(OFFSET=2.0)
        self.assertEqual(self.box.get_cut_offset(), 0.0)

    def test_bare_call_opens_prompt(self):
        self.box.gcode.lines.clear()
        self.run_cmd()
        self.assertIn("action:prompt_show", self.box.gcode.lines)
        self.run_cmd(CLOSE=1)
        self.assertEqual(self.box.gcode.lines[-1], "action:prompt_end")

    def test_cut_filament_moves_to_trimmed_x(self):
        source = (EXTRAS / "box.py").read_text()
        body = function_source(ast.parse(source), source, "cut_filament")
        self.assertIn("effective_cut_x()", body)
        self.assertNotIn("x=self.cut_x", body)


class ReleaseFiles(unittest.TestCase):
    def test_every_module_compiles(self):
        for path in sorted(EXTRAS.glob("*.py")):
            compile(path.read_text(), str(path), "exec")

    def test_release_index_is_current(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "release_index.py"),
             "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
