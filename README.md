# Eufy Clean Rooms for Home Assistant

Control a Eufy robot vacuum from Home Assistant, including sending it to clean a specific room. The room list comes from the robot's own map, the same one the Eufy Clean app shows. Rename, split or merge rooms in the app and Home Assistant follows.

It talks to Eufy's cloud MQTT broker, the same channel the app uses. Room selection never crosses the local Tuya connection on these robots, so a local-only integration can't do this.

## What you get

- **Vacuum entity** with start, pause, stop, return to dock, locate, spot clean and suction level.
- **HA's built-in "clean area" support.** Once each Eufy room is mapped to a Home Assistant area in the vacuum entity's settings, Assist and the `vacuum.clean_area` action can target rooms by area: "vacuum the living room".
- **Two buttons per room**: `Vacuum <room>` and `Mop <room>`. They're created and removed automatically as the room list changes.
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
    clean_times: 1
  ```

  `vacuum.send_command` with `command: room_clean` and `params: {rooms: [...]}` works too.
- **Sensors**: battery, current task, room count (room IDs and names are in its attributes) and error code.

## Install

1. Copy `custom_components/eufy_clean_rooms` into your Home Assistant `config/custom_components/`, or add this repo to HACS as a custom repository.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Eufy Clean Rooms**. Log in with your Eufy app email and password and pick the robot.

If the room list is empty after setup, open the map in the Eufy Clean app once. That makes the robot publish its rooms, and the integration saves them so they survive restarts.

## Supported robots

Robots on Eufy's newer protobuf protocol that appear in the Eufy MQTT device list: X-series, L-series, G-series, C-series and the S1 / S1 Pro. Older Tuya-only models don't show up in that list and the setup form will say so.

## Credits

Protocol field numbers come from Eufy's `.proto` definitions as published in [martijnpoppen/eufy-clean](https://github.com/martijnpoppen/eufy-clean) (Copyright (c) Martijn Poppen), and the cloud endpoints are the ones documented there and in [jeppesens/eufy-clean](https://github.com/jeppesens/eufy-clean). This project doesn't include their code: the protobuf codec, MQTT client and Home Assistant integration were written from scratch. See [NOTICE.md](NOTICE.md).

Not affiliated with or endorsed by Anker or eufy.
