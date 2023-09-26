import builtins
from typing import List, Optional, Set, Union

from lamin_utils import logger
from lnschema_core import Dataset, File, Registry, Run, Transform
from lnschema_core.models import HasParents, format_field_value

from lamindb._utils import attach_func_to_class_method

from . import _TESTING
from ._registry import StrField, get_default_str_field

LAMIN_GREEN_LIGHTER = "#10b981"
LAMIN_GREEN_DARKER = "#065f46"
GREEN_FILL = "honeydew"
TRANSFORM_EMOJIS = {"notebook": "📔", "app": "🖥️", "pipeline": "🧩"}
is_run_from_ipython = getattr(builtins, "__IPYTHON__", False)


def _transform_emoji(transform: Transform):
    if transform is not None:
        return TRANSFORM_EMOJIS.get(transform.type, "💫")
    else:
        return TRANSFORM_EMOJIS["pipeline"]


def _view(u):
    try:
        if is_run_from_ipython:
            from IPython import get_ipython
            from IPython.display import display

            #  True if the code is running in a Jupyter Notebook or Lab environment
            if get_ipython().__class__.__name__ == "TerminalInteractiveShell":
                return u.view()
            else:
                display(u)
        else:
            return u
    except (FileNotFoundError, RuntimeError):  # pragma: no cover
        logger.error(
            "please install the graphviz executable on your system:\n  - Ubuntu: `sudo"
            " apt-get install graphviz`\n  - Windows:"
            " https://graphviz.org/download/#windows\n  - Mac: `brew install graphviz`"
        )


def view_parents(
    self,
    field: Optional[StrField] = None,
    with_children: bool = False,
    distance: int = 5,
):
    if field is None:
        field = get_default_str_field(self)
    if not isinstance(field, str):
        field = field.field.name

    return _view_parents(
        record=self, field=field, with_children=with_children, distance=distance
    )


def view_flow(data: Union[File, Dataset], with_children: bool = True) -> None:
    """Graph of data flow.

    Notes:
        For more info, see use cases: :doc:`docs:data-flow`.

    Examples:
        >>> dataset.view_flow()
        >>> file.view_flow()
    """
    import graphviz

    df_values = _get_all_parent_runs(data)
    if with_children:
        df_values += _get_all_child_runs(data)
    df_edges = _df_edges_from_runs(df_values)

    data_label = _label_data_run_transform(data)

    u = graphviz.Digraph(
        data.id,
        node_attr={
            "fillcolor": GREEN_FILL,
            "color": LAMIN_GREEN_DARKER,
            "fontname": "Helvetica",
            "fontsize": "10",
        },
        edge_attr={"arrowsize": "0.5"},
    )

    def add_node(
        record: Union[Run, File, Dataset],
        node_id: str,
        node_label: str,
        u: graphviz.Digraph,
    ):
        if isinstance(record, Run):
            fillcolor = "gainsboro"
        else:
            fillcolor = GREEN_FILL
        u.node(
            node_id,
            label=node_label,
            shape="box",
            style="rounded,filled",
            fillcolor=fillcolor,
        )

    for _, row in df_edges.iterrows():
        add_node(row["source_record"], row["source"], row["source_label"], u)
        if row["target_record"] not in df_edges["source_record"]:
            add_node(row["target_record"], row["target"], row["target_label"], u)

        u.edge(row["source"], row["target"], color="dimgrey")
    # label the searched file
    u.node(
        data.id,
        label=data_label,
        style="rounded,filled",
        fillcolor=LAMIN_GREEN_LIGHTER,
        shape="box",
    )

    _view(u)


