"""autofigure — v2/v3 command entry point."""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "prepare": "tools.v2.prepare",
    "convert": "tools.v2.convert",
    "check": "tools.v2.check",
    "math": "tools.v2.math",
    "arrows": "tools.v2.arrows",
    "ingest": "tools.v2.ingest",
    "repair": "tools.v2.repair",
    "providers": "tools.v2.providers",
    "layout": "tools.v2.layout",
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="autofigure", description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args()
    module = importlib.import_module(COMMANDS[args.command])
    sys.argv = [f"autofigure {args.command}", *rest]
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
