"""Offline-suite global guards.

The experiment-log durability mirror (``runner._mirror_log_row``) is
disabled for the entire suite via its kill-switch env var: on developer
machines a real ``.env`` exists, and the loop/baseline tests that
exercise TSV appends must never open network connections or write to the
live mirror table (SR-6 hermeticity; reviews/17_tsv_mirror/ R-M2).
Mirror-specific tests in ``tests/test_mirror.py`` clear the variable
explicitly inside their own scope.

``setdefault`` (not assignment) so an operator running the suite with a
deliberate override keeps their value.
"""

import os

os.environ.setdefault("EXPERIMENT_LOG_MIRROR_DISABLE", "1")
