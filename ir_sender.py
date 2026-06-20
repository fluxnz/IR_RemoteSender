from __future__ import annotations

import os
import re
import time
import threading

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - handled by runtime error message
    serial = None
    list_ports = None


class IRSendError(Exception):
    pass


_HEX_WORD = re.compile(r"^[0-9A-Fa-f]{1,4}$")

_SERIAL_LOCK = threading.Lock()
_SERIAL_SESSION = None
_SERIAL_SESSION_PORT = None


def _is_access_denied_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        isinstance(exc, PermissionError)
        or "permissionerror" in msg
        or "access is denied" in msg
        or "errno 13" in msg
    )


def _locked_port_help(port: str) -> str:
    return (
        f"Could not open {port}: access denied (port is busy). "
        "Close Serial Monitor/Plotter, Arduino IDE, any other app using this COM port, "
        "then try again."
    )


def _normalize_pronto(command_hex: str) -> str:
    tokens = [token for token in str(command_hex).strip().split() if token]
    if len(tokens) < 6:
        raise IRSendError("Command is too short to be a Pronto HEX sequence.")
    if any(not _HEX_WORD.match(token) for token in tokens):
        raise IRSendError("Command contains non-hex words. Expected Pronto HEX like '0000 006C ...'.")
    return " ".join(token.upper() for token in tokens)


def _auto_detect_arduino_port() -> str | None:
    if list_ports is None:
        return None

    ports = list(list_ports.comports())
    if not ports:
        return None

    ranked: list[tuple[int, str]] = []
    for port in ports:
        haystack = " ".join(
            [
                str(port.device or ""),
                str(port.description or ""),
                str(port.manufacturer or ""),
                str(port.hwid or ""),
            ]
        ).lower()
        score = 0
        if "arduino" in haystack:
            score += 10
        if "uno" in haystack:
            score += 8
        if "r4" in haystack:
            score += 8
        if "cdc" in haystack or "usb serial" in haystack:
            score += 2
        ranked.append((score, str(port.device)))

    ranked.sort(key=lambda entry: entry[0], reverse=True)
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]

    return None


def _available_port_list() -> str:
    if list_ports is None:
        return "(pyserial unavailable)"
    ports = [str(p.device) for p in list_ports.comports()]
    return ", ".join(ports) if ports else "(no serial ports found)"


def list_serial_ports() -> list[str]:
    if list_ports is None:
        return []
    return [str(p.device) for p in list_ports.comports()]


def _open_serial_no_reset(port: str, baudrate: int, timeout: float):
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baudrate
    ser.timeout = 0.4
    ser.write_timeout = timeout
    ser.rtscts = False
    ser.dsrdtr = False

    # Keep control lines low before opening so boards that auto-reset on DTR/RTS
    # behave more like an already-open Arduino IDE serial session.
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    ser.open()

    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    return ser


def _open_serial_standard(port: str, baudrate: int, timeout: float):
    return serial.Serial(port, baudrate=baudrate, timeout=0.4, write_timeout=timeout)


def _serial_attempts(port: str, baudrate: int, timeout: float):
    return [
        ("standard", lambda: _open_serial_standard(port, baudrate, timeout), 1.8),
        ("no-reset", lambda: _open_serial_no_reset(port, baudrate, timeout), 0.2),
    ]


def _close_serial_session():
    global _SERIAL_SESSION, _SERIAL_SESSION_PORT

    if _SERIAL_SESSION is not None:
        try:
            _SERIAL_SESSION.close()
        except Exception:
            pass

    _SERIAL_SESSION = None
    _SERIAL_SESSION_PORT = None


def _get_serial_session(port: str, baudrate: int, timeout: float):
    global _SERIAL_SESSION, _SERIAL_SESSION_PORT

    if _SERIAL_SESSION is not None:
        try:
            if _SERIAL_SESSION.is_open and _SERIAL_SESSION_PORT == port:
                return _SERIAL_SESSION
        except Exception:
            pass
        _close_serial_session()

    last_exc = None
    for strategy_name, opener, startup_delay in _serial_attempts(port, baudrate, timeout):
        try:
            ser = opener()
            time.sleep(startup_delay)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            _SERIAL_SESSION = ser
            _SERIAL_SESSION_PORT = port
            return ser
        except Exception as exc:
            last_exc = exc
            try:
                ser.close()
            except Exception:
                pass
            if _is_access_denied_error(exc):
                raise IRSendError(_locked_port_help(port)) from exc
            if strategy_name == "standard":
                raise IRSendError(f"Unable to open serial connection on {port}: {exc}") from exc

    if last_exc is not None:
        raise IRSendError(f"Unable to open serial connection on {port}: {last_exc}") from last_exc
    raise IRSendError(f"Unable to open serial connection on {port}")


