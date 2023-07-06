import builtins
from typing import Dict, Iterable, List, Literal, NamedTuple, Optional, Set, Union

import pandas as pd
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import CharField, TextField
from django.db.models.query_utils import DeferredAttribute as Field
from lamin_logger import logger
from lamin_logger._lookup import Lookup
from lamin_logger._search import search as base_search
from lamindb_setup.dev._docs import doc_args
from lnschema_core import ORM
from lnschema_core.types import ListLike, StrField

from lamindb.dev.utils import attach_func_to_class_method

from . import _TESTING
from ._from_values import _has_species_field, get_or_create_records

IPYTHON = getattr(builtins, "__IPYTHON__", False)


class ValidationError(Exception):
    pass


def init_self_from_db(self: ORM, existing_record: ORM):
    new_args = [
        getattr(existing_record, field.attname) for field in self._meta.concrete_fields
    ]
    super(self.__class__, self).__init__(*new_args)
    self._state.adding = False  # mimic from_db
    self._state.db = "default"


def validate_required_fields(orm: ORM, kwargs):
    required_fields = {
        k.name for k in orm._meta.fields if not k.null and k.default is None
    }
    required_fields_not_passed = {k: None for k in required_fields if k not in kwargs}
    kwargs.update(required_fields_not_passed)
    missing_fields = [
        k for k, v in kwargs.items() if v is None and k in required_fields
    ]
    if missing_fields:
        raise TypeError(f"{missing_fields} are required.")


def suggest_objects_with_same_name(orm: ORM, kwargs) -> Optional[str]:
    if kwargs.get("name") is None:
        return None
    else:
        results = orm.search(kwargs["name"])
        if results.shape[0] == 0:
            return None

        # subset results to those with at least 0.85 levensteihn distance
        results = results.loc[results.__ratio__ >= 85]

        # test for exact match
        if len(results) > 0:
            if results.index[0] == kwargs["name"]:
                logger.warning("Object with exact same name exists, returning it")
                return "object-with-same-name-exists"
            else:
                msg = "Entries with similar names exist:"
                if IPYTHON:
                    from IPython.display import display

                    logger.warning(f"{msg}")
                    display(results)
                else:
                    logger.warning(f"{msg}\n{results.name}")
    return None


def __init__(orm: ORM, *args, **kwargs):
    if not args:
        validate_required_fields(orm, kwargs)
        from .dev._settings import settings

        if settings.upon_create_search_names:
            result = suggest_objects_with_same_name(orm, kwargs)
            if result == "object-with-same-name-exists":
                existing_record = orm.select(name=kwargs["name"])[0]
                init_self_from_db(orm, existing_record)
                return None
        super(ORM, orm).__init__(**kwargs)
    elif len(args) != len(orm._meta.concrete_fields):
        raise ValueError("Please provide keyword arguments, not plain arguments")
    else:
        # object is loaded from DB (**kwargs could be omitted below, I believe)
        super(ORM, orm).__init__(*args, **kwargs)


@classmethod  # type:ignore
@doc_args(ORM.from_values.__doc__)
def from_values(cls, identifiers: ListLike, field: StrField, **kwargs) -> List["ORM"]:
    """{}"""
    if isinstance(field, str):
        field = getattr(cls, field)
    if not isinstance(field, Field):  # field is DeferredAttribute
        raise TypeError(
            "field must be a string or an ORM field, e.g., `CellType.name`!"
        )
    from_bionty = True if cls.__module__.startswith("lnschema_bionty.") else False
    return get_or_create_records(
        iterable=identifiers, field=field, from_bionty=from_bionty, **kwargs
    )


def _search(
    cls,
    string: str,
    *,
    field: Optional[StrField] = None,
    top_hit: bool = False,
    case_sensitive: bool = True,
    synonyms_field: Optional[Union[str, TextField, CharField]] = "synonyms",
    synonyms_sep: str = "|",
) -> Union["pd.DataFrame", "ORM"]:
    """{}"""
    if field is None:
        field = get_default_str_field(cls)
    if not isinstance(field, str):
        field = field.field.name

    records = cls.all() if isinstance(cls, models.QuerySet) else cls.objects.all()
    cls = cls.model if isinstance(cls, models.QuerySet) else cls

    df = pd.DataFrame.from_records(records.values())

    result = base_search(
        df=df,
        string=string,
        field=field,
        synonyms_field=str(synonyms_field),
        case_sensitive=case_sensitive,
        return_ranked_results=not top_hit,
        synonyms_sep=synonyms_sep,
        tuple_name=cls.__name__,
    )

    if not top_hit or result is None:
        return result
    else:
        if isinstance(result, list):
            return [records.get(id=r.id) for r in result]
        else:
            return records.get(id=result.id)


