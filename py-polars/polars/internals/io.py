from __future__ import annotations

import glob
from contextlib import contextmanager, suppress
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, BinaryIO, ContextManager, Iterator, TextIO, overload

import polars.internals as pli
from polars.datatypes import PolarsDataType
from polars.dependencies import _FSSPEC_AVAILABLE, fsspec
from polars.exceptions import NoDataError
from polars.utils import normalise_filepath

with suppress(ImportError):
    from polars.polars import ipc_schema as _ipc_schema
    from polars.polars import parquet_schema as _parquet_schema


def _check_empty(b: BytesIO, context: str, read_position: int | None = None) -> BytesIO:
    if not b.getbuffer().nbytes:
        hint = (
            f" (buffer position = {read_position}; try seek(0) before reading?)"
            if context in ("StringIO", "BytesIO") and read_position
            else ""
        )
        raise NoDataError(f"empty CSV data from {context}{hint}")
    return b


def _process_http_file(path: str, encoding: str | None = None) -> BytesIO:
    from urllib.request import urlopen

    with urlopen(path) as f:
        if not encoding or encoding in {"utf8", "utf8-lossy"}:
            return BytesIO(f.read())
        else:
            return BytesIO(f.read().decode(encoding).encode("utf8"))


@overload
def _prepare_file_arg(
    file: str | list[str] | Path | BinaryIO | bytes, **kwargs: Any
) -> ContextManager[str | BinaryIO]:
    ...


@overload
def _prepare_file_arg(
    file: str | TextIO | Path | BinaryIO | bytes, **kwargs: Any
) -> ContextManager[str | BinaryIO]:
    ...


@overload
def _prepare_file_arg(
    file: str | list[str] | TextIO | Path | BinaryIO | bytes, **kwargs: Any
) -> ContextManager[str | list[str] | BinaryIO | list[BinaryIO]]:
    ...


def _prepare_file_arg(
    file: str | list[str] | TextIO | Path | BinaryIO | bytes,
    encoding: str | None = None,
    use_pyarrow: bool | None = None,
    **kwargs: Any,
) -> ContextManager[str | BinaryIO | list[str] | list[BinaryIO]]:
    """
    Prepare file argument.

    Utility for read_[csv, parquet]. (not to be used by scan_[csv, parquet]).
    Returned value is always usable as a context.

    A :class:`StringIO`, :class:`BytesIO` file is returned as a :class:`BytesIO`.
    A local path is returned as a string.
    An http URL is read into a buffer and returned as a :class:`BytesIO`.

    When ``encoding`` is not ``utf8`` or ``utf8-lossy``, the whole file is
    first read in python and decoded using the specified encoding and
    returned as a :class:`BytesIO` (for usage with ``read_csv``).

    A `bytes` file is returned as a :class:`BytesIO` if ``use_pyarrow=True``.

    When fsspec is installed, remote file(s) is (are) opened with
    `fsspec.open(file, **kwargs)` or `fsspec.open_files(file, **kwargs)`.
    If encoding is not ``utf8`` or ``utf8-lossy``, decoding is handled by
    fsspec too.

    """

    # Small helper to use a variable as context
    @contextmanager
    def managed_file(file: Any) -> Iterator[Any]:
        try:
            yield file
        finally:
            pass

    has_non_utf8_non_utf8_lossy_encoding = (
        encoding not in {"utf8", "utf8-lossy"} if encoding else False
    )
    encoding_str = encoding if encoding else "utf8"

    if isinstance(file, bytes):
        if has_non_utf8_non_utf8_lossy_encoding:
            return _check_empty(
                BytesIO(file.decode(encoding_str).encode("utf8")),
                context="bytes",
            )
        if use_pyarrow:
            return _check_empty(BytesIO(file), context="bytes")

    if isinstance(file, StringIO):
        return _check_empty(
            BytesIO(file.read().encode("utf8")),
            context="StringIO",
            read_position=file.tell(),
        )

    if isinstance(file, BytesIO):
        if has_non_utf8_non_utf8_lossy_encoding:
            return _check_empty(
                BytesIO(file.read().decode(encoding_str).encode("utf8")),
                context="BytesIO",
                read_position=file.tell(),
            )
        return managed_file(
            _check_empty(
                b=file,
                context="BytesIO",
                read_position=file.tell(),
            )
        )

    if isinstance(file, Path):
        if has_non_utf8_non_utf8_lossy_encoding:
            return _check_empty(
                BytesIO(file.read_bytes().decode(encoding_str).encode("utf8")),
                context=f"Path ({file!r})",
            )
        return managed_file(normalise_filepath(file))

    if isinstance(file, str):
        # make sure that this is before fsspec
        # as fsspec needs requests to be installed
        # to read from http
        if file.startswith("http"):
            return _process_http_file(file, encoding_str)
        if _FSSPEC_AVAILABLE:
            from fsspec.utils import infer_storage_options

            if not has_non_utf8_non_utf8_lossy_encoding:
                if infer_storage_options(file)["protocol"] == "file":
                    return managed_file(normalise_filepath(file))
            kwargs["encoding"] = encoding
            return fsspec.open(file, **kwargs)

    if isinstance(file, list) and bool(file) and all(isinstance(f, str) for f in file):
        if _FSSPEC_AVAILABLE:
            from fsspec.utils import infer_storage_options

            if not has_non_utf8_non_utf8_lossy_encoding:
                if all(infer_storage_options(f)["protocol"] == "file" for f in file):
                    return managed_file([normalise_filepath(f) for f in file])
            kwargs["encoding"] = encoding
            return fsspec.open_files(file, **kwargs)

    if isinstance(file, str):
        file = normalise_filepath(file)
        if has_non_utf8_non_utf8_lossy_encoding:
            with open(file, encoding=encoding_str) as f:
                return _check_empty(
                    BytesIO(f.read().encode("utf8")), context=f"{file!r}"
                )

    return managed_file(file)


def read_ipc_schema(file: str | BinaryIO | Path | bytes) -> dict[str, PolarsDataType]:
    """
    Get a schema of the IPC file without reading data.

    Parameters
    ----------
    file
        Path to a file or a file-like object.

    Returns
    -------
    Dictionary mapping column names to datatypes

    """
    if isinstance(file, (str, Path)):
        file = normalise_filepath(file)

    return _ipc_schema(file)


def read_parquet_schema(
    file: str | BinaryIO | Path | bytes,
) -> dict[str, PolarsDataType]:
    """
    Get a schema of the Parquet file without reading data.

    Parameters
    ----------
    file
        Path to a file or a file-like object.

    Returns
    -------
    Dictionary mapping column names to datatypes

    """
    if isinstance(file, (str, Path)):
        file = normalise_filepath(file)

    return _parquet_schema(file)


def _is_local_file(file: str) -> bool:
    try:
        next(glob.iglob(file, recursive=True))
        return True
    except StopIteration:
        return False


def _update_columns(df: pli.DataFrame, new_columns: list[str]) -> pli.DataFrame:
    if df.width > len(new_columns):
        cols = df.columns
        for i, name in enumerate(new_columns):
            cols[i] = name
        new_columns = cols
    df.columns = new_columns
    return df
