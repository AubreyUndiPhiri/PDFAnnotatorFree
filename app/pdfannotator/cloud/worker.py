"""Run network calls off the UI thread and get the result back on it.

    run(fn, on_done, on_error)      # fn runs in a worker thread
"""
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_pool = QThreadPool()
_pool.setMaxThreadCount(4)
_live = set()   # keep each job's messenger alive until it has reported


class _Messenger(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn, messenger):
        super().__init__()
        self.fn, self.messenger = fn, messenger

    def run(self):
        try:
            result = self.fn()
        except Exception as e:  # noqa: BLE001 - handed to on_error on the UI thread
            e.trace = traceback.format_exc()
            self._emit(self.messenger.failed, e)
        else:
            self._emit(self.messenger.done, result)

    @staticmethod
    def _emit(signal, value):
        try:
            signal.emit(value)
        except RuntimeError:
            pass  # the app is closing


def run(fn, on_done=None, on_error=None):
    """Call fn() in a worker thread; then on_done(result) or on_error(exc)
    on the UI thread."""
    messenger = _Messenger()
    _live.add(messenger)

    def finish(callback, value):
        _live.discard(messenger)
        if callback is not None:
            callback(value)

    messenger.done.connect(lambda value: finish(on_done, value))
    messenger.failed.connect(lambda exc: finish(on_error, exc))
    _pool.start(_Job(fn, messenger))


def wait_all(msecs=30000):
    """Wait for running jobs (tests, and closing the app)."""
    return _pool.waitForDone(msecs)
