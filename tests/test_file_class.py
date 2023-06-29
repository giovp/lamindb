import shutil
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest

import lamindb as ln

# how do we properly abstract out the default storage variable?
# currently, we're only mocking it through `default_storage` as
# set in conftest.py

df = pd.DataFrame({"feat1": [1, 2], "feat2": [3, 4]})

adata = ad.AnnData(
    X=np.array([[1, 2, 3], [4, 5, 6]]),
    obs=dict(Obs=["A", "B"]),
    var=dict(Feat=["a", "b", "c"]),
    obsm=dict(X_pca=np.array([[1, 2], [3, 4]])),
)

# tests as from readme


@pytest.mark.parametrize("name", [None, "my name"])
@pytest.mark.parametrize("feature_list", [None, [], df.columns])
def test_create_from_dataframe(name, feature_list):
    if feature_list is not None:
        feature_set = ln.FeatureSet.from_values(feature_list)
    else:
        feature_set = None
    file = ln.File(df, name=name, feature_sets=feature_set)
    assert file.name is None if name is None else file.name == name
    assert file.key is None
    file.save()
    feature_set_queried = file.feature_sets.get()  # exactly one
    feature_list_queried = ln.Feature.select(feature_sets=feature_set_queried).list()
    feature_list_queried = [feature.name for feature in feature_list_queried]
    if feature_list is None:
        assert set(feature_list_queried) == set(df.columns)
    else:
        assert set(feature_list_queried) == set(feature_list)
    file.delete(storage=True)


@pytest.fixture(
    scope="module", params=[(True, "./default_storage/"), (False, "./outside_storage/")]
)
def get_test_filepaths(request):  # -> Tuple[bool, Path, Path]
    isin_default_storage: bool = request.param[0]
    root_dir: Path = Path(request.param[1])
    test_dir = root_dir / "my_dir/"
    test_dir.mkdir(parents=True)
    test_filepath = test_dir / "my_file.csv"
    test_filepath.write_text("a")
    # return a boolean indicating whether test filepath is in default storage
    # and the test filepath
    yield (isin_default_storage, test_dir, test_filepath)
    shutil.rmtree(test_dir)


# this tests the basic (non-provenance-related) metadata
@pytest.mark.parametrize("key", [None, "my_new_dir/my_file.csv"])
@pytest.mark.parametrize("name", [None, "my name"])
def test_create_from_filepath(get_test_filepaths, key, name):
    isin_default_storage = get_test_filepaths[0]
    test_filepath = get_test_filepaths[2]
    file = ln.File(test_filepath, key=key, name=name)
    assert file.name is None if name is None else file.name == name
    assert file.suffix == ".csv"
    if key is None:
        assert (
            file.key == "my_dir/my_file.csv"
            if isin_default_storage
            else file.key is None
        )
    else:
        assert file.key == key
    assert file.storage.root == Path("./default_storage").resolve().as_posix()
    assert file.hash == "DMF1ucDxtqgxw5niaXcmYQ"
    if isin_default_storage and key is None:
        assert str(test_filepath.resolve()) == str(file.path())

    # need to figure out how to save!
    # now save it
    # file.save()
    # assert file.path().exists()
    # file.delete(storage=True)


def test_init_from_directory(get_test_filepaths):
    isin_default_storage = get_test_filepaths[0]
    test_dirpath = get_test_filepaths[1]
    if not isin_default_storage:
        with pytest.raises(RuntimeError):
            ln.File.from_dir(test_dirpath)
    else:
        records = ln.File.from_dir(test_dirpath)
        assert len(records) == 1
        # also execute tree
        ln.File.tree(test_dirpath.name)


def test_delete(get_test_filepaths):
    test_filepath = get_test_filepaths[2]
    file = ln.File(test_filepath, name="My test file to delete")
    file.save()
    storage_path = file.path()
    file.delete(storage=True)
    assert ln.File.select(name="My test file to delete").first() is None
    assert not Path(storage_path).exists()
