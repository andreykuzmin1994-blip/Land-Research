"""Offline-suite global guards.

The experiment-log durability mirror (``runner._mirror_log_row``) is
disabled for the entire suite via its kill-switch env var: on developer
machines a real ``.env`` exists, and the loop/baseline tests that
exercise TSV appends must never open network connections or write to the
live mirror table (SR-6 hermeticity; reviews/17_tsv_mirror/ R-M2).
Mirror-specific tests in ``tests/test_mirror.py`` clear the variable
explicitly inside their own scope.

A falsy value (unset OR empty string) is coerced to "1": the empty
string is the documented idiom for RE-ENABLING the mirror, and honoring
it suite-wide would let ``EXPERIMENT_LOG_MIRROR_DISABLE= make tests`` on
a machine with a real ``.env`` write fabricated rows into the live
mirror — the exact SR-6 violation this guard exists to prevent
(adversarial review F1, reviews/17_tsv_mirror/). A deliberate truthy
override (e.g. "0"... any non-empty value disables the mirror) is kept.
"""

import os

if not os.environ.get("EXPERIMENT_LOG_MIRROR_DISABLE"):
    os.environ["EXPERIMENT_LOG_MIRROR_DISABLE"] = "1"
