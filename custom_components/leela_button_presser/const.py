"""Constants for Leela Button Presser."""

DOMAIN = "leela_button_presser"
API_PATH = "/api/leela_button_presser/mcp"

CONF_SECRET = "secret"
CONF_LIGHTS = "lights"
CONF_BUTTONS = "buttons"
CONF_NUMBERS = "numbers"
CONF_VACUUM = "vacuum"
CONF_ROOMS = "rooms"
CONF_TIMERS = "timers"
CONF_SENSORS = "sensors"
CONF_RATE_LIMIT = "rate_limit_per_minute"
CONF_PRELOAD = "preload_dial_defaults"
CONF_REGENERATE = "regenerate_secret"

DEFAULT_RATE_LIMIT = 30

# Entities of the M5 Dial (esphome/m5dial.yaml); offered as a one-click preset.
DIAL_DEFAULTS = {
    CONF_LIGHTS: [
        "light.bathroom_nightlight_bathroom_nightlight",
        "light.kitchen_lights_small_lights",
        "light.kitchen_lights_big_lights",
        "light.desk_light_desk_light",
    ],
    CONF_BUTTONS: ["button.fmd_rafa_volume_ring_device"],
    CONF_NUMBERS: [],
    CONF_VACUUM: "vacuum.juanita",
    CONF_ROOMS: (
        "corridor=16\nbathroom=17\nbedroom=19\nrafas_office=18\n"
        "carpet=22\nkitchen=24\njadalnia=23"
    ),
    CONF_TIMERS: ["timer.egg"],
    CONF_SENSORS: [
        "sensor.mt15_mt29_carbon_dioxide",
        "sensor.mt15_mt29_formaldehyde",
    ],
}
