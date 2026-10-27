"""Constants for the Eufy Clean Rooms integration."""

from __future__ import annotations

DOMAIN = "eufy_clean_rooms"

CONF_DEVICE_SN = "device_sn"  # Eufy device_sn (MQTT robots) or Tuya devId (Tuya robots)
CONF_DEVICE_MODEL = "device_model"
CONF_DEVICE_NAME = "device_name"
CONF_OPENUDID = "openudid"
CONF_TRANSPORT = "transport"  # absent on entries made before 0.2.0 -> MQTT
CONF_TUYA_REGION = "tuya_region"
CONF_DOCK_HOST = "dock_host"  # LAN address of the dock, for the P2P room list

TRANSPORT_MQTT = "mqtt"
TRANSPORT_TUYA = "tuya"

# Tuya cloud, as used by the Eufy app for robots that are not on Eufy's MQTT
# broker. Public app identifiers, from jeppesens/eufy-clean (see NOTICE.md).
TUYA_CLIENT_ID = "yx5v9uc3ef9wg3v9atje"
TUYA_SECRET = "s8x78u7xwymasd9kqa7a73pjhxqsedaj"
TUYA_SECRET2 = "cepev5pfnhua4dkqkdpmnrdxx378mpjr"
TUYA_CERT_SIGN = "A"
TUYA_API_ET_VERSION = "0.0.1"
TUYA_REGIONS = {
    "EU": "https://a1.tuyaeu.com/api.json",
    "US": "https://a1.tuyaus.com/api.json",
}
TUYA_REGION_ORDER = ("EU", "US")
TUYA_POLL_SECONDS = 30
# Tuya productId -> Eufy model, for robots the Eufy device list doesn't describe
TUYA_PRODUCT_MODELS = {"k8ttkrhlqtnfd8e9": "T2080A"}
# Models whose firmware reads SelectRoomsClean.Room.order as a zero-based slot
ZERO_BASED_ORDER_MODELS = ("T2080",)

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
DPS_MAP_EDIT_REQUEST = "170"  # MapEditRequest (per-room custom clean params)
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
