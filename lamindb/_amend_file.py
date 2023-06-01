from pathlib import Path
from typing import Optional

from anndata import AnnData
from anndata import __version__ as anndata_v
from lamindb_setup import settings as setup_settings
from lnschema_core import File
from lnschema_core._core import filepath_from_file_or_folder
from lnschema_core.link import RunIn
from lnschema_core.types import DataLike
from packaging import version
from sqlalchemy.orm.session import object_session

from lamindb._context import context
from lamindb.dev import LazyDataFrame
from lamindb.dev.storage import load_to_memory
from lamindb.dev.storage.object import _subset_anndata_file
from lamindb.dev.storage.object._anndata_accessor import AnnDataAccessor

from ._settings import settings
from .dev.db._add import add as ln_add

File.__doc__ = """Files: serialized data objects.

Args:
   data: `Union[PathLike, DataLike] = None` - A file path or an in-memory data
      object to serialize. Can be a cloud path.
   key: `Optional[str] = None` - A storage key, a relative filepath within the
      storage location, e.g., an S3 or GCP bucket.
   name: `Optional[str] = None` - A name. Defaults to a file name for a file.
   run: `Optional[Run] = None` - The generating run.
   features: `List[Features] = None` - A feature set record.
   id: `Optional[str] = None` - The id of the file. Auto-generated if not passed.
   input_of: `List[Run] = None` - Runs for which the file
      is as an input.

Often, files represent atomic datasets in object storage:
jointly measured observations of features (:class:`~lamindb.Features`).
They are generated by running code (:class:`~lamindb.Transform`),
instances of :class:`~lamindb.Run`.

Data objects often have canonical on-disk and in-memory representations. LaminDB
makes some configurable default choices (e.g., serialize a `DataFrame` as a
`.parquet` file).

Some datasets do not have a canonical in-memory representation, for
instance, `.fastq`, `.vcf`, or files describing QC of datasets.

.. note:: Examples for storage ⟷ memory correspondence:

   - Table: `.csv`, `.tsv`, `.parquet`, `.ipc`, `.feather`
     ⟷ `pd.DataFrame`, `polars.DataFrame`
   - Annotated matrix: `.h5ad`, `.h5mu`, `.zarr` ⟷ `anndata.AnnData`, `mudata.MuData`
   - Image: `.jpg`, `.png` ⟷ `np.ndarray`, ...
   - Tensor: zarr directory, TileDB store ⟷ zarr loader, TileDB loader
   - Fastq: `.fastq` ⟷ /
   - VCF: `.vcf` ⟷ /
   - QC: `.html` ⟷ /

"""


def backed(self: File) -> AnnDataAccessor:
    """Return a cloud-backed AnnData object for streaming."""
    if self.suffix not in (".h5ad", ".zrad", ".zarr"):
        raise ValueError("File should have an AnnData object as the underlying data")
    return AnnDataAccessor(self)


def subsetter(self: File) -> LazyDataFrame:
    """A subsetter to pass to ``.stream()``.

    Currently, this returns an instance of an
    unconstrained :class:`~lamindb.dev.LazyDataFrame`
    to be evaluated in ``.stream()``.

    In the future, this will be constrained by metadata of the file, it's
    feature- and sample-level descriptors, like `.obs`, `.var`, `.columns`, `.rows`.
    """
    return LazyDataFrame()


def stream(
    self: File,
    subset_obs: Optional[LazyDataFrame] = None,
    subset_var: Optional[LazyDataFrame] = None,
    is_run_input: Optional[bool] = None,
) -> AnnData:
    """Stream the file into memory. Allows subsetting an AnnData object.

    Args:
        subset_obs: ``Optional[LazyDataFrame] = None`` - A DataFrame query to
            evaluate on ``.obs`` of an underlying ``AnnData`` object.
        subset_var: ``Optional[LazyDataFrame] = None`` - A DataFrame query to
            evaluate on ``.var`` of an underlying ``AnnData`` object.

    Returns:
        The streamed AnnData object.

    Example:

    >>> file = ln.select(ln.File).where(...).one()
    >>> obs = file.subsetter()
    >>> obs = (
    >>>     obs.cell_type.isin(["dendritic cell", "T cell")
    >>>     & obs.disease.isin(["Alzheimer's"])
    >>> )
    >>> file.stream(subset_obs=obs, is_run_input=True)

    """
    if self.suffix not in (".h5ad", ".zarr"):
        raise ValueError("File should have an AnnData object as the underlying data")
    _track_run_input(self, is_run_input)

    if subset_obs is None and subset_var is None:
        return load_to_memory(filepath_from_file_or_folder(self), stream=True)

    if self.suffix == ".h5ad" and subset_obs is not None and subset_var is not None:
        raise ValueError(
            "Can not subset along both subset_obs and subset_var at the same time"
            " for an AnnData object stored as a h5ad file."
            " Please resave your AnnData as zarr to be able to do this"
        )

    if self.suffix == ".zarr" and version.parse(anndata_v) < version.parse("0.9.1"):
        raise ValueError(
            f"anndata=={anndata_v} does not support `.subset` of zarr stored AnnData."
            " Please install anndata>=0.9.1"
        )

    return _subset_anndata_file(self, subset_obs, subset_var)  # type: ignore


def _track_run_input(file: File, is_run_input: Optional[bool] = None):
    if is_run_input is None:
        track_run_input = settings.track_run_inputs_upon_load
    else:
        track_run_input = is_run_input
    if track_run_input:
        if context.run is None:
            raise ValueError(
                "No global run context set. Call ln.context.track() or link input to a"
                " run object via `run.inputs.append(file)`"
            )
        if object_session(file) is None:
            # slower, no session open, doesn't use relationship
            run_in = RunIn(file_id=file.id, run_id=context.run.id)
            # create a separate session under-the-hood for this transaction
            ln_add(run_in)
        else:
            # relationship-based, needs session
            if context.run not in file.input_of:
                file.input_of.append(context.run)
                session = object_session(file)
                session.add(file)
                session.commit()


def load(file: File, is_run_input: Optional[bool] = None) -> DataLike:
    """Stage and load to memory.

    Returns in-memory representation if possible, e.g., an `AnnData` object
    for an `h5ad` file.
    """
    _track_run_input(file, is_run_input)
    return load_to_memory(filepath_from_file_or_folder(file))


def stage(file: File, is_run_input: Optional[bool] = None) -> Path:
    """Update cache from cloud storage if outdated.

    Returns a path to a locally cached on-disk object (say, a
    `.jpg` file).
    """
    if file.suffix in (".zrad", ".zarr"):
        raise RuntimeError("zarr object can't be staged, please use load() or stream()")
    _track_run_input(file, is_run_input)
    return setup_settings.instance.storage.cloud_to_local(
        filepath_from_file_or_folder(file)
    )


File.backed = backed
File.stage = stage
File.subsetter = subsetter
File.stream = stream
File.load = load
