"""Constants for the Eufy Clean Rooms integration."""

from __future__ import annotations

DOMAIN = "eufy_clean_rooms"

CONF_DEVICE_SN = "device_sn"
CONF_DEVICE_MODEL = "device_model"
CONF_DEVICE_NAME = "device_name"
CONF_OPENUDID = "openudid"

# Eufy cloud endpoints (same ones the Eufy Clean / eufy apps use)
HOME_API = "https://home-api.eufylife.com"
API = "https://api.eufylife.com"
AIOT_API = "https://aiot-clean-api-pr.eufylife.com"

LOGIN_CONFIGS = (
    # (url, client_id, client_secret, category) -- public app identifiers
    (f"{HOME_API}/v1/user/v2/email/login", "eufy-app", "8FHf22gaTKu7MZXqz5zytw", "Health"),
    (f"{HOME_API}/v1/user/email/login", "eufyhome-app", "GQCpr9dSp3uQpsOMgJ4xQ", "Home"),
)
URL_USER_INFO = f"{API}/v1/user/user_center_info"
URL_DEVICE_V2 = f"{API}/v1/device/v2"
URL_AIOT_DEVICES = f"{AIOT_API}/app/devicerelation/get_device_list"
URL_MQTT_INFO = f"{AIOT_API}/app/devicemanage/get_user_mqtt_info"
USER_AGENT = "EufyHome-Android-3.1.3-753"
MQTT_PORT = 8883

# DPS slots used by "novel" (protobuf) Eufy robovacs
DPS_MODE_CTRL = "152"  # ModeCtrlRequest / ModeCtrlResponse
DPS_WORK_STATUS = "153"  # WorkStatus
DPS_CLEAN_PARAM = "154"  # CleanParamRequest / CleanParamResponse
DPS_FAN_SPEED = "158"  # suction: int index on X-series, string on S1 Pro
DPS_FIND_ROBOT = "160"
DPS_BATTERY = "163"
DPS_ROOMS = "165"  # UniversalDataResponse / RoomParams (room list)
DPS_ERROR = "177"
# Tuya-era slots the S1 Pro also reports
DPS_BATTERY_ALT = "8"
DPS_FAN_SPEED_ALT = "9"

FAN_SPEEDS = ["Quiet", "Standard", "Turbo", "Max"]

CLEAN_TYPE_OPTIONS = ["vacuum", "mop", "vacuum_and_mop"]

SIGNAL_ROOMS_UPDATED = f"{DOMAIN}_rooms_updated_{{}}"

SERVICE_CLEAN_ROOMS = "clean_rooms"
ATTR_ROOMS = "rooms"
ATTR_CLEAN_TYPE = "clean_type"
ATTR_FAN_SPEED = "fan_speed"
ATTR_MOP_LEVEL = "mop_level"
ATTR_CLEAN_TIMES = "clean_times"
