"""Pure allowlist / tool logic (no Home Assistant imports, unit-testable).

Every tool is derived from the options configured in the UI. A tool call is
first validated against its parameter spec, planned into a ServiceCall or
ReadCall, and finally re-checked by ``check_call`` right before execution.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import re
from typing import Any

from .const import (
    CONF_BUTTONS,
    CONF_LIGHTS,
    CONF_NUMBERS,
    CONF_RATE_LIMIT,
    CONF_ROOMS,
    CONF_SENSORS,
    CONF_TIMER_LABEL,
    CONF_TIMERS,
    CONF_VACUUM,
    DEFAULT_RATE_LIMIT,
)

ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
ROOM_RE = re.compile(r"^[a-z0-9_]{1,40}$")

LIGHT_DOMAINS = ("light",)
BUTTON_DOMAINS = ("button", "input_button")
NUMBER_DOMAINS = ("number", "input_number")
VACUUM_DOMAINS = ("vacuum",)
TIMER_DOMAINS = ("timer",)
TEXT_DOMAINS = ("input_text",)
DURATION_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
MAX_TIMER_SECONDS = 24 * 3600
MAX_LABEL_CHARS = 60

# (domain, service) -> data keys that may ever be sent (entity_id always).
ALLOWED_SERVICES: dict[tuple[str, str], frozenset[str]] = {
    ("light", "turn_on"): frozenset({"entity_id", "brightness"}),
    ("light", "turn_off"): frozenset({"entity_id"}),
    ("light", "toggle"): frozenset({"entity_id"}),
    ("button", "press"): frozenset({"entity_id"}),
    ("input_button", "press"): frozenset({"entity_id"}),
    ("number", "set_value"): frozenset({"entity_id", "value"}),
    ("input_number", "set_value"): frozenset({"entity_id", "value"}),
    ("vacuum", "start"): frozenset({"entity_id"}),
    ("vacuum", "send_command"): frozenset({"entity_id", "command", "params"}),
    ("timer", "start"): frozenset({"entity_id", "duration"}),
    ("timer", "cancel"): frozenset({"entity_id"}),
    ("input_text", "set_value"): frozenset({"entity_id", "value"}),
}
VACUUM_ROOM_COMMAND = "app_segment_clean"

READ_ATTRIBUTES = (
    "friendly_name",
    "unit_of_measurement",
    "device_class",
    "brightness",
    "min",
    "max",
    "step",
    "finishes_at",
    "remaining",
    "duration",
    "status",
    "battery_level",
)


class ToolError(Exception):
    """A problem with a tool call, reported to the model as a tool error."""


@dataclass(frozen=True)
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ReadCall:
    entity_id: str | None  # None -> list the allowlist


@dataclass(frozen=True)
class Param:
    name: str
    description: str
    kind: str  # "enum" | "int" | "number"
    required: bool = True
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None

    def json_schema(self) -> dict[str, Any]:
        if self.kind == "string":
            out_s: dict[str, Any] = {"type": "string", "description": self.description}
            if self.minimum is not None:
                out_s["minLength"] = int(self.minimum)
            if self.maximum is not None:
                out_s["maxLength"] = int(self.maximum)
            return out_s
        if self.kind == "enum":
            return {
                "type": "string",
                "enum": list(self.choices),
                "description": self.description,
            }
        out: dict[str, Any] = {
            "type": "integer" if self.kind == "int" else "number",
            "description": self.description,
        }
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        return out

    def coerce(self, value: Any) -> Any:
        if self.kind == "string":
            if not isinstance(value, str):
                raise ToolError(f"'{self.name}' must be text")
            text = " ".join(value.split())  # one line, no control characters
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ToolError(f"'{self.name}' has invalid characters")
            if self.minimum is not None and len(text) < self.minimum:
                raise ToolError(f"'{self.name}' must be at least {int(self.minimum)} characters")
            if self.maximum is not None and len(text) > self.maximum:
                raise ToolError(f"'{self.name}' must be at most {int(self.maximum)} characters")
            return text
        if self.kind == "enum":
            if not isinstance(value, str) or value not in self.choices:
                raise ToolError(
                    f"'{self.name}' must be one of: {', '.join(self.choices)}"
                )
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolError(f"'{self.name}' must be a number")
        if self.kind == "int":
            if isinstance(value, float):
                if not value.is_integer():
                    raise ToolError(f"'{self.name}' must be an integer")
                value = int(value)
        elif value != value or value in (float("inf"), float("-inf")):
            raise ToolError(f"'{self.name}' must be a finite number")
        if self.minimum is not None and value < self.minimum:
            raise ToolError(f"'{self.name}' must be >= {self.minimum:g}")
        if self.maximum is not None and value > self.maximum:
            raise ToolError(f"'{self.name}' must be <= {self.maximum:g}")
        return value


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: tuple[Param, ...]
    build: Callable[[dict[str, Any]], ServiceCall | ReadCall]
    read_only: bool = False

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {p.name: p.json_schema() for p in self.params},
            "required": [p.name for p in self.params if p.required],
            "additionalProperties": False,
        }

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema(),
            "annotations": {"readOnlyHint": self.read_only},
        }

    def plan(self, args: Any) -> ServiceCall | ReadCall:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        known = {p.name for p in self.params}
        extra = sorted(set(args) - known)
        if extra:
            raise ToolError(f"unknown argument(s): {', '.join(extra)}")
        clean: dict[str, Any] = {}
        for p in self.params:
            if p.name in args and args[p.name] is not None:
                clean[p.name] = p.coerce(args[p.name])
            elif p.required:
                raise ToolError(f"missing required argument '{p.name}'")
        return self.build(clean)


@dataclass(frozen=True)
class Options:
    lights: tuple[str, ...] = ()
    buttons: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    vacuum: str | None = None
    rooms: Mapping[str, int] = field(default_factory=dict)
    timers: tuple[str, ...] = ()
    timer_label: str | None = None
    sensors: tuple[str, ...] = ()
    rate_limit: int = DEFAULT_RATE_LIMIT

    def controllable(self) -> set[str]:
        out = {*self.lights, *self.buttons, *self.numbers, *self.timers}
        if self.timer_label:
            out.add(self.timer_label)
        if self.vacuum:
            out.add(self.vacuum)
        return out

    def readable(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for e in (
            *self.lights,
            *self.numbers,
            *self.timers,
            *([self.timer_label] if self.timer_label else []),
            *([self.vacuum] if self.vacuum else []),
            *self.sensors,
        ):
            seen.setdefault(e)
        return tuple(seen)


def parse_rooms(text: str) -> dict[str, int]:
    """Parse 'name=segment_id' lines. Raises ValueError on bad input."""
    rooms: dict[str, int] = {}
    for raw in text.replace(",", "\n").splitlines():
        line = raw.strip()
        if not line:
            continue
        name, sep, seg = line.partition("=")
        name = name.strip().lower().replace(" ", "_")
        seg = seg.strip()
        if not sep or not ROOM_RE.match(name) or not seg.isdigit():
            raise ValueError(line)
        seg_id = int(seg)
        if not 0 < seg_id < 100000:
            raise ValueError(line)
        rooms[name] = seg_id
    return rooms


def _entities(raw: Any, domains: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    out: dict[str, None] = {}
    for e in raw:
        if (
            isinstance(e, str)
            and ENTITY_RE.match(e)
            and e.split(".", 1)[0] in domains
        ):
            out.setdefault(e)
    return tuple(out)


def parse_options(raw: Mapping[str, Any]) -> Options:
    """Tolerant parse of stored options; invalid entries are dropped."""
    vac = _entities(raw.get(CONF_VACUUM) or [], VACUUM_DOMAINS)
    rooms_raw = raw.get(CONF_ROOMS) or ""
    try:
        rooms = parse_rooms(rooms_raw) if isinstance(rooms_raw, str) else {}
    except ValueError:
        rooms = {}
    try:
        rate = int(raw.get(CONF_RATE_LIMIT, DEFAULT_RATE_LIMIT))
    except (TypeError, ValueError):
        rate = DEFAULT_RATE_LIMIT
    label = _entities(raw.get(CONF_TIMER_LABEL), TEXT_DOMAINS)
    return Options(
        lights=_entities(raw.get(CONF_LIGHTS), LIGHT_DOMAINS),
        buttons=_entities(raw.get(CONF_BUTTONS), BUTTON_DOMAINS),
        numbers=_entities(raw.get(CONF_NUMBERS), NUMBER_DOMAINS),
        vacuum=vac[0] if vac else None,
        rooms=rooms,
        timers=_entities(raw.get(CONF_TIMERS), TIMER_DOMAINS),
        timer_label=label[0] if label else None,
        sensors=_sensor_list(raw.get(CONF_SENSORS)),
        rate_limit=max(1, min(600, rate)),
    )


def _sensor_list(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    out: dict[str, None] = {}
    for e in raw:
        if isinstance(e, str) and ENTITY_RE.match(e):
            out.setdefault(e)
    return tuple(out)


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _entity_param(name: str, desc: str, choices: tuple[str, ...]) -> Param:
    return Param(name, f"{desc} (entity_id)", "enum", choices=choices)


def build_tools(opts: Options) -> dict[str, Tool]:
    """Build the tool set for the configured allowlist."""
    tools: dict[str, Tool] = {}

    def add(tool: Tool) -> None:
        tools[tool.name] = tool

    if opts.lights:
        light = _entity_param("light", "Which light", opts.lights)
        add(
            Tool(
                "light_on",
                "Turn a light on, optionally at a brightness (1-255).",
                (light, Param("brightness", "Brightness 1-255", "int", False, minimum=1, maximum=255)),
                lambda a: ServiceCall(
                    "light",
                    "turn_on",
                    {"entity_id": a["light"]}
                    | ({"brightness": a["brightness"]} if "brightness" in a else {}),
                ),
            )
        )
        add(
            Tool(
                "light_off",
                "Turn a light off.",
                (light,),
                lambda a: ServiceCall("light", "turn_off", {"entity_id": a["light"]}),
            )
        )
        add(
            Tool(
                "light_toggle",
                "Toggle a light.",
                (light,),
                lambda a: ServiceCall("light", "toggle", {"entity_id": a["light"]}),
            )
        )

        def set_brightness(a: dict[str, Any]) -> ServiceCall:
            if a["brightness"] == 0:
                return ServiceCall("light", "turn_off", {"entity_id": a["light"]})
            return ServiceCall(
                "light",
                "turn_on",
                {"entity_id": a["light"], "brightness": a["brightness"]},
            )

        add(
            Tool(
                "light_set_brightness",
                "Set a light's brightness, 0 (off) to 255 (full).",
                (light, Param("brightness", "Brightness 0-255", "int", minimum=0, maximum=255)),
                set_brightness,
            )
        )

    if opts.buttons:
        add(
            Tool(
                "button_press",
                "Press a button (e.g. make the phone ring).",
                (_entity_param("button", "Which button", opts.buttons),),
                lambda a: ServiceCall(
                    _domain(a["button"]), "press", {"entity_id": a["button"]}
                ),
            )
        )

    if opts.numbers:
        add(
            Tool(
                "number_set",
                "Set a number / input_number entity to a value within its range.",
                (
                    _entity_param("number", "Which number", opts.numbers),
                    Param("value", "New value", "number"),
                ),
                lambda a: ServiceCall(
                    _domain(a["number"]),
                    "set_value",
                    {"entity_id": a["number"], "value": a["value"]},
                ),
            )
        )

    if opts.timers:
        timer_param = _entity_param("timer", "Which timer", opts.timers)

        def start_timer(a: dict[str, Any]) -> ServiceCall:
            data: dict[str, Any] = {"entity_id": a["timer"]}
            if "seconds" in a:
                data["duration"] = hms(a["seconds"])
            return ServiceCall("timer", "start", data)

        add(
            Tool(
                "timer_start",
                "Start a timer. Give seconds (1-86400) for a custom length; "
                "without it the timer's configured duration is used. "
                "Starting a running timer restarts it with the new length.",
                (
                    timer_param,
                    Param(
                        "seconds",
                        "Length in seconds, 1 to 86400",
                        "int",
                        False,
                        minimum=1,
                        maximum=MAX_TIMER_SECONDS,
                    ),
                ),
                start_timer,
            )
        )
        add(
            Tool(
                "timer_cancel",
                "Cancel a running timer without ringing.",
                (timer_param,),
                lambda a: ServiceCall("timer", "cancel", {"entity_id": a["timer"]}),
            )
        )
        if opts.timer_label:
            label_entity = opts.timer_label
            add(
                Tool(
                    "timer_label_set",
                    "Set the short text shown with the timer (empty text clears it). "
                    "It also clears itself when a timer finishes or is cancelled. "
                    "Set it before starting the timer.",
                    (
                        Param(
                            "text",
                            f"Label, up to {MAX_LABEL_CHARS} characters",
                            "string",
                            True,
                            minimum=0,
                            maximum=MAX_LABEL_CHARS,
                        ),
                    ),
                    lambda a: ServiceCall(
                        "input_text",
                        "set_value",
                        {"entity_id": label_entity, "value": a["text"]},
                    ),
                )
            )

    if opts.vacuum:
        vac = opts.vacuum
        add(
            Tool(
                "vacuum_start",
                "Start a full clean of the whole flat.",
                (),
                lambda a: ServiceCall("vacuum", "start", {"entity_id": vac}),
            )
        )
        if opts.rooms:
            rooms = dict(opts.rooms)
            add(
                Tool(
                    "vacuum_clean_room",
                    "Clean a single room.",
                    (Param("room", "Room name", "enum", choices=tuple(rooms)),),
                    lambda a: ServiceCall(
                        "vacuum",
                        "send_command",
                        {
                            "entity_id": vac,
                            "command": VACUUM_ROOM_COMMAND,
                            "params": [{"segments": [rooms[a["room"]]], "repeat": 1}],
                        },
                    ),
                )
            )

    readable = opts.readable()
    if readable:
        add(
            Tool(
                "get_state",
                "Read the state (and a few safe attributes, e.g. brightness) "
                "of an allowlisted entity. Read-only.",
                (_entity_param("entity", "Which entity", readable),),
                lambda a: ReadCall(a["entity"]),
                read_only=True,
            )
        )
        add(
            Tool(
                "list_entities",
                "List the entities and rooms this server can control or read.",
                (),
                lambda a: ReadCall(None),
                read_only=True,
            )
        )
    return tools


def hms(seconds: int) -> str:
    """Seconds -> 'HH:MM:SS' for timer.start's duration."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def check_call(opts: Options, call: ServiceCall) -> None:
    """Final guard before execution. Raises ToolError if not allowlisted."""
    keys = ALLOWED_SERVICES.get((call.domain, call.service))
    if keys is None:
        raise ToolError("service not allowed")
    if not set(call.data) <= keys:
        raise ToolError("unexpected service data")
    entity = call.data.get("entity_id")
    if (
        not isinstance(entity, str)
        or entity not in opts.controllable()
        or _domain(entity) != call.domain
    ):
        raise ToolError("entity not allowed")
    if call.domain == "vacuum" and call.service == "send_command":
        params = call.data.get("params")
        ok = (
            call.data.get("command") == VACUUM_ROOM_COMMAND
            and isinstance(params, list)
            and len(params) == 1
            and isinstance(params[0], dict)
            and set(params[0]) == {"segments", "repeat"}
            and params[0]["repeat"] == 1
            and isinstance(params[0]["segments"], list)
            and len(params[0]["segments"]) == 1
            and params[0]["segments"][0] in set(opts.rooms.values())
        )
        if not ok:
            raise ToolError("vacuum command not allowed")
    if call.domain == "timer" and "duration" in call.data:
        d = call.data["duration"]
        if not isinstance(d, str) or not DURATION_RE.match(d):
            raise ToolError("duration must be HH:MM:SS")
        h, m, sec = (int(x) for x in d.split(":"))
        if not 1 <= h * 3600 + m * 60 + sec <= MAX_TIMER_SECONDS or m > 59 or sec > 59:
            raise ToolError("duration out of range")
    if call.domain == "input_text":
        value = call.data.get("value")
        if entity != opts.timer_label:
            raise ToolError("text entity not allowed")
        if not isinstance(value, str) or len(value) > MAX_LABEL_CHARS:
            raise ToolError("text too long")
    if call.service == "turn_on" and "brightness" in call.data:
        b = call.data["brightness"]
        if isinstance(b, bool) or not isinstance(b, int) or not 1 <= b <= 255:
            raise ToolError("brightness out of range")


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)
