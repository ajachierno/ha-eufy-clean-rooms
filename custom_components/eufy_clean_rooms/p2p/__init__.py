"""Local, encrypted Tuya P2P client (vendored from jeppesens/eufy-clean, see THIRD_PARTY_LICENSES.md).

Opens the P2P map channel on the LAN with the device local key and streams the
map objects for robots that expose their room list nowhere else (Tuya-cloud
devices such as the S1 Pro). This integration only reads the room table from it.
"""

from __future__ import annotations

from .session import P2PError, P2PRefused, P2PSession

__all__ = ["P2PError", "P2PRefused", "P2PSession"]
