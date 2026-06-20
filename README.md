# IR Remote Sender

[![Platform](https://img.shields.io/badge/platform-Windows-blue)](https://github.com/fluxnz/IR_RemoteSender)
[![Python](https://img.shields.io/badge/python-3.11%2B-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-See%20repo-lightgrey)](https://github.com/fluxnz/IR_RemoteSender)

A Windows desktop app for learning, storing, and sending IR commands through an Arduino-compatible device over USB serial.

## What It Does

- Learn IR codes from a remote and save them per action
- Send stored commands from a simple desktop interface
- Manage built-in and custom device profiles
- Use global hotkeys even while the app is minimized
- Keep the app in the system tray with quick restore/quit actions
- Tune image-based overlays for supported remotes

## Quick Start

1. Install Python 3.11 or later.
2. Connect an Arduino-compatible board running the included IR Learn + Replay firmware.
3. Wire the IR receiver and IR LED hardware to the board.
4. Run the app:

```powershell
python Remote.py
```

## Build For Windows

Use PyInstaller with the provided spec file:

```powershell
python -m PyInstaller --noconfirm Remote.spec
```

The packaged app is created in the build output folder defined by the spec or your build command.

## Versioning

The app version starts at `v2.0` and increments with each new build.

- The current version is stored in `conf/app_version.txt`
- The version is shown in the About dialog
- Rebuilding with `Remote.spec` bumps the version automatically

## Project Structure

- `Remote.py` - app entry point
- `remote_app.py` - main UI and application logic
- `ir_sender.py` - serial communication and IR send/learn helpers
- `remote_config.py` - config persistence
- `remote_devices.py` - built-in device definitions
- `sketch/IR_remote/IR_remote.ino` - Arduino firmware
- `images/` - UI assets and device artwork
- `conf/` - application settings and generated version file

## Notes

- The app is designed for Arduino-compatible hardware, not only a specific model.
- This repository is ready to build locally or package into a Windows executable.
