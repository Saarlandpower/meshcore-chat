#!/usr/bin/env python3
"""
meshcore-chat — Web-based MeshCore Companion Radio Client
https://github.com/Saarlandpower/meshcore-chat

Bridges a MeshCore Companion Radio (USB Serial) to a WebSocket-based web UI.
Supports: Direct Messages, Channels, Contact List, GPS Map.
"""

import asyncio
import os
import threading
import logging
import time
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from meshcore import MeshCore
from meshcore.events import EventType

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("meshcore-chat")

# ── Config (override via environment variables) ───────────────────────────────
SERIAL_PORT  = os.environ.get("MC_SERIAL_PORT",
    "/dev/serial/by-id/usb-YOUR_DEVICE_HERE")
BAUD_RATE    = int(os.environ.get("MC_BAUD_RATE", "115200"))
HTTP_HOST    = os.environ.get("MC_HTTP_HOST", "0.0.0.0")
HTTP_PORT    = int(os.environ.get("MC_HTTP_PORT", "5003"))
NUM_CHANNELS = int(os.environ.get("MC_NUM_CHANNELS", "8"))  # Set MC_NUM_CHANNELS=2 if you only have 2 channels

# ── Flask / SocketIO ──────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("MC_SECRET_KEY", "meshcore-chat-secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Global state ──────────────────────────────────────────────────────────────
mc        = None
mc_loop   = None
self_info = {}
contacts  = {}   # key_hex -> contact dict
channels  = {}   # idx -> channel dict
connected = False


def ts():
    return int(time.time())


def contact_to_dict(key, c):
    """Normalize a meshcore contact dict for the frontend."""
    lat = c.get("adv_lat", 0.0) or 0.0
    lon = c.get("adv_lon", 0.0) or 0.0
    ctype = c.get("type", 0)
    type_label = {0: "client", 1: "client", 2: "repeater", 3: "room"}.get(ctype, "unknown")
    last_advert = c.get("last_advert", 0) or 0
    flags = c.get("flags", 0) or 0
    return {
        "key":              key[:16],
        "full_key":         key,
        "name":             c.get("adv_name") or c.get("name") or key[:8],
        "type":             type_label,
        "lat":              lat,
        "lon":              lon,
        "has_gps":          (lat != 0.0 or lon != 0.0),
        "last_advert":      last_advert,
        "last_seen":        last_advert,
        "flags":            flags,
        "out_path_len":     c.get("out_path_len", -1),
        "out_path":         c.get("out_path", ""),
        "out_path_hash_mode": c.get("out_path_hash_mode", -1),
        # flag bits: bit0=battery, bit1=moving, bit3=store_fwd
        "flag_battery":     bool(flags & 0x01),
        "flag_store_fwd":   bool(flags & 0x08),
    }


# ── MeshCore background thread ────────────────────────────────────────────────

def run_meshcore():
    global mc_loop
    mc_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(mc_loop)
    mc_loop.run_until_complete(_meshcore_loop())


