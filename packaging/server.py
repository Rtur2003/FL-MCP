"""Entry point of the .mcpb bundle: the package sits next to this file."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fl_mcp.cli import main  # noqa: E402

main(["serve"])
