# DIY USB IR Blaster & Receiver

A simple USB-connected infrared (IR) blaster and receiver designed for controlling home audio equipment, learning remote control codes, and integrating legacy IR devices into automation systems.

This project was built primarily to control vintage Rotel audio equipment, including amplifiers and tuners, while also providing IR receive capability for learning and troubleshooting remote control commands.

---

## Features

- IR transmit (blaster) functionality
- IR receive functionality
- USB powered
- Status LED indication
- Compatible with standard 38kHz IR remote controls
- Suitable for home automation and media equipment control
- Compact and low-cost DIY design

---

## Hardware Used

| Component | Description |
|------------|-------------|
| IR LED | High-power infrared transmitter LED |
| IR Receiver Module | 38kHz demodulating IR receiver |
| Green 5mm LED (80mcd Diffused) | Status indicator |
| 100nF Ceramic Capacitor | Receiver power supply decoupling |
| Microcontroller | USB-capable controller |
| Current Limiting Resistors | LED protection |
| USB Connection | Power and communications |

---

## Wiring

### IR Receiver

| Receiver Pin | Connection |
|--------------|------------|
| VCC | +5V |
| GND | Ground |
| OUT | Microcontroller IR Input |

### Receiver Decoupling Capacitor

Install a **100nF ceramic capacitor** directly across:

- VCC
- GND

This helps suppress electrical noise and improves reception reliability.

> Ceramic capacitors are non-polarized, so orientation does not matter.

### IR Transmitter LED

| IR LED Pin | Connection |
|------------|------------|
| Anode (+) | Transmit output via current limiting resistor |
| Cathode (-) | Ground |

### Status LED

| LED Pin | Connection |
|----------|------------|
| Anode (+) | GPIO via resistor |
| Cathode (-) | Ground |

The status LED can be used to indicate:

- Power On
- USB Connected
- IR Transmission Activity
- Learning Mode

---

## Operation

### Receiving IR Commands

1. Point a remote control at the receiver.
2. Press a button.
3. The microcontroller decodes the incoming IR data.
4. Codes can be logged or stored for later use.

### Transmitting IR Commands

1. Select a stored IR code.
2. The microcontroller modulates the IR LED at 38kHz.
3. The target device receives the command exactly as if it came from the original remote.

---

## Example Applications

- Rotel amplifier control
- Rotel tuner control
- Home Assistant integration
- Media centre automation
- Learning unknown IR remote codes
- Legacy equipment control

---

## Future Improvements

- Higher-power IR LED for increased range
- Multiple IR emitters
- OLED status display
- Web-based configuration
- MQTT support
- Home Assistant auto-discovery
- IR code database storage

---

## Notes

The current design prioritises simplicity and reliability while remaining easy to assemble using readily available components.

If greater transmission distance is required, the IR LED can be upgraded to a higher-output model without significant circuit changes, provided current limits remain within safe operating specifications.

---

## License

MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files to deal in the Software
without restriction.

## Uploading a new app to this repository (VS Code)

1. Copy your app files into this project folder.
2. Open the folder in VS Code.
3. Open **Source Control** in VS Code and review changed files.
4. Stage files, add a commit message, and commit.
5. Push the commit to GitHub from VS Code.
