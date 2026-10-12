"""Transient, throttled progress on the terminal; never an audit-log event."""
import sys
import threading
import time
from contextlib import contextmanager
import math


class Progress:
    def __init__(self, stream=None, *, clock=time.monotonic, redact=None, _root=None,
                 _sink=None, _start=0, _end=1):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = self.stream.isatty()
        self.clock = clock
        self.redact = redact or (lambda value: value)
        self.last_time = float("-inf")
        self.stage = None
        self.visible = False
        self.root = _root or self
        self.mutex = self.root.mutex if _root else threading.RLock()
        self.percent = 0
        self.directory = "."
        self._sink = _sink or (lambda fraction: fraction)
        self._start, self._end = _start, _end

    def _map(self, fraction):
        return self._sink(self._start + (self._end - self._start) * fraction)

    def scope(self, start, end):
        """Reserve a fraction of this operation in the overall backup estimate."""
        return Progress(self.stream, clock=self.clock, redact=self.redact, _root=self.root,
                        _sink=self._map, _start=start, _end=end)

    def parallel(self, weights):
        """Combine concurrent workers by completed weight, not finishing order."""
        weights = [max(1, weight) for weight in weights]
        fractions = [0] * len(weights)
        total = sum(weights)
        def report(index, fraction):
            fractions[index] = max(fractions[index], fraction)
            return self._map(sum(w * f for w, f in zip(weights, fractions)) / total)
        return [Progress(self.stream, clock=self.clock, redact=self.redact, _root=self.root,
                         _sink=lambda fraction, i=i: report(i, fraction)) for i in range(len(weights))]

    def update(self, stage, directory, completed=0, total=None, *, force=False):
        if not self.enabled:
            return
        root = self.root
        with root.mutex:
            now = self.clock()
            fraction = 0 if total is None else min(1, max(0, completed / max(1, total)))
            root.percent = max(root.percent, min(99.9, self._map(fraction) * 100))
            directory = str(directory)
            if (not force and stage == root.stage and directory == root.directory
                    and now - root.last_time < 0.2):
                return
            # Paths can contain terminal control characters, including newlines.
            label = self.redact(f"{stage} | {directory}")
            label = "".join(c if c.isprintable() else "?" for c in label)
            self.stream.write(f"\r\033[2K{root.percent:5.1f}% do backup (estimado) | {label}")
            self.stream.flush()
            root.stage, root.directory, root.last_time, root.visible = stage, directory, now, True

    def finish(self):
        """Only a successfully completed invocation can display 100%."""
        if self.enabled:
            with self.root.mutex:
                self.root.percent = 100
                self.update("Backup concluído", self.root.directory, force=True)
                self.stream.write("\n")
                self.stream.flush()
                self.root.visible = False

    def clear(self):
        if self.enabled:
            with self.mutex:
                if self.root.visible:
                    self.stream.write("\r\033[2K")
                    self.stream.flush()
                    self.root.visible = False

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
            self.update(stage, directory, 100, 100, force=True)
