"""The agent's track: what Verity is doing, step by step, for each applicant.

The agent reports through a Tracker. On the command line that just prints; the
harness (server.py) subclasses it to feed the monitoring UI.
"""

import os
import time
from pathlib import Path

# The steps every applicant goes through, in order: (key, label)
STEPS = [
    ("read", "Read input row"),
    ("open", "Open agency site"),
    ("search", "Enter search"),
    ("security", "Security check"),
    ("record", "Open license record"),
    ("parse", "Read record"),
    ("compare", "Compare with input"),
    ("close", "Close search"),
]
STEP_LABELS = dict(STEPS)

# Step states
ACTIVE, DONE, WAITING, FAILED, SKIPPED = "active", "done", "waiting", "failed", "skipped"


class Tracker:
    """Base tracker: prints progress. Override the on_* hooks to observe a run."""

    def __init__(self, echo=print):
        self.echo = echo
        self.cancelled = False
        self.live_path = None      # where to keep the latest view of the agent's browser
        self._last_shot = 0.0
        self.states = {}           # (row, step key) -> latest state

    # -- called by the agent -------------------------------------------------
    def log(self, msg, row=None, level="info"):
        self.echo(msg if row is None else f"    {msg}")
        self.on_log(msg, row, level)

    def step(self, row, key, state, detail=""):
        self.states[(row, key)] = state
        if state in (WAITING, FAILED) or (detail and state in (ACTIVE, DONE)):
            mark = {WAITING: "!", FAILED: "x"}.get(state, "-")
            self.echo(f"    {mark} {STEP_LABELS[key]}" + (f": {detail}" if detail else ""))
        self.on_step(row, key, state, detail)

    def shot(self, page, min_gap=0.0):
        """Keep a current picture of the agent's browser for the live view."""
        if not self.live_path or time.time() - self._last_shot < min_gap:
            return
        try:
            tmp = Path(str(self.live_path) + ".tmp")
            page.screenshot(path=str(tmp), type="jpeg", quality=70)
            os.replace(tmp, self.live_path)
            self._last_shot = time.time()
            self.on_shot()
        except Exception:
            pass

    # -- hooks ---------------------------------------------------------------
    def on_start(self, rows, out_dir): pass
    def on_log(self, msg, row, level): pass
    def on_step(self, row, key, state, detail): pass
    def on_shot(self): pass
    def on_row_result(self, row, result): pass
    def on_finish(self, xlsx, report, results): pass
