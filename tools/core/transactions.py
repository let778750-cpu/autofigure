"""Small recoverable file transactions for flat AutoFigure cases."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


@contextmanager
def recoverable_case_transaction(
    paths: list[Path], *, staging_root: Path, label: str
) -> Iterator[None]:
    """Restore every declared path if a multi-file case mutation fails."""

    staging_root.mkdir(parents=True, exist_ok=True)
    transaction_dir = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=staging_root))
    snapshots = transaction_dir / "snapshots"
    snapshots.mkdir()
    records: list[dict[str, object]] = []
    unique_paths = list(dict.fromkeys(path.resolve() for path in paths))
    for index, path in enumerate(unique_paths):
        existed = path.is_file()
        snapshot = snapshots / f"{index:04d}.bin"
        if existed:
            shutil.copy2(path, snapshot)
        records.append(
            {
                "path": str(path),
                "existed": existed,
                "snapshot": str(snapshot) if existed else None,
            }
        )
    journal = transaction_dir / "recovery-journal.json"
    journal.write_text(
        json.dumps(
            {"schema_version": "1.0.0", "label": label, "records": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        yield
    except BaseException:
        for record in records:
            destination = Path(str(record["path"]))
            if record["existed"]:
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix + ".rollback")
                shutil.copy2(Path(str(record["snapshot"])), temporary)
                os.replace(temporary, destination)
            elif destination.is_file():
                destination.unlink()
        raise
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass


@contextmanager
def staged_case_copy(
    case_root: Path, *, staging_root: Path, label: str
) -> Iterator[Path]:
    """Yield a private, disposable copy of one flat case.

    Expensive compilers should write only inside this copy.  The caller can
    validate the complete result before publishing selected files back to the
    formal case.  Keeping the shadow below the project-owned staging root also
    makes an interrupted build discoverable without placing temporary files in
    the case itself.
    """

    source = case_root.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    staging = staging_root.resolve()
    if staging == source or source in staging.parents:
        raise ValueError("staging_root must not be inside the case being copied")
    staging.mkdir(parents=True, exist_ok=True)
    transaction_dir = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=staging))
    shadow = transaction_dir / "case" / source.name
    try:
        shutil.copytree(source, shadow)
        yield shadow
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        try:
            staging.rmdir()
        except OSError:
            pass


def _publish_replace(source: Path, destination: Path) -> None:
    """Copy one staged file beside its destination and atomically replace it."""

    temporary: Path | None = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle, raw_path = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".publish",
            dir=destination.parent,
        )
        os.close(handle)
        temporary = Path(raw_path)
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_staged_files(
    files: Sequence[tuple[Path, Path]], *, staging_root: Path, label: str
) -> None:
    """Atomically publish a validated set of staged files as one transaction.

    Each individual replacement uses ``os.replace``.  If any replacement or
    later filesystem operation fails, ``recoverable_case_transaction`` restores
    every destination to its exact pre-publication bytes (and removes files
    that did not exist before publication).
    """

    normalized: list[tuple[Path, Path]] = []
    destinations: set[Path] = set()
    for raw_source, raw_destination in files:
        source = raw_source.resolve()
        destination = raw_destination.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if destination in destinations:
            raise ValueError(f"duplicate publication destination: {destination}")
        destinations.add(destination)
        normalized.append((source, destination))

    with recoverable_case_transaction(
        [destination for _, destination in normalized],
        staging_root=staging_root,
        label=f"{label}-publish",
    ):
        for source, destination in normalized:
            _publish_replace(source, destination)
