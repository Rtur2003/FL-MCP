"""Regenerate src/fl_mcp/api.json from the fl-studio-api-stubs package.

Usage: python scripts/gen_api_index.py [stubs_root]   (default: the installed stubs)
Only names, signatures and constant values are kept (facts, no documentation text).
"""

import ast
import importlib.util
import json
import sys
from pathlib import Path

MODULES = [
    "transport",
    "mixer",
    "channels",
    "patterns",
    "playlist",
    "plugins",
    "ui",
    "general",
    "arrangement",
]
OUT = Path(__file__).resolve().parents[1] / "src" / "fl_mcp" / "api.json"


def signature(fn: ast.FunctionDef) -> str:
    args = fn.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parts = []
    for arg, default in zip(positional, defaults, strict=True):
        text = arg.arg
        if arg.annotation is not None:
            text += ":" + ast.unparse(arg.annotation)
        if default is not None:
            text += "=" + ast.unparse(default)
        parts.append(text)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    ret = ast.unparse(fn.returns) if fn.returns else "None"
    return f"{fn.name}({', '.join(parts)})" + ("" if ret == "None" else f"->{ret}")


def functions(folder: Path) -> list[str]:
    found: dict[str, str] = {}
    for path in sorted(folder.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            if any(isinstance(d, ast.Name) and d.id == "overload" for d in node.decorator_list):
                continue
            found[node.name] = signature(node)
    return sorted(found.values())


def evaluate(node: ast.AST, env: dict[str, int]) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -evaluate(node.operand, env)
    if isinstance(node, ast.BinOp):
        left, right = evaluate(node.left, env), evaluate(node.right, env)
        ops = {
            ast.Add: left + right,
            ast.Sub: left - right,
            ast.Mult: left * right,
            ast.LShift: left << right,
            ast.BitOr: left | right,
            ast.BitAnd: left & right,
        }
        return ops[type(node.op)]
    raise ValueError("unsupported")


def constants(folder: Path) -> list[str]:
    pending: dict[str, ast.AST] = {}
    for path in sorted(folder.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    pending[target.id] = node.value
    env: dict[str, int] = {}
    progress = True
    while pending and progress:
        progress = False
        for name, expr in list(pending.items()):
            try:
                env[name] = evaluate(expr, env)
            except (ValueError, KeyError, TypeError):
                continue
            del pending[name]
            progress = True
    return [f"{name}={value}" for name, value in sorted(env.items())]


def main() -> None:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    else:
        spec = importlib.util.find_spec("transport")
        if spec is None or spec.origin is None:
            sys.exit("install fl-studio-api-stubs or pass the stubs folder")
        root = Path(spec.origin).parent.parent
    index = {name: functions(root / name) for name in MODULES}
    index["midi"] = constants(root / "midi")
    OUT.write_text(json.dumps(index, indent=0, ensure_ascii=True), encoding="utf-8")
    print({k: len(v) for k, v in index.items()}, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
