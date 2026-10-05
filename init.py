#!/usr/bin/env python3
"""Install agents-infra: host prerequisites plus all (or the named) components.

    python3 init.py              # everything
    python3 init.py openclaw     # one component
    python3 init.py --help
"""

import sys

if sys.version_info < (3, 11):
    sys.exit(f"init.py needs Python 3.11+, found {sys.version.split()[0]}")

from installer.cli import main

if __name__ == "__main__":
    sys.exit(main())
