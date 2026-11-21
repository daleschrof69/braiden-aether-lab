#!/usr/bin/env python3
"""Entry point. `python run.py` -> GUI, `python run.py <args>` -> CLI."""
import sys


def main():
    if len(sys.argv) > 1:
        from aetherlab.cli import main as cli_main
        return cli_main(sys.argv[1:])
    from aetherlab.ui.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
