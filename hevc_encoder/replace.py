from __future__ import annotations

import os
import stat
from pathlib import Path


class ReplaceError(RuntimeError):
    pass


def same_volume(a: Path, b: Path) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def copy_file_access(source: Path, dest: Path) -> None:
    """Copy Unix permission bits (and owner/group when allowed).

    FFmpeg creates a new inode with the process umask (often ``600``). After
    an in-place replace that file is what other machines see, so "everyone"
    can end up with no access. Match the original instead.
    """
    src = source.stat()
    mode = stat.S_IMODE(src.st_mode) & 0o777
    try:
        os.chmod(dest, mode)
    except OSError:
        pass
    try:
        os.chown(dest, src.st_uid, src.st_gid)
    except OSError:
        try:
            os.chown(dest, -1, src.st_gid)
        except OSError:
            pass


def commit_output(
    original: Path,
    encoded: Path,
    dest: Path,
    *,
    replace_original: bool,
) -> Path:
    """Promote a validated encode. Never called for incomplete files.

    ``replace_original=False`` writes ``dest`` beside the source and leaves
    the original untouched (testing). ``True`` swaps in place with ``.bak``
    rollback, including if the process is interrupted mid-swap.
    """
    if not encoded.exists():
        raise ReplaceError(f"encoded file missing: {encoded}")
    if encoded.resolve() == original.resolve():
        raise ReplaceError("refusing to replace a file with itself")
    if not same_volume(original.parent, encoded.parent):
        raise ReplaceError(
            f"encoded temp {encoded} is not on the same volume as {original}; "
            "set encode.temp_dir to a path on the source volume"
        )

    copy_file_access(original, encoded)

    if not replace_original:
        if dest.resolve() == original.resolve():
            raise ReplaceError("sidecar path collided with the original file")
        os.replace(encoded, dest)
        copy_file_access(original, dest)
        return dest

    return replace_in_place(original, encoded, dest)


def replace_in_place(original: Path, encoded: Path, dest: Path) -> Path:
    """Move encoded output over the original, with ``.bak`` rollback.

    Sequence: ``original → original.bak``, ``encoded → dest``, delete ``.bak``.
    If the move into place fails or is interrupted, the original is restored.
    """
    if dest.exists() and dest.resolve() != original.resolve():
        raise ReplaceError(
            f"destination already exists: {dest} (will not overwrite a different file)"
        )

    bak = Path(str(original) + ".bak")
    if bak.exists():
        bak.unlink()

    try:
        original.rename(bak)
    except OSError as exc:
        raise ReplaceError(f"could not move original aside: {exc}") from exc

    try:
        os.replace(encoded, dest)
    except BaseException as exc:
        _restore_original(original, bak)
        if isinstance(exc, OSError):
            raise ReplaceError(
                f"could not move encoded file into place: {exc}"
            ) from exc
        raise

    copy_file_access(bak, dest)
    try:
        bak.unlink()
    except OSError:
        pass
    return dest


def _restore_original(original: Path, bak: Path) -> None:
    if original.exists() or not bak.exists():
        return
    try:
        bak.rename(original)
    except OSError:
        pass
