import pytest

from utils.instance_lock import AlreadyRunningError, InstanceLock


def test_deuxieme_instance_refusee(tmp_path):
    lock = tmp_path / "bot.lock"
    with InstanceLock(lock):
        with pytest.raises(AlreadyRunningError):
            InstanceLock(lock).acquire()


def test_verrou_libere_apres_arret(tmp_path):
    lock = tmp_path / "bot.lock"
    with InstanceLock(lock):
        pass
    with InstanceLock(lock):        # relance possible tout de suite
        pass