async def _meshcore_loop():
    global mc, self_info, contacts, channels, connected

    while True:
        try:
            logger.info(f"Connecting to {SERIAL_PORT} @ {BAUD_RATE} baud...")
            mc = await MeshCore.create_serial(SERIAL_PORT, BAUD_RATE, auto_reconnect=True, default_timeout=10.0)
            connected = True
            socketio.emit("status", {"connected": True, "msg": f"Verbunden · {SERIAL_PORT}"})
            logger.info("MeshCore connected")

            # ── Event subscriptions ────────────────────────────────────────

            def on_self_info(event):
                global self_info
                info = event.payload if hasattr(event, "payload") and event.payload is not None else {}
                if isinstance(info, dict):
                    self_info = {
                        "name":    info.get("name", "Unknown"),
                        "key":     info.get("public_key", "")[:16],
                        "lat":     info.get("adv_lat", 0.0),
                        "lon":     info.get("adv_lon", 0.0),
                        "freq":    info.get("radio_freq", 0),
                        "sf":      info.get("radio_sf", 0),
                        "bw":      info.get("radio_bw", 0),
                        "cr":      info.get("radio_cr", 0),
                        "tx_power": info.get("tx_power", 0),
                    }
                    socketio.emit("self_info", self_info)

            def on_contacts(event):
                asyncio.ensure_future(_push_contacts())

            def on_new_contact(event):
                asyncio.ensure_future(_push_contacts())

            def on_advert(event):
                # Update last_seen on advertisement
                asyncio.ensure_future(_push_contacts())

            def on_direct_msg(event):
                msg = event.payload if hasattr(event, "payload") and event.payload is not None else {}
                if not isinstance(msg, dict):
                    return
                sender_key = msg.get("pubkey", "") or msg.get("pub_key", "") or ""
                if isinstance(sender_key, bytes):
                    sender_key = sender_key.hex()
                sender_name = ""
                if sender_key and sender_key in contacts:
                    sender_name = contacts[sender_key].get("name", sender_key[:8])
                elif sender_key:
                    sender_name = sender_key[:8]
                text = msg.get("text", "") or msg.get("msg", "")
                socketio.emit("message", {
                    "type":      "direct",
                    "from":      sender_name or "Unknown",
                    "from_key":  sender_key[:16] if sender_key else "",
                    "text":      str(text),
                    "ts":        ts(),
                    "self":      False,
                })

            def on_channel_msg(event):
                msg = event.payload if hasattr(event, "payload") and event.payload is not None else {}
                if not isinstance(msg, dict):
                    return
                raw_text = msg.get("text", "") or msg.get("msg", "")
                ch_idx   = msg.get("channel_idx", 0)
                ch_name  = channels.get(ch_idx, {}).get("name", f"Ch{ch_idx}")
                # sender_name may be separate or embedded as "Name: message"
                sender   = msg.get("sender_name") or msg.get("sender") or ""
                text     = raw_text
                if not sender and ": " in raw_text:
                    parts  = raw_text.split(": ", 1)
                    sender = parts[0]
                    text   = parts[1]
                socketio.emit("message", {
                    "type":     "channel",
                    "from":     str(sender) or "?",
                    "channel":  ch_idx,
                    "ch_name":  ch_name,
                    "text":     str(text),
                    "ts":       ts(),
                    "self":     False,
                })

            def on_disconnect(event):
                global connected
                connected = False
                socketio.emit("status", {"connected": False, "msg": "Radio getrennt"})

            mc.subscribe(EventType.SELF_INFO,        on_self_info)
            mc.subscribe(EventType.CONTACTS,         on_contacts)
            mc.subscribe(EventType.NEW_CONTACT,      on_new_contact)
            mc.subscribe(EventType.NEXT_CONTACT,     on_new_contact)
            mc.subscribe(EventType.ADVERTISEMENT,    on_advert)
            mc.subscribe(EventType.CONTACT_MSG_RECV, on_direct_msg)
            mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel_msg)
            mc.subscribe(EventType.DISCONNECTED,     on_disconnect)

            # Initial data load — contacts first, then start fetching, then channels
            await asyncio.sleep(1)
            await _push_contacts()
            await mc.start_auto_message_fetching()
            await asyncio.sleep(1)
            await _push_channels()

            while mc.is_connected:
                await asyncio.sleep(2)

        except Exception as e:
            connected = False
            mc = None
            logger.error(f"MeshCore error: {e}", exc_info=True)
            socketio.emit("status", {"connected": False, "msg": f"Verbindungsfehler: {e}"})
            await asyncio.sleep(5)


async def _push_contacts():
    global contacts
    try:
        await mc.ensure_contacts(follow=True)
        raw = mc.contacts or {}
        contacts = {}
        if isinstance(raw, dict):
            for key, c in raw.items():
                if isinstance(c, dict):
                    contacts[key] = contact_to_dict(key, c)
        socketio.emit("contacts", list(contacts.values()))
        # Also push map markers for nodes with GPS
        markers = [c for c in contacts.values() if c["has_gps"]]
        socketio.emit("map_markers", markers)
    except Exception as e:
        logger.error(f"Contacts error: {e}")