@classmethod  # type: ignore
@doc_args(ORM.search.__doc__)
def search(
    cls,
    string: str,
    *,
    field: Optional[StrField] = None,
    top_hit: bool = False,
    case_sensitive: bool = True,
    synonyms_field: Optional[Union[str, TextField, CharField]] = "synonyms",
    synonyms_sep: str = "|",
) -> Union["pd.DataFrame", "ORM"]:
    """{}"""
    return _search(
        cls=cls,
        string=string,
        field=field,
        top_hit=top_hit,
        case_sensitive=case_sensitive,
        synonyms_field=synonyms_field,
        synonyms_sep=synonyms_sep,
    )


def _lookup(cls, field: Optional[StrField] = None) -> NamedTuple:
    """{}"""
    if field is None:
        field = get_default_str_field(cls)
    if not isinstance(field, str):
        field = field.field.name

    records = cls.all() if isinstance(cls, models.QuerySet) else cls.objects.all()
    cls = cls.model if isinstance(cls, models.QuerySet) else cls

    return Lookup(
        records=records,
        values=[i.get(field) for i in records.values()],
        tuple_name=cls.__name__,
        prefix="ln",
    ).lookup()


@classmethod  # type: ignore
@doc_args(ORM.lookup.__doc__)
def lookup(cls, field: Optional[StrField] = None) -> NamedTuple:
    """{}"""
    return _lookup(cls=cls, field=field)


def _inspect(
    cls,
    identifiers: ListLike,
    field: StrField,
    *,
    case_sensitive: bool = False,
    inspect_synonyms: bool = True,
    return_df: bool = False,
    logging: bool = True,
    **kwargs,
) -> Union["pd.DataFrame", Dict[str, List[str]]]:
    """{}"""
    from lamin_logger._inspect import inspect

    if not isinstance(field, str):
        field = field.field.name

    cls = cls.model if isinstance(cls, models.QuerySet) else cls

    return inspect(
        df=_filter_df_based_on_species(orm=cls, species=kwargs.get("species")),
        identifiers=identifiers,
        field=str(field),
        case_sensitive=case_sensitive,
        inspect_synonyms=inspect_synonyms,
        return_df=return_df,
        logging=logging,
    )


@classmethod  # type: ignore
@doc_args(ORM.inspect.__doc__)
def inspect(
    cls,
    identifiers: ListLike,
    field: StrField,
    *,
    case_sensitive: bool = False,
    inspect_synonyms: bool = True,
    return_df: bool = False,
    logging: bool = True,
    **kwargs,
) -> Union["pd.DataFrame", Dict[str, List[str]]]:
    """{}"""
    return _inspect(
        cls=cls,
        identifiers=identifiers,
        field=field,
        case_sensitive=case_sensitive,
        inspect_synonyms=inspect_synonyms,
        return_df=return_df,
        logging=logging,
        **kwargs,
    )


def _map_synonyms(
    cls,
    synonyms: Iterable,
    *,
    return_mapper: bool = False,
    case_sensitive: bool = False,
    keep: Literal["first", "last", False] = "first",
    synonyms_field: str = "synonyms",
    synonyms_sep: str = "|",
    field: Optional[str] = None,
    **kwargs,
) -> Union[List[str], Dict[str, str]]:
    """{}"""
    from lamin_logger._map_synonyms import map_synonyms

    if field is None:
        field = get_default_str_field(cls)
    if not isinstance(field, str):
        field = field.field.name

    cls = cls.model if isinstance(cls, models.QuerySet) else cls

    try:
        cls._meta.get_field(synonyms_field)
        df = _filter_df_based_on_species(orm=cls, species=kwargs.get("species"))
    except FieldDoesNotExist:
        df = pd.DataFrame()
    return map_synonyms(
        df=df,
        identifiers=synonyms,
        field=field,
        return_mapper=return_mapper,
        case_sensitive=case_sensitive,
        keep=keep,
        synonyms_field=synonyms_field,
        sep=synonyms_sep,
    )


@classmethod  # type: ignore
@doc_args(ORM.map_synonyms.__doc__)
def map_synonyms(
    cls,
    synonyms: Iterable,
    *,
    return_mapper: bool = False,
    case_sensitive: bool = False,
    keep: Literal["first", "last", False] = "first",
    synonyms_field: str = "synonyms",
    synonyms_sep: str = "|",
    field: Optional[str] = None,
    **kwargs,
) -> Union[List[str], Dict[str, str]]:
    """{}"""
    return _map_synonyms(
        cls=cls,
        synonyms=synonyms,
        return_mapper=return_mapper,
        case_sensitive=case_sensitive,
        keep=keep,
        synonyms_field=synonyms_field,
        synonyms_sep=synonyms_sep,
        field=field,
        **kwargs,
    )