def send_ir_command(command_hex: str, port: str | None = None, baudrate: int = 115200, timeout: float = 6.0) -> str:
    if serial is None:
        raise IRSendError("pyserial is required. Install with: pip install pyserial")

    pronto = _normalize_pronto(command_hex)
    selected_port = port or os.getenv("IR_REMOTE_PORT") or _auto_detect_arduino_port()
    if not selected_port:
        raise IRSendError(f"No Arduino Compatible Device serial port found. Available ports: {_available_port_list()}")

    last_timeout_details = "no response"

    with _SERIAL_LOCK:
        try:
            ser = _get_serial_session(selected_port, baudrate, timeout)
            try:
                ser.reset_input_buffer()

                def _wait_for_ack() -> bool:
                    nonlocal last_timeout_details
                    deadline = time.time() + timeout
                    last_lines: list[str] = []
                    while time.time() < deadline:
                        line = ser.readline().decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        last_lines.append(line)
                        if len(last_lines) > 8:
                            last_lines.pop(0)

                        upper = line.upper()
                        if upper.startswith("OK"):
                            return True
                        if upper.startswith("ERR"):
                            raise IRSendError(line)

                    last_timeout_details = " | ".join(last_lines) if last_lines else "no response"
                    return False

                payload = f"P {pronto}\r\n".encode("ascii")
                ser.write(payload)
                ser.flush()
                if _wait_for_ack():
                    return selected_port

                # One retry after a quick resync probe.
                ser.write(b"h\r\n")
                ser.flush()
                time.sleep(0.15)
                ser.reset_input_buffer()
                ser.write(payload)
                ser.flush()
                if _wait_for_ack():
                    return selected_port
            except IRSendError:
                raise
            except Exception as exc:
                _close_serial_session()
                if _is_access_denied_error(exc):
                    raise IRSendError(_locked_port_help(selected_port)) from exc
                raise IRSendError(f"Serial send failed on {selected_port}: {exc}") from exc
        except IRSendError:
            raise
        except Exception as exc:
            _close_serial_session()
            if _is_access_denied_error(exc):
                raise IRSendError(_locked_port_help(selected_port)) from exc
            raise IRSendError(f"Unable to open serial connection on {selected_port}: {exc}") from exc

    raise IRSendError(f"Timed out waiting for Arduino Compatible Device ACK on {selected_port}. Last output: {last_timeout_details}")


def test_arduino_connection(port: str | None = None, baudrate: int = 115200, timeout: float = 6.0) -> tuple[str, str]:
    if serial is None:
        raise IRSendError("pyserial is required. Install with: pip install pyserial")

    selected_port = port or os.getenv("IR_REMOTE_PORT") or _auto_detect_arduino_port()
    if not selected_port:
        raise IRSendError(f"No Arduino Compatible Device serial port found. Available ports: {_available_port_list()}")

    last_timeout_details = "no response"

    with _SERIAL_LOCK:
        try:
            ser = _get_serial_session(selected_port, baudrate, timeout)
            try:
                ser.reset_input_buffer()

                ser.write(b"h\r\n")
                ser.flush()

                deadline = time.time() + timeout
                lines: list[str] = []
                while time.time() < deadline:
                    line = ser.readline().decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    lines.append(line)
                    if len(lines) > 12:
                        lines.pop(0)

                    lower = line.lower()
                    if "ir learn + replay" in lower or "commands:" in lower or "resend last captured" in lower:
                        return selected_port, line
                    if lower.startswith("ok") or lower.startswith("err"):
                        # Any parseable response proves COM link is alive.
                        return selected_port, line

                last_timeout_details = " | ".join(lines) if lines else "no response"
            except IRSendError:
                raise
            except Exception as exc:
                _close_serial_session()
                if _is_access_denied_error(exc):
                    raise IRSendError(_locked_port_help(selected_port)) from exc
                raise IRSendError(f"Connection test failed on {selected_port}: {exc}") from exc
        except IRSendError:
            raise
        except Exception as exc:
            _close_serial_session()
            if _is_access_denied_error(exc):
                raise IRSendError(_locked_port_help(selected_port)) from exc
            raise IRSendError(f"Unable to open serial connection on {selected_port}: {exc}") from exc

    raise IRSendError(f"Arduino Compatible Device did not respond as expected on {selected_port}. Last output: {last_timeout_details}")


