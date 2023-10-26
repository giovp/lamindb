import lamindb as ln


def test_file_visibility():
    # create a file with default visibility
    with open("./test-visibility.txt", "w") as f:
        f.write("visibility")
    file = ln.File("./test-visibility.txt", description="test-visibility")
    assert file.visibility == 0
    file.save()

    # create a dataset from file
    dataset = ln.Dataset(file, name="test-visibility")
    dataset.save()

    # delete a dataset will put both dataset and linked file in trash
    dataset.delete()
    assert dataset.file.visibility == 2
    result = ln.Dataset.filter(name="test-visibility").all()
    assert len(result) == 0
    result = ln.Dataset.filter(name="test-visibility", visibility="default").all()
    assert len(result) == 0
    result = ln.Dataset.filter(name="test-visibility", visibility=None).all()
    assert len(result) == 1

    # restore
    dataset.restore()
    assert dataset.visibility == 0
    assert dataset.file.visibility == 0

    # permanent delete
    dataset.delete(permanent=True)
    result = ln.File.filter(description="test-visibility", visibility=None).all()
    # also permanently deleted linked file
    assert len(result) == 0
