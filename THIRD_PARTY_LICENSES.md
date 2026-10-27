# Third-party licenses

## Eufy-Clean License (jeppesens/eufy-clean, martijnpoppen/eufy-clean)

The following files are copied from [jeppesens/eufy-clean](https://github.com/jeppesens/eufy-clean)
(a fork of [martijnpoppen/eufy-clean](https://github.com/martijnpoppen/eufy-clean)),
Copyright (c) Martijn Poppen. The P2P client was written by hclaria and chrisns
(pull requests #169 and #182 on that repository). Each file carries the notice
and a line describing any modification, as the license requires:

- `custom_components/eufy_clean_rooms/p2p/framing.py` (verbatim)
- `custom_components/eufy_clean_rooms/p2p/stun.py` (verbatim)
- `custom_components/eufy_clean_rooms/p2p/kcp.py` (camera conversation constant removed)
- `custom_components/eufy_clean_rooms/p2p/control.py` (camera/video helpers removed)
- `custom_components/eufy_clean_rooms/p2p/streams.py` (verbatim)
- `custom_components/eufy_clean_rooms/p2p/session.py` (verbatim)

`custom_components/eufy_clean_rooms/tuya.py` is a port of that repository's
`custom_components/robovac_mqtt/api/tuya_cloud.py`, trimmed and with its logging
changed so no session identifiers are written to the log.

The protobuf field numbers in `custom_components/eufy_clean_rooms/proto.py` and
the Eufy/Tuya endpoint URLs and public app identifiers in `const.py` are taken
from the same projects' `.proto` files and constants (see `NOTICE.md`).

### License text

```
Eufy-Clean License

> Copyright (c) Martijn Poppen
>
> Repository: https://github.com/martijnpoppen/eufy-clean

Version 1.0 - [2024-09-01]

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
Conditions:

- Attribution: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
- Modifications: Any modifications to the original source code must be clearly marked as such.

Limitations:

- No Warranty: The software is provided "as-is," without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement.
- Liability: In no event shall the authors be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.
```
