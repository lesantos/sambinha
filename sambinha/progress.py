"""Transient, throttled progress on the terminal; never an audit-log event."""
import sys
import threading
import time
from contextlib import contextmanager
import math


class Progress:
    def __init__(self, stream=None, *, clock=time.monotonic, redact=None):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = self.stream.isatty()
        self.clock = clock
        self.redact = redact or (lambda value: value)
        self.last_time = float("-inf")
        self.stage = None
        self.visible = False
        self.mutex = threading.Lock()

    def update(self, stage, directory, completed=0, total=None, *, force=False):
        if not self.enabled:
            return
        with self.mutex:
            now = self.clock()
            finished = total is not None and completed >= total
            if not force and stage == self.stage and not finished and now - self.last_time < 0.2:
                return
            percent = 0 if total is None else min(100, max(0, completed * 100 / max(1, total)))
            # Paths can contain terminal control characters, including newlines.
            label = self.redact(f"{stage} | {directory}")
            label = "".join(c if c.isprintable() else "?" for c in label)
            self.stream.write(f"\r\033[2K{label} | {percent:5.1f}% estimado")
            self.stream.flush()
            self.stage, self.last_time, self.visible = stage, now, True

    def clear(self):
        if self.enabled:
            with self.mutex:
                if self.visible:
                    self.stream.write("\r\033[2K")
                    self.stream.flush()
                    self.visible = False

    def close(self):
        self.clear()

    @contextmanager
    def task(self, stage, directory):
        """Estimate opaque subprocess work by elapsed time, capped below 100%."""
        if not self.enabled:
            yield
            return
        stop = threading.Event()
        started = self.clock()
        self.update(stage, directory, 0, 100, force=True)
        def heartbeat():
            while not stop.wait(0.25):
                estimate = min(95, 100 * (1 - math.exp(-(self.clock() - started) / 30)))
                self.update(stage, directory, estimate, 100)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            try:
                yield
            finally:
                stop.set()
                thread.join()
        except BaseException:
            self.clear()
            raise
        else:
            self.update(stage, directory, 100, 100)
