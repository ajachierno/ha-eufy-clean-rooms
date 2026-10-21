# Eufy Clean Rooms for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/ajachierno/ha-eufy-clean-rooms/actions/workflows/validate.yml/badge.svg)](https://github.com/ajachierno/ha-eufy-clean-rooms/actions/workflows/validate.yml)
[![GitHub release](https://img.shields.io/github/v/release/ajachierno/ha-eufy-clean-rooms)](https://github.com/ajachierno/ha-eufy-clean-rooms/releases)

Tell your Eufy robot vacuum to clean one specific room from Home Assistant: "vacuum the living room", "mop the kitchen". The room list comes from the robot's own map, the same one the Eufy Clean app shows, so rooms you add, rename, split or merge in the app show up in Home Assistant on their own.

## Inspired by ha-eufy-robovac-s1-pro

This project wouldn't exist without **[tkoba1974/ha-eufy-robovac-s1-pro](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro)**, the integration I used for my S1 Pro before writing this one. Its big strength is **local control**: after a one-time Eufy login to fetch the robot's encryption key, every command and status update stays on your own network. No cloud round trip, nothing breaks when Eufy's servers have a bad day, and nothing about your house leaves it. If you want basic control, consumable tracking and cleaning stats with zero cloud dependency, use that integration. It's excellent at it.

The one thing it can't do is per-room cleaning, and that isn't a gap in its code. On these robots the app sends room selection, room IDs and room names over Eufy's cloud channel. None of it crosses the local Tuya connection, so a local-only integration has no way to see your rooms or ask for one. This integration exists to fill that gap.

## How this differs from ha-eufy-robovac-s1-pro

| | [ha-eufy-robovac-s1-pro](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro) | Eufy Clean Rooms (this repo) |
|---|---|---|
| Connection | **Local** (Tuya protocol on your LAN) | Cloud (Eufy's MQTT broker, the same channel the app uses) |
| Works without internet | **Yes** | No |
| Clean a specific room | No | **Yes**, by name or room ID |
| Room list from the robot's map | No | **Yes**, updates automatically |
| Per-room Vacuum / Mop buttons | No | **Yes**, one pair per room |
| HA native "clean area" (Assist: "vacuum the kitchen") | No | **Yes** |
| Start, pause, dock, suction | Yes | Yes |
| Consumables, cleaning statistics, auto-return switch | **Yes** | No |
| Robots | S1 Pro | Eufy robots on the newer protobuf protocol that appear in Eufy's MQTT device list (S1 / S1 Pro, X, L, G and C series) |
| Updates | Polling | Push |

The two can run side by side on the same robot: keep the local integration for everyday control and stats, and use this one for room cleaning.

## Features

- **Vacuum entity**: start, pause, stop, return to dock, locate, spot clean and suction level.
- **Room buttons**: a `Vacuum <room>` and a `Mop <room>` button for every room on the map. They're added and removed as the map changes.
- **Home Assistant's built-in clean-area support.** Once each Eufy room is mapped to a Home Assistant area in the vacuum entity's settings, Assist and the `vacuum.clean_area` action can target rooms by area.
- **`eufy_clean_rooms.clean_rooms` action** for automations and scripts:

  ```yaml
  action: eufy_clean_rooms.clean_rooms
  target:
    entity_id: vacuum.robovac
  data:
    rooms: ["Living Room", "Kitchen"]   # names from the Eufy app, or room IDs
    clean_type: vacuum_and_mop          # vacuum | mop | vacuum_and_mop (optional)
    fan_speed: Turbo                    # Quiet | Standard | Turbo | Max (optional)
    mop_level: medium                   # low | medium | high (optional)
    clean_times: 1                      # passes per room, 1-3
  ```

  `vacuum.send_command` with `command: room_clean` and `params: {rooms: [...]}` works too.
- **Sensors**: battery, current task, room count (the room IDs and names are in its attributes) and error code.

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/ajachierno/ha-eufy-clean-rooms` with category **Integration**.
3. Search for **Eufy Clean Rooms**, install, then restart Home Assistant.

### Manual

Copy `custom_components/eufy_clean_rooms` into your `config/custom_components/` folder and restart Home Assistant.

## Setup

1. Settings → Devices & services → **Add integration** → **Eufy Clean Rooms**.
2. Log in with the email and password you use in the Eufy Clean app.
3. Pick your robot.

If the room list is empty after setup, open the map in the Eufy Clean app once. The robot publishes its rooms when the map is viewed, and the integration saves them so they survive restarts. The **Refresh rooms** action re-reads them from the cloud.

## Troubleshooting

- **"This account has no robots on Eufy's MQTT cloud"**: your robot is an older Tuya-only model. It doesn't appear in the device list this integration uses, so it isn't supported.
- **A room clean starts but ignores vacuum/mop**: some models take the clean type from the app's own setting. Set it in the app and file an issue with your model number.
- **Debug logs**: add this to `configuration.yaml`, reproduce the problem, then attach the log to an issue.

  ```yaml
  logger:
    logs:
      custom_components.eufy_clean_rooms: debug
  ```

## Status

Version 0.1.0 is new. The protocol code reproduces the exact commands the Eufy app sends an S1 Pro, and the integration has a test suite that runs against Home Assistant. Reports from other models are welcome, good or bad, in [Issues](https://github.com/ajachierno/ha-eufy-clean-rooms/issues).

## Credits

- [tkoba1974/ha-eufy-robovac-s1-pro](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro) for the inspiration, and for the S1 Pro command captures that confirmed which protocol the robot speaks.
- [martijnpoppen/eufy-clean](https://github.com/martijnpoppen/eufy-clean) (Copyright (c) Martijn Poppen) and [jeppesens/eufy-clean](https://github.com/jeppesens/eufy-clean) for documenting Eufy's cloud endpoints and protobuf message layouts. No code from either project is included; see [NOTICE.md](NOTICE.md).

Not affiliated with or endorsed by Anker or eufy.

## Buy me a coffee

Did you find this helpful? Consider supporting additional development:

<a href="https://www.buymeacoffee.com/ajachiernoo" target="_blank">
  <img src="https://raw.githubusercontent.com/ajachierno/ha-eufy-clean-rooms/main/brand/bmc_button.png" alt="Buy me a coffee" height="50">
</a>

<br><br>

<a href="https://www.buymeacoffee.com/ajachiernoo" target="_blank">
  <img src="https://raw.githubusercontent.com/ajachierno/ha-eufy-clean-rooms/main/brand/buymeacoffee_qr.png" alt="Buy me a coffee QR code" width="200">
</a>
