from __future__ import annotations

from typing import TYPE_CHECKING, Dict

import numpy as np
from lamin_utils import colors, logger
from lnschema_core.models import Artifact, Collection, Data, Feature, Registry

from lamindb._feature_set import dict_related_model_to_related_name
from lamindb._from_values import _print_values
from lamindb._registry import (
    REGISTRY_UNIQUE_FIELD,
    get_default_str_field,
    transfer_fk_to_default_db_bulk,
    transfer_to_default_db,
)
from lamindb._save import save

from ._settings import settings

if TYPE_CHECKING:
    from lamindb._query_set import QuerySet


def get_labels_as_dict(self: Data):
    labels = {}
    for related_model, related_name in dict_related_model_to_related_name(
        self.__class__
    ).items():
        if related_name in {
            "feature_sets",
            "unordered_artifacts",
            "input_of",
            "collections",
            "source_of",
            "report_of",
            "environment_of",
        }:
            continue
        if self.id is not None:
            labels[related_name] = (related_model, self.__getattribute__(related_name))
    return labels


def print_labels(self: Data):
    labels_msg = ""
    for related_name, (related_model, labels) in get_labels_as_dict(self).items():
        if labels.exists():
            n = labels.count()
            field = get_default_str_field(labels)
            print_values = _print_values(labels.list(field), n=10)
            labels_msg += f"  📎 {related_name} ({n}, {colors.italic(related_model)}): {print_values}\n"
    if len(labels_msg) > 0:
        return f"{colors.green('Labels')}:\n{labels_msg}"
    else:
        return ""


def transfer_add_labels(labels, features_lookup_self, self, row, parents: bool = True):
    def transfer_single_registry(validated_labels, new_labels):
        # here the new labels are transferred to the self db
        if len(new_labels) > 0:
            transfer_fk_to_default_db_bulk(new_labels, using_key=None)
            for label in new_labels:
                transfer_to_default_db(
                    label, using_key=None, mute=True, transfer_fk=False
                )
            # not saving parents for Organism during transfer
            registry = new_labels[0].__class__
            logger.info(f"saving {len(new_labels)} new {registry.__name__} records")
            save(new_labels)
        # link labels records from self db
        self._host.labels.add(
            validated_labels + new_labels,
            feature=getattr(features_lookup_self, row["name"]),
        )

    # validate labels on the default db
    result = validate_labels(labels, parents=parents)
    if isinstance(result, Dict):
        for _, (validated_labels, new_labels) in result.items():
            transfer_single_registry(validated_labels, new_labels)
    else:
        transfer_single_registry(*result)


def validate_labels(labels: QuerySet | list | dict, parents: bool = True):
    def validate_labels_registry(
        labels: QuerySet | list | dict, parents: bool = True
    ) -> tuple[list[str], list[str]]:
        if len(labels) == 0:
            return [], []
        registry = labels[0].__class__
        field = REGISTRY_UNIQUE_FIELD.get(registry.__name__.lower(), "uid")
        if hasattr(registry, "ontology_id") and parents:
            field = "ontology_id"
        if registry.__get_name_with_schema__() == "bionty.Organism":
            parents = False
        # if the field value is None, use uid field
        label_uids = np.array(
            [getattr(label, field) for label in labels if label is not None]
        )
        # save labels from ontology_ids so that parents are populated
        if field == "ontology_id" and len(label_uids) > 0:
            try:
                records = registry.from_values(label_uids, field=field)
                if len(records) > 0:
                    save(records, parents=parents)
            except Exception:
                pass
            field = "uid"
            label_uids = np.array(
                [getattr(label, field) for label in labels if label is not None]
            )
        validated = registry.validate(label_uids, field=field, mute=True)
        validated_uids = label_uids[validated]
        validated_labels = registry.filter(**{f"{field}__in": validated_uids}).list()
        new_labels = [labels[int(i)] for i in np.argwhere(~validated).flatten()]
        return validated_labels, new_labels

    if isinstance(labels, Dict):
        result = {}
        for registry, labels_registry in labels.items():
            result[registry] = validate_labels_registry(
                labels_registry, parents=parents
            )
    else:
        return validate_labels_registry(labels, parents=parents)


