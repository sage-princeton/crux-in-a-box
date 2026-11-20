"""Open only regular files beneath an untrusted Linux directory tree."""
import contextlib
import os
import stat
from pathlib import Path


@contextlib.contextmanager
def open_regular(root: Path, relative: str):
    """Open a file through directory descriptors and reject devices, FIFOs and symlinks.

    :param root: Trusted starting directory.
    :param relative: Workspace-relative regular file path.
    :returns: Context manager yielding a binary stream.
    """
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Invalid artifact path")
    with contextlib.ExitStack() as stack:
        directory = os.open(root, os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        for part in path.parts[:-1]:
            directory = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stream = stack.enter_context(os.fdopen(fd, "rb"))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Expected a regular artifact")
        yield stream
