# AudioViz Project Description

## Overview

AudioViz is a networked music visualization project that connects a Windows PC, Spotify playback data, MQTT communication, a Raspberry Pi, a WS2812B LED strip, and a 64x64 HUB75 RGB LED matrix. The goal of the project is to create a physical display that reacts to the music currently playing on the PC while also showing Spotify album cover artwork on the LED matrix.

The system is split into two main software parts:

- **PC node:** captures the computer's audio output, analyzes the sound, reads Spotify metadata, and publishes the current state over MQTT.
- **Raspberry Pi node:** receives the MQTT messages, converts the audio-band information into LED strip effects, downloads Spotify cover art, and displays the artwork on the LED matrix.

This separation keeps the more desktop-specific tasks, such as audio loopback capture and Spotify OAuth login, on the PC. The Raspberry Pi focuses on real-time hardware output and LED control.

## Main Purpose

The project is designed to make music playback visible in two complementary ways:

- The **LED strip** reacts to the energy of the music. Different frequency ranges trigger different visual effects.
- The **LED matrix** shows the album cover of the currently playing Spotify track.

Together, these outputs create a visual representation of both the sound and the song identity. The LED strip provides motion and beat response, while the matrix provides recognizable track artwork.

## High-Level Architecture

The project follows a sender/receiver architecture.

On the PC, the program continuously listens to the system audio output. It converts small blocks of audio into frequency information and reduces that information into three basic trigger values: low, mid, and high. At the same time, another part of the program polls Spotify for the currently playing track and album cover URL.

The PC then combines this information into one JSON payload and publishes it using MQTT.

On the Raspberry Pi, a separate program subscribes to the same MQTT topic. When a message arrives, it reads the band triggers and passes them to the LED strip controller. It also reads the Spotify section of the message and passes the album cover URL to the matrix controller. The Pi then updates the hardware outputs.

The current MQTT topic used by the project is:

```text
audio/bands/matvitt
```

The PC code allows the MQTT broker address, port, and topic to be configured through environment variables. By default, the PC publisher uses:

```text
MQTT_BROKER=192.168.0.165
MQTT_PORT=1883
MQTT_TOPIC=audio/bands/matvitt
```

The Raspberry Pi receiver currently connects to:

```text
127.0.0.1:1883
```

This means the intended setup is that the Pi runs a local MQTT broker, such as Mosquitto, and the PC publishes messages to the Pi over the local network.

## MQTT Communication

MQTT is used as the communication layer between the PC and the Raspberry Pi. It is well suited for this project because it is lightweight, fast enough for frequent updates, and allows the sender and receiver to be separated cleanly.

The PC acts as an MQTT publisher. It sends a JSON message roughly 30 times per second. The message includes the current state of the three audio frequency bands and the latest Spotify metadata.

An example message looks like this:

```json
{
  "low": false,
  "mid": true,
  "high": false,
  "spotify": {
    "track_id": "spotify_track_id",
    "track_name": "Song Title",
    "artist_name": "Artist Name",
    "album_cover_url": "https://i.scdn.co/image/...",
    "progress_ms": 42000,
    "duration_ms": 180000,
    "is_playing": true
  }
}
```

The Raspberry Pi acts as an MQTT subscriber. It subscribes to the same topic and reacts whenever a new message is received. The band values are used immediately for the LED strip. The Spotify data is used by the matrix display, mainly to detect when a new album cover URL appears.

The project currently uses MQTT without guaranteed delivery features, using QoS 0. This is appropriate for live visual data because old frames are not important. If one message is missed, the next message will replace it almost immediately.

## PC Node

The PC node is located in the `pc` folder. Its main entry point is:

```text
pc/main.py
```

The PC node has three major responsibilities.

First, it captures system audio. The code searches for a WASAPI loopback or Stereo Mix input device. If it finds one, it uses that device to capture the audio currently playing on the computer. If it cannot find a dedicated loopback device, it falls back to the default input.

Second, it analyzes the captured audio. The audio is converted from stereo to mono, then processed by the band analyzer. The analyzer uses FFT-based frequency analysis and adaptive thresholds to decide whether the low, mid, or high band is currently active. These bands are intentionally simple because they are meant to drive visual effects rather than perform detailed audio measurement.

Third, it polls Spotify. The Spotify poller uses the Spotify Web API to read the currently playing track. It collects the track ID, track name, artist name, album cover URL, playback progress, total duration, and play/pause state. This information is cached in a thread-safe way so the MQTT publishing loop can include it in each outgoing message.

The PC node combines the audio trigger state and the Spotify state into one JSON payload and publishes it over MQTT at a target rate of 30 frames per second.

## Raspberry Pi Node

The Raspberry Pi node is located in the `rpi` folder. Its main entry point is:

```text
rpi/main.py
```

The Pi node initializes two hardware controller modules:

