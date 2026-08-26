"""autofigure — v2/v3 command entry point."""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "prepare": "tools.prepare",
    "convert": "tools.convert",
    "check": "tools.check",
    "math": "tools.math",
    "arrows": "tools.arrows",
    "ingest": "tools.ingest",
    "trace": "tools.trace",
    "freeze": "tools.reference_inventory",
    "repair": "tools.repair",
    "release": "tools.release",
    "providers": "tools.providers",
    "layout": "tools.layout",
    "cases": "tools.cases",
    "compare": "tools.compare",
    "hygiene": "tools.hygiene",
    "migrate-v4": "tools.migrate_v4",
    "normalize-source": "tools.normalize_source",
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
