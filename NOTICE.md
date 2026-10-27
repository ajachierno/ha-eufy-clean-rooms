# Notice

The protobuf field numbers and enum values in `custom_components/eufy_clean_rooms/proto.py`, and the Eufy cloud endpoint URLs and public app client identifiers in `const.py`, were taken from:

- martijnpoppen/eufy-clean, https://github.com/martijnpoppen/eufy-clean
  Copyright (c) Martijn Poppen. Licensed under the Eufy-Clean License v1.0, which allows use, modification and distribution provided the copyright notice is kept and modifications are clearly marked.
- jeppesens/eufy-clean, https://github.com/jeppesens/eufy-clean (a fork of the above, same license)

The wire-format codec, the MQTT client and the Home Assistant integration in this repository are a separate implementation, written against the message layouts those projects document. Two parts are copied or ported from jeppesens/eufy-clean and are listed, with their modifications, in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md): the local P2P map-channel client (`custom_components/eufy_clean_rooms/p2p/`, by hclaria and chrisns) and the Tuya cloud client (`custom_components/eufy_clean_rooms/tuya.py`).

The no-warranty and limitation-of-liability terms of the Eufy-Clean License apply to the parts derived from it.