- `rpi/led_strip.py` controls the WS2812B LED strip.
- `rpi/led_matrix.py` controls the HUB75 RGB LED matrix.

The Pi also loads its hardware settings from:

```text
rpi/config.json
```

The configuration file stores values such as the number of LEDs, the GPIO pin used for the LED strip, the LED strip brightness, the matrix size, the matrix hardware mapping, and matrix timing parameters.

The Pi subscribes to the MQTT topic and handles incoming messages. For each message, it updates the LED strip trigger state and sends Spotify metadata to the matrix module. The LED strip is rendered continuously at about 30 frames per second. The matrix is updated only when needed, such as when a new album cover URL appears.

The Pi code must be run with `sudo` because the LED libraries need low-level access to GPIO, PWM, and DMA hardware.

## LED Strip Behavior

The WS2812B LED strip is used as the real-time music-reactive part of the project. It currently contains 30 LEDs, configured through `num_leds` in `rpi/config.json`.

The strip uses three effect layers:

- **Low frequencies:** trigger a red pulse expanding from the center of the strip.
- **Mid frequencies:** trigger a green wave moving across the strip.
- **High frequencies:** trigger short white sparkles.

The effects are combined additively into one frame. This means multiple effects can be visible at the same time. For example, a low-frequency pulse and high-frequency sparkles can overlap without replacing each other.

The strip is driven using GPIO 19 with PWM channel 1. This is an important design choice because GPIO 18 is needed by the LED matrix hardware modification.

## LED Matrix Behavior

The HUB75 RGB LED matrix is used to show Spotify album artwork. The current configuration is for a 64x64 matrix.

When the Pi receives Spotify metadata over MQTT, the matrix module checks the album cover URL. If the URL is the same as the currently displayed one, it does nothing. If the URL has changed, it downloads the new image, converts it to RGB, resizes it to 64x64 pixels, and sends it to the matrix.

The matrix code also includes fade behavior:

- New album artwork fades in when it is displayed.
- The artwork can fade out near the end of the track, based on the remaining playback time.

The matrix module uses a browser-like HTTP user agent when downloading images. This helps avoid problems with Spotify CDN requests being blocked or rejected. It also forces the system certificate bundle path so HTTPS still works when the script is run with `sudo`.

## Hardware Components

The project uses the following hardware components:

- **Windows PC:** captures system audio, communicates with the Spotify API, and publishes MQTT messages.
- **Raspberry Pi:** receives MQTT messages and controls the LED hardware.
- **64x64 HUB75 RGB LED matrix:** displays Spotify album cover artwork.
- **Adafruit RGB Matrix Bonnet or compatible HUB75 matrix adapter:** connects the Raspberry Pi GPIO header to the HUB75 LED matrix.
- **WS2812B addressable RGB LED strip:** displays real-time audio-reactive light effects.
- **5 V power supply:** powers the LED matrix and LED strip. The required current depends on the exact matrix and strip brightness.
- **Network connection:** allows the PC to publish MQTT messages to the Raspberry Pi.
- **Wiring for GPIO, power, and ground:** connects the Pi, matrix bonnet, matrix panel, and LED strip.

The exact current rating of the power supply should be chosen based on the number of LEDs and the matrix requirements. A 64x64 RGB matrix can draw significant current at high brightness, so power capacity and common grounding are important.

## Hardware Modifications

Several hardware modifications were made so the LED matrix and LED strip can work reliably at the same time.

### GPIO 4 to GPIO 18 Solder Bridge

The GPIO 4 to GPIO 18 solder modification connects the matrix bonnet's output-enable timing to the Raspberry Pi hardware PWM signal on GPIO 18.

This is done to use the matrix library's higher-quality hardware PWM mode. Hardware PWM gives the RGB matrix more stable timing and helps reduce flicker, especially on larger panels such as a 64x64 matrix.

Because this modification uses the Pi's PWM0 hardware, it affects other functions that also depend on that PWM channel. One important consequence is that the Raspberry Pi onboard audio should be disabled, because onboard audio can use the same PWM hardware. In the system configuration, this is typically handled by setting:

```text
dtparam=audio=off
```

In the project configuration, this hardware change is reflected by:

```json
"matrix_hardware_mapping": "adafruit-hat-pwm",
"matrix_disable_hardware_pulsing": false
```

This tells the matrix library to use the Adafruit PWM hardware mapping and to keep hardware pulsing enabled.

### E-Jumper Solder Modification

The E-jumper modification is needed because a 64x64 HUB75 matrix requires an additional address line compared with smaller panels.

Many HUB75 panels use address lines labeled A, B, C, and D. A 64x64 panel commonly also needs an E address line so the controller can select all row groups correctly. Without the E line, the panel may show duplicated rows, incorrect scanning, or only part of the image.

Soldering the E-jumper on the bonnet routes the required E address signal from the Raspberry Pi/bonnet to the matrix connector. This allows the 64x64 panel to be addressed correctly.

