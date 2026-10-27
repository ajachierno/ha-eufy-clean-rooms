# Eufy Clean Rooms for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/ajachierno/ha-eufy-clean-rooms/actions/workflows/validate.yml/badge.svg)](https://github.com/ajachierno/ha-eufy-clean-rooms/actions/workflows/validate.yml)
[![GitHub release](https://img.shields.io/github/v/release/ajachierno/ha-eufy-clean-rooms)](https://github.com/ajachierno/ha-eufy-clean-rooms/releases)

Tell your Eufy robot vacuum to clean one specific room from Home Assistant: "vacuum the living room", "mop the kitchen". The room list comes from the robot's own map, the same one the Eufy Clean app shows, so rooms you add, rename, split or merge in the app show up in Home Assistant on their own.

## Inspired by ha-eufy-robovac-s1-pro

This project wouldn't exist without **[tkoba1974/ha-eufy-robovac-s1-pro](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro)**, the integration I used for my S1 Pro before writing this one. Its big strength is **local control**: after a one-time Eufy login to fetch the robot's encryption key, every command and status update stays on your own network. No cloud round trip, nothing breaks when Eufy's servers have a bad day, and nothing about your house leaves it. If you want basic control, consumable tracking and cleaning stats with zero cloud dependency, use that integration. It's excellent at it.

The one thing it can't do is per-room cleaning, and that isn't a gap in its code. On these robots the room names and IDs never cross the plain local Tuya connection: the app reads them over an encrypted peer-to-peer channel and sends room commands through the cloud. This integration exists to fill that gap.

## How this differs from ha-eufy-robovac-s1-pro

| | [ha-eufy-robovac-s1-pro](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro) | Eufy Clean Rooms (this repo) |
|---|---|---|
| Connection | **Local** (Tuya protocol on your LAN) | Eufy's MQTT cloud (push), or the Tuya cloud (polling) plus a local peer-to-peer session for the room list |
| Works without internet | **Yes** | No |
| Clean a specific room | No | **Yes**, by name or room ID |
| Room list from the robot's map | No | **Yes**, updates automatically |
| Per-room Vacuum / Mop buttons | No | **Yes**, one pair per room |
| HA native "clean area" (Assist: "vacuum the kitchen") | No | **Yes** |
| Start, pause, dock, suction | Yes | Yes |
| Consumables, cleaning statistics, auto-return switch | **Yes** | No |
| Robots | S1 Pro | Eufy robots on the protobuf protocol: MQTT models (S1, X, L, G and C series) and Tuya-only models (S1 Pro / T2080A) |
| Updates | Polling | Push (MQTT robots) / every 30 s (Tuya robots) |

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

## How it connects

Eufy runs two clouds, and your robot is on one of them. The integration works out which at setup; you only ever log in with your Eufy account.

| | MQTT robots (S1, X, L, G, C series) | Tuya robots (S1 Pro / T2080A) |
|---|---|---|
| Commands | Eufy's MQTT broker | Tuya cloud |
| Status | Pushed as it changes | Polled every 30 seconds |
| Room list | From the robot's cloud messages | Read from the robot itself over your local network (P2P), at startup and on **Refresh rooms** |
| Needs Home Assistant on the same network as the dock | No | Only for the room list; commands and status work from anywhere |

For Tuya robots the dock is found automatically on the local network. If Home Assistant sits on another subnet, set the dock's IP address under the integration's **Configure** button. A few things worth knowing:

- The dock accepts only a handful of local connections, and a local integration (such as ha-eufy-robovac-s1-pro) and the Eufy app's map view each hold one. If the dock is busy the room refresh waits a minute and tries again, up to five times; nothing breaks, the last known rooms stay in use.
- Each room refresh opens one short session on the robot, which is why it runs only when the saved list is older than a day or you ask for it.
- Room cleaning on the S1 Pro uses the robot's local map id and zero-based room order, which is what its firmware expects (other projects' cloud map id and one-based order are why their room cleans landed in the wrong room).

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

If the room list is empty after setup on an MQTT robot, open the map in the Eufy Clean app once. The robot publishes its rooms when the map is viewed, and the integration saves them so they survive restarts. The **Refresh rooms** action re-reads them (from the cloud for MQTT robots, from the robot over your network for Tuya robots).

## Troubleshooting

- **"This account has no robots"**: the account you logged in with has no robot on either of Eufy's clouds. Check the robot is set up in the Eufy Clean app with the same email.
- **Repair: "Can't reach \<robot\> on your network"**: a Tuya robot's dock couldn't be reached for the room list. Home Assistant and the dock must be on the same network, or set the dock's IP address under **Configure**. Commands and status still work; the last known rooms stay in use.
- **A room clean starts but ignores vacuum/mop**: some models take the clean type from the app's own setting. Set it in the app and file an issue with your model number.
- **Debug logs**: add this to `configuration.yaml`, reproduce the problem, then attach the log to an issue.

  ```yaml
  logger:
    logs:
      custom_components.eufy_clean_rooms: debug
  ```

## Status

Version 0.2.0 adds the Tuya path, developed and tested on an S1 Pro (T2080A); the MQTT path is unchanged from 0.1. The integration has a test suite that runs against Home Assistant. Reports from other models are welcome, good or bad, in [Issues](https://github.com/ajachierno/ha-eufy-clean-rooms/issues).

## Credits

- [tkoba1974/ha-eufy-robovac-s1-pro](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro) for the inspiration, the S1 Pro command captures that confirmed which protocol the robot speaks, and [issue #24](https://github.com/tkoba1974/ha-eufy-robovac-s1-pro/issues/24) on the S1 Pro's timer and map-edit messages.
- [martijnpoppen/eufy-clean](https://github.com/martijnpoppen/eufy-clean) (Copyright (c) Martijn Poppen) and [jeppesens/eufy-clean](https://github.com/jeppesens/eufy-clean) for documenting Eufy's cloud endpoints and protobuf message layouts, and for the Tuya cloud client this integration ports.
- hclaria and chrisns for the local P2P map-channel client ([PR #169](https://github.com/jeppesens/eufy-clean/pull/169), [PR #182](https://github.com/jeppesens/eufy-clean/pull/182)), vendored here for the room list, and gurt-il for pinning down the S1 Pro's local map id ([PR #166](https://github.com/jeppesens/eufy-clean/pull/166)). See [NOTICE.md](NOTICE.md) and [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

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
