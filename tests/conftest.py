import sys

import pytest

if sys.platform == "win32":
    # Windows' asyncio loop needs a local socketpair, which pytest-socket blocks.
    # The Eufy cloud is fully mocked in these tests, so lifting the guard is safe.
    import pytest_socket

    pytest_socket.disable_socket = lambda *a, **k: None
    pytest_socket.socket_allow_hosts = lambda *a, **k: None

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
