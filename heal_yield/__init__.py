"""heal-yield -- a measurement harness for self-healing test loops.

Of every generated test that failed on first attempt, the fraction the repair
loop actually rescued. This tool publishes that number for every run, including
the bad ones.

The loop is commodity. The ledger is the point.
"""

__version__ = "0.1.0"
