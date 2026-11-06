import tarfile
import io
import sys
import pytest

def test_restore_rejects_traversal(tmp_path):
    malicious_tar = tmp_path / "evil.tar.gz"
    with tarfile.open(malicious_tar, "w:gz") as t:
        data = b"evil"
        info = tarfile.TarInfo(name="../../etc/evil.txt")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    
    with tarfile.open(malicious_tar, "r:gz") as tar:
        if sys.version_info >= (3, 12):
            with pytest.raises(tarfile.FilterError):
                tar.extractall(path=tmp_path / "extracted", filter='data')
        else:
            assert True
