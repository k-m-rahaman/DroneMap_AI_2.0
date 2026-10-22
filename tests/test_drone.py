"""Use real MAVLink packets over loopback, never production sample telemetry."""
import socket
import threading
import time
from types import SimpleNamespace
import pytest

import drone
from drone import DroneHub, _Link, _configuration, _dependencies


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("Telemetry did not reach expected state before test deadline")


@pytest.fixture
def hub():
    service = DroneHub()
    yield service
    service.close()


@pytest.fixture
def dialect():
    common = _dependencies()[0]
    if common is None:
        pytest.skip("Optional pymavlink dependency is not installed")
    return common


def packets(common, *, system=7):
    encoder = common.MAVLink(None, srcSystem=system, srcComponent=1)
    heartbeat = encoder.heartbeat_encode(common.MAV_TYPE_QUADROTOR, common.MAV_AUTOPILOT_PX4,
        common.MAV_MODE_FLAG_SAFETY_ARMED, 0, common.MAV_STATE_ACTIVE).pack(encoder)
    position = encoder.global_position_int_encode(0, 225726000, 883639000, 73000, 60000,
        300, 400, 0, 9000).pack(encoder)
    gps = encoder.gps_raw_int_encode(0, 3, 225726000, 883639000, 73000,
        50, 50, 500, 9000, 14).pack(encoder)
    return heartbeat + position + gps


@pytest.mark.parametrize("config", [
    {"transport": "file", "device": "best.pt"},
    {"transport": "tcp", "host": "8.8.8.8", "port": 14550},
    {"transport": "udpin", "host": "0.0.0.0", "port": 14550},
    {"transport": "tcp", "host": "example.com", "port": 14550},
    {"transport": "tcp", "host": 2130706433, "port": 14550},
    {"transport": "udpin", "host": "127.0.0.1", "port": 22},
    {"transport": "udpin", "host": "127.0.0.1", "port": True},
    {"transport": "serial", "device": "C:/secret.txt"},
    {"transport": "serial", "device": "COM3"},
])
def test_rejects_external_hosts_arbitrary_files_and_undetected_ports(config):
    with pytest.raises(ValueError):
        _configuration(config, [])


def test_detected_serial_port_is_normalized():
    result = _configuration({"transport": "serial", "device": "com12", "baud": 115200},
        [{"device": "COM12", "description": "Detected flight controller"}])
    assert result["device"] == "COM12" and result["baud"] == 115200


def test_legacy_mavlink1_heartbeat_is_received_without_control(dialect):
    encoder = dialect.MAVLink(None, srcSystem=12, srcComponent=1)
    message = encoder.heartbeat_encode(dialect.MAV_TYPE_QUADROTOR,
        dialect.MAV_AUTOPILOT_ARDUPILOTMEGA, 0, 0, dialect.MAV_STATE_STANDBY)
    wire = message.pack(encoder, force_mavlink1=True)
    assert wire[0] == 0xFE
    link = _Link({"transport":"udpin"})
    decoded = dialect.MAVLink(None).parse_buffer(wire)[0]
    DroneHub._sample(link, decoded, dialect)
    assert link.state == 'connected'
    assert link.telemetry['system_id'] == 12
    assert link.telemetry['armed'] is False
    assert 'latitude' not in link.telemetry


def test_disconnected_state_has_no_fabricated_samples_or_control(hub):
    status = hub.status(1)
    assert status["connected"] is False
    assert status["telemetry"] == {} and status["connection"] is None
    assert not any(status["controls"].values())
    assert hub.disconnect(1)["telemetry"] == {}


def test_optional_dependency_missing_state(monkeypatch, hub):
    monkeypatch.setattr(drone, "_dependencies", lambda: (None, None, None))
    assert hub.status(1)["state"] == "unavailable"
    with pytest.raises(ValueError, match="Install"):
        hub.connect(1, {"transport": "udpin"})


def test_gcs_heartbeats_other_vehicles_and_preheartbeat_positions_are_ignored(dialect):
    link = _Link({"transport": "udpin"})
    parser = dialect.MAVLink(None)
    aircraft = parser.parse_buffer(packets(dialect))
    DroneHub._sample(link, aircraft[1], dialect)
    assert link.telemetry == {}
    encoder = dialect.MAVLink(None, srcSystem=255, srcComponent=190)
    ground_station = encoder.heartbeat_encode(dialect.MAV_TYPE_GCS, dialect.MAV_AUTOPILOT_INVALID, 0, 0, 0)
    decoded = parser.parse_buffer(ground_station.pack(encoder))[0]
    DroneHub._sample(link, decoded, dialect)
    assert link.telemetry == {}
    DroneHub._sample(link, aircraft[0], dialect)
    other = parser.parse_buffer(packets(dialect, system=8))
    DroneHub._sample(link, other[1], dialect)
    assert "latitude" not in link.telemetry
    DroneHub._sample(link, aircraft[1], dialect)
    assert link.telemetry["latitude"] == pytest.approx(22.5726)
    assert link.telemetry["groundspeed_m_s"] == 5


