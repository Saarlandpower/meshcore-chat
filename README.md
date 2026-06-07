# meshcore-chat

A web-based chat client for [MeshCore](https://meshcore.co.uk) LoRa mesh networks.  
Connects to a MeshCore Companion Radio via USB Serial and exposes a browser-based UI with:

- 💬 **Direct messages** and **channel messages**
- 📋 **Contact list** with node types (client / repeater / room server)
- 🗺 **Live map** (Leaflet + OpenStreetMap) showing nodes that broadcast GPS in their advert
- 🔄 **Auto-reconnect** to the radio
- ⚙️ **Configurable** via environment variables

Built for use on a Raspberry Pi running 24/7, so you can connect from any browser on your network.

---

## Hardware

- Any MeshCore-supported device flashed with **Companion Radio (USB Serial)** firmware  
  e.g. Seeed XIAO ESP32S3 + Wio-SX1262, Heltec V3, LILYGO T-Beam, ...
- Raspberry Pi (or any Linux box) as host

## Requirements

- Python 3.10+
- See `requirements.txt`

```bash
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

Or set environment variables directly:

| Variable         | Default                                      | Description                  |
|------------------|----------------------------------------------|------------------------------|
| `MC_SERIAL_PORT` | `/dev/serial/by-id/usb-Espressif_...`        | Serial port of companion radio |
| `MC_BAUD_RATE`   | `115200`                                     | Baud rate                    |
| `MC_HTTP_HOST`   | `0.0.0.0`                                    | HTTP bind address            |
| `MC_HTTP_PORT`   | `5003`                                       | HTTP port                    |
| `MC_NUM_CHANNELS`| `8`                                          | Number of channels to query  |
| `MC_SECRET_KEY`  | `meshcore-chat-secret`                       | Flask secret key             |

## Running

```bash
python server.py
```

Then open `http://<your-pi-ip>:5003` in your browser.

## systemd service

```bash
sudo cp meshcore-chat.service /etc/systemd/system/
sudo systemctl enable --now meshcore-chat
```

Edit the service file to set your serial port if needed.

## License

MIT
