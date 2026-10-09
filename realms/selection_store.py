"""Non-mutating snapshots of the plugin's small rollback-journal stores."""
from contextlib import contextmanager
import fcntl  # windows-footgun: ok — runtime package rejects non-Linux hosts
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time

LOCK_TIMEOUT = 30.0
_held = threading.local()


def _acquire(fd, mode):
    deadline = time.monotonic() + LOCK_TIMEOUT
    delay = 0.001
    while True:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Realms store stayed locked by another process") from None
            time.sleep(delay)
            delay = min(delay * 2, 0.01)


@contextmanager
def store_lock(path, *, shared):
    """Cross-process reader/writer lock for one store, re-entrant per thread.

    Every Hermes CLI chat and profile backend is its own process, and all of
    them share these stores. Writers hold the lock exclusively for their whole
    transaction and readers share it for their whole snapshot, so a reader never
    meets another process's in-flight write. It is a flock on the store's
    directory: readers create nothing, and the kernel releases it when the
    holder dies.

    A flock on the store file itself is a turnstile that every caller passes
    through before taking the directory lock. A waiting writer keeps it closed,
    so a steady stream of overlapping readers cannot starve it. (SQLite's own
    locks are POSIX fcntl locks, which do not interact with flock on Linux.)
    """
    stores = _held.__dict__.setdefault("stores", {})
    key = os.path.abspath(os.path.dirname(path))
    entry = stores.get(key)
    if entry is not None:
        if entry[0] and not shared:
            raise RuntimeError("A store cannot be written while this thread is reading it")
        yield
        return
    try:
        gate = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        gate = None  # no store yet (or an unsafe one, which the snapshot refuses)
    fd = None
    try:
        if gate is not None:
            _acquire(gate, fcntl.LOCK_EX)
        fd = os.open(key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        _acquire(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    finally:
        if gate is not None:
            os.close(gate)  # reopen the turnstile once this caller is through
    try:
        stores[key] = (shared,)
        try:
            yield
        finally:
            del stores[key]
    finally:
        os.close(fd)  # releases the lock


@contextmanager
def read_snapshot(path, *, mode=None):
    path = Path(path)
    with store_lock(path, shared=True):
        with _read_snapshot(path, mode=mode) as db:
            yield db


@contextmanager
def _read_snapshot(path, *, mode=None):
    """Read the validated object, never let SQLite reopen its filename.

    These stores default to DELETE journaling. WAL/hot journals require an
    explicit writer/recovery; a selector must neither create SHM nor ignore a
    committed WAL. Metadata and sidecar checks bracket both copying and use so
    concurrent writes/replacement refuse rather than authorize a torn snapshot.
    """
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()  # windows-footgun: ok — runtime package rejects non-Linux hosts
                or (mode is not None and stat.S_IMODE(opened.st_mode) != mode)
                or not 100 <= opened.st_size <= 16 * 1024 * 1024):
            raise ValueError("Selection store cannot be safely read")

        def stamp(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)

        def unchanged():
            if (stamp(os.fstat(fd)) != stamp(opened)
                    or stamp(path.lstat()) != stamp(opened)):
                raise ValueError("Selection store changed while reading")
            for suffix in ('-journal', '-wal', '-shm'):
                if os.path.lexists(str(path) + suffix):
                    raise ValueError("Selection store journal requires explicit recovery")

        unchanged()
        data = bytearray()
        while len(data) < opened.st_size:
            chunk = os.read(fd, min(65536, opened.st_size - len(data)))
            if not chunk:
                raise ValueError("Selection store was truncated")
            data.extend(chunk)
        unchanged()
        if data[:16] != b'SQLite format 3\0' or data[18:20] != b'\1\1':
            raise ValueError("Selection requires a rollback-journal SQLite store")
        db = sqlite3.connect(':memory:')
        try:
            # Python builds without deserialize cannot safely perform this read.
            if not hasattr(db, 'deserialize'):
                raise ValueError("SQLite snapshot reads are unavailable")
            db.deserialize(bytes(data))
            db.execute('PRAGMA query_only=ON')
            yield db
            unchanged()
        finally:
            db.close()
    finally:
        os.close(fd)
