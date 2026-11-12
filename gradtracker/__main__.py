"""python -m gradtracker is the same as the grad-tracker command."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
