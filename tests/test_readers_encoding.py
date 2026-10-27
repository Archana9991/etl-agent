from etl_agent.readers import read_file


def test_utf8_file_with_bom(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("ID,NAME\n1,Asha\n".encode("utf-8-sig"))
    assert list(read_file(str(path)).columns) == ["ID", "NAME"]


def test_windows_cp1252_file(tmp_path):
    path = tmp_path / "win.csv"
    path.write_bytes("ID,NAME\n1,Jos\xe9\n".encode("cp1252"))
    df = read_file(str(path))
    assert df.loc[0, "NAME"] == "Jos\xe9"


def test_plain_utf8_with_accents(tmp_path):
    path = tmp_path / "u.csv"
    path.write_bytes("ID,NAME\n1,Jos\xe9\n".encode("utf-8"))
    assert read_file(str(path)).loc[0, "NAME"] == "Jos\xe9"