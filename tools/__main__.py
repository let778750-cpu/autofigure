"""autofigure — v2/v3 command entry point."""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "prepare": "tools.pipeline.prepare",
    "convert": "tools.pipeline.convert",
    "check": "tools.pipeline.check",
    "math": "tools.pipeline.math",
    "arrows": "tools.arrows.arrows",
    "ingest": "tools.pipeline.ingest",
    "trace": "tools.assets.trace",
    "freeze": "tools.assets.reference_inventory",
    "repair": "tools.repair.repair",
    "release": "tools.qa.release",
    "providers": "tools.providers.providers",
    "layout": "tools.pipeline.layout",
    "cases": "tools.qa.cases",
    "compare": "tools.qa.compare",
    "hygiene": "tools.qa.hygiene",
    "migrate-v4": "tools.migrate_v4",
    "normalize-source": "tools.pipeline.normalize_source",
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