def learn_ir_command(
    port: str | None = None,
    baudrate: int = 115200,
    timeout: float = 12.0,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str] | None:
    if serial is None:
        raise IRSendError("pyserial is required. Install with: pip install pyserial")

    selected_port = port or os.getenv("IR_REMOTE_PORT") or _auto_detect_arduino_port()
    if not selected_port:
        raise IRSendError(f"No Arduino Compatible Device serial port found. Available ports: {_available_port_list()}")

    last_details = "no response"

    def _is_retryable_learn_err(line: str) -> bool:
        lower = line.lower()
        return (
            "no recent capture" in lower
            or "no captured code" in lower
            or "capture settling" in lower
        )

    with _SERIAL_LOCK:
        try:
            ser = _get_serial_session(selected_port, baudrate, timeout)
            try:
                def _arm_learn(clear_first: bool) -> None:
                    if clear_first:
                        ser.write(b"c\r\n")
                        ser.flush()
                    ser.write(b"l\r\n")
                    ser.flush()

                # Start from a clean state and arm fresh learning.
                ser.reset_input_buffer()
                _arm_learn(clear_first=True)
                _arm_wait_deadline = time.time() + 1.2
                while time.time() < _arm_wait_deadline:
                    _cl = ser.readline().decode("utf-8", errors="replace").strip()
                    if _cl.upper().startswith("OK LEARN ARMED"):
                        break

                deadline = time.time() + timeout
                lines: list[str] = []
                retryable_err_count = 0
                next_get_at = 0.0
                next_rearm_at = time.time() + 1.8

                while time.time() < deadline:
                    if cancel_event is not None and cancel_event.is_set():
                        try:
                            ser.write(b"c\r\n")
                            ser.flush()
                        except Exception:
                            pass
                        return None

                    now = time.time()
                    if now >= next_get_at:
                        ser.write(b"g\r\n")
                        ser.flush()
                        next_get_at = now + 0.18

                    attempt_deadline = min(deadline, time.time() + 0.5)
                    got_retryable_err = False

                    while time.time() < attempt_deadline:
                        if cancel_event is not None and cancel_event.is_set():
                            try:
                                ser.write(b"c\r\n")
                                ser.flush()
                            except Exception:
                                pass
                            return None

                        line = ser.readline().decode("utf-8", errors="replace").strip()
                        if not line:
                            continue

                        lines.append(line)
                        if len(lines) > 16:
                            lines.pop(0)

                        upper = line.upper()
                        if upper.startswith("PRONTO "):
                            pronto = _normalize_pronto(line[7:])
                            return selected_port, pronto

                        if "SAVED. PROTOCOL=" in upper:
                            # Firmware confirmed a fresh capture; request it immediately.
                            ser.write(b"g\r\n")
                            ser.flush()
                            next_get_at = time.time() + 0.08

                        if upper.startswith("ERR"):
                            if _is_retryable_learn_err(line):
                                got_retryable_err = True
                                retryable_err_count += 1
                                break
                            raise IRSendError(line)

                    if got_retryable_err:
                        # Re-arm periodically so stale/aborted capture states recover automatically.
                        now = time.time()
                        if now >= next_rearm_at or retryable_err_count >= 8:
                            _arm_learn(clear_first=True)
                            retryable_err_count = 0
                            next_rearm_at = now + 1.8
                        time.sleep(0.1)
                        continue

                last_details = " | ".join(lines) if lines else "no response"
            except IRSendError:
                raise
            except Exception as exc:
                _close_serial_session()
                if _is_access_denied_error(exc):
                    raise IRSendError(_locked_port_help(selected_port)) from exc
                raise IRSendError(f"Learn request failed on {selected_port}: {exc}") from exc
        except IRSendError:
            raise
        except Exception as exc:
            _close_serial_session()
            if _is_access_denied_error(exc):
                raise IRSendError(_locked_port_help(selected_port)) from exc
            raise IRSendError(f"Unable to open serial connection on {selected_port}: {exc}") from exc

    raise IRSendError(
        f"Timed out waiting for learned command on {selected_port}. "
        f"Point the source remote at the receiver and press/hold once during capture. "
        f"Last output: {last_details}"
    )