async def _push_channels():
    global channels
    try:
        ch_tmp = {}
        for i in range(NUM_CHANNELS):
            try:
                res = await mc.commands.get_channel(i)
                if res and hasattr(res, "payload") and isinstance(res.payload, dict):
                    p    = res.payload
                    name = p.get("channel_name", "")
                    idx  = p.get("channel_idx", i)
                    if name:
                        ch_tmp[idx] = {
                            "idx":  idx,
                            "name": name,
                            "hash": p.get("channel_hash", ""),
                        }
                await asyncio.sleep(1.2)
            except Exception as e:
                logger.warning(f"get_channel({i}) failed: {e}")
                await asyncio.sleep(0.5)
        channels = ch_tmp
        socketio.emit("channels", list(channels.values()))
        logger.info(f"Channels loaded: {[c['name'] for c in channels.values()]}")
    except Exception as e:
        logger.error(f"Channels error: {e}")



# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── SocketIO events ───────────────────────────────────────────────────────────

@socketio.on("connect")
def on_ws_connect():
    emit("status", {
        "connected": connected,
        "msg": "Verbunden" if connected else "Warte auf Radio..."
    })
    if self_info: emit("self_info", self_info)
    if contacts:  emit("contacts", list(contacts.values()))
    if channels:  emit("channels", list(channels.values()))
    markers = [c for c in contacts.values() if c.get("has_gps")]
    if markers:   emit("map_markers", markers)


@socketio.on("send_message")
def on_send(data):
    if not mc or not mc_loop:
        emit("error", {"msg": "Kein Radio verbunden"})
        return
    target_key = data.get("to", "__channel__")
    ch_idx     = int(data.get("channel", 0))
    text       = data.get("text", "").strip()
    if not text:
        return

    async def _send():
        try:
            if target_key == "__channel__":
                await mc.commands.send_chan_msg(ch_idx, text)
                ch_name = channels.get(ch_idx, {}).get("name", f"Ch{ch_idx}")
                socketio.emit("message", {
                    "type":    "channel",
                    "from":    self_info.get("name", "Ich"),
                    "channel": ch_idx,
                    "ch_name": ch_name,
                    "text":    text,
                    "ts":      ts(),
                    "self":    True,
                })
            else:
                contact = mc.get_contact_by_key_prefix(target_key)
                if contact:
                    await mc.commands.send_msg(contact, text)
                    name = contacts.get(target_key, {}).get("name", target_key[:8])
                    socketio.emit("message", {
                        "type":    "direct",
                        "from":    self_info.get("name", "Ich"),
                        "to":      name,
                        "to_key":  target_key[:16],
                        "text":    text,
                        "ts":      ts(),
                        "self":    True,
                    })
                else:
                    socketio.emit("error", {"msg": f"Kontakt nicht gefunden: {target_key[:8]}"})
        except Exception as e:
            logger.error(f"Send error: {e}")
            socketio.emit("error", {"msg": str(e)})

    asyncio.run_coroutine_threadsafe(_send(), mc_loop)


@socketio.on("refresh")
def on_refresh():
    if mc and mc_loop:
        asyncio.run_coroutine_threadsafe(_push_contacts(), mc_loop)
        asyncio.run_coroutine_threadsafe(_push_channels(), mc_loop)


@socketio.on("get_map_markers")
def on_get_markers():
    markers = [c for c in contacts.values() if c.get("has_gps")]
    emit("map_markers", markers)


if __name__ == "__main__":
    threading.Thread(target=run_meshcore, daemon=True).start()
    socketio.run(
        app,
        host=HTTP_HOST,
        port=HTTP_PORT,
        debug=False,
        allow_unsafe_werkzeug=True
    )