def _filter_df_based_on_species(
    orm: Union[ORM, models.QuerySet], species: Optional[Union[str, ORM]] = None
):
    import pandas as pd

    records = orm.all() if isinstance(orm, models.QuerySet) else orm.objects.all()
    if _has_species_field(orm):
        # here, we can safely import lnschema_bionty
        from lnschema_bionty._bionty import create_or_get_species_record

        species_record = create_or_get_species_record(species=species, orm=orm)
        if species_record is not None:
            records = records.filter(species__name=species_record.name)

    return pd.DataFrame.from_records(records.values())


def get_default_str_field(orm: Union[ORM, models.QuerySet]) -> str:
    """Get the 1st char or text field from the orm."""
    if isinstance(orm, models.QuerySet):
        orm = orm.model
    model_field_names = [i.name for i in orm._meta.fields]

    # set default field
    if "name" in model_field_names:
        # by default use the name field
        field = orm._meta.get_field("name")
    else:
        # first char or text field that doesn't contain "id"
        for i in orm._meta.fields:
            if "id" in i.name:
                continue
            if i.get_internal_type() in {"CharField", "TextField"}:
                field = i
                break

    # no default field can be found
    if field is None:
        raise ValueError("Please specify a field to search against!")

    return field.name


def _add_or_remove_synonyms(
    synonym: Union[str, Iterable],
    record: ORM,
    action: Literal["add", "remove"],
    force: bool = False,
):
    """Add or remove synonyms."""

    def check_synonyms_in_all_records(synonyms: Set[str], record: ORM):
        """Errors if input synonym is associated with other records in the DB."""
        import pandas as pd
        from IPython.display import display

        syns_all = (
            record.__class__.objects.exclude(synonyms="").exclude(synonyms=None).all()
        )
        if len(syns_all) == 0:
            return
        df = pd.DataFrame(syns_all.values())
        df["synonyms"] = df["synonyms"].str.split("|")
        df = df.explode("synonyms")
        matches_df = df[(df["synonyms"].isin(synonyms)) & (df["id"] != record.id)]
        if matches_df.shape[0] > 0:
            records_df = pd.DataFrame(syns_all.filter(id__in=matches_df["id"]).values())
            logger.error(
                f"Input synonyms {matches_df['synonyms'].unique()} already associated"
                " with the following records:\n(Pass `force=True` to ignore this error)"
            )
            display(records_df)
            raise SystemExit(AssertionError)

    # passed synonyms
    if isinstance(synonym, str):
        syn_new_set = set([synonym])
    else:
        syn_new_set = set(synonym)
    # nothing happens when passing an empty string or list
    if len(syn_new_set) == 0:
        return
    # because we use | as the separator
    if any(["|" in i for i in syn_new_set]):
        raise AssertionError("A synonym can't contain '|'!")

    # existing synonyms
    syns_exist = record.synonyms
    if syns_exist is None or len(syns_exist) == 0:
        syns_exist_set = set()
    else:
        syns_exist_set = set(syns_exist.split("|"))

    if action == "add":
        if not force:
            check_synonyms_in_all_records(syn_new_set, record)
        syns_exist_set.update(syn_new_set)
    elif action == "remove":
        syns_exist_set = syns_exist_set.difference(syn_new_set)

    if len(syns_exist_set) == 0:
        syns_str = None
    else:
        syns_str = "|".join(syns_exist_set)

    record.synonyms = syns_str

    # if record is already in DB, save the changes to DB
    if not record._state.adding:
        record.save()


def _check_synonyms_field_exist(record: ORM):
    try:
        record.__getattribute__("synonyms")
    except AttributeError:
        raise NotImplementedError(
            f"No synonyms field found in table {record.__class__.__name__}!"
        )


def add_synonym(self, synonym: Union[str, ListLike], force: bool = False):
    _check_synonyms_field_exist(self)
    _add_or_remove_synonyms(synonym=synonym, record=self, force=force, action="add")


def remove_synonym(self, synonym: Union[str, ListLike]):
    _check_synonyms_field_exist(self)
    _add_or_remove_synonyms(synonym=synonym, record=self, action="remove")


METHOD_NAMES = [
    "__init__",
    "search",
    "lookup",
    "map_synonyms",
    "inspect",
    "add_synonym",
    "remove_synonym",
    "from_values",
]

if _TESTING:
    from inspect import signature

    SIGS = {
        name: signature(getattr(ORM, name))
        for name in METHOD_NAMES
        if not name.startswith("__")
    }

for name in METHOD_NAMES:
    attach_func_to_class_method(name, ORM, globals())


@classmethod  # type: ignore
def __name_with_type__(cls) -> str:
    schema_module_name = cls.__module__.split(".")[0]
    schema_name = schema_module_name.replace("lnschema_", "")
    return f"{schema_name}.{cls.__name__}"


setattr(ORM, "__name_with_type__", __name_with_type__)