def _view_parents(
    record: Registry, field: str, with_children: bool = False, distance: int = 100
):
    """Graph of parents."""
    if not hasattr(record, "parents"):
        raise NotImplementedError(
            f"Parents view is not supported for {record.__class__.__name__}!"
        )
    import graphviz
    import pandas as pd

    df_edges = None
    df_edges_parents = _df_edges_from_parents(
        record=record, field=field, distance=distance
    )
    if df_edges_parents is not None:
        df_edges = df_edges_parents
    if with_children:
        df_edges_children = _df_edges_from_parents(
            record=record, field=field, distance=distance, children=True
        )
        if df_edges_children is not None:
            if df_edges is not None:
                df_edges = pd.concat(
                    [df_edges_parents, df_edges_children]
                ).drop_duplicates()
            else:
                df_edges = df_edges_children

    record_label = record.__getattribute__(field)

    u = graphviz.Digraph(
        record.id,
        node_attr={
            "color": LAMIN_GREEN_DARKER,
            "fillcolor": GREEN_FILL,
            "shape": "box",
            "style": "rounded,filled",
            "fontname": "Helvetica",
            "fontsize": "10",
        },
        edge_attr={"arrowsize": "0.5"},
    )
    u.node(
        record.id,
        label=_label_data_run_transform(record)
        if record.__class__.__name__ == "Transform"
        else _add_emoji(record, record_label),
        fillcolor=LAMIN_GREEN_LIGHTER,
    )
    if df_edges is not None:
        for _, row in df_edges.iterrows():
            u.node(row["source"], label=row["source_label"])
            u.node(row["target"], label=row["target_label"])
            u.edge(row["source"], row["target"], color="dimgrey")

    _view(u)


def _get_parents(record: Registry, field: str, distance: int, children: bool = False):
    """Recursively get parent records within a distance."""
    if children:
        key = "parents"
    else:
        key = "children"
    model = record.__class__
    condition = f"{key}__{field}"
    results = model.filter(**{condition: record.__getattribute__(field)}).all()
    if distance < 2:
        return results

    d = 2
    while d < distance:
        condition = f"{key}__{condition}"
        records = model.filter(**{condition: record.__getattribute__(field)}).all()

        if len(records) == 0:
            return results

        results = results | records
        d += 1
    return results


def _df_edges_from_parents(
    record: Registry, field: str, distance: int, children: bool = False
):
    """Construct a DataFrame of edges as the input of graphviz.Digraph."""
    key = "children" if children else "parents"
    parents = _get_parents(
        record=record, field=field, distance=distance, children=children
    )
    all = record.__class__.objects
    records = parents | all.filter(id=record.id)
    df = records.distinct().df(include=[f"{key}__id"])
    if f"{key}__id" not in df.columns:
        return None
    df_edges = df[[f"{key}__id"]]
    df_edges = df_edges.explode(f"{key}__id")
    df_edges.index.name = "target"
    df_edges = df_edges.reset_index()
    df_edges.dropna(axis=0, inplace=True)
    df_edges.rename(columns={f"{key}__id": "source"}, inplace=True)
    df_edges = df_edges.drop_duplicates()

    # colons messes with the node formatting:
    # https://graphviz.readthedocs.io/en/stable/node_ports.html
    df_edges["source_record"] = df_edges["source"].apply(lambda x: all.get(id=x))
    df_edges["target_record"] = df_edges["target"].apply(lambda x: all.get(id=x))
    if record.__class__.__name__ == "Transform":
        df_edges["source_label"] = df_edges["source_record"].apply(
            _label_data_run_transform
        )
        df_edges["target_label"] = df_edges["target_record"].apply(
            _label_data_run_transform
        )
    else:
        df_edges["source_label"] = df_edges["source_record"].apply(
            lambda x: x.__getattribute__(field)
        )
        df_edges["target_label"] = df_edges["target_record"].apply(
            lambda x: x.__getattribute__(field)
        )
    return df_edges


def _add_emoji(record: Registry, label: str):
    if record.__class__.__name__ == "Transform":
        emoji = TRANSFORM_EMOJIS.get(record.type, "💫")
    elif record.__class__.__name__ == "Run":
        emoji = TRANSFORM_EMOJIS.get(record.transform.type, "💫")
    else:
        emoji = ""
    return f"{emoji} {label}"