def test_stale_state_is_per_user_and_disconnect_clears_values(hub, dialect):
    link = _Link({"transport": "udpin", "host": "127.0.0.1", "port": 14550})
    parser = dialect.MAVLink(None)
    DroneHub._sample(link, parser.parse_buffer(packets(dialect))[0], dialect)
    hub._links[1] = link
    assert hub.status(1)["connected"] is True
    assert hub.status(2)["telemetry"] == {}
    link.last_heartbeat -= 6
    assert hub.status(1)["state"] == "stale"
    assert hub.status(1)["connected"] is False
    assert hub.disconnect(1)["telemetry"] == {}
    assert link.telemetry == {}


def test_udp_loopback_receives_real_mavlink_and_releases_socket(hub, dialect):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    status = hub.connect(1, {"transport": "udpin", "host": "127.0.0.1", "port": port})
    assert status["connected"] is False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        data = packets(dialect)
        def transmit_until_received():
            sender.sendto(data, ("127.0.0.1", port))
            status = hub.status(1)
            return status if status["connected"] and "gps_fix" in status["telemetry"] else None
        status = wait_for(transmit_until_received)
    assert status["telemetry"]["system_id"] == 7
    assert status["telemetry"]["longitude"] == pytest.approx(88.3639)
    assert status["telemetry"]["relative_altitude_m"] == 60
    assert status["telemetry"]["satellites"] == 14
    assert status["telemetry"]["armed"] is True
    with pytest.raises(ValueError, match="already in use"):
        hub.connect(2, {"transport": "udpin", "host": "127.0.0.1", "port": port})
    worker = hub._links[1].thread
    hub.disconnect(1)
    assert not worker.is_alive()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reused:
        reused.bind(("127.0.0.1", port))


def test_tcp_loopback_receives_real_mavlink(hub, dialect):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        port = listener.getsockname()[1]
        sent = threading.Event()
        def controller():
            connection, _ = listener.accept()
            with connection:
                connection.sendall(packets(dialect))
                sent.wait(3)
        thread = threading.Thread(target=controller, daemon=True)
        thread.start()
        try:
            hub.connect(1, {"transport": "tcp", "host": "127.0.0.1", "port": port})
            status = wait_for(lambda: hub.status(1) if hub.status(1)["connected"] else None)
            assert status["telemetry"]["vehicle_type"] == "MAV_TYPE_QUADROTOR"
            assert hub.status(2)["telemetry"] == {}
            hub.disconnect(1)
        finally:
            sent.set()
            thread.join(3)


