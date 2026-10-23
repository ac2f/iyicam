"""``python -m iyicam`` starts the GUI; ``python -m iyicam <command> ...`` runs the CLI."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .cli import COMMANDS, main as cli_main
    if argv and argv[0] in COMMANDS:
        return cli_main(argv)
    from .gui.app import main as gui_main
    return gui_main([sys.argv[0]] + argv)


if __name__ == "__main__":
    sys.exit(main())