def _get_all_parent_runs(data: Union[File, Dataset]) -> List:
    """Get all input file/dataset runs recursively."""
    run_inputs_outputs = []

    runs = [data.run]
    while any([r.input_files.exists() for r in runs if r is not None]) or any(
        [r.input_datasets.exists() for r in runs if r is not None]
    ):
        inputs = []
        for r in runs:
            inputs_run = r.input_files.list() + r.input_datasets.list()
            run_inputs_outputs += [(inputs_run, r)]
            run_inputs_outputs += [
                (r, r.output_files.list() + r.output_datasets.list())
            ]
            inputs += inputs_run
        runs = [f.run for f in inputs]
    return run_inputs_outputs


def _get_all_child_runs(data: Union[File, Dataset]) -> List:
    """Get all output file/dataset runs recursively."""
    all_runs: Set[Run] = set()
    run_inputs_outputs = []

    runs = {f.run for f in data.run.output_files.all()}
    runs.update({f.run for f in data.run.output_datasets.all()})
    while runs.difference(all_runs):
        all_runs.update(runs)
        child_runs: Set[Run] = set()
        for r in runs:
            if r != data.run:
                run_inputs_outputs += [
                    (r.input_files.list() + r.input_datasets.list(), r)
                ]
            run_outputs = r.output_files
            run_inputs_outputs += [(r, run_outputs.list())]
            child_runs.update(
                Run.filter(input_files__id__in=r.output_files.list("id")).list()
            )
            child_runs.update(
                Run.filter(input_datasets__id__in=r.output_datasets.list("id")).list()
            )
        runs = child_runs
    return run_inputs_outputs


def _label_data_run_transform(record: Union[File, Run, Transform]):
    if isinstance(record, File):
        if record.description is None:
            name = record.key
        else:
            name = record.description.replace("&", "&amp;")

        return (
            rf'<📄 {name}<BR/><FONT COLOR="GREY" POINT-SIZE="10"'
            rf' FACE="Monospace">id={record.id}<BR/>suffix={record.suffix}</FONT>>'
        )
    elif isinstance(record, Dataset):
        name = record.name.replace("&", "&amp;")
        return (
            rf'<🍱 {name}<BR/><FONT COLOR="GREY" POINT-SIZE="10"'
            rf' FACE="Monospace">id={record.id}<BR/>version={record.version}</FONT>>'
        )
    elif isinstance(record, Run):
        name = f'{record.transform.name.replace("&", "&amp;")}'
        return (
            rf'<{TRANSFORM_EMOJIS.get(str(record.transform.type), "💫")} {name}<BR/><FONT COLOR="GREY" POINT-SIZE="10"'  # noqa
            rf' FACE="Monospace">id={record.id}<BR/>type={record.transform.type},'
            rf" user={record.created_by.name}<BR/>run_at={format_field_value(record.run_at)}</FONT>>"  # noqa
        )
    elif isinstance(record, Transform):
        name = f'{record.name.replace("&", "&amp;")}'
        return (
            rf'<{TRANSFORM_EMOJIS.get(str(record.type), "💫")} {name}<BR/><FONT COLOR="GREY" POINT-SIZE="10"'  # noqa
            rf' FACE="Monospace">id={record.id}<BR/>type={record.type},'
            rf" user={record.created_by.name}<BR/>updated_at={format_field_value(record.updated_at)}</FONT>>"  # noqa
        )


def _df_edges_from_runs(df_values: List):
    import pandas as pd

    df = pd.DataFrame(df_values, columns=["source_record", "target_record"])
    df = df.explode("source_record")
    df = df.explode("target_record")
    df = df.drop_duplicates().dropna()
    df["source"] = [i.id for i in df["source_record"]]
    df["target"] = [i.id for i in df["target_record"]]
    df["source_label"] = df["source_record"].apply(_label_data_run_transform)
    df["target_label"] = df["target_record"].apply(_label_data_run_transform)
    return df


METHOD_NAMES = [
    "view_parents",
]

if _TESTING:  # type: ignore
    from inspect import signature

    SIGS = {
        name: signature(getattr(HasParents, name))
        for name in METHOD_NAMES
        if not name.startswith("__")
    }

for name in METHOD_NAMES:
    attach_func_to_class_method(name, HasParents, globals())
