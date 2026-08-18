"""autofigure — v2 四命令入口（prepare/convert/check/math）。"""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "prepare": "tools.v2.prepare",
    "convert": "tools.v2.convert",
    "check": "tools.v2.check",
    "math": "tools.v2.math",
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
