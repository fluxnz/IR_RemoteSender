import configparser
from pathlib import Path


def _is_true(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class RemoteConfigManager:
    def __init__(self, config_path: Path):
        self.path = Path(config_path)
        # Use '=' as the only delimiter so keys like 'command:Power' remain intact.
        # strict=False prevents startup failure if older files contain duplicate options.
        self.config = configparser.ConfigParser(delimiters=("="), strict=False)
        self.config.optionxform = str
        self.load()

    def load(self):
        if self.path.exists():
            self.config.read(self.path, encoding="utf-8")

    def save(self):
        with self.path.open("w", encoding="utf-8") as handle:
            self.config.write(handle)

    def get_skin(self) -> str:
        return self.config.get("UI", "skin", fallback="Classic")

    def set_skin(self, skin: str):
        if "UI" not in self.config:
            self.config["UI"] = {}
        self.config["UI"]["skin"] = skin

    def device_settings_section(self, device_name: str) -> str:
        return f"DeviceSettings:{device_name}"

    def get_device_skin(self, device_name: str) -> str:
        section = self.device_settings_section(device_name)
        return self.config.get(section, "skin", fallback="Classic")

    def set_device_skin(self, device_name: str, skin: str):
        section = self.device_settings_section(device_name)
        if section not in self.config:
            self.config[section] = {}
        self.config[section]["skin"] = skin

    def get_show_overlays(self) -> bool:
        return self.config.getboolean("UI", "show_overlays", fallback=False)

    def set_show_overlays(self, value: bool):
        if "UI" not in self.config:
            self.config["UI"] = {}
        self.config["UI"]["show_overlays"] = str(bool(value))

    def get_serial_port(self) -> str:
        return self.config.get("Connection", "serial_port", fallback="").strip()

    def set_serial_port(self, port: str):
        if "Connection" not in self.config:
            self.config["Connection"] = {}
        self.config["Connection"]["serial_port"] = (port or "").strip()

    def get_enabled_devices(self) -> list[str]:
        if "Devices" not in self.config:
            return []
        return [name for name, flag in self.config["Devices"].items() if _is_true(flag)]

    def set_enabled_devices(self, devices: list[str]):
        self.config["Devices"] = {name: "true" for name in devices}

    def hotkey_section(self, device_name: str) -> str:
        return f"Hotkeys:{device_name}"

    def hotkey_toggle_section(self, device_name: str) -> str:
        return f"HotkeyToggle:{device_name}"

    def get_hotkey(self, device_name: str, action_name: str) -> str:
        section = self.hotkey_section(device_name)
        if section in self.config:
            return self.config.get(section, action_name, fallback="")
        # backward compatibility with old per-device section layout
        if device_name in self.config:
            return self.config.get(device_name, action_name, fallback="")
        return ""

    def set_hotkey(self, device_name: str, action_name: str, value: str):
        section = self.hotkey_section(device_name)
        if section not in self.config:
            self.config[section] = {}
        self.config[section][action_name] = value

    def get_hotkey_toggle_partner(self, device_name: str, action_name: str) -> str:
        section = self.hotkey_toggle_section(device_name)
        return self.config.get(section, action_name, fallback="").strip()

    def set_hotkey_toggle_partner(self, device_name: str, action_name: str, partner_action_name: str):
        section = self.hotkey_toggle_section(device_name)
        if section not in self.config:
            self.config[section] = {}
        self.config[section][action_name] = (partner_action_name or "").strip()

    def clear_hotkeys_for_devices(self, devices: list[str], actions_by_device: dict[str, list[str]]):
        for device_name in devices:
            section = self.hotkey_section(device_name)
            toggle_section = self.hotkey_toggle_section(device_name)
            if section not in self.config:
                self.config[section] = {}
            if toggle_section not in self.config:
                self.config[toggle_section] = {}
            for action_name in actions_by_device.get(device_name, []):
                self.config[section][action_name] = ""
                self.config[toggle_section][action_name] = ""

    def overlay_section(self, device_name: str) -> str:
        return f"Overlay:{device_name}"

    def get_overlay_regions(self, device_name: str) -> dict[str, tuple[float, float, float, float]]:
        section = self.overlay_section(device_name)
        regions: dict[str, tuple[float, float, float, float]] = {}
        if section not in self.config:
            return regions
        for key in list(self.config[section]):
            try:
                vals = [float(x) for x in self.config[section][key].split(",")]
                if len(vals) == 4:
                    regions[key] = (vals[0], vals[1], vals[2], vals[3])
            except Exception:
                continue
        return regions

    def set_overlay_region(self, device_name: str, action_name: str, region: tuple[float, float, float, float]):
        section = self.overlay_section(device_name)
        if section not in self.config:
            self.config[section] = {}

        old_keys = [k for k in self.config[section] if k.lower() == action_name.lower() and k != action_name]
        for key in old_keys:
            del self.config[section][key]

        x0, y0, x1, y1 = region
        self.config[section][action_name] = f"{x0},{y0},{x1},{y1}"

    def clear_overlay_regions(self, device_name: str):
        section = self.overlay_section(device_name)
        if section in self.config:
            del self.config[section]

    def custom_device_section(self, device_name: str) -> str:
        return f"CustomDevice:{device_name}"

    def get_custom_devices(self) -> dict[str, dict[str, object]]:
        devices: dict[str, dict[str, object]] = {}
        prefix = "CustomDevice:"
        for section in self.config.sections():
            if not section.startswith(prefix):
                continue
            device_name = section[len(prefix) :]
            image_file = self.config.get(section, "image_file", fallback="").strip() or None
            commands: dict[str, str] = {}
            for key, value in self.config[section].items():
                if key.startswith("command:"):
                    action_name = key.split(":", 1)[1].strip()
                    command_hex = (value or "").strip()
                    if action_name and command_hex:
                        commands[action_name] = command_hex
            if commands:
                devices[device_name] = {
                    "image_file": image_file,
                    "commands": commands,
                }
        return devices

    def set_custom_device(self, device_name: str, commands: dict[str, str], image_file: str | None = None):
        section = self.custom_device_section(device_name)
        self.config[section] = {}
        self.config[section]["image_file"] = (image_file or "").strip()
        for action_name, command_hex in commands.items():
            if action_name and command_hex:
                self.config[section][f"command:{action_name}"] = command_hex

    def remove_custom_device(self, device_name: str):
        section = self.custom_device_section(device_name)
        if section in self.config:
            del self.config[section]

