"""Every FL API call in the controller script is checked against the official stubs."""

import ast
import inspect

import midi

from tests.fakefl import MODULES, SCRIPT

STUBS = {module.__name__: module for module in MODULES}
TREE = ast.parse(SCRIPT.read_text(encoding="utf-8"))


def fl_attribute(node):
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in STUBS | {"midi": midi}
    )


def test_calls_match_stub_signatures():
    checked, problems = 0, []
    for node in ast.walk(TREE):
        if not (isinstance(node, ast.Call) and fl_attribute(node.func)):
            continue
        module, name = STUBS.get(node.func.value.id), node.func.attr
        if module is None:
            continue
        fn = getattr(module, name, None)
        if fn is None:
            problems.append(f"{node.func.value.id}.{name} does not exist")
            continue
        args = [object()] * len(node.args)
        kwargs = {k.arg: object() for k in node.keywords}
        try:
            inspect.signature(fn).bind(*args, **kwargs)
        except TypeError as exc:
            problems.append(f"{node.func.value.id}.{name}: {exc} (line {node.lineno})")
        checked += 1
    assert not problems, "\n".join(problems)
    assert checked > 70  # guards against a vacuous pass


def test_every_referenced_attribute_exists():
    missing = [
        f"{node.value.id}.{node.attr}"
        for node in ast.walk(TREE)
        if fl_attribute(node) and not hasattr({**STUBS, "midi": midi}[node.value.id], node.attr)
    ]
    assert not missing, missing


def test_midi_output_is_called_with_a_single_packed_message():
    bound = inspect.signature(STUBS["device"].midiOutMsg).bind(0xBF | (102 << 8) | (5 << 16))
    assert bound.arguments


def test_call_allowlist_only_names_real_modules():
    import importlib.util

    spec = importlib.util.spec_from_file_location("allow", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert set(script._MODS) <= set(STUBS) - {"device"}
    for name, module in script._MODS.items():
        assert module is STUBS[name]


def test_script_header_is_what_fl_expects():
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# name=FL MCP"
    assert lines[1] == "# supportedDevices=FL MCP"


def test_script_stays_compatible_with_older_embedded_pythons():
    names = ("Match", "NamedExpr", "TypeAlias")  # TypeAlias exists only in ast of Python 3.12+
    banned = tuple(getattr(ast, name) for name in names if hasattr(ast, name))
    for node in ast.walk(TREE):
        assert not isinstance(node, banned), f"line {node.lineno}"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # `a | b` on types is 3.10+; on ints it is fine
            assert not isinstance(node.left, ast.Name) or node.left.id != "None"
    assert "removeprefix" not in SCRIPT.read_text(encoding="utf-8")
