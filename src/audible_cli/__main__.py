import sys

from .cli import main as _main

if __name__ == "__main__":
    sys.exit(_main(prog_name="python -m audible"))
