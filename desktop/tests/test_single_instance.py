import socket
import sys
import threading

import pytest
from gamevault_sync.single_instance import SingleInstance


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows decides with a mutex")
def test_a_second_copy_brings_the_first_to_the_front():
    port = _free_port()
    first = SingleInstance(port)
    shown = threading.Event()
    first.on_show(shown.set)
    assert first.acquire()
    try:
        assert not SingleInstance(port).acquire()
        assert shown.wait(2)
    finally:
        first.release()
    # Once the first copy is gone, the next one starts normally.
    again = SingleInstance(port)
    assert again.acquire()
    again.release()
