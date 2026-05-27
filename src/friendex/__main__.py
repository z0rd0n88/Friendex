"""``python -m friendex`` entry point.

Thin shim that delegates to :func:`friendex.main.main`. Kept separate from
:mod:`friendex.__init__` so importing the package does not run the CLI.
"""

from __future__ import annotations

from friendex.main import main

if __name__ == "__main__":
    main()