class LabelManager:
    """Label manager (:attr:`~lamindb.core.Data.labels`).

    This allows to manage untyped labels :class:`~lamindb.ULabel` and arbitrary
    typed labels (e.g., :class:`~bionty.CellLine`) and associate labels
    with features.

    See :class:`~lamindb.core.Data` for more information.
    """

    def __init__(self, host: Artifact | Collection):
        self._host = host

    def __repr__(self) -> str:
        msg = print_labels(self._host)
        if len(msg) > 0:
            return msg
        else:
            return "no linked labels"

    def add(
        self,
        records: Registry | list[Registry] | QuerySet,
        feature: Feature | None = None,
    ) -> None:
        """Add one or several labels and associate them with a feature.

        Args:
            records: Label records to add.
            feature: Feature under which to group the labels.
            field: Field to parse iterable with from_values.
        """
        from ._data import add_labels

        return add_labels(self._host, records=records, feature=feature)

    def get(
        self,
        feature: Feature,
        mute: bool = False,
        flat_names: bool = False,
    ) -> QuerySet | dict[str, QuerySet] | list:
        """Get labels given a feature.

        Args:
            feature: Feature under which labels are grouped.
            mute: Show no logging.
            flat_names: Flatten list to names rather than returning records.
        """
        from ._data import get_labels

        return get_labels(self._host, feature=feature, mute=mute, flat_names=flat_names)

    def add_from(self, data: Data, parents: bool = True):
        """Transfer labels from a file or collection.

        Examples:
            >>> file1 = ln.Artifact(pd.DataFrame(index=[0, 1]))
            >>> file1.save()
            >>> file2 = ln.Artifact(pd.DataFrame(index=[2, 3]))
            >>> file2.save()
            >>> ulabels = ln.ULabel.from_values(["Label1", "Label2"], field="name")
            >>> ln.save(ulabels)
            >>> labels = ln.ULabel.filter(name__icontains = "label").all()
            >>> file1.ulabels.set(labels)
            >>> file2.labels.add_from(file1)
        """
        features_lookup_self = Feature.lookup()
        features_lookup_data = Feature.objects.using(data._state.db).lookup()
        for _, feature_set in data.features._feature_set_by_slot.items():
            # add labels stratified by feature
            if feature_set.registry == "core.Feature":
                # df_slot is the Feature table with type and registries
                df_slot = feature_set.features.df()
                for _, row in df_slot.iterrows():
                    if row["type"] == "category" and row["registries"] is not None:
                        logger.info(f"transferring {row['name']}")
                        # labels records from data db
                        labels = data.labels.get(
                            getattr(features_lookup_data, row["name"]), mute=True
                        )
                        transfer_add_labels(
                            labels, features_lookup_self, self, row, parents=parents
                        )

        # for now, have this be duplicated, need to disentangle above
        using_key = settings._using_key
        for related_name, (_, labels) in get_labels_as_dict(data).items():
            labels = labels.all()
            if len(labels) == 0:
                continue
            validated_labels, new_labels = validate_labels(
                labels.all(), parents=parents
            )
            if len(new_labels) > 0:
                transfer_fk_to_default_db_bulk(new_labels, using_key)
                for label in new_labels:
                    transfer_to_default_db(
                        label, using_key, mute=True, transfer_fk=False
                    )
                save(new_labels, parents=parents)
            # this should not occur as file and collection should have the same attributes
            # but this might not be true for custom schema
            labels_list = validated_labels + new_labels
            if hasattr(self._host, related_name):
                getattr(self._host, related_name).add(*labels_list)
