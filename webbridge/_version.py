"""Single source of truth for the webbridge package version.

Read by:
- webbridge/__init__.py    (re-exported as webbridge.__version__)
- webbridge/server.py      (used in /health and /version endpoints)
- pyproject.toml           (kept in sync manually)
"""

__version__ = "4.2.0"