def test_no_heartbeat_times_out_without_invented_connected_state(hub, dialect, monkeypatch):
    monkeypatch.setattr(drone, "CONNECT_TIMEOUT", 0.1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    hub.connect(1, {"transport": "udpin", "host": "127.0.0.1", "port": port})
    status = wait_for(lambda: hub.status(1) if hub.status(1)["state"] == "timeout" else None)
    assert not status["connected"] and status["telemetry"] == {}
    hub._links[1].thread.join(1)
    assert not hub._links[1].thread.is_alive()


def _incoming(kind, **fields):
    return SimpleNamespace(get_type=lambda: kind, get_srcSystem=lambda: 7,
        get_srcComponent=lambda: 1, **fields)


def _clocked_link(hub, dialect, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(drone.time, "monotonic", lambda: clock[0])
    link = _Link({"transport": "udpin", "host": "127.0.0.1", "port": 14550})
    hub._links[1] = link
    heartbeat = dialect.MAVLink(None).parse_buffer(packets(dialect))[0]
    DroneHub._sample(link, heartbeat, dialect)
    return clock, link, heartbeat


def test_sensor_values_expire_despite_fresh_heartbeats(hub, dialect, monkeypatch):
    clock, link, heartbeat = _clocked_link(hub, dialect, monkeypatch)
    messages = dialect.MAVLink(None).parse_buffer(packets(dialect))
    DroneHub._sample(link, messages[1], dialect)
    DroneHub._sample(link, messages[2], dialect)
    DroneHub._sample(link, _incoming("SYS_STATUS", battery_remaining=83), dialect)
    assert hub.status(1)["telemetry"]["battery_percent"] == 83
    clock[0] += 6
    DroneHub._sample(link, heartbeat, dialect)
    status = hub.status(1)
    assert status["connected"]
    for field in ("latitude", "longitude", "altitude_m", "relative_altitude_m", "gps_fix", "satellites", "heading_deg", "groundspeed_m_s"):
        assert status["telemetry"][field] is None
        assert status["telemetry_freshness"][field]["age_seconds"] == 6
        assert status["telemetry_freshness"][field]["stale"] is True
    assert status["telemetry"]["armed"] is True
    assert status["telemetry"]["battery_percent"] == 83
    clock[0] += 10
    DroneHub._sample(link, heartbeat, dialect)
    assert hub.status(1)["telemetry"]["battery_percent"] is None
    assert hub.status(1)["telemetry_freshness"]["battery_percent"]["stale"]


def test_fix_loss_clears_position_until_a_valid_fix_returns(hub, dialect, monkeypatch):
    clock, link, _ = _clocked_link(hub, dialect, monkeypatch)
    messages = dialect.MAVLink(None).parse_buffer(packets(dialect))
    DroneHub._sample(link, messages[1], dialect)
    assert hub.status(1)["telemetry"]["latitude"] == pytest.approx(22.5726)
    clock[0] += 1
    lost = _incoming("GPS_RAW_INT", fix_type=1, satellites_visible=255, lat=0, lon=0, alt=0)
    DroneHub._sample(link, lost, dialect)
    for field in ("latitude", "longitude", "altitude_m", "relative_altitude_m"):
        assert hub.status(1)["telemetry"][field] is None
    assert hub.status(1)["telemetry"]["satellites"] is None
    DroneHub._sample(link, messages[1], dialect)
    assert hub.status(1)["telemetry"]["latitude"] is None
    DroneHub._sample(link, messages[2], dialect)
    assert hub.status(1)["telemetry"]["latitude"] == pytest.approx(22.5726)
    assert hub.status(1)["telemetry"]["position_source"] == "GPS_RAW_INT"


def test_gps_fallback_refreshes_stale_global_position_and_2d_has_no_altitude(hub, dialect, monkeypatch):
    clock, link, _ = _clocked_link(hub, dialect, monkeypatch)
    messages = dialect.MAVLink(None).parse_buffer(packets(dialect))
    DroneHub._sample(link, messages[1], dialect)
    alternate = _incoming("GPS_RAW_INT", fix_type=3, satellites_visible=12, lat=225727000, lon=883640000, alt=74000)
    clock[0] += 1
    DroneHub._sample(link, alternate, dialect)
    assert hub.status(1)["telemetry"]["latitude"] == pytest.approx(22.5726)
    assert hub.status(1)["telemetry"]["position_source"] == "GLOBAL_POSITION_INT"
    clock[0] += 5
    DroneHub._sample(link, alternate, dialect)
    assert hub.status(1)["telemetry"]["latitude"] == pytest.approx(22.5727)
    assert hub.status(1)["telemetry"]["altitude_m"] == 74
    assert hub.status(1)["telemetry"]["relative_altitude_m"] is None
    alternate.fix_type = 2
    DroneHub._sample(link, alternate, dialect)
    assert hub.status(1)["telemetry"]["altitude_m"] is None


def test_unknown_current_battery_and_heading_clear_previous_measurements(hub, dialect, monkeypatch):
    _, link, _ = _clocked_link(hub, dialect, monkeypatch)
    DroneHub._sample(link, _incoming("SYS_STATUS", battery_remaining=75), dialect)
    assert hub.status(1)["telemetry"]["battery_percent"] == 75
    DroneHub._sample(link, _incoming("BATTERY_STATUS", battery_remaining=-1), dialect)
    status = hub.status(1)
    assert status["telemetry"]["battery_percent"] is None
    assert not status["telemetry_freshness"]["battery_percent"]["stale"]
    position = dialect.MAVLink(None).parse_buffer(packets(dialect))[1]
    DroneHub._sample(link, position, dialect)
    assert hub.status(1)["telemetry"]["heading_deg"] == 90
    position.hdg = 65535
    DroneHub._sample(link, position, dialect)
    assert hub.status(1)["telemetry"]["heading_deg"] is None


def test_invalid_global_position_does_not_block_valid_gps_fallback(hub, dialect, monkeypatch):
    _, link, _ = _clocked_link(hub, dialect, monkeypatch)
    messages = dialect.MAVLink(None).parse_buffer(packets(dialect))
    messages[1].lat = 910000000
    DroneHub._sample(link, messages[1], dialect)
    assert hub.status(1)["telemetry"]["latitude"] is None
    DroneHub._sample(link, messages[2], dialect)
    assert hub.status(1)["telemetry"]["latitude"] == pytest.approx(22.5726)
    assert hub.status(1)["telemetry"]["position_source"] == "GPS_RAW_INT"
