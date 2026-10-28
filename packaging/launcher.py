"""Entry point of the packaged program: ``iyiCAM`` opens the window (and the files given to it), ``iyicam-cli``
runs the command line commands (``iyicam-cli --help``)."""
import os
import sys


def main() -> int:
    if os.path.basename(sys.argv[0]).lower().startswith("iyicam-cli"):
        from iyicam.cli import main as cli_main
        return cli_main(sys.argv[1:] or ["--help"])
    from iyicam.__main__ import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
