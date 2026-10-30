"""Transient, throttled progress on the terminal; never an audit-log event."""
import math
import sys
import threading
import time
from contextlib import contextmanager

from .terminal import cell_width, lane_line, progress_line, terminal_columns, terminal_rows


class Progress:
    def __init__(self, stream=None, *, clock=time.monotonic, redact=None, _root=None,
                 _sink=None, _start=0, _end=1, _lane=None):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = self.stream.isatty()
        self.clock = clock
        self.redact = redact or (lambda value: value)
        self.last_time = float("-inf")
        self.stage = None
        self.visible = False
        self._rendered_columns = 0
        self.root = _root or self
        self.mutex = self.root.mutex if _root else threading.RLock()
        self.percent = 0
        self.directory = "."
        self._sink = _sink or (lambda fraction: fraction)
        self._start, self._end = _start, _end
        self._lane = _lane
        # Root only: one display line per active parallel worker, keyed by lane index.
        self.lanes = {}
        self._rendered_rows = 0
        self._global = ""

    def _map(self, fraction):
        return self._sink(self._start + (self._end - self._start) * fraction)

    def scope(self, start, end):
        """Reserve a fraction of this operation in the overall backup estimate."""
        return Progress(self.stream, clock=self.clock, redact=self.redact, _root=self.root,
                        _sink=self._map, _start=start, _end=end, _lane=self._lane)

    def parallel(self, weights, labels=None):
        """Combine concurrent workers by completed weight, not finishing order.

        With labels, each worker also owns one display line (until release()) instead of
        every worker overwriting the single shared line."""
        weights = [max(1, weight) for weight in weights]
        fractions = [0] * len(weights)
        total = sum(weights)
        def report(index, fraction):
            fractions[index] = max(fractions[index], fraction)
            if index in self.root.lanes:
                self.root.lanes[index]["fraction"] = fractions[index]
            return self._map(sum(w * f for w, f in zip(weights, fractions, strict=False)) / total)
        return [Progress(self.stream, clock=self.clock, redact=self.redact, _root=self.root,
                         _sink=lambda fraction, i=i: report(i, fraction),
                         _lane=(i, labels[i]) if labels else None) for i in range(len(weights))]

    def release(self):
        """Drop this worker's display line once its work is over."""
        if self._lane is not None and self.enabled:
            with self.root.mutex:
                self.root.lanes.pop(self._lane[0], None)
                self.root._redraw()

    def update(self, stage, directory, completed=0, total=None, *, force=False, activity=None,
               indeterminate=False):
        if not self.enabled:
            return
        root = self.root
        with root.mutex:
            now = self.clock()
            if self._lane is not None:
                root.lanes.setdefault(self._lane[0], {"fraction": 0})
            fraction = 0 if total is None else min(1, max(0, completed / max(1, total)))
            root.percent = max(root.percent, min(99.9, self._map(fraction) * 100))
            directory = str(directory)
            # Redact and sanitize before measuring terminal cells/truncation.
            def clean(value):
                return "".join(c if c.isprintable() else "?" for c in self.redact(str(value)))
            if self._lane is not None:
                lane = root.lanes.setdefault(self._lane[0], {"fraction": 0})
                lane.update(label=clean(self._lane[1]), stage=clean(stage), directory=clean(directory),
                            activity=clean(activity) if activity else "", fraction=lane.get("fraction", 0))
                if not force and now - root.last_time < 0.2:
                    return
                root.last_time = now
                root._redraw()
                return
            if (not force and stage == root.stage and directory == root.directory
                    and now - root.last_time < 0.2):
                return
            columns = terminal_columns(self.stream)
            text = progress_line(root.percent, clean(stage), clean(directory),
                                 clean(activity) if activity else "", columns,
                                 indeterminate=indeterminate)
            root.stage, root.directory, root.last_time, root._global = stage, directory, now, text
            if root.lanes:
                root._redraw()
                return
            self.stream.write(self._erase(columns) + text)
            self.stream.flush()
            root._rendered_columns = cell_width(text)
            root._rendered_rows = 1
            root.visible = True

    def _redraw(self):
        """Render the overall line plus one line per active worker (root only, mutex held)."""
        if not self.enabled:
            return
        columns = terminal_columns(self.stream)
        if not self.lanes:
            # Last worker finished: fall back to the single overall line.
            if self.visible:
                self.stream.write(self._erase(columns) + self._global)
                self.stream.flush()
                self._rendered_columns, self._rendered_rows = cell_width(self._global), 1
            return
        lines = [progress_line(self.percent, "Lotes em andamento", f"{len(self.lanes)} em paralelo", "", columns)]
        room = max(0, terminal_rows(self.stream) - 3)
        shown = sorted(self.lanes.items())[:room]
        lines += [lane_line(lane, columns) for _, lane in shown]
        if len(shown) < len(self.lanes):
            lines.append(f"  +{len(self.lanes) - len(shown)} em andamento")
        self.stream.write(self._erase(columns) + "\n".join(lines))
        self.stream.flush()
        self._rendered_columns = max(cell_width(line) for line in lines)
        self._rendered_rows = len(lines)
        self.visible = True

    def finish(self):
        """Only a successfully completed invocation can display 100%."""
        if self.enabled:
            with self.root.mutex:
                self.root.percent = 100
                self.root.lanes.clear()
                self.update("Backup concluído", self.root.directory, force=True)
                self.stream.write("\n")
                self.stream.flush()
                self.root.visible = False
                self.root._rendered_columns = 0
                self.root._rendered_rows = 0

    def _erase(self, columns):
        # Resizing can reflow the previously displayed line into several rows.
        root = self.root
        if not root.visible:
            rows = 1
        elif root._rendered_rows > 1:
            rows = root._rendered_rows
        else:
            rows = max(1, math.ceil(root._rendered_columns / columns))
        return "\r\033[2K" + "\033[1A\r\033[2K" * (rows - 1)

    def clear(self):
        if self.enabled:
            with self.mutex:
                if self.root.visible:
                    self.stream.write(self._erase(terminal_columns(self.stream)))
                    self.stream.flush()
                    self.root.visible = False
                    self.root._rendered_columns = 0
                    self.root._rendered_rows = 0

    def close(self):
        self.clear()

    @contextmanager
    def task(self, stage, directory, *, measured=False):
        """Estimate opaque subprocess work by elapsed time, capped below 100%."""
        if not self.enabled:
            yield
            return
        if measured:
            # Discovery and deletion have no reliable percentage denominator.
            # Keep the measured percentage and show counters/elapsed activity
            # while waiting for rclone's next statistics sample.
            stop = threading.Event()
            state_lock = threading.RLock()
            started = self.clock()
            percent, current_activity = None, "aguardando estatísticas do rclone"
            tick = 0

            def display(*, force=False):
                nonlocal tick
                elapsed = max(0, int(self.clock() - started))
                spinner = "|/-\\"[tick % 4]
                tick += 1
                self.update(stage, directory, percent if percent is not None else 0, 100, force=force,
                            activity=f"{spinner} {current_activity} | tempo: {elapsed}s",
                            indeterminate=percent is None)

            def report(value=None, *, activity=None):
                nonlocal percent, current_activity
                with state_lock:
                    percent = value
                    if activity is not None:
                        current_activity = activity
                    display()

            def heartbeat():
                while not stop.wait(0.25):
                    with state_lock:
                        display()

            display(force=True)
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                try:
                    yield report
                finally:
                    stop.set()
                    thread.join()
            except BaseException:
                self.clear()
                raise
            else:
                self.update(stage, directory, 100, 100, force=True)
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
