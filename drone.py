"""Receive real MAVLink telemetry without exposing aircraft control commands.

Only local/privately addressed links and detected serial ports may be opened.
The optional pymavlink decoder supports MAVLink 1/2; no simulated samples are
substituted when hardware, packets, or dependencies are unavailable.
"""
from __future__ import annotations

import copy
import ipaddress
import math
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

CONNECT_TIMEOUT = 8.0
STALE_AFTER = 5.0
MAX_LINKS = 8
# A heartbeat proves that the aircraft link is alive, not that every sensor is.
FIELD_TTLS = {
    **dict.fromkeys(("latitude", "longitude", "relative_altitude_m", "altitude_m",
                    "heading_deg", "groundspeed_m_s", "gps_fix", "satellites", "armed", "position_source"), 5.0),
    "battery_percent": 15.0,
}
_POSITION_FIELDS = ("latitude", "longitude", "relative_altitude_m", "altitude_m", "position_source")
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
))


def _dependencies():
    try:
        from pymavlink.dialects.v20 import common
        import serial
        from serial.tools import list_ports
        return common, serial, list_ports
    except ImportError:
        return None, None, None


def _number(value, low, high, label, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{label} must be {'an integer ' if integer else ''}between {low} and {high}.")
    return int(value) if integer else float(value)


def _configuration(config, ports):
    if not isinstance(config, dict):
        raise ValueError("Connection settings must be an object.")
    transport = config.get("transport", "udpin")
    if transport not in ("udpin", "tcp", "serial"):
        raise ValueError("Choose UDP listener, TCP, or serial transport.")
    result = {"transport": transport, "host": None, "port": None, "device": None, "baud": None}
    if transport == "serial":
        device = config.get("device", "")
        if not isinstance(device, str) or not device or len(device) > 160:
            raise ValueError("Select a detected serial port.")
        detected = {item["device"].casefold(): item["device"] for item in ports}
        if device.casefold() not in detected:
            raise ValueError("Serial port is not currently detected. Refresh the port list and select it.")
        # Detected COM devices are allowed; filenames, URLs, and named pipes are not.
        selected = detected[device.casefold()]
        if selected.upper().startswith("COM") and not re.fullmatch(r"COM[1-9][0-9]{0,3}", selected, re.I):
            raise ValueError("Invalid Windows serial port.")
        if not re.fullmatch(r"COM[1-9][0-9]{0,3}", selected, re.I) and not selected.startswith(("/dev/tty", "/dev/cu.")):
            raise ValueError("Unsupported serial device.")
        result.update(device=selected, baud=_number(config.get("baud", 57600), 1200, 921600, "Baud rate", integer=True))
    else:
        host = config.get("host", "127.0.0.1")
        if not isinstance(host, str):
            raise ValueError("Use an explicit local or private IPv4 address.")
        if host == "localhost":
            host = "127.0.0.1"
        try:
            address = ipaddress.IPv4Address(host)
        except (ValueError, TypeError, ipaddress.AddressValueError) as exc:
            raise ValueError("Use an explicit local or private IPv4 address.") from exc
        if not any(address in network for network in _PRIVATE_NETWORKS):
            raise ValueError("Drone links must use loopback or a private IPv4 network address.")
        result.update(host=str(address), port=_number(config.get("port", 14550), 1024, 65535, "Port", integer=True))
    return result


@dataclass
class _Link:
    config: dict
    state: str = "connecting"
    message: str = "Waiting for an aircraft heartbeat."
    telemetry: dict = field(default_factory=dict)
    sample_times: dict = field(default_factory=dict)
    sample_dates: dict = field(default_factory=dict)
    global_position_at: float | None = None
    gps_valid: bool | None = None
    last_heartbeat: float | None = None
    selected_system: int | None = None
    selected_component: int | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    stream: object = None
    thread: threading.Thread | None = None


class DroneHub:
    def __init__(self):
        self._links = {}
        self._mutation = threading.RLock()
        self._closed = False

    def ports(self):
        _, _, list_ports = _dependencies()
        if list_ports is None:
            return []
        try:
            return [{"device": p.device, "description": p.description or p.device}
                    for p in sorted(list_ports.comports(), key=lambda p: p.device)]
        except (OSError, RuntimeError):
            return []

    def status(self, user_id):
        dependency_available = _dependencies()[0] is not None
        with self._mutation:
            link = self._links.get(user_id)
        result = {
            "available": dependency_available, "dependency_available": dependency_available,
            "connected": False, "state": "disconnected" if dependency_available else "unavailable",
            "message": "No aircraft connected." if dependency_available else "Install requirements-drone.txt to enable MAVLink telemetry.",
            "connection": None, "telemetry": {}, "telemetry_freshness": {},
            "supported_transports": ["udpin", "tcp", "serial"],
            "controls": {"can_arm": False, "can_upload_mission": False, "can_capture": False},
        }
        if link:
            with link.lock:
                now = time.monotonic()
                stale = link.last_heartbeat is not None and now - link.last_heartbeat > STALE_AFTER
                state = "stale" if stale and link.state == "connected" else link.state
                telemetry = copy.deepcopy(link.telemetry)
                freshness = {}
                for key, observed in link.sample_times.items():
                    age = max(0.0, now - observed)
                    expired = age > FIELD_TTLS[key]
                    freshness[key] = {"age_seconds": round(age, 3),
                        "updated_at": link.sample_dates[key], "stale": expired}
                    if expired:
                        telemetry[key] = None
                result.update(
                    state=state, connected=state == "connected",
                    message="Aircraft heartbeat lost; displayed telemetry is stale." if state == "stale" else link.message,
                    connection=copy.deepcopy(link.config), telemetry=telemetry, telemetry_freshness=freshness,
                )
        return result

    def connect(self, user_id, config):
        normalized = _configuration(config, self.ports())
        if _dependencies()[0] is None:
            raise ValueError("Install requirements-drone.txt before connecting a MAVLink aircraft.")
        with self._mutation:
            if self._closed:
                raise ValueError("Drone service is shutting down.")
            key = self._endpoint(normalized)
            for owner, other in self._links.items():
                if owner == user_id:
                    continue
                if self._endpoint(other.config) == key and other.thread and other.thread.is_alive():
                    raise ValueError("This drone link is already in use by another session.")
            active = sum(bool(link.thread and link.thread.is_alive()) for owner, link in self._links.items() if owner != user_id)
            if active >= MAX_LINKS:
                raise ValueError("The maximum number of telemetry links is already open.")
            self._disconnect(user_id)
            link = _Link(normalized)
            self._links[user_id] = link
            link.thread = threading.Thread(target=self._receive, args=(link,), name="dronemap-telemetry", daemon=True)
            link.thread.start()
        return self.status(user_id)

    @staticmethod
    def _endpoint(config):
        if config["transport"] == "serial":
            return ("serial", config["device"].casefold())
        return config["transport"], config["host"], config["port"]

    def _disconnect(self, user_id):
        link = self._links.pop(user_id, None)
        if link is None:
            return
        link.stop.set()
        with link.lock:
            stream = link.stream
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        if link.thread and link.thread is not threading.current_thread():
            link.thread.join(timeout=1.5)
        with link.lock:
            link.telemetry.clear()
            link.sample_times.clear()
            link.sample_dates.clear()
            link.global_position_at = None
            link.gps_valid = None
            link.last_heartbeat = None
            link.state = "disconnected"

    def disconnect(self, user_id):
        with self._mutation:
            self._disconnect(user_id)
        return self.status(user_id)

    def close(self):
        with self._mutation:
            self._closed = True
            for user_id in list(self._links):
                self._disconnect(user_id)

    @staticmethod
    def _receive(link):
        common, serial, _ = _dependencies()
        stream = None
        started = time.monotonic()
        try:
            config = link.config
            if config["transport"] == "udpin":
                stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Exclusive bind: do not enable SO_REUSEADDR for aircraft links.
                stream.bind((config["host"], config["port"]))
                stream.settimeout(0.25)
            elif config["transport"] == "tcp":
                stream = socket.create_connection((config["host"], config["port"]), timeout=1.0)
                stream.settimeout(0.25)
            else:
                stream = serial.Serial(config["device"], config["baud"], timeout=0.25, write_timeout=0.25)
            with link.lock:
                link.stream = stream
            parser = common.MAVLink(None)
            parser.robust_parsing = True
            while not link.stop.is_set():
                if link.last_heartbeat is None and time.monotonic() - started > CONNECT_TIMEOUT:
                    with link.lock:
                        link.state = "timeout"
                        link.message = "No aircraft heartbeat received within 8 seconds. Check the controller telemetry output and connection settings."
                    break
                try:
                    if config["transport"] == "serial":
                        data = stream.read(4096)
                    elif config["transport"] == "udpin":
                        data = stream.recvfrom(65535)[0]
                    else:
                        data = stream.recv(4096)
                        if not data:
                            raise OSError("The controller closed the TCP link.")
                except (socket.timeout, TimeoutError):
                    continue
                if not data:
                    continue
                for message in parser.parse_buffer(data) or []:
                    DroneHub._sample(link, message, common)
        except Exception:
            if not link.stop.is_set():
                with link.lock:
                    link.state = "error"
                    # Never expose arbitrary operating-system exception strings.
                    link.message = "Unable to read this drone link. Check the selected address/port, cable, and whether another application is using it."
        finally:
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            with link.lock:
                link.stream = None

    @staticmethod
    def _fields(link, now, **values):
        observed_at = datetime.now(timezone.utc).isoformat()
        link.telemetry.update(values)
        for key in values:
            link.sample_times[key] = now
            link.sample_dates[key] = observed_at

    @staticmethod
    def _position(link, now, lat, lon, altitude, *, source, relative=None):
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            DroneHub._fields(link, now, **dict.fromkeys(_POSITION_FIELDS))
            return False
        DroneHub._fields(link, now, latitude=lat, longitude=lon, altitude_m=altitude,
            relative_altitude_m=relative, position_source=source)
        return True

    @staticmethod
    def _sample(link, message, common):
        kind = message.get_type()
        if kind == "BAD_DATA":
            return
        system, component = message.get_srcSystem(), message.get_srcComponent()
        if not 1 <= system <= 255:
            return
        with link.lock:
            now = time.monotonic()
            if kind == "HEARTBEAT":
                if message.type == common.MAV_TYPE_GCS or message.autopilot == common.MAV_AUTOPILOT_INVALID:
                    return
                if link.selected_system is None:
                    link.selected_system, link.selected_component = system, component
                if (system, component) != (link.selected_system, link.selected_component):
                    return
                link.last_heartbeat = now
                link.state = "connected"
                link.message = "Receiving live aircraft telemetry."
                vehicle = common.enums["MAV_TYPE"].get(message.type)
                autopilot = common.enums["MAV_AUTOPILOT"].get(message.autopilot)
                link.telemetry.update(
                    system_id=system, component_id=component,
                    vehicle_type=vehicle.name if vehicle else str(message.type),
                    autopilot=autopilot.name if autopilot else str(message.autopilot),
                    last_heartbeat_at=datetime.now(timezone.utc).isoformat(),
                )
                DroneHub._fields(link, now, armed=bool(message.base_mode & common.MAV_MODE_FLAG_SAFETY_ARMED))
                return
            if system != link.selected_system:
                return
            if kind == "GLOBAL_POSITION_INT":
                lat, lon = message.lat / 1e7, message.lon / 1e7
                if link.gps_valid is not False:
                    valid_position = DroneHub._position(link, now, lat, lon, message.alt / 1000.0,
                        source="GLOBAL_POSITION_INT", relative=message.relative_alt / 1000.0)
                    link.global_position_at = now if valid_position else None
                DroneHub._fields(link, now, heading_deg=message.hdg / 100.0 if 0 <= message.hdg <= 36000 else None,
                    groundspeed_m_s=math.hypot(message.vx, message.vy) / 100.0)
            elif kind == "GPS_RAW_INT":
                DroneHub._fields(link, now, gps_fix=int(message.fix_type),
                    satellites=None if message.satellites_visible == 255 else int(message.satellites_visible))
                link.gps_valid = 2 <= message.fix_type <= 8
                if not link.gps_valid:
                    DroneHub._fields(link, now, **dict.fromkeys(_POSITION_FIELDS))
                    link.global_position_at = None
                elif link.global_position_at is None or now - link.global_position_at > FIELD_TTLS["latitude"]:
                    lat, lon = message.lat / 1e7, message.lon / 1e7
                    DroneHub._position(link, now, lat, lon,
                        message.alt / 1000.0 if message.fix_type >= 3 else None, source="GPS_RAW_INT")
            elif kind in ("SYS_STATUS", "BATTERY_STATUS"):
                remaining = message.battery_remaining
                DroneHub._fields(link, now, battery_percent=int(remaining) if 0 <= remaining <= 100 else None)
            elif kind == "VFR_HUD":
                DroneHub._fields(link, now,
                    groundspeed_m_s=float(message.groundspeed) if math.isfinite(message.groundspeed) and message.groundspeed >= 0 else None,
                    heading_deg=float(message.heading) if 0 <= message.heading <= 360 else None)
