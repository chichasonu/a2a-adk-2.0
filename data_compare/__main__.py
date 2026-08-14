"""Module entry point.

Usage:
    python -m data_compare --table FeedBack --join-columns chatmessageid
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