### Moving the LED Strip to GPIO 19

WS2812B LED strips are often driven from GPIO 18 because it supports PWM. In this project, GPIO 18 is already used by the matrix PWM modification. To avoid a conflict, the LED strip was moved to GPIO 19, which can use PWM channel 1.

The project configuration reflects this with:

```json
"ws281_gpio": 19,
"ws281_channel": 1
```

This allows the matrix to use GPIO 18/PWM0 while the LED strip uses GPIO 19/PWM1.

The Raspberry Pi system configuration should also enable the two-channel PWM overlay:

```text
dtoverlay=pwm-2chan
```

This makes both PWM channels available for the intended hardware setup.

## Key Software Libraries

### `sounddevice`

Used on the PC to capture live audio from an input or loopback device. It provides access to PortAudio and makes it possible to receive audio blocks in a callback.

### `numpy`

Used for numerical processing. On the PC it is used for FFT-based audio analysis. On the Raspberry Pi it is used for LED frame composition and image array handling.

### `paho-mqtt`

Used on both the PC and Raspberry Pi for MQTT communication. The PC uses it as a publisher, while the Raspberry Pi uses it as a subscriber.

### `spotipy`

Used on the PC to communicate with the Spotify Web API. It handles OAuth login, token caching, token refresh, and requests for the currently playing track.

### `python-dotenv`

Used on the PC to load environment variables from a `.env` file. This is useful for Spotify credentials and MQTT configuration.

### `requests`

Used on the Raspberry Pi to download Spotify album cover images over HTTPS.

### `Pillow`

Used on the Raspberry Pi to open, convert, resize, and prepare album cover images before sending them to the LED matrix.

### `rpi_ws281x`

Used on the Raspberry Pi to drive the WS2812B LED strip. It provides low-level timing control using PWM/DMA so the addressable LEDs receive accurate data.

### `rgbmatrix`

Used on the Raspberry Pi to control the HUB75 RGB LED matrix. This is the Python binding for the `rpi-rgb-led-matrix` library, which handles the timing-sensitive matrix refresh logic.

### Mosquitto or Another MQTT Broker

The project expects an MQTT broker to be available. The current receiver code connects to a broker running locally on the Raspberry Pi. Mosquitto is a common choice for this role.

## Configuration Summary

The most important hardware-related configuration values are stored in `rpi/config.json`.

Important LED strip settings include:

- `num_leds`: number of LEDs in the strip.
- `ws281_gpio`: GPIO pin used for LED strip data, currently GPIO 19.
- `ws281_channel`: PWM channel used by the strip, currently channel 1.
- `ws281_brightness`: strip brightness.
- `pulse_decay_ms`, `wave_speed_ms_per_led`, and `sparkle_density`: effect tuning values.

Important matrix settings include:

- `matrix_rows` and `matrix_cols`: matrix resolution, currently 64x64.
- `matrix_brightness`: matrix brightness.
- `matrix_hardware_mapping`: currently `adafruit-hat-pwm` because of the GPIO 4 to GPIO 18 solder modification.
- `matrix_gpio_slowdown`: timing slowdown value, currently 4.
- `matrix_pwm_bits`: matrix PWM color depth, currently 11.
- `matrix_disable_hardware_pulsing`: currently false so hardware PWM timing is used.

## Current Implementation Notes

The current logic is intentionally modular. The PC node does not directly control hardware, and the Raspberry Pi does not perform Spotify authentication or desktop audio capture. MQTT acts as the boundary between the two.

The detailed visualization logic may still change. The current implementation should therefore be treated as the present version of the project rather than a final design. The important long-term concept is that the PC converts music and Spotify state into simple network messages, and the Pi converts those messages into physical LED output.

There are also a few practical workarounds in the current code:

- HTTPS certificate paths are set explicitly on the Pi so `requests` works correctly when Python is run with `sudo`.
- The matrix downloader uses a browser-like user agent to improve compatibility with Spotify image URLs.
- The Pi configuration is hot-reloaded when MQTT messages arrive, allowing some configuration changes without restarting the program.
- Both the LED strip and matrix modules can run in mock mode if their hardware libraries are missing, which helps with development away from the Raspberry Pi.

## Open Questions for Final Documentation

The following details should be confirmed before this description is turned into full formal documentation:

- Exact Raspberry Pi model.
- Exact LED matrix model, scan rate, and manufacturer if known.
- Exact Adafruit bonnet or HAT model.
- Exact WS2812B strip length, density, and power wiring.
- Power supply voltage and current rating.
- Whether Mosquitto is definitely the MQTT broker used on the Pi.
- Final IP address or hostname strategy for the Pi.
- Whether the project should document a single startup command or a systemd service for automatic startup.
- Whether the soldered E-jumper was connected to a specific labeled pad, such as `8`, depending on the bonnet version.

