# IR Remote Sender

A Windows desktop app for learning, storing, and sending IR commands through an Arduino-compatible device connected over USB serial.

## Features

- Learn IR codes from a remote and store them per device action
- Send saved commands from a desktop UI
- Support for custom device profiles and built-in templates
- Global hotkeys for quick actions, even while the app is minimized
- System tray support with restore / quit actions
- Overlay tuning for image-based remotes
- Packaged Windows build via PyInstaller

## Requirements

- Windows
- Python 3.11+
- An Arduino-compatible device running the included IR Learn + Replay firmware
- IR receiver and IR LED hardware wired to the Arduino

## Running From Source

1. Open a terminal in the project folder.
2. Activate the virtual environment if needed.
3. Run the app entry point:

```powershell
python Remote.py
```

## Building the App

The project includes a PyInstaller spec file for creating a distributable Windows app.

```powershell
python -m PyInstaller --noconfirm Remote.spec
```

The packaged app will be created in the `dist` output folder defined by your build command or spec file.

## Versioning

The app version starts at `v2.0` and increments with each new build.

- The current build version is stored in `conf/app_version.txt`
- The version is shown in the About dialog inside the app
- Rebuilding with `Remote.spec` bumps the version forward automatically

## Project Layout

- `Remote.py` - app entry point
- `remote_app.py` - main UI and application logic
- `ir_sender.py` - serial communication and IR send/learn helpers
- `remote_config.py` - config persistence
- `remote_devices.py` - built-in device definitions
- `sketch/IR_remote/IR_remote.ino` - Arduino firmware
- `images/` - UI assets and device images
- `conf/` - application settings and generated version file

## Notes

- The app is designed to work with Arduino-compatible boards, not only a specific model.
- If you want the latest packaged build, use the app from the GitHub repository release or rebuild it locally with PyInstaller.
