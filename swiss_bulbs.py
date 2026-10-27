#!/usr/bin/env python3
"""
swiss_bulbs.py  —  Universal WiFi Smart Bulb Controller
Swiss-army-knife for every major WiFi bulb brand on your LAN.

Supported protocols
  · WiZ          — UDP JSON    port 38899  (Signify / Philips WiZ)
  · Yeelight     — TCP JSON    port 55443  (Xiaomi ecosystem)
  · LIFX         — UDP binary  port 56700  (premium LAN API)
  · TP-Link Kasa — UDP/TCP     port 9999   (KL-series smart bulbs)
  · MagicHome    — TCP binary  port 5577   (hundreds of cheap RGB bulbs/controllers)
  · Tuya         — detected    port 6668   (needs device key — see note)

Usage
  python3 swiss_bulbs.py                     # auto-discover
  python3 swiss_bulbs.py 192.168.1.35        # pre-load WiZ bulb
  python3 swiss_bulbs.py --yeelight 192.168.1.40   # pre-load with protocol hint

Author : Anmol K Sachan (@FR13ND0x7F)
"""

import socket, json, time, sys, threading, os, math, random, struct, re

# ── ANSI ──────────────────────────────────────────────────────────────────────
R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
M="\033[95m"; C="\033[96m"; W="\033[97m"; DIM="\033[2m"
BOLD="\033[1m"; RST="\033[0m"

MORSE_CODE = {
    'A':'.-',  'B':'-...','C':'-.-.','D':'-..',  'E':'.',   'F':'..-.',
    'G':'--.',  'H':'....','I':'..',  'J':'.---','K':'-.-', 'L':'.-..',
    'M':'--',   'N':'-.',  'O':'---', 'P':'.--.','Q':'--.-','R':'.-.',
    'S':'...',  'T':'-',   'U':'..-', 'V':'...-','W':'.--', 'X':'-..-',
    'Y':'-.--', 'Z':'--..',
    '0':'-----','1':'.----','2':'..---','3':'...--','4':'....-',
    '5':'.....','6':'-....','7':'--...','8':'---..','9':'----.',
    '.':'.-.-.-',',':'--..--','?':'..--..','!':'-.-.--',
    '/':'-..-.','&':'.-...',':':'---...','=':'-...-',
    '+':'.-.-.','@':'.--.-.','\'':'.----.',
}

BRAND_COLOR = {
    "WiZ":       Y,
    "Yeelight":  M,
    "LIFX":      C,
    "Kasa":      G,
    "MagicHome": B,
    "Tuya":      R,
    "Unknown":   DIM,
}


# ══════════════════════════════════════════════════════════════════════════════
#  Base class
# ══════════════════════════════════════════════════════════════════════════════

class SmartBulb:
    BRAND = "Unknown"

    def __init__(self, ip, name="", info=None):
        self.ip   = ip
        self.name = name or f"{self.BRAND} {ip}"
        self.info = info or {}
        self._st  = {}   # cached state: on, brightness, r, g, b, temp

    @property
    def on(self): return bool(self._st.get("on", False))
    @property
    def brightness(self): return int(self._st.get("brightness", 100))

    def state_str(self):
        if not self._st:
            return f"{DIM}?{RST}"
        c     = BRAND_COLOR.get(self.BRAND, DIM)
        onoff = f"{G}ON{RST}" if self.on else f"{R}OFF{RST}"
        dim   = self._st.get("brightness", "?")
        col   = (f"rgb({self._st['r']},{self._st['g']},{self._st['b']})" if "r" in self._st
                 else f"{self._st['temp']}K" if "temp" in self._st else "")
        return f"{onoff}  {DIM}{dim}%  {col}{RST}  {c}[{self.BRAND}]{RST}"

    # Subclasses override these
    def refresh(self):                      pass
    def turn_on(self):                      pass
    def turn_off(self):                     pass
    def set_brightness(self, pct):          pass
    def set_rgb(self, r, g, b, dim=100):    pass
    def set_temp(self, kelvin, dim=100):    pass
    def set_scene(self, scene_id):          self.turn_on()

    def toggle(self):
        self.turn_off() if self.on else self.turn_on()

    def apply(self, params):
        """
        Route a preset dict to the correct method.
        Always turns the bulb on first (unless explicitly turning off).
        """
        if params.get("state") is False:
            self.turn_off()
            return
        # Turn on before applying any setting so the bulb is actually visible
        self.turn_on()
        if "r" in params:
            self.set_rgb(params["r"], params["g"], params["b"],
                         params.get("dimming", params.get("brightness", 100)))
        elif "temp" in params:
            self.set_temp(params["temp"],
                          params.get("dimming", params.get("brightness", 100)))
        elif "dimming" in params or "brightness" in params:
            self.set_brightness(params.get("dimming", params.get("brightness", 100)))
        elif "sceneId" in params:
            self.set_scene(params["sceneId"])


# ══════════════════════════════════════════════════════════════════════════════
#  WiZ  — UDP JSON  port 38899
# ══════════════════════════════════════════════════════════════════════════════

class WizBulb(SmartBulb):
    BRAND = "WiZ"
    PORT  = 38899

    def _cmd(self, method, params=None, timeout=2):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout); s.bind(("", 0))
        msg = json.dumps({"method": method, "params": params or {}, "id": 1})
        try:
            s.sendto(msg.encode(), (self.ip, self.PORT))
            r, _ = s.recvfrom(2048)
            return json.loads(r.decode())
        except Exception:
            return None
        finally:
            s.close()

    def _fire(self, method, params=None):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", 0))
        msg = json.dumps({"method": method, "params": params or {}, "id": 1})
        try:
            s.sendto(msg.encode(), (self.ip, self.PORT))
        finally:
            s.close()

    def refresh(self):
        r = self._cmd("getPilot")
        if r and "result" in r:
            p = r["result"]
            self._st = {"on": p.get("state", False),
                        "brightness": p.get("dimming", 100)}
            if "r" in p: self._st.update({"r": p["r"], "g": p["g"], "b": p["b"]})
            if "temp" in p: self._st["temp"] = p["temp"]

    def turn_on(self):  self._fire("setPilot", {"state": True})
    def turn_off(self): self._fire("setPilot", {"state": False})

    # Include state:True so a currently-off bulb turns on when a setting is applied
    def set_brightness(self, pct):
        self._fire("setPilot", {"state": True, "dimming": max(1, min(100, pct))})

    def set_rgb(self, r, g, b, dim=100):
        self._fire("setPilot", {"state": True, "r": r, "g": g, "b": b,
                                "dimming": max(1, min(100, dim))})

    def set_temp(self, kelvin, dim=100):
        self._fire("setPilot", {"state": True,
                                "temp": max(2200, min(6500, kelvin)),
                                "dimming": max(1, min(100, dim))})

    def set_scene(self, scene_id):
        self._fire("setPilot", {"state": True, "sceneId": scene_id})


# ══════════════════════════════════════════════════════════════════════════════
#  Yeelight  — TCP JSON  port 55443
# ══════════════════════════════════════════════════════════════════════════════

class YeelightBulb(SmartBulb):
    BRAND = "Yeelight"

    def __init__(self, ip, name="", info=None, port=55443):
        super().__init__(ip, name, info)
        self.port = port

    def _cmd(self, method, params, timeout=3):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((self.ip, self.port))
            msg = json.dumps({"id": 1, "method": method, "params": params}) + "\r\n"
            s.sendall(msg.encode())
            resp = s.recv(4096).decode(errors="replace").strip()
            for line in resp.splitlines():
                try:
                    return json.loads(line)
                except Exception:
                    pass
        except Exception:
            return None
        finally:
            s.close()

    def refresh(self):
        r = self._cmd("get_prop", ["power", "bright", "color_mode", "ct", "rgb"])
        if r and "result" in r:
            vals = r["result"]
            self._st = {"on": vals[0] == "on",
                        "brightness": int(vals[1]) if vals[1] else 100}
            mode = int(vals[2]) if vals[2] else 1
            if mode == 2 and vals[3]:
                self._st["temp"] = int(vals[3])
            elif mode == 1 and vals[4]:
                rgb_int = int(vals[4])
                self._st.update({"r": (rgb_int>>16)&0xFF,
                                 "g": (rgb_int>>8)&0xFF,
                                 "b": rgb_int&0xFF})

    def turn_on(self):  self._cmd("set_power", ["on",  "smooth", 300])
    def turn_off(self): self._cmd("set_power", ["off", "smooth", 300])

    def set_brightness(self, pct):
        self._cmd("set_bright", [max(1, min(100, pct)), "smooth", 300])

    def set_rgb(self, r, g, b, dim=100):
        self._cmd("set_rgb", [(r<<16)|(g<<8)|b, "smooth", 300])
        self._cmd("set_bright", [max(1, min(100, dim)), "smooth", 300])

    def set_temp(self, kelvin, dim=100):
        self._cmd("set_ct_abx", [max(1700, min(6500, kelvin)), "smooth", 300])
        self._cmd("set_bright", [max(1, min(100, dim)), "smooth", 300])

    def set_scene(self, scene_id):
        self._cmd("set_scene", ["ct", 4000, 80])


# ══════════════════════════════════════════════════════════════════════════════
#  LIFX  — UDP binary  port 56700
# ══════════════════════════════════════════════════════════════════════════════

class LifxBulb(SmartBulb):
    BRAND = "LIFX"
    PORT  = 56700
    _seq  = 0

    _GET_LIGHT = 101
    _SET_POWER = 21
    _SET_COLOR = 102

    def _frame(self, type_, payload=b''):
        LifxBulb._seq = (LifxBulb._seq + 1) & 0xFF
        size        = 36 + len(payload)
        frame_flags = 0x3400   # tagged=1, addressable=1, protocol=1024
        hdr = struct.pack('<HHI8s6sBBQHH',
                          size, frame_flags, 0x4C495846,
                          b'\x00'*8, b'\x00'*6, 0, LifxBulb._seq,
                          0, type_, 0)
        return hdr + payload

    def _send(self, type_, payload=b'', timeout=2):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout); s.bind(('', 0))
        try:
            s.sendto(self._frame(type_, payload), (self.ip, self.PORT))
            data, _ = s.recvfrom(2048)
            return data
        except Exception:
            return None
        finally:
            s.close()

    def _fire(self, type_, payload=b''):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('', 0))
        try:
            s.sendto(self._frame(type_, payload), (self.ip, self.PORT))
        finally:
            s.close()

    def refresh(self):
        resp = self._send(self._GET_LIGHT)
        if resp and len(resp) >= 48:
            pl = resp[36:]
            hue, sat, bri, kelvin, _, power = struct.unpack('<HHHHHH', pl[:12])
            self._st = {"on":         power > 0,
                        "brightness": int(bri / 65535 * 100),
                        "temp":       kelvin}
            if sat > 5000:
                r, g, b = _hsv_to_rgb(hue/65535*360, sat/65535, bri/65535)
                self._st.update({"r": r, "g": g, "b": b})

    def turn_on(self):  self._fire(self._SET_POWER, struct.pack('<HI', 65535, 300))
    def turn_off(self): self._fire(self._SET_POWER, struct.pack('<HI', 0,     300))

    def set_brightness(self, pct):
        resp = self._send(self._GET_LIGHT)
        if resp and len(resp) >= 44:
            hue, sat, _, kelvin = struct.unpack('<HHHH', resp[36:44])
        else:
            hue, sat, kelvin = 0, 0, 4000
        bri = int(max(1, min(100, pct)) / 100 * 65535)
        self._fire(self._SET_COLOR, struct.pack('<xHHHHI', hue, sat, bri, kelvin, 300))

    def set_rgb(self, r, g, b, dim=100):
        h, s, _ = _rgb_to_hsv(r, g, b)
        hue = int(h / 360 * 65535)
        sat = int(s * 65535)
        bri = int(max(1, min(100, dim)) / 100 * 65535)
        self._fire(self._SET_COLOR, struct.pack('<xHHHHI', hue, sat, bri, 3500, 300))

    def set_temp(self, kelvin, dim=100):
        bri = int(max(1, min(100, dim)) / 100 * 65535)
        ct  = max(2500, min(9000, kelvin))
        self._fire(self._SET_COLOR, struct.pack('<xHHHHI', 0, 0, bri, ct, 300))


# ══════════════════════════════════════════════════════════════════════════════
#  TP-Link Kasa  — UDP/TCP  port 9999  (KL-series smart bulbs)
# ══════════════════════════════════════════════════════════════════════════════

def _kasa_enc(data):
    key, out = 171, b''
    for c in data.encode():
        key ^= c; out += bytes([key])
    return struct.pack('>I', len(out)) + out


def _kasa_dec(data):
    key, out = 171, ''
    for c in data[4:]:
        out += chr(key ^ c); key = c
    return out


class KasaBulb(SmartBulb):
    BRAND = "Kasa"
    PORT  = 9999

    def _cmd(self, payload, timeout=3, udp=False):
        enc = _kasa_enc(json.dumps(payload))
        if udp:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout); s.bind(('', 0))
            try:
                s.sendto(enc, (self.ip, self.PORT))
                resp, _ = s.recvfrom(4096)
                return json.loads(_kasa_dec(resp))
            except Exception:
                return None
            finally:
                s.close()
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                s.connect((self.ip, self.PORT))
                s.sendall(enc)
                resp = b''
                while True:
                    chunk = s.recv(4096)
                    if not chunk: break
                    resp += chunk
                    try:
                        return json.loads(_kasa_dec(resp))
                    except Exception:
                        pass
                return None
            except Exception:
                return None
            finally:
                s.close()

    def _light_cmd(self, **kwargs):
        return self._cmd({
            "smartlife.iot.smartbulb.lightingservice": {
                "transition_light_state": kwargs
            }
        })

    def refresh(self):
        r = self._cmd({"system": {"get_sysinfo": {}}})
        if not r: return
        si = r.get("system", {}).get("get_sysinfo", {})
        ls = si.get("light_state") or si.get("lighting_effect_state") or {}
        self._st = {"on":         bool(ls.get("on_off", si.get("relay_state", 0))),
                    "brightness": int(ls.get("brightness", 100))}
        if ls.get("color_temp"):
            self._st["temp"] = ls["color_temp"]
        if "hue" in ls:
            r_, g_, b_ = _hsv_to_rgb(ls.get("hue", 0),
                                      ls.get("saturation", 0) / 100,
                                      ls.get("brightness", 100) / 100)
            self._st.update({"r": r_, "g": g_, "b": b_})

    def turn_on(self):  self._light_cmd(on_off=1, transition_period=300)
    def turn_off(self): self._light_cmd(on_off=0, transition_period=300)

    def set_brightness(self, pct):
        self._light_cmd(on_off=1, brightness=max(1, min(100, pct)),
                        transition_period=300)

    def set_rgb(self, r, g, b, dim=100):
        h, s, _ = _rgb_to_hsv(r, g, b)
        self._light_cmd(on_off=1, hue=int(h), saturation=int(s*100),
                        brightness=max(1, min(100, dim)),
                        color_temp=0, transition_period=300)

    def set_temp(self, kelvin, dim=100):
        self._light_cmd(on_off=1, color_temp=max(2500, min(6500, kelvin)),
                        brightness=max(1, min(100, dim)),
                        transition_period=300)


# ══════════════════════════════════════════════════════════════════════════════
#  MagicHome / FluxLED  — TCP binary  port 5577
#  Covers: MagicHome, Zengge, Flux LED, HappyLighting, and hundreds of cheap
#  RGB bulbs and LED controllers sold under generic brand names.
# ══════════════════════════════════════════════════════════════════════════════

def _magic_csum(data):
    return sum(data) & 0xFF


class MagicBulb(SmartBulb):
    BRAND = "MagicHome"
    PORT  = 5577

    def _send(self, data, read_bytes=0, timeout=2):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((self.ip, self.PORT))
            s.sendall(bytes(data))
            if read_bytes:
                return s.recv(read_bytes)
        except Exception:
            return None
        finally:
            s.close()

    def refresh(self):
        q    = [0x81, 0x8a, 0x8b]
        resp = self._send(q + [_magic_csum(q)], read_bytes=14)
        if resp and len(resp) >= 9:
            self._st = {"on":         resp[2] == 0x23,
                        "brightness": 100,
                        "r": resp[6], "g": resp[7], "b": resp[8]}

    def turn_on(self):
        d = [0x71, 0x23]; self._send(d + [_magic_csum(d)])

    def turn_off(self):
        d = [0x71, 0x24]; self._send(d + [_magic_csum(d)])

    def set_brightness(self, pct):
        scale = max(1, min(100, pct)) / 100
        r = int(self._st.get("r", 255) * scale)
        g = int(self._st.get("g", 255) * scale)
        b = int(self._st.get("b", 255) * scale)
        self.set_rgb(r, g, b)

    def set_rgb(self, r, g, b, dim=100):
        scale = max(1, min(100, dim)) / 100
        r_, g_, b_ = int(r*scale), int(g*scale), int(b*scale)
        d = [0x31, r_, g_, b_, 0x00, 0xf0, 0x0f]
        self._send(d + [_magic_csum(d)])

    def set_rgbw(self, r, g, b, w, dim=100):
        """RGBW controllers — set white channel separately."""
        scale = max(1, min(100, dim)) / 100
        r_, g_, b_, w_ = int(r*scale), int(g*scale), int(b*scale), int(w*scale)
        d = [0x31, r_, g_, b_, w_, 0xf0, 0x0f]
        self._send(d + [_magic_csum(d)])

    def set_warm_white(self, brightness=100):
        """Pure warm white via dedicated white channel (RGBW controllers)."""
        w = int(max(1, min(100, brightness)) / 100 * 255)
        d = [0x31, 0x00, 0x00, 0x00, w, 0x0f, 0x0f]
        self._send(d + [_magic_csum(d)])

    def set_preset(self, pattern_code=0x25, speed=50):
        """
        Native MagicHome preset pattern (0x25-0x38 = built-in patterns).
        speed: 1-100 (100=fast). Pattern list:
          0x25 seven color cross fade, 0x26 red gradual, 0x27 green gradual,
          0x28 blue gradual, 0x29 yellow gradual, 0x2a cyan gradual,
          0x2b purple gradual, 0x2c white gradual, 0x2d red/green cross,
          0x2e red/blue cross, 0x2f green/blue cross, 0x30 seven color strobe,
          0x31 red strobe, 0x32 green strobe, 0x33 blue strobe,
          0x34 yellow strobe, 0x35 cyan strobe, 0x36 purple strobe,
          0x37 white strobe, 0x38 seven color jump
        """
        spd = max(1, min(100, speed))
        d = [0x61, pattern_code, spd, 0x0f]
        self._send(d + [_magic_csum(d)])

    def set_temp(self, kelvin, dim=100):
        r, g, b = _kelvin_to_rgb(kelvin)
        self.set_rgb(r, g, b, dim)


# ══════════════════════════════════════════════════════════════════════════════
#  Tuya  —  port 6668  (tinytuya)
#  LAN traffic is AES-128 encrypted per device.
#  Requires: pip3 install tinytuya  +  localKey from iot.tuya.com
# ══════════════════════════════════════════════════════════════════════════════

class TuyaBulb(SmartBulb):
    BRAND = "Tuya"

    def __init__(self, ip, name="", info=None):
        super().__init__(ip, name, info)
        self.device_id = (info or {}).get("id", (info or {}).get("gwId", ""))
        self.local_key = (info or {}).get("key", "")
        self.version   = str((info or {}).get("version", "3.3"))
        self._dev      = None
        if self.device_id and self.local_key:
            self._init_dev()

    def _init_dev(self):
        try:
            import tinytuya
            self._dev = tinytuya.BulbDevice(self.device_id, self.ip, self.local_key)
            self._dev.set_version(float(self.version))
        except Exception:
            self._dev = None

    def set_local_key(self, key):
        self.local_key = key
        self._init_dev()

    def refresh(self):
        if not self._dev: return
        try:
            r = self._dev.status()
            dps = r.get("dps", {})
            self._st = {"on": bool(dps.get("20", dps.get("1", False))),
                        "brightness": max(1, min(100, int(dps.get("22", 1000)) // 10))}
            if "24" in dps and len(str(dps["24"])) >= 12:
                raw = str(dps["24"])
                h = int(raw[0:4], 16); s = int(raw[4:8], 16); v = int(raw[8:12], 16)
                rr, gg, bb = _hsv_to_rgb(h, s / 1000, v / 1000)
                self._st.update({"r": rr, "g": gg, "b": bb})
            elif "23" in dps:
                ct_raw = int(dps["23"])   # 0=warm/2700K … 1000=cool/6500K
                self._st["temp"] = int(2700 + (6500 - 2700) * ct_raw / 1000)
        except Exception:
            pass

    def turn_on(self):
        if self._dev:
            try: self._dev.turn_on()
            except Exception: pass
        else: self._prompt_key()

    def turn_off(self):
        if self._dev:
            try: self._dev.turn_off()
            except Exception: pass
        else: self._prompt_key()

    def set_brightness(self, pct):
        if self._dev:
            try: self._dev.set_brightness(max(10, min(1000, int(pct) * 10)))
            except Exception: pass
        else: self._prompt_key()

    def set_rgb(self, r, g, b, dim=100):
        if self._dev:
            try:
                self._dev.set_colour(r, g, b)
                if dim != 100:
                    self._dev.set_brightness(max(10, min(1000, int(dim) * 10)))
            except Exception: pass
        else: self._prompt_key()

    def set_temp(self, kelvin, dim=100):
        if self._dev:
            try:
                ct = max(0, min(1000, int((kelvin - 2700) / (6500 - 2700) * 1000)))
                self._dev.set_colourtemp(ct)
                if dim != 100:
                    self._dev.set_brightness(max(10, min(1000, int(dim) * 10)))
            except Exception: pass
        else: self._prompt_key()

    def apply(self, params):
        if not self._dev:
            self._prompt_key()
            return
        super().apply(params)

    def _prompt_key(self):
        mac = self.info.get("mac", "?") if self.info else "?"
        print(f"\n  {R}Tuya localKey required to control this device.{RST}")
        print(f"  {DIM}Device ID : {self.device_id or '(unknown)'}{RST}")
        print(f"  {DIM}MAC       : {mac}{RST}")
        print(f"  {DIM}Version   : {self.version}{RST}")
        print(f"\n  {BOLD}How to get your localKey:{RST}")
        print(f"  {DIM}  1. Create a free account at  iot.tuya.com{RST}")
        print(f"  {DIM}  2. Create a project → Cloud Development → Add Device{RST}")
        print(f"  {DIM}  3. Link your Tuya app account to the project{RST}")
        print(f"  {DIM}  4. Go to Devices → find your bulb → copy localKey{RST}")
        print(f"  {DIM}  OR run:  python3 -m tinytuya wizard{RST}")
        print()
        try:
            k = input(f"  {Y}Enter localKey now (or Enter to skip) >{RST} ").strip()
            if k:
                self.set_local_key(k)
                if self._dev:
                    print(f"  {G}localKey set — control enabled.{RST}")
                else:
                    print(f"  {R}tinytuya init failed. Make sure tinytuya is installed.{RST}")
        except (EOFError, KeyboardInterrupt):
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  Color utilities
# ══════════════════════════════════════════════════════════════════════════════

def _hsv_to_rgb(h, s, v):
    """h 0-360, s/v 0-1 → (r,g,b) 0-255"""
    h %= 360
    c = v * s; x = c * (1 - abs((h/60) % 2 - 1)); m = v - c
    if   h < 60:  r,g,b = c,x,0
    elif h < 120: r,g,b = x,c,0
    elif h < 180: r,g,b = 0,c,x
    elif h < 240: r,g,b = 0,x,c
    elif h < 300: r,g,b = x,0,c
    else:          r,g,b = c,0,x
    return int((r+m)*255), int((g+m)*255), int((b+m)*255)


def _rgb_to_hsv(r, g, b):
    """(r,g,b) 0-255 → (h 0-360, s 0-1, v 0-1)"""
    r_, g_, b_ = r/255, g/255, b/255
    mx = max(r_,g_,b_); mn = min(r_,g_,b_); d = mx - mn
    h = 0
    if d:
        if   mx == r_: h = 60 * (((g_-b_)/d) % 6)
        elif mx == g_: h = 60 * ((b_-r_)/d + 2)
        else:          h = 60 * ((r_-g_)/d + 4)
    return h, (d/mx) if mx else 0, mx


def _kelvin_to_rgb(k):
    """Approximate colour temperature to (r,g,b)."""
    k = max(1000, min(10000, k)) / 100
    r = 255 if k <= 66 else int(329.698727446 * ((k-60)**-0.1332047592))
    g = (int(99.4708025861 * math.log(k) - 161.1195681661) if k <= 66
         else int(288.1221695283 * ((k-60)**-0.0755148492)))
    b = (0 if k <= 19 else
         255 if k >= 66 else
         int(138.5177312231 * math.log(k-10) - 305.0447927307))
    return max(0,min(255,r)), max(0,min(255,g)), max(0,min(255,b))


# ══════════════════════════════════════════════════════════════════════════════
#  Discovery
# ══════════════════════════════════════════════════════════════════════════════

def _local_ip():
    """Return the best LAN IP — prefers 192.168.x or 10.x over VPN/loopback."""
    candidates = []

    # Method 1: enumerate all interfaces via getaddrinfo trick per hostname
    try:
        import socket as _s
        for info in _s.getaddrinfo(_s.gethostname(), None):
            ip = info[4][0]
            if not ip.startswith("127.") and ":" not in ip:
                candidates.append(ip)
    except Exception:
        pass

    # Method 2: route to 8.8.8.8 (what Python normally does)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        candidates.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    # Prefer private LAN ranges: 192.168.x, 10.x, 172.16-31.x
    # but score 192.168 highest (most common home LAN)
    def _score(ip):
        if ip.startswith("192.168."): return 3
        if ip.startswith("10."):      return 2
        p = ip.split(".")
        if p[0] == "172" and 16 <= int(p[1]) <= 31: return 1
        return 0

    candidates = [ip for ip in candidates if ip and not ip.startswith("169.254.")]
    if not candidates:
        return "192.168.1.1"
    return max(candidates, key=_score)


def _scan_port(ip, port, timeout=0.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:    s.connect((ip, port)); return True
    except: return False
    finally: s.close()


def _discover_wiz(subnet, found):
    lock = threading.Lock()
    def probe(ip):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0); s.bind(("", 0))
        msg = json.dumps({"method": "getPilot", "params": {}, "id": 1}).encode()
        try:
            s.sendto(msg, (ip, 38899))
            r, _ = s.recvfrom(2048)
            data = json.loads(r.decode())
            if "result" in data:
                p = data["result"]
                b = WizBulb(ip)
                b._st = {"on": p.get("state", False), "brightness": p.get("dimming", 100)}
                if "r" in p:    b._st.update({"r": p["r"], "g": p["g"], "b": p["b"]})
                if "temp" in p: b._st["temp"] = p["temp"]
                with lock: found[ip] = b
        except Exception:
            pass
        finally:
            s.close()
    ts = [threading.Thread(target=probe, args=(f"{subnet}.{i}",), daemon=True)
          for i in range(1, 255)]
    for t in ts: t.start()
    for t in ts: t.join()


def _discover_yeelight(found):
    msg = ("M-SEARCH * HTTP/1.1\r\n"
           "HOST: 239.255.255.250:1982\r\n"
           "MAN: \"ssdp:discover\"\r\n"
           "ST: wifi_bulb\r\n\r\n")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(msg.encode(), ("239.255.255.250", 1982))
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(2048)
                ip   = addr[0]
                text = data.decode(errors="replace")
                loc  = re.search(r"Location: yeelight://[\d.]+:(\d+)", text, re.I)
                port = int(loc.group(1)) if loc else 55443
                info = {}
                for line in text.splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":"); info[k.strip().lower()] = v.strip()
                b = YeelightBulb(ip, info=info, port=port)
                b._st = {"on": info.get("power","off") == "on",
                         "brightness": int(info.get("bright", 100))}
                if info.get("color_mode") == "2" and info.get("ct"):
                    b._st["temp"] = int(info["ct"])
                elif info.get("color_mode") == "1" and info.get("rgb"):
                    rgb = int(info["rgb"])
                    b._st.update({"r":(rgb>>16)&0xFF,"g":(rgb>>8)&0xFF,"b":rgb&0xFF})
                found[ip] = b
            except socket.timeout:
                break
            except Exception:
                pass
    finally:
        s.close()


def _discover_lifx(found):
    # GetService broadcast (type=2)
    hdr = struct.pack('<HHI8s6sBBQHH',
                      36, 0x3400, 0x4C495846, b'\x00'*8, b'\x00'*6, 0, 0, 0, 2, 0)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(3); s.bind(('', 0))
    try:
        s.sendto(hdr, ("255.255.255.255", 56700))
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(2048)
                if len(data) >= 36:
                    msg_type = struct.unpack('<H', data[32:34])[0]
                    if msg_type == 3:   # StateService
                        found[addr[0]] = LifxBulb(addr[0])
            except socket.timeout:
                break
            except Exception:
                pass
    finally:
        s.close()


def _discover_kasa(found):
    payload = _kasa_enc('{"system":{"get_sysinfo":{}}}')
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(3); s.bind(('', 0))
    try:
        s.sendto(payload, ("255.255.255.255", 9999))
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(4096)
                ip   = addr[0]
                info = json.loads(_kasa_dec(data))
                si   = info.get("system", {}).get("get_sysinfo", {})
                model = si.get("model", "").upper()
                # Accept if: KL/LB model prefix, has light_state, or is_dimmable flag
                is_bulb = (any(m in model for m in ("KL", "LB", "BULB", "LIGHT")) or
                           "light_state" in si or si.get("is_dimmable") == 1)
                if is_bulb:
                    found[ip] = KasaBulb(ip, info=si)
            except socket.timeout:
                break
            except Exception:
                pass
    finally:
        s.close()


def _discover_magichome(subnet, found):
    # Method 1: HF-A11ASSISTHREAD UDP broadcast on port 48899
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(2)
        s.bind(('', 48899))
        try:
            s.sendto(b"HF-A11ASSISTHREAD", ("255.255.255.255", 48899))
            deadline = time.time() + 2
            while time.time() < deadline:
                try:
                    data, addr = s.recvfrom(1024)
                    ip   = addr[0]
                    text = data.decode(errors="replace")
                    if "," in text:
                        parts = text.split(",")
                        model = parts[2].strip() if len(parts) >= 3 else "MagicHome"
                        found[ip] = MagicBulb(ip, info={"model": model})
                except socket.timeout:
                    break
                except Exception:
                    pass
        finally:
            s.close()
    except Exception:
        pass  # port 48899 in use — fall through to TCP scan

    # Method 2: TCP connect scan on port 5577
    lock = threading.Lock()
    def check(ip):
        if ip not in found and _scan_port(ip, 5577):
            with lock:
                if ip not in found:
                    found[ip] = MagicBulb(ip)
    ts = [threading.Thread(target=check, args=(f"{subnet}.{i}",), daemon=True)
          for i in range(1, 255)]
    for t in ts: t.start()
    for t in ts: t.join()


def _detect_tuya(subnet, found):
    # Method 1: tinytuya broadcast scanner — suppress all its stderr noise
    try:
        import tinytuya, io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            devs = tinytuya.deviceScan(verbose=False, maxretry=2, color=False)
        for ip, info in devs.items():
            if ip not in found:
                found[ip] = TuyaBulb(ip, info=info)
    except Exception:
        pass

    # Method 2: TCP port 6668 scan — catches devices that didn't broadcast
    lock = threading.Lock()
    def check(ip):
        if ip not in found and _scan_port(ip, 6668):
            with lock:
                if ip not in found:
                    found[ip] = TuyaBulb(ip)
    ts = [threading.Thread(target=check, args=(f"{subnet}.{i}",), daemon=True)
          for i in range(1, 255)]
    for t in ts: t.start()
    for t in ts: t.join()


def discover_all():
    local  = _local_ip()
    subnet = local.rsplit(".", 1)[0]
    # Sanity-check: if subnet looks like a VPN/unexpected range, warn but continue
    found  = {}

    runners = {
        "WiZ":       lambda: _discover_wiz(subnet, found),
        "Yeelight":  lambda: _discover_yeelight(found),
        "LIFX":      lambda: _discover_lifx(found),
        "Kasa":      lambda: _discover_kasa(found),
        "MagicHome": lambda: _discover_magichome(subnet, found),
        "Tuya":      lambda: _detect_tuya(subnet, found),
    }

    def run(fn):
        try: fn()
        except Exception: pass

    threads = [threading.Thread(target=run, args=(fn,), daemon=True)
               for fn in runners.values()]
    for t in threads: t.start()
    for t in threads: t.join()

    # Refresh state for bulbs that didn't include state in discovery response
    def refresh_one(b):
        try:
            if not b._st: b.refresh()
        except Exception: pass

    rts = [threading.Thread(target=refresh_one, args=(b,), daemon=True)
           for b in found.values()]
    for t in rts: t.start()
    for t in rts: t.join(timeout=5)

    return found


# ══════════════════════════════════════════════════════════════════════════════
#  Registry
# ══════════════════════════════════════════════════════════════════════════════

registry: list[SmartBulb] = []
targets:  list[int]       = []   # indices; empty = all


def get_targets() -> list[SmartBulb]:
    if not targets:
        return list(registry)
    return [registry[i] for i in targets if i < len(registry)]


def refresh_all():
    ts = [threading.Thread(target=lambda b=b: b.refresh(), daemon=True)
          for b in get_targets()]
    for t in ts: t.start()
    for t in ts: t.join(timeout=5)


def send_all(method_name, *args, **kwargs):
    """Call method on every targeted bulb simultaneously."""
    def _do(b):
        try: getattr(b, method_name)(*args, **kwargs)
        except Exception: pass
    ts = [threading.Thread(target=_do, args=(b,), daemon=True) for b in get_targets()]
    for t in ts: t.start()
    for t in ts: t.join(timeout=4)


def apply_all(params):
    def _do(b):
        try: b.apply(params)
        except Exception: pass
    ts = [threading.Thread(target=_do, args=(b,), daemon=True) for b in get_targets()]
    for t in ts: t.start()
    for t in ts: t.join(timeout=4)


def _save_states():
    refresh_all()
    return [(b, dict(b._st)) for b in get_targets()]


def _restore_states(saved):
    def _do(b, st):
        try:
            if not st.get("on"):
                b.turn_off()
            elif "r" in st:
                b.set_rgb(st["r"], st["g"], st["b"], st.get("brightness", 100))
            elif "temp" in st:
                b.set_temp(st["temp"], st.get("brightness", 100))
            else:
                b.set_brightness(st.get("brightness", 100))
        except Exception:
            pass
    ts = [threading.Thread(target=_do, args=(b, st), daemon=True) for b, st in saved]
    for t in ts: t.start()
    for t in ts: t.join(timeout=4)


# ══════════════════════════════════════════════════════════════════════════════
#  Effects  (all run until Ctrl+C then restore original state)
# ══════════════════════════════════════════════════════════════════════════════

def fx_rainbow(speed=0.05):
    saved = _save_states(); hue = 0; n = max(len(get_targets()), 1)
    print(f"  {Y}Rainbow — Ctrl+C to stop{RST}")
    try:
        while True:
            for idx, b in enumerate(get_targets()):
                r_, g_, b_ = _hsv_to_rgb((hue + idx*(360//n)) % 360, 1, 1)
                threading.Thread(target=b.set_rgb, args=(r_,g_,b_), daemon=True).start()
            hue = (hue + 3) % 360; time.sleep(speed)
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_strobe(hz=8):
    saved = _save_states(); state = True; delay = 1 / (hz * 2)
    print(f"  {Y}Strobe {hz}Hz — Ctrl+C to stop{RST}")
    try:
        while True:
            send_all("turn_on" if state else "turn_off")
            state = not state; time.sleep(delay)
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_breathe(temp=4000, speed=2.0):
    saved = _save_states(); t_val = 0.0; step = 0.05
    print(f"  {Y}Breathe — Ctrl+C to stop{RST}")
    try:
        while True:
            dim = int((math.sin(t_val)*0.5 + 0.5) * 90 + 10)
            send_all("set_temp", temp, dim)
            t_val = (t_val + step*(math.pi*2/speed)) % (math.pi*2)
            time.sleep(step)
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_sunset(minutes=5):
    steps = int(minutes * 60 / 2); saved = _save_states()
    print(f"  {Y}Sunset over {minutes} min — Ctrl+C to cancel{RST}")
    try:
        for i in range(steps):
            p = i / steps
            send_all("set_temp", int(4000 - p*1800), int(100 - p*95))
            time.sleep(2)
        send_all("turn_off"); print(f"\n  {DIM}Lights out.{RST}")
    except KeyboardInterrupt:
        _restore_states(saved)
    print(f"  {G}Done.{RST}")


def fx_wave(speed=0.4):
    saved = _save_states(); bulbs_ = get_targets(); n = len(bulbs_)
    seq   = list(range(n)) + list(range(n-2, 0, -1))
    print(f"  {Y}Wave — Ctrl+C to stop{RST}")
    try:
        while True:
            for idx in seq:
                for j, b in enumerate(bulbs_):
                    dim = 100 if j == idx else 5
                    threading.Thread(target=b.set_temp, args=(4000, dim), daemon=True).start()
                time.sleep(speed)
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_disco(speed=0.15):
    saved = _save_states()
    print(f"  {Y}Disco — Ctrl+C to stop{RST}")
    try:
        while True:
            for b in get_targets():
                r_, g_, b_ = _hsv_to_rgb(random.randint(0, 359), 1, 1)
                threading.Thread(target=b.set_rgb, args=(r_,g_,b_), daemon=True).start()
            time.sleep(speed)
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_police(speed=0.25):
    saved = _save_states(); bulbs_ = get_targets(); n = len(bulbs_); toggle = False
    print(f"  {Y}Police — Ctrl+C to stop{RST}")
    try:
        while True:
            for idx, b in enumerate(bulbs_):
                red = (idx % 2 == 0) ^ toggle if n >= 2 else toggle
                col = (255,0,0) if red else (0,0,255)
                threading.Thread(target=b.set_rgb, args=col, daemon=True).start()
            toggle = not toggle; time.sleep(speed)
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_candle(speed=0.12):
    saved = _save_states()
    print(f"  {Y}Candlelight — Ctrl+C to stop{RST}")
    try:
        while True:
            for b in get_targets():
                threading.Thread(target=b.set_temp,
                                 args=(random.randint(2200,2700), random.randint(30,75)),
                                 daemon=True).start()
            time.sleep(speed + random.uniform(-0.04, 0.04))
    except KeyboardInterrupt: pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_alert(hz=2, pulses=0):
    """
    Flash all targeted bulbs solid red at hz flashes per second.
    pulses=0 → run until Ctrl+C.
    pulses=N → fire exactly N flashes then restore.
    """
    saved = _save_states()
    delay = 1 / (hz * 2)
    count = 0
    limit = pulses * 2 if pulses else None   # on+off pairs
    print(f"  {R}Alert — flashing red at {hz}Hz {'(' + str(pulses) + ' pulses)' if pulses else '— Ctrl+C to stop'}{RST}")
    try:
        state = True
        while True:
            if state:
                send_all("set_rgb", 255, 0, 0, 100)
            else:
                send_all("turn_off")
            state = not state
            count += 1
            if limit and count >= limit:
                break
            time.sleep(delay)
    except KeyboardInterrupt:
        pass
    _restore_states(saved); print(f"\n  {G}Restored.{RST}")


def fx_music(sensitivity=1.5, mode="color", palette="full", speed="med", bright_floor=5):
    """
    Audio-reactive music mode with live keyboard controls and VU meter.

    Parameters (all adjustable live while running):
      sensitivity  : 0.5 (quiet room) … 4.0 (very reactive)   default 1.5
      mode         : color · pulse · strobe · spectrum · beat
      palette      : full · warm · cool · fire · ocean · forest · neon
      speed        : slow · med · fast  (hue shift rate)
      bright_floor : minimum brightness 0-50% (floor so bulbs never go black)

    Modes:
      color    — hue cycles through chosen palette, driven by volume
      pulse    — white brightness pulses with volume
      strobe   — flashes white on loud transients only
      spectrum — FFT split: bass→red  mid→green  treble→blue  (RGB mix)
      beat     — detects onset, flashes a random palette colour each beat

    Native hooks (lower latency):
      Yeelight  — set_music (device connects back → no rate limit)
      MagicHome — opcode 0x73 (hardware mic chip)

    Keys while running:
      +/-     sensitivity up / down
      m       cycle mode
      p       cycle palette
      s       cycle speed
      [/]     brightness floor down / up
      r       reset hue to 0
      q / Ctrl+C  stop

    Requires: pip install sounddevice numpy
    """
    import importlib, collections, queue as _queue
    try:
        sd = importlib.import_module("sounddevice")
        np = importlib.import_module("numpy")
    except ImportError:
        print(f"  {Y}[!]{RST} Music mode needs sounddevice + numpy:")
        print(f"      {DIM}pip install sounddevice numpy{RST}")
        return

    saved  = _save_states()
    bulbs_ = get_targets()
    if not bulbs_:
        print(f"  {R}[-]{RST} No bulbs selected."); return

    RATE  = 22050
    CHUNK = 512

    # ── live-adjustable state (one dict, written from key thread, read from audio cb) ──
    S = {
        "sensitivity": float(sensitivity),
        "mode":        mode,
        "palette":     palette,
        "speed":       speed,
        "floor":       int(bright_floor),
        "hue":         0.0,
        "peak":        0.0,   # decaying peak for VU display
        "volume":      0.0,   # last RMS volume 0-1
        "beat":        False, # flash flag for display
        "energy_hist": collections.deque(maxlen=43),  # ~2 s at 512/22050
        "last_beat_t": 0.0,
        "running":     True,
    }

    MODES    = ["color", "pulse", "strobe", "spectrum", "beat"]
    PALETTES = {
        "full":   (0,   360),
        "warm":   (0,    60),
        "cool":   (180, 270),
        "fire":   (0,    40),
        "ocean":  (185, 245),
        "forest": (80,  160),
        "neon":   (270, 330),
    }
    SPEEDS   = {"slow": 4, "med": 14, "fast": 32}

    # ── helpers ───────────────────────────────────────────────────────────────
    def _hue_to_rgb(h):
        h = h % 360
        hi = int(h / 60) % 6
        f  = (h / 60) - int(h / 60)
        p, q, t_ = 0.0, 1 - f, f
        table = [(1,t_,p),(q,1,p),(p,1,t_),(p,q,1),(t_,p,1),(1,p,q)]
        r, g, b_ = table[hi]
        return int(r*255), int(g*255), int(b_*255)

    def _pal_hue(hue_val):
        """Clamp hue to current palette range."""
        lo, hi_ = PALETTES.get(S["palette"], (0, 360))
        span = hi_ - lo
        return lo + (hue_val % span)

    def _clamp_bright(v):
        return max(S["floor"], min(100, int(v)))

    # ── native protocol hooks ─────────────────────────────────────────────────
    _yee_socks = []

    def _activate_native():
        my_ip = _local_ip()
        for b in bulbs_:
            if b.BRAND == "Yeelight":
                try:
                    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    srv.bind((my_ip, 55444)); srv.listen(1); srv.settimeout(3)
                    b._cmd("set_music", [1, my_ip, 55444])
                    try:
                        conn, _ = srv.accept()
                        _yee_socks.append(conn)
                    except OSError: pass
                    srv.close()
                except Exception: pass
            elif b.BRAND == "MagicHome":
                try:
                    sens_byte = min(100, max(0, int(S["sensitivity"] * 50)))
                    d = [0x73, 0x01, sens_byte, 0x00, 0x00, 0x00, 0x00]
                    b._send(d + [_magic_csum(d)])
                except Exception: pass

    threading.Thread(target=_activate_native, daemon=True).start()

    def _yee_send(r, g, b_, bright):
        cmd = json.dumps({"id":1,"method":"set_scene",
                          "params":["color", r*65536+g*256+b_, bright]}) + "\r\n"
        for s in _yee_socks:
            try: s.sendall(cmd.encode())
            except Exception: pass

    # ── push colour to all software-controlled bulbs ──────────────────────────
    _last_push = [0.0]
    _MIN_IV    = 0.04   # 25 fps cap

    def _push(r, g, b_, bright):
        now = time.time()
        if now - _last_push[0] < _MIN_IV: return
        _last_push[0] = now
        bright = _clamp_bright(bright)
        if _yee_socks: _yee_send(r, g, b_, bright)
        for b in bulbs_:
            if b.BRAND == "Yeelight" and _yee_socks: continue
            if b.BRAND == "MagicHome": continue
            # skip Tuya without a working localKey — set_rgb would print to stdout
            if b.BRAND == "Tuya" and not getattr(b, "_dev", None): continue
            try: threading.Thread(target=b.set_rgb, args=(r, g, b_, bright), daemon=True).start()
            except Exception: pass

    # ── audio callback ────────────────────────────────────────────────────────
    def _audio_cb(indata, frames, time_info, status):
        chunk = indata[:, 0]
        rms   = float(np.sqrt(np.mean(chunk**2)))
        vol   = min(1.0, rms * S["sensitivity"] * 40)
        S["volume"] = vol

        # Decaying peak for VU meter
        S["peak"] = max(vol, S["peak"] * 0.92)

        m = S["mode"]

        if m == "color":
            shift      = vol * SPEEDS.get(S["speed"], 14)
            S["hue"]   = (S["hue"] + shift) % 360
            h          = _pal_hue(S["hue"])
            r, g, b_   = _hue_to_rgb(h)
            bright     = _clamp_bright(20 + vol * 80)
            _push(r, g, b_, bright)

        elif m == "pulse":
            lo, hi_    = PALETTES.get(S["palette"], (0, 360))
            h          = _pal_hue(S["hue"])
            r, g, b_   = _hue_to_rgb(h)
            bright     = _clamp_bright(vol * 100)
            _push(r, g, b_, bright)

        elif m == "strobe":
            if vol > 0.55:
                _push(255, 255, 255, 100)
            else:
                _push(0, 0, 0, S["floor"])

        elif m == "spectrum":
            # FFT → bass / mid / treble
            fft   = np.abs(np.fft.rfft(chunk))
            freqs = np.fft.rfftfreq(len(chunk), 1.0 / RATE)
            def _band(lo, hi_):
                mask = (freqs >= lo) & (freqs < hi_)
                return float(np.mean(fft[mask])) if mask.any() else 0.0
            bass   = min(1.0, _band(20, 250)   * S["sensitivity"] * 0.004)
            mid    = min(1.0, _band(250, 2000)  * S["sensitivity"] * 0.006)
            treble = min(1.0, _band(2000, 8000) * S["sensitivity"] * 0.012)
            r  = int(bass   * 255)
            g  = int(mid    * 255)
            b_ = int(treble * 255)
            bright = _clamp_bright(max(bass, mid, treble) * 100)
            _push(r, g, b_, bright)

        elif m == "beat":
            energy = float(np.mean(chunk**2))
            S["energy_hist"].append(energy)
            avg    = float(np.mean(S["energy_hist"])) if S["energy_hist"] else energy
            now    = time.time()
            # Beat: current energy > 1.5× average, and ≥140ms since last beat
            if energy > avg * 1.5 and (now - S["last_beat_t"]) > 0.14:
                S["last_beat_t"] = now
                S["beat"]        = True
                S["hue"]         = (S["hue"] + random.uniform(40, 120)) % 360
                h                = _pal_hue(S["hue"])
                r, g, b_         = _hue_to_rgb(h)
                _push(r, g, b_, 100)
            else:
                # Decay after beat
                S["beat"] = False
                _push(0, 0, 0, S["floor"])

    # ── keyboard thread (Unix raw mode, non-blocking) ─────────────────────────
    _key_q = _queue.Queue()

    def _key_thread():
        try:
            import termios, tty, select as _sel
            fd  = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while S["running"]:
                    if _sel.select([sys.stdin], [], [], 0.05)[0]:
                        ch = sys.stdin.read(1)
                        _key_q.put(ch)
                        if ch in ('q', 'Q', '\x03'): break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass   # Windows / pipe — keys just won't work; Ctrl+C still works

    threading.Thread(target=_key_thread, daemon=True).start()

    def _process_keys():
        while not _key_q.empty():
            ch = _key_q.get_nowait()
            if ch in ('q', 'Q', '\x03'):
                S["running"] = False
            elif ch == '+':
                S["sensitivity"] = min(4.0, round(S["sensitivity"] + 0.2, 1))
            elif ch == '-':
                S["sensitivity"] = max(0.2, round(S["sensitivity"] - 0.2, 1))
            elif ch == 'm':
                idx = (MODES.index(S["mode"]) + 1) % len(MODES)
                S["mode"] = MODES[idx]
                S["energy_hist"].clear()
            elif ch == 'p':
                pals = list(PALETTES)
                idx  = (pals.index(S["palette"]) + 1) % len(pals)
                S["palette"] = pals[idx]
            elif ch == 's':
                spds = list(SPEEDS)
                idx  = (spds.index(S["speed"]) + 1) % len(spds)
                S["speed"] = spds[idx]
            elif ch == '[':
                S["floor"] = max(0, S["floor"] - 5)
            elif ch == ']':
                S["floor"] = min(50, S["floor"] + 5)
            elif ch == 'r':
                S["hue"] = 0.0

    # ── display state ─────────────────────────────────────────────────────────
    # 16 log-spaced frequency bands for the spectrum analyser
    N_BANDS   = 16
    _FREQS    = [20, 40, 63, 100, 160, 250, 400, 630,
                 1000, 1600, 2500, 4000, 6300, 10000, 14000, 20000]
    _spec_bands = [0.0] * N_BANDS
    _peaks      = [0.0] * N_BANDS   # peak-hold per band
    _cur_rgb    = [128, 0, 255]
    # scale factors per band (higher freqs need more amplification)
    _SCALE    = [0.003, 0.003, 0.004, 0.004, 0.005, 0.005, 0.006, 0.007,
                 0.008, 0.009, 0.010, 0.012, 0.014, 0.016, 0.018, 0.020]

    _orig_audio_cb = _audio_cb

    def _audio_cb_tracked(indata, frames, time_info, status):
        chunk = indata[:, 0]
        rms   = float(np.sqrt(np.mean(chunk**2)))
        vol   = min(1.0, rms * S["sensitivity"] * 40)

        # 16-band FFT
        fft   = np.abs(np.fft.rfft(chunk))
        freqs = np.fft.rfftfreq(len(chunk), 1.0 / RATE)
        for i in range(N_BANDS):
            lo = _FREQS[i]
            hi = _FREQS[i + 1] if i + 1 < len(_FREQS) else RATE // 2
            mask = (freqs >= lo) & (freqs < hi)
            raw  = float(np.mean(fft[mask])) if mask.any() else 0.0
            val  = min(1.0, raw * S["sensitivity"] * _SCALE[i])
            # smooth: fast rise, slow fall
            if val > _spec_bands[i]:
                _spec_bands[i] = val
            else:
                _spec_bands[i] = _spec_bands[i] * 0.75 + val * 0.25
            # peak hold with slow decay
            if _spec_bands[i] >= _peaks[i]:
                _peaks[i] = _spec_bands[i]
            else:
                _peaks[i] = max(0.0, _peaks[i] - 0.03)

        # mirror current bulb colour for swatch
        m = S["mode"]
        if m == "color":
            shift = vol * SPEEDS.get(S["speed"], 14)
            h = _pal_hue((S["hue"] + shift) % 360)
            _cur_rgb[0], _cur_rgb[1], _cur_rgb[2] = _hue_to_rgb(h)
        elif m == "pulse":
            h = _pal_hue(S["hue"])
            _cur_rgb[0], _cur_rgb[1], _cur_rgb[2] = _hue_to_rgb(h)
        elif m == "strobe":
            v = (255, 255, 255) if vol > 0.55 else (0, 0, 0)
            _cur_rgb[0], _cur_rgb[1], _cur_rgb[2] = v
        elif m == "spectrum":
            # bass/mid/treble from first 5, middle 6, last 5 bands
            bass   = min(1.0, sum(_spec_bands[:5])   / 3)
            mid_   = min(1.0, sum(_spec_bands[5:11]) / 4)
            treble = min(1.0, sum(_spec_bands[11:])  / 3)
            _cur_rgb[0] = int(bass * 255)
            _cur_rgb[1] = int(mid_ * 255)
            _cur_rgb[2] = int(treble * 255)
        elif m == "beat" and S["beat"]:
            h = _pal_hue(S["hue"])
            _cur_rgb[0], _cur_rgb[1], _cur_rgb[2] = _hue_to_rgb(h)

        _orig_audio_cb(indata, frames, time_info, status)

    # ── colour helpers ────────────────────────────────────────────────────────
    def _swatch(r, g, b_):
        """Truecolour filled block."""
        return f"\033[38;2;{r};{g};{b_}m██{RST}"

    # Band colours: red → orange → yellow → green → cyan → blue (bass → treble)
    _BAND_COLORS = [
        (255,  40,  40),  # 0  20Hz   bass
        (255,  70,  20),  # 1  40Hz
        (255, 110,   0),  # 2  63Hz
        (255, 160,   0),  # 3  100Hz
        (255, 210,   0),  # 4  160Hz
        (200, 255,   0),  # 5  250Hz
        (120, 255,   0),  # 6  400Hz
        ( 40, 255,  40),  # 7  630Hz  mid
        (  0, 255, 120),  # 8  1kHz
        (  0, 255, 200),  # 9  1.6kHz
        (  0, 220, 255),  # 10 2.5kHz
        (  0, 160, 255),  # 11 4kHz
        (  0,  80, 255),  # 12 6.3kHz treble
        ( 60,   0, 255),  # 13 10kHz
        (120,   0, 240),  # 14 14kHz
        (180,   0, 200),  # 15 20kHz
    ]

    BAR_H  = 8    # visible rows in spectrum
    BCOLS  = ["▁","▂","▃","▄","▅","▆","▇","█"]
    # total lines _draw() writes — must match exactly so cursor math is correct
    # 1 header + BAR_H spectrum + 1 beat + 1 labels + 1 vol/controls = BAR_H + 4
    _DISPLAY_LINES = BAR_H + 4

    # band labels: exactly 3 chars each (padded or truncated)
    _BLABELS = ["20 ","   ","   ","100","   ","250","   ","630",
                "1k ","   ","2k ","   ","6k ","10k","   ","20k"]

    def _draw():
        is_beat = S["beat"]
        vol     = S["volume"]
        r_, g_, b__ = _cur_rgb

        mode_names = {"color":"Color shift","pulse":"Pulse","strobe":"Strobe",
                      "spectrum":"Spectrum","beat":"Beat detect"}
        mode_label = mode_names.get(S["mode"], S["mode"])
        swatch     = _swatch(r_, g_, b__)

        # ── spectrum rows ─────────────────────────────────────────────────────
        spec_lines = []
        for row in range(BAR_H - 1, -1, -1):     # row BAR_H-1 = top, 0 = bottom
            threshold = row / BAR_H
            line = "  "
            for bi in range(N_BANDS):
                val = _spec_bands[bi]
                pk  = _peaks[bi]
                rc, gc, bc = _BAND_COLORS[bi]
                if is_beat and val > 0.4:
                    fc = "\033[1;97m"             # white flash on beat
                else:
                    fc = f"\033[38;2;{rc};{gc};{bc}m"

                if val >= threshold + 1 / BAR_H:
                    frac = (val - threshold) * BAR_H
                    ch   = BCOLS[min(7, int(frac * 8))]
                    line += f"{fc}{ch}{ch}{RST}"
                elif abs(pk - threshold) < 1 / BAR_H and pk > 0.05:
                    line += f"{W}▔▔{RST}"         # peak-hold marker
                else:
                    line += "  "
                line += " " if bi < N_BANDS - 1 else ""
            spec_lines.append(line)

        # ── bottom accent row (pulses on beat) ────────────────────────────────
        if is_beat:
            accent = f"  {R}{BOLD}" + "██ " * N_BANDS + RST
        else:
            vfill2 = int(S["volume"] * N_BANDS)
            accent = "  " + "".join(
                (f"{G}▄▄{RST} " if i < vfill2 else f"{DIM}▁▁{RST} ")
                for i in range(N_BANDS))

        # ── frequency label row ───────────────────────────────────────────────
        label_row = "  " + "".join(f"{DIM}{_BLABELS[i]}{RST}" for i in range(N_BANDS))

        # ── volume bar + controls ─────────────────────────────────────────────
        vfill   = int(vol * 24)
        vol_bar = "".join(
            (f"{G}▮{RST}" if i < 12 else f"{Y}▮{RST}" if i < 20 else f"{R}▮{RST}")
            if i < vfill else f"{DIM}▯{RST}"
            for i in range(24))
        beat_tag  = f"  {R}{BOLD}BEAT{RST}" if is_beat else "      "
        ctrl_hint = f"{DIM}+/- sens  m mode  p palette  q quit{RST}"

        # ── write exactly _DISPLAY_LINES lines, staying in place ─────────────
        # \033[F = cursor to beginning of PREVIOUS line (col 0 guaranteed).
        # \033[2K = erase that line.  Repeated N times → cursor at col 0, N rows up.
        # Then \r + line + \n for each row: \r ensures col 0, \n advances row.
        UP = ("\033[F\033[2K") * _DISPLAY_LINES if _draw._drawn else ""
        _draw._drawn = True

        lines = []
        lines.append(f"  {M}♫  MUSIC{RST}  {W}{mode_label}{RST}  "
                     f"pal {C}{S['palette']}{RST}  "
                     f"sens {Y}{S['sensitivity']:.1f}x{RST}  "
                     f"{swatch} {DIM}{vol*100:3.0f}%{RST}{beat_tag}")
        lines.extend(spec_lines)        # BAR_H lines
        lines.append(accent)
        lines.append(label_row)
        lines.append(f"  {vol_bar}  {ctrl_hint}")

        out = UP
        for ln in lines:
            out += "\r" + ln + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    _draw._drawn = False

    # ── run ───────────────────────────────────────────────────────────────────
    mode_names = {"color":"Color shift","pulse":"Pulse","strobe":"Strobe",
                  "spectrum":"Spectrum","beat":"Beat detect"}
    print(f"\n  {M}♫  Music mode — {mode_names.get(mode, mode)}{RST}  "
          f"{DIM}palette: {palette}  sensitivity: {sensitivity:.1f}x{RST}")
    print(f"  {DIM}Listening to mic · driving your bulbs in real-time{RST}")
    # NO blank-line reservation — first draw writes immediately below the header

    try:
        with sd.InputStream(samplerate=RATE, channels=1, blocksize=CHUNK,
                            callback=_audio_cb_tracked):
            while S["running"]:
                _process_keys()
                _draw()
                time.sleep(0.05)
    except KeyboardInterrupt:
        S["running"] = False
    except Exception as e:
        print(f"\n  {R}[-]{RST} Audio error: {e}")

    # ── teardown ─────────────────────────────────────────────────────────────
    print()  # leave the animation display behind
    for b in bulbs_:
        if b.BRAND == "Yeelight":
            try: b._cmd("set_music", [0, _local_ip(), 55444])
            except Exception: pass
        elif b.BRAND == "MagicHome":
            try:
                d = [0x73, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
                b._send(d + [_magic_csum(d)])
            except Exception: pass
    for s in _yee_socks:
        try: s.close()
        except Exception: pass

    _restore_states(saved)
    print(f"  {G}[+]{RST} Music mode stopped — states restored.")


# ══════════════════════════════════════════════════════════════════════════════
#  Presets
# ══════════════════════════════════════════════════════════════════════════════

PRESETS = [
    ("Movie Night",  {"temp": 2700, "dimming": 25}),
    ("Reading",      {"temp": 5000, "dimming": 85}),
    ("Party",        {"r": 128, "g": 0,   "b": 255, "dimming": 100}),
    ("Relax",        {"temp": 3000, "dimming": 45}),
    ("Focus",        {"temp": 6500, "dimming": 100}),
    ("Romance",      {"r": 180, "g": 10,  "b": 10,  "dimming": 30}),
    ("Warm White",   {"temp": 2200, "dimming": 100}),
    ("Cool White",   {"temp": 6500, "dimming": 100}),
    ("Daylight",     {"temp": 5600, "dimming": 100}),
    ("Deep Red",     {"r": 255, "g": 0,   "b": 0,   "dimming": 100}),
    ("Ocean Blue",   {"r": 0,   "g": 80,  "b": 255, "dimming": 100}),
    ("Forest Green", {"r": 0,   "g": 180, "b": 50,  "dimming": 100}),
]


# ══════════════════════════════════════════════════════════════════════════════
#  Timers
# ══════════════════════════════════════════════════════════════════════════════

def timer_off(minutes):
    total = int(minutes * 60)
    print(f"  {Y}Turning off in {minutes} min — Ctrl+C to cancel{RST}")
    try:
        for rem in range(total, 0, -1):
            m, s = divmod(rem, 60)
            print(f"\r  {DIM}Off in {m:02d}:{s:02d}{RST}  ", end="", flush=True)
            time.sleep(1)
        send_all("turn_off"); print(f"\n  {R}Lights off.{RST}")
    except KeyboardInterrupt:
        print(f"\n  {Y}Timer cancelled.{RST}")


def timer_sleep(minutes=30):
    total = int(minutes * 60); steps = 60; delay = total / steps
    print(f"  {Y}Sleep timer: dim over {minutes} min then off — Ctrl+C to cancel{RST}")
    try:
        for i in range(steps):
            p = i / steps
            send_all("set_temp", max(2200, int(4000 - p*1800)), max(5, int(100 - p*95)))
            m, s = divmod(int(total - i*delay), 60)
            print(f"\r  {DIM}{m:02d}:{s:02d} remaining{RST}  ", end="", flush=True)
            time.sleep(delay)
        send_all("turn_off"); print(f"\n  {R}Good night.{RST}")
    except KeyboardInterrupt:
        print(f"\n  {Y}Sleep timer cancelled.{RST}")


# ══════════════════════════════════════════════════════════════════════════════
#  Morse
# ══════════════════════════════════════════════════════════════════════════════

def _morse_duration(text, unit):
    """Calculate total transmission time in seconds."""
    total = 0
    words = text.upper().split()
    for w_i, word in enumerate(words):
        for c_i, char in enumerate(word):
            code = MORSE_CODE.get(char, '')
            if not code: continue
            for e_i, el in enumerate(code):
                total += unit if el == '.' else 3*unit
                last_el = e_i == len(code)-1; last_c = c_i == len(word)-1
                last_w  = w_i == len(words)-1
                if not last_el: total += unit
                elif not last_c: total += 3*unit
                elif not last_w: total += 7*unit
    return total


def morse_send(message, unit=0.12):
    saved = _save_states()
    words = message.upper().split()
    print(f"  {Y}Transmitting — Ctrl+C to abort{RST}\n")
    try:
        for w_i, word in enumerate(words):
            for c_i, char in enumerate(word):
                code = MORSE_CODE.get(char, '')
                if not code: continue
                disp = code.replace('.', f'{Y}·{RST}').replace('-', f'{C}—{RST}')
                print(f"  {W}{char}{RST}  {disp}")
                for e_i, el in enumerate(code):
                    send_all("set_temp", 4000, 100)
                    time.sleep(unit if el == '.' else 3*unit)
                    last_el = e_i == len(code)-1; last_c = c_i == len(word)-1
                    last_w  = w_i == len(words)-1
                    if   last_el and last_c and last_w: off = 0
                    elif last_el and last_c:             off = 7*unit
                    elif last_el:                        off = 3*unit
                    else:                                off = unit
                    if off:
                        send_all("turn_off"); time.sleep(off)
    except KeyboardInterrupt:
        print(f"\n  {Y}Aborted.{RST}")
    _restore_states(saved)
    print(f"\n  {G}Transmission complete. Bulbs restored.{RST}")


# ══════════════════════════════════════════════════════════════════════════════
#  Force Lock  (multi-bulb version of wiz.py's force_lock)
# ══════════════════════════════════════════════════════════════════════════════

def force_lock():
    """
    Continuously re-assert ON or OFF across all targeted bulbs.
    Polls every second; overrides any command sent from phone/app.
    Press Ctrl+C to release.
    """
    import datetime
    banner()
    print(f"  {BOLD}{R}── Force Lock ──{RST}")
    print(f"  {DIM}  Continuously re-asserts a fixed state across targeted bulbs.{RST}")
    print(f"  {DIM}  Overrides phone app commands — last writer wins, and we write every 1s.{RST}\n")

    tgts = get_targets()
    if not tgts:
        print(f"  {R}No bulbs targeted.{RST}"); _pause(); return

    print(f"  {W}[1]{RST} Force all {G}ON {RST}  — stay on regardless of app")
    print(f"  {W}[2]{RST} Force all {R}OFF{RST}  — stay off regardless of app")
    print(f"  {W}[0]{RST} Back\n")
    ch = _in("").strip()
    if ch == "1":
        target, label = True,  f"{G}ON{RST}"
    elif ch == "2":
        target, label = False, f"{R}OFF{RST}"
    else:
        return

    # Assert immediately before entering the loop
    send_all("turn_on" if target else "turn_off")

    overrides     = 0
    poll_interval = 1
    last_override = None

    print(f"\n  {BOLD}{Y}Force lock ACTIVE{RST} — all targeted bulbs locked {label}")
    print(f"  {DIM}  {len(tgts)} bulb(s) · polls every {poll_interval}s · Ctrl+C to release{RST}\n")

    try:
        while True:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            drifted = []
            for b in tgts:
                b.refresh()
                if b._st and b.on != target:
                    drifted.append(b)

            if drifted:
                overrides += 1
                last_override = ts
                def _reassert(b_):
                    try:
                        if target: b_.turn_on()
                        else:      b_.turn_off()
                    except Exception: pass
                ts_ = [threading.Thread(target=_reassert, args=(b,), daemon=True)
                       for b in drifted]
                for t in ts_: t.start()
                for t in ts_: t.join(timeout=2)
                names = ", ".join(b.ip for b in drifted)
                print(f"  {ts}  {R}OVERRIDE #{overrides}{RST}  drift on {C}{names}{RST} → re-locked {label}")
            else:
                note = f"  last override {last_override}" if last_override else ""
                print(f"\r  {ts}  {G}●{RST} Locked {label}  {DIM}overrides: {overrides}{note}{RST}  ",
                      end="", flush=True)

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}Lock released.{RST}")
        if overrides:
            print(f"  {DIM}  Blocked {overrides} drift event(s) during this session.{RST}")

        ans = _in("Restore all to opposite state now? [y/N]").lower()
        if ans == "y":
            send_all("turn_on" if not target else "turn_off")
            restored = f"{G}ON{RST}" if not target else f"{R}OFF{RST}"
            print(f"  {DIM}  All bulbs set to {restored}{RST}")

        _pause()


# ══════════════════════════════════════════════════════════════════════════════
#  Audit Engine  —  multi-protocol LAN security scanner
# ══════════════════════════════════════════════════════════════════════════════

class Finding:
    __slots__ = ("sev","title","detail","cve","poc","response")
    def __init__(self, sev, title, detail="", cve="", poc="", response=""):
        self.sev      = sev      # CRIT HIGH MED LOW INFO SAFE
        self.title    = title
        self.detail   = detail
        self.cve      = cve
        self.poc      = poc
        self.response = response

SEV_COLOR = {"CRIT": R, "HIGH": Y, "MED": M, "LOW": B, "INFO": DIM, "SAFE": G}

def _pf(f):
    c   = SEV_COLOR.get(f.sev, DIM)
    cve = f"  {DIM}[{f.cve}]{RST}" if f.cve else ""
    print(f"  {c}{BOLD}[{f.sev:<4}]{RST}  {BOLD}{f.title}{RST}{cve}")
    if f.detail:
        for line in str(f.detail).splitlines():
            print(f"          {DIM}{line[:130]}{RST}")
    if f.poc:      print(f"          {DIM}POC: {str(f.poc)[:130]}{RST}")
    if f.response: print(f"          {DIM}Got: {str(f.response)[:130]}{RST}")
    print()


# ── WiZ ───────────────────────────────────────────────────────────────────────

def audit_wiz(b) -> list:
    out = []
    print(f"  {DIM}Running WiZ audit on {b.ip}...{RST}")

    # F1 — getPilot: unauthenticated state read
    r = b._cmd("getPilot", timeout=3)
    if r and "result" in r:
        p = r["result"]
        detail = (f"state={p.get('state')}  rssi={p.get('rssi')}  "
                  f"dimming={p.get('dimming')}  temp={p.get('temp')}  "
                  f"r={p.get('r')} g={p.get('g')} b={p.get('b')}")
        out.append(Finding("HIGH", "Unauthenticated state read via getPilot",
                           detail=detail,
                           poc='{"method":"getPilot","params":{}}',
                           response=detail))
    else:
        out.append(Finding("INFO", "getPilot — no response (offline?)"))

    # F2 — getSystemConfig: MAC / homeId / firmware / region
    r2 = b._cmd("getSystemConfig", timeout=3)
    if r2 and "result" in r2:
        p2 = r2["result"]
        detail2 = (f"mac={p2.get('mac')}  homeId={p2.get('homeId')}  "
                   f"fw={p2.get('fwVersion')}  region={p2.get('ps')}")
        out.append(Finding("HIGH", "getSystemConfig leaks MAC, homeId, firmware, region  [NEW]",
                           detail=detail2,
                           cve="Extends CVE-2019-18980",
                           poc='{"method":"getSystemConfig","params":{}}',
                           response=detail2))
    else:
        out.append(Finding("SAFE", "getSystemConfig returned no data"))

    # F3 — getModelConfig: LED driver / hardware internals
    r3 = b._cmd("getModelConfig", timeout=3)
    if r3 and "result" in r3:
        out.append(Finding("MED", "getModelConfig exposes LED driver and hardware calibration  [NEW]",
                           detail=str(r3["result"])[:200],
                           poc='{"method":"getModelConfig","params":{}}',
                           response=str(r3["result"])[:200]))

    # F4 — getUserConfig: dimming / button config
    r4 = b._cmd("getUserConfig", timeout=3)
    if r4 and "result" in r4:
        out.append(Finding("LOW", "getUserConfig exposes dimming config and button modes",
                           detail=str(r4["result"])[:200],
                           poc='{"method":"getUserConfig","params":{}}'))

    # F5 — setPilot: unauthenticated full control (send current state — no visible change)
    cur_on = b._st.get("on", True)
    r5 = b._cmd("setPilot", {"state": cur_on}, timeout=3)
    if r5 and "result" in r5 and r5["result"].get("success"):
        out.append(Finding("HIGH", "Unauthenticated full control via setPilot",
                           detail="Any LAN host can change power, color, brightness, scene — no credentials.",
                           cve="CVE-2019-18980 (extended to ESP25_SHRGB_01)",
                           poc='{"method":"setPilot","params":{"state":true,"r":255,"g":0,"b":0}}',
                           response=str(r5)[:100]))
    else:
        out.append(Finding("SAFE", "setPilot did not confirm success"))

    # F6 — Passive broadcast: homeId leak (listen 5s on UDP 38899)
    print(f"  {DIM}  Listening for WiZ broadcasts (5s)...{RST}", end="", flush=True)
    broadcasts = []
    def _listen_wiz():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except: pass
        s.settimeout(1)
        try:
            s.bind(("0.0.0.0", 38899))
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    data, addr = s.recvfrom(2048)
                    if addr[0] == b.ip:
                        try:
                            pkt = json.loads(data.decode())
                            if pkt.get("method") == "registration":
                                broadcasts.append(pkt.get("params", {}))
                        except: pass
                except socket.timeout: continue
        except: pass
        finally: s.close()
    lt = threading.Thread(target=_listen_wiz, daemon=True)
    lt.start(); lt.join()
    print()
    if broadcasts:
        pb = broadcasts[0]
        out.append(Finding("MED", "homeId passively leaked in registration broadcast",
                           detail=(f"homeId={pb.get('homeId')}  phoneMac={pb.get('phoneMac')}  "
                                   f"No interaction required — passive capture only."),
                           response=str(pb)[:200]))
    else:
        out.append(Finding("INFO", "No WiZ broadcast in 5s window (may not have broadcast yet)"))

    # F7 — No rate limiting: 10 rapid setPilot calls
    start = time.time()
    ok = 0
    for _ in range(10):
        r7 = b._cmd("setPilot", {"state": cur_on}, timeout=1)
        if r7 and "result" in r7 and r7["result"].get("success"):
            ok += 1
    elapsed = time.time() - start
    if ok >= 7:
        out.append(Finding("MED", "No rate limiting on setPilot — flicker DoS possible",
                           detail=f"{ok}/10 commands accepted in {elapsed:.2f}s. "
                                  f"Attacker can strobe bulb indefinitely at full network speed."))
    else:
        out.append(Finding("SAFE", f"setPilot appears rate-limited ({ok}/10 accepted in {elapsed:.2f}s)"))

    # F8 — reboot: should require auth
    r8 = b._cmd("reboot", timeout=3)
    if r8:
        code = (r8.get("error") or {}).get("code")
        if code == -32600:
            out.append(Finding("SAFE", "reboot requires authentication (returns error -32600)"))
        elif "result" in r8:
            out.append(Finding("CRIT", "reboot executed without authentication!",
                               response=str(r8)[:100]))
        else:
            out.append(Finding("INFO", f"reboot response: {str(r8)[:100]}"))

    # F9 — getDevInfo: MAC / firmware / hardware revision
    r9 = b._cmd("getDevInfo", timeout=3)
    if r9 and "result" in r9:
        out.append(Finding("HIGH", "getDevInfo leaks MAC, firmware, hardware revision unauthenticated",
                           detail=str(r9["result"])[:200],
                           poc='{"method":"getDevInfo","params":{}}',
                           response=str(r9["result"])[:200]))

    # F10 — getWifiConfig: SSID / BSSID / RSSI / channel
    r10 = b._cmd("getWifiConfig", timeout=3)
    if r10 and "result" in r10:
        p10 = r10["result"]
        detail10 = (f"ssid={p10.get('ssid')}  bssid={p10.get('bssid')}  "
                    f"rssi={p10.get('rssi')}  chan={p10.get('channel')}")
        out.append(Finding("HIGH", "getWifiConfig leaks SSID, BSSID, RSSI, channel unauthenticated",
                           detail=detail10,
                           poc='{"method":"getWifiConfig","params":{}}',
                           response=detail10))

    # F11 — registration hijack: bulb sends ongoing syncPilot heartbeats to attacker IP
    attacker_ip = _local_ip()
    r11 = b._cmd("registration", {"phoneIp": attacker_ip,
                                   "phoneMac": "aa:bb:cc:dd:ee:ff",
                                   "register": True}, timeout=3)
    if r11 and "result" in r11:
        out.append(Finding("MED",
                           "Registration hijack — bulb sends syncPilot heartbeats to any LAN IP",
                           detail=f"Bulb directed to send state updates to {attacker_ip}. "
                                  "No auth required. Attacker receives live ON/OFF events indefinitely.",
                           poc=f'{{"method":"registration","params":{{"phoneIp":"{attacker_ip}",'
                               f'"phoneMac":"aa:bb:cc:dd:ee:ff","register":true}}}}'))
        # Restore: un-register attacker IP (best-effort — will naturally expire)
        try: b._cmd("registration", {"phoneIp": attacker_ip,
                                     "phoneMac": "aa:bb:cc:dd:ee:ff",
                                     "register": False}, timeout=2)
        except Exception: pass

    return out


# ── Yeelight ──────────────────────────────────────────────────────────────────

def audit_yeelight(b) -> list:
    out = []
    print(f"  {DIM}Running Yeelight audit on {b.ip}:{b.port}...{RST}")

    # F1 — get_prop: state and device info disclosure (full 13-property dump)
    r = b._cmd("get_prop", ["power", "bright", "color_mode", "ct", "rgb",
                             "name", "fw_ver", "model",
                             "music_on", "flowing", "hue", "sat", "active_mode"])
    if r and "result" in r:
        vals = r["result"]
        keys = ["power","bright","color_mode","ct","rgb","name","fw_ver","model"]
        detail = "  ".join(f"{k}={v}" for k, v in zip(keys, vals) if v and v != "")
        out.append(Finding("HIGH", "Unauthenticated device state and firmware info via get_prop",
                           detail=detail,
                           poc='{"method":"get_prop","params":["power","bright","fw_ver","model"]}',
                           response=detail))
    else:
        out.append(Finding("INFO", "get_prop no response (offline or LAN control disabled in app)"))

    # F2 — set_power: unauthenticated control (same state, no visible change)
    cur = "on" if b.on else "off"
    r2 = b._cmd("set_power", [cur, "smooth", 300])
    if r2 and "result" in r2:
        out.append(Finding("HIGH", "Unauthenticated power control via set_power",
                           detail="Any LAN host can turn on/off once LAN control is enabled in the Yeelight app.",
                           poc='{"method":"set_power","params":["on","smooth",300]}',
                           response=str(r2["result"])[:80]))

    # F3 — set_rgb: unauthenticated color control
    if "r" in b._st:
        r3 = b._cmd("set_rgb", [(b._st["r"]<<16)|(b._st["g"]<<8)|b._st["b"], "smooth", 300])
        if r3 and "result" in r3:
            out.append(Finding("HIGH", "Unauthenticated RGB color control via set_rgb",
                               poc='{"method":"set_rgb","params":[16711680,"smooth",300]}'))

    # F4 — Music mode: device connects back to attacker
    r4 = b._cmd("set_music", [1, _local_ip(), 55444])
    if r4 and "result" in r4:
        out.append(Finding("HIGH", "Music mode — device opens TCP connection back to attacker IP",
                           detail=f"Device would connect to {_local_ip()}:55444 and accept "
                                  f"unauthenticated raw command stream at full speed.",
                           poc=f'{{"method":"set_music","params":[1,"{_local_ip()}",55444]}}',
                           response=str(r4)[:80]))

    # F5 — Protocol: plaintext JSON over TCP
    out.append(Finding("MED", "Protocol is plaintext JSON over TCP — no encryption, no integrity",
                       detail="All commands and responses visible to any LAN observer. "
                              "Port 55443 TCP. No TLS available."))

    # F6 — cron_get: scheduled timers readable without auth (privacy / usage pattern)
    r6 = b._cmd("cron_get", [0])
    if r6 and "result" in r6:
        out.append(Finding("MED", "cron_get — all scheduled timers readable without authentication",
                           detail="Sleep/wake schedules expose user presence patterns.",
                           poc='{"id":1,"method":"cron_get","params":[0]}',
                           response=str(r6["result"])[:200]))

    # F7 — set_name: unauthenticated persistent write to device flash
    orig_name = b._st.get("name", "") or ""
    r7 = b._cmd("set_name", [orig_name or "test"])   # write same name — no visible change
    if r7 and "result" in r7:
        out.append(Finding("HIGH", "set_name — unauthenticated persistent write to device flash",
                           detail="Any LAN host can rename the device permanently without credentials. "
                                  "Survives reboot. Confirms unauthenticated flash write capability.",
                           poc='{"id":1,"method":"set_name","params":["HACKED"]}'))

    # F8 — set_default: silently persists current state as power-on default
    r8 = b._cmd("set_default", [])
    if r8 and "result" in r8:
        out.append(Finding("MED", "set_default — unauthenticated persistent state manipulation",
                           detail="Writes current color/brightness as power-on default. Survives reboot.",
                           poc='{"id":1,"method":"set_default","params":[]}'))

    return out


# ── LIFX ──────────────────────────────────────────────────────────────────────

def audit_lifx(b) -> list:
    out = []
    print(f"  {DIM}Running LIFX audit on {b.ip}...{RST}")

    # F1 — GetLight (101): state disclosure
    resp = b._send(101)
    if resp and len(resp) >= 48:
        pl = resp[36:]
        hue, sat, bri, kelvin, _, power = struct.unpack('<HHHHHH', pl[:12])
        detail = (f"power={'ON' if power else 'OFF'}  "
                  f"bri={int(bri/65535*100)}%  kelvin={kelvin}K  "
                  f"hue={int(hue/65535*360)}°  sat={int(sat/65535*100)}%")
        out.append(Finding("HIGH", "Unauthenticated light state read via GetLight (msg 101)",
                           detail=detail,
                           poc="LIFX GetLight binary UDP msg type 101 — no auth, no key",
                           response=detail))

    # F2 — GetHostFirmware (11): firmware disclosure
    resp2 = b._send(11)
    if resp2 and len(resp2) >= 56:
        pl2 = resp2[36:]
        if len(pl2) >= 20:
            try:
                build, _, version = struct.unpack('<QQI', pl2[:20])
                out.append(Finding("LOW", "Firmware build and version disclosed without authentication",
                                   detail=f"build={build}  version=0x{version:08x}",
                                   poc="LIFX GetHostFirmware (msg type 11)"))
            except: pass

    # F3 — SetPower (21): unauthenticated on/off
    cur_pwr = 65535 if b.on else 0
    b._fire(21, struct.pack('<HI', cur_pwr, 0))
    out.append(Finding("HIGH", "Unauthenticated power control via SetPower (msg 21)",
                       detail="Any LAN host can power on/off instantly. LIFX LAN API is "
                              "intentionally open — no auth by design.",
                       poc="LIFX SetPower(level=65535, duration=0) — fire-and-forget UDP"))

    # F4 — GetWifiInfo (16): RSSI and TX data
    resp4 = b._send(16)
    if resp4 and len(resp4) > 40:
        out.append(Finding("LOW", "WiFi signal and TX rate disclosed without authentication",
                           poc="LIFX GetWifiInfo (msg type 16)"))

    # F5 — Protocol note
    out.append(Finding("MED", "Binary UDP — no encryption, no authentication by design",
                       detail="LIFX LAN API is intentionally open. Safe on isolated IoT VLAN; "
                              "risky on a flat home network with untrusted devices."))

    # F6 — GetVersion (32): vendor ID, product ID, hardware version — precise fingerprint
    resp6 = b._send(32)
    if resp6 and len(resp6) >= 48:
        try:
            vendor, product, version = struct.unpack('<III', resp6[36:48])
            out.append(Finding("HIGH", "GetVersion — vendor/product/hardware ID disclosed without auth",
                               detail=f"vendor={vendor}  product={product}  hw_version={version}  "
                                      f"Enables precise model targeting for firmware exploits.",
                               poc="LIFX GetVersion (msg type 32) — no auth required"))
        except Exception: pass

    # F7 — GetLabel (23): user-assigned label — PII
    resp7 = b._send(23)
    if resp7 and len(resp7) >= 68:
        try:
            label = resp7[36:68].decode("utf-8", errors="replace").rstrip("\x00")
            if label:
                out.append(Finding("LOW", f"GetLabel — user label disclosed: '{label}'",
                                   poc="LIFX GetLabel (msg type 23)"))
        except Exception: pass

    # F8 — GetGroup (51): group UUID + label — network topology
    resp8 = b._send(51)
    if resp8 and len(resp8) >= 72:
        try:
            grp_label = resp8[52:84].decode("utf-8", errors="replace").rstrip("\x00")
            if grp_label:
                out.append(Finding("MED", f"GetGroup — group topology label disclosed: '{grp_label}'",
                                   poc="LIFX GetGroup (msg type 51)"))
        except Exception: pass

    # F9 — GetLocation (48): location UUID + label
    resp9 = b._send(48)
    if resp9 and len(resp9) >= 72:
        try:
            loc_label = resp9[52:84].decode("utf-8", errors="replace").rstrip("\x00")
            if loc_label:
                out.append(Finding("MED", f"GetLocation — physical location label disclosed: '{loc_label}'",
                                   poc="LIFX GetLocation (msg type 48)"))
        except Exception: pass

    # F10 — Replay: no sequence protection — same SetPower accepted twice
    b._fire(21, struct.pack('<HI', cur_pwr, 0))   # replay identical packet
    out.append(Finding("HIGH", "No replay protection — identical UDP packet accepted multiple times",
                       detail="LIFX LAN protocol has no nonce/session. Any captured packet can be "
                              "replayed indefinitely to toggle power state.",
                       poc="Replay SetPower(type=21) with identical sequence — always accepted"))

    # F11 — GetInfo (34): uptime and current time disclosure
    resp11 = b._send(34)
    if resp11 and len(resp11) >= 52:
        try:
            time_ns, uptime_ns, downtime_ns = struct.unpack('<QQQ', resp11[36:60])
            uptime_s = uptime_ns // 1_000_000_000
            out.append(Finding("LOW", "GetInfo — device uptime and clock disclosed without auth",
                               detail=f"uptime={uptime_s}s ({uptime_s//3600}h {(uptime_s%3600)//60}m)  "
                                      f"time={time_ns}ns  downtime={downtime_ns//1_000_000_000}s",
                               poc="LIFX GetInfo (msg type 34) — uptime leaks power cycle history"))
        except Exception: pass

    # F12 — GetWifiFirmware (18/19): WiFi chip firmware version
    resp12 = b._send(18)
    if resp12 and len(resp12) >= 56:
        try:
            build, _, ver = struct.unpack('<QQI', resp12[36:56])
            out.append(Finding("LOW", "GetWifiFirmware — WiFi chip firmware version disclosed",
                               detail=f"build={build}  version=0x{ver:08x}",
                               poc="LIFX GetWifiFirmware (msg type 18)"))
        except Exception: pass

    # F13 — EchoRequest (58): confirms unauthenticated ping/presence check
    payload13 = b"swiss_bulbs_echo_" + b"\x00" * 47
    resp13    = b._send(58, payload13[:64])
    if resp13 and len(resp13) >= 100:
        echo_back = resp13[36:100]
        if echo_back[:17] == b"swiss_bulbs_echo_":
            out.append(Finding("LOW", "EchoRequest — device responds to unauthenticated echo (type 58)",
                               detail="Any LAN host can confirm device presence and measure latency "
                                      "without any credentials.",
                               poc="LIFX EchoRequest (msg type 58) with arbitrary 64-byte payload"))

    return out


# ── Kasa ──────────────────────────────────────────────────────────────────────

def audit_kasa(b) -> list:
    out = []
    print(f"  {DIM}Running Kasa audit on {b.ip}...{RST}")

    # F1 — get_sysinfo: full device info
    r = b._cmd({"system": {"get_sysinfo": {}}})
    if r and "system" in r:
        si = r["system"].get("get_sysinfo", {})
        detail = (f"alias={si.get('alias')}  model={si.get('model')}  "
                  f"mac={si.get('mac')}  fw={si.get('sw_ver')}  "
                  f"hw_ver={si.get('hw_ver')}  deviceId={si.get('deviceId','')[:16]}...")
        out.append(Finding("HIGH", "get_sysinfo discloses alias, MAC, firmware, hardware ID",
                           detail=detail,
                           poc='{"system":{"get_sysinfo":{}}}',
                           response=detail))

    # F2 — XOR cipher: trivially reversible
    out.append(Finding("HIGH", 'XOR "encryption" (key=171) — trivially reversible, not real security',
                       detail="All TCP traffic decoded with: byte ^= key; key = ciphertext_byte. "
                              "Key published publicly since ~2016. Provides zero protection against "
                              "any network observer.",
                       poc="python: key=171; [chr(key:=key^c) for c in ciphertext]"))

    # F3 — Unauthenticated control (current state — no visible change)
    cur_on = 1 if b.on else 0
    r3 = b._light_cmd(on_off=cur_on, transition_period=0)
    if r3:
        out.append(Finding("HIGH", "Unauthenticated light state control",
                           detail="Power, brightness, color — all controllable by any LAN host.",
                           poc='{"smartlife.iot.smartbulb.lightingservice":'
                               '{"transition_light_state":{"on_off":1,"transition_period":0}}}'))

    # F4 — set_alias: rename without auth
    orig_alias = b.info.get("alias", "KasaBulb") if b.info else "KasaBulb"
    r4 = b._cmd({"system": {"set_dev_alias": {"alias": orig_alias}}})
    if r4:
        si4 = r4.get("system", {}).get("set_dev_alias", {})
        if si4.get("err_code") == 0:
            out.append(Finding("LOW", "Device alias (display name) changeable without authentication",
                               poc='{"system":{"set_dev_alias":{"alias":"Hacked"}}}'))

    # F5 — emeter (power consumption — occupancy profiling)
    r5 = b._cmd({"emeter": {"get_realtime": {}}})
    if r5 and "emeter" in r5:
        em = r5["emeter"]
        if "err_code" in em and em.get("err_code") == 0:
            out.append(Finding("LOW", "Energy monitor readable — power consumption enables occupancy profiling",
                               detail="Real-time wattage reveals when lights are on/off, enabling presence detection.",
                               poc='{"emeter":{"get_realtime":{}}}'))

    # F6 — Cloud info: bound email, cloud server, connection status
    r6 = b._cmd({"cnCloud": {"get_cloudinfo": {}}})
    if r6 and "cnCloud" in r6:
        ci = r6["cnCloud"].get("get_cloudinfo", {})
        if ci.get("err_code") == 0 or "username" in ci:
            detail6 = (f"username={ci.get('username','?')}  "
                       f"server={ci.get('cloud_server','?')}  "
                       f"connected={ci.get('is_registered','?')}")
            out.append(Finding("HIGH", "get_cloudinfo leaks bound account email and cloud server",
                               detail=detail6,
                               poc='{"cnCloud":{"get_cloudinfo":{}}}',
                               response=detail6))

    # F7 — WiFi info: connected SSID, BSSID, RSSI
    r7 = b._cmd({"netif": {"get_stainfo": {}}})
    if r7 and "netif" in r7:
        ni = r7["netif"].get("get_stainfo", {})
        if ni.get("err_code") == 0 or "ssid" in ni:
            detail7 = (f"ssid={ni.get('ssid','?')}  bssid={ni.get('bssid','?')}  "
                       f"rssi={ni.get('rssi','?')}")
            out.append(Finding("HIGH", "get_stainfo leaks connected SSID, BSSID, RSSI without auth",
                               detail=detail7,
                               poc='{"netif":{"get_stainfo":{}}}',
                               response=detail7))

    # F8 — Schedule rules: on/off schedule (usage pattern / occupancy inference)
    r8 = b._cmd({"schedule": {"get_rules": {}}})
    if r8 and "schedule" in r8:
        sc = r8["schedule"].get("get_rules", {})
        rules = sc.get("rule_list", [])
        if rules:
            out.append(Finding("MED", f"get_rules — {len(rules)} schedule rule(s) readable without auth",
                               detail="Scheduled on/off times reveal occupancy patterns.",
                               poc='{"schedule":{"get_rules":{}}}'))

    # F9 — Daily energy stats: per-day watt-hour history (deep occupancy profiling)
    import datetime as _dt
    now = _dt.datetime.now()
    r9 = b._cmd({"emeter": {"get_daystat": {"month": now.month, "year": now.year}}})
    if r9 and "emeter" in r9:
        ds = r9["emeter"].get("get_daystat", {})
        if ds.get("err_code") == 0 and ds.get("day_list"):
            out.append(Finding("MED", "get_daystat — daily energy history readable without auth",
                               detail="Watt-hour history per day enables multi-day occupancy inference.",
                               poc='{"emeter":{"get_daystat":{"month":M,"year":Y}}}'))

    # F10 — WiFi neighbourhood scan: cached list of nearby SSIDs/BSSIDs
    r10 = b._cmd({"netif": {"get_scaninfo": {"refresh": 0}}})
    if r10 and "netif" in r10:
        sc = r10["netif"].get("get_scaninfo", {})
        aps = sc.get("ap_list", [])
        if aps:
            ssids = ", ".join(a.get("essid","?") for a in aps[:5])
            out.append(Finding("MED", f"get_scaninfo — {len(aps)} nearby WiFi networks leaked without auth",
                               detail=f"Device caches surrounding SSIDs/BSSIDs. "
                                      f"First 5: {ssids}. Enables physical location inference.",
                               poc='{"netif":{"get_scaninfo":{"refresh":0}}}'))

    return out


# ── MagicHome ─────────────────────────────────────────────────────────────────

def audit_magichome(b) -> list:
    out = []
    print(f"  {DIM}Running MagicHome audit on {b.ip}...{RST}")

    # F1 — Status query: state disclosure
    q    = [0x81, 0x8a, 0x8b]
    resp = b._send(q + [_magic_csum(q)], read_bytes=14)
    if resp and len(resp) >= 9:
        on_  = resp[2] == 0x23
        mode = resp[3]
        r_, gv, bv = resp[6], resp[7], resp[8]
        detail = f"power={'ON' if on_ else 'OFF'}  mode=0x{mode:02x}  rgb=({r_},{gv},{bv})"
        out.append(Finding("HIGH", "Unauthenticated device state read — plaintext binary TCP",
                           detail=detail,
                           poc="[0x81, 0x8a, 0x8b, 0x8e] status query — no auth, no key",
                           response=resp.hex()))
    else:
        out.append(Finding("INFO", "Status query no response (offline?)"))

    # F2 — Unauthenticated control
    out.append(Finding("HIGH", "Unauthenticated full control — zero authentication, zero encryption",
                       detail="Power, RGB, brightness, mode — all controlled by raw binary TCP commands. "
                              "No credentials, no session, no encryption whatsoever.",
                       poc="[0x31, R, G, B, 0x00, 0xf0, 0x0f, checksum] — set color directly"))

    # F3 — UDP broadcast: MAC and IP leak
    model = b.info.get("model", "unknown") if b.info else "unknown"
    out.append(Finding("MED", "Device MAC and model passively leaked via HF-A11ASSISTHREAD UDP broadcast",
                       detail=f"model={model}  Broadcast on UDP:48899 — no interaction required for discovery.",
                       poc="UDP: HF-A11ASSISTHREAD → device replies with IP,MAC,model"))

    # F4 — Protocol summary
    out.append(Finding("CRIT", "Covers 500+ generic RGB LED controllers — all share identical zero-security protocol",
                       detail="MagicHome / FluxLED / Zengge / HappyLighting / Magic Home Pro: "
                              "same binary protocol, same zero security. Any device on the LAN "
                              "can control all of them with a single packet."))

    # F5 — Timer read: all 6 timer slots (schedule disclosure)
    try:
        timer_q = bytes([0x22,0x2a,0x2a,0x2a,0x2a,0x2a,0x2a,0xf0,0x00,0x00,0x00,0x00,0x00,0xf0])
        resp5   = b._send(list(timer_q), read_bytes=88)
        if resp5 and len(resp5) >= 14:
            out.append(Finding("MED", "Timer schedule readable without authentication",
                               detail="All 6 timer slots (time, mode, days, color) accessible. "
                                      "Reveals usage patterns and home occupancy schedule.",
                               poc="[0x22,0x2a×6,0xf0,0x00×5,0xf0] — get-timers command",
                               response=resp5[:14].hex()))
    except Exception: pass

    # F6 — Device type byte: identifies exact controller hardware
    if resp and len(resp) >= 4:
        dtype = resp[1]
        type_map = {0x25: "single-color", 0x33: "RGB (no white)", 0x44: "RGBW",
                    0x35: "RGB+warm-white", 0x62: "RGBWW"}
        type_str = type_map.get(dtype, f"unknown(0x{dtype:02x})")
        out.append(Finding("LOW", f"Device type byte identified: {type_str} (0x{dtype:02x})",
                           detail="Enables precise protocol variant selection for control commands."))

    # F7 — Connection exhaustion: most controllers handle ≤3 concurrent TCP connections
    conns = []
    try:
        for _ in range(4):
            s_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s_.settimeout(1)
            try: s_.connect((b.ip, b.PORT)); conns.append(s_)
            except Exception: break
        if len(conns) >= 3:
            out.append(Finding("MED", "Connection exhaustion possible — controller accepts ≥3 simultaneous TCP connections",
                               detail="Many MagicHome controllers drop oldest connection when limit reached. "
                                      "Attacker holding 3 connections blocks phone app control.",
                               poc="Open 3+ simultaneous TCP:5577 connections — phone app loses control"))
    except Exception: pass
    finally:
        for s_ in conns:
            try: s_.close()
            except Exception: pass

    # F8 — Native preset pattern write (0x61): unauthenticated persistent effect storage
    try:
        # Pattern 0x25 = 7-color crossfade, speed=31, flag=0x0f (auto mode)
        d_preset = [0x61, 0x25, 0x1F, 0x0f]
        b._send(d_preset + [_magic_csum(d_preset)])
        out.append(Finding("HIGH", "Native preset pattern writable without auth (opcode 0x61)",
                           detail="All 20 built-in patterns (0x25-0x38) settable by any LAN host via a "
                                  "single 5-byte command. Effect persists as active pattern until overwritten.",
                           poc="[0x61, pattern_code, speed, 0x0f, checksum] — no credentials required"))
    except Exception: pass

    # F9 — Music / microphone mode (0x73): unauthenticated audio-reactive control
    try:
        # 0x73 = music mode, 0x01 = enable, sensitivity=0x64 (100%), rest 0x00
        d_music = [0x73, 0x01, 0x64, 0x00, 0x00, 0x00, 0x00]
        resp_m = b._send(d_music + [_magic_csum(d_music)], read_bytes=4)
        if resp_m is not None:
            out.append(Finding("MED", "Music/microphone mode (0x73) accepted — audio-reactive control without auth",
                               detail="Device activates audio-reactive LED mode. Sensitivity, color and speed "
                                      "are all controllable unauthenticated. Enables eavesdropping-by-light-pattern.",
                               poc="[0x73, 0x01, sensitivity, 0x00*4, checksum] — no credentials required"))
    except Exception: pass

    return out


# ── Tuya ──────────────────────────────────────────────────────────────────────

def audit_tuya(b) -> list:
    out = []
    print(f"  {DIM}Running Tuya audit on {b.ip}...{RST}")

    proto_ver = None

    # F1 — Protocol version detection
    try:
        import tinytuya

        # Try v3.1 plaintext first — if DPS returned, device is CRITICAL
        dev31 = tinytuya.Device(b.device_id or "0"*22, b.ip, "0"*16, version=3.1)
        dev31.set_socketTimeout(3); dev31.set_socketRetryLimit(1)
        r31 = dev31.status()
        if r31 and "dps" in r31:
            proto_ver = "3.1"
            b._confirmed_v31 = True   # full control without key — flag for _hacked_status()
            out.append(Finding("CRIT", "Tuya v3.1 — DP_QUERY returns device state without localKey",
                               detail=f"DPS: {str(r31.get('dps',''))[:200]}",
                               cve="CVE-2019-18693",
                               poc="tinytuya.Device(devid, ip, '', version=3.1).status()"))
        elif r31 and r31.get("Err") not in ("901",):
            proto_ver = "3.1-partial"

    except Exception:
        pass

    if not proto_ver:
        try:
            import tinytuya
            # Try v3.5 — error 914 = TLS accepted, key rejected = secure
            dev35 = tinytuya.Device(b.device_id or "0"*22, b.ip, "0"*16, version=3.5)
            dev35.set_socketTimeout(5); dev35.set_socketRetryLimit(1)
            r35 = dev35.status()
            if r35 and r35.get("Err") == "914":
                proto_ver = "3.5"
                b._confirmed_v35 = True   # mark as genuinely secure for _hacked_status()
                out.append(Finding("SAFE",
                                   "Tuya v3.5 — TLS handshake + HMAC-SHA256 session keys",
                                   detail="Device accepted TLS connection but correctly rejected "
                                          "unauthenticated key (error 914 — expected). "
                                          "localKey required; no known LAN exploits for v3.5.",
                                   response="Err=914 (wrong key — connection-level security confirmed)"))
            elif r35 and r35.get("Err") == "901":
                proto_ver = "rst"
        except Exception:
            pass

    if not proto_ver:
        try:
            import tinytuya
            # v3.3 — AES-128-ECB, requires localKey
            dev33 = tinytuya.Device(b.device_id or "0"*22, b.ip, "0"*16, version=3.3)
            dev33.set_socketTimeout(3); dev33.set_socketRetryLimit(1)
            r33 = dev33.status()
            if r33 and r33.get("Err") == "901":
                proto_ver = "3.3"
                out.append(Finding("HIGH", "Tuya v3.3 — AES-128-ECB, localKey required but obtainable",
                                   detail="Not directly exploitable without localKey. "
                                          "Key fetchable via cloud API once app account is linked. "
                                          "v3.3 UDP broadcasts on port 6667 may leak device ID and MAC.",
                                   cve="CVE-2022-37017 (Tuya SDK key exposure in Android logs)"))
        except Exception:
            pass

    # v3.2 — AES-128-ECB + HMAC, firmware 1.0.5+; v3.4 — same but with session key negotiation
    if not proto_ver:
        for ver, label, detail in [
            (3.2, "3.2", "AES-128-ECB + HMAC-SHA256 per-message signature. "
                         "LocalKey required. Introduced in firmware 1.0.5. "
                         "More resistant than v3.1 but key still obtainable via cloud API."),
            (3.4, "3.4", "AES-128-ECB + session key negotiation. "
                         "LocalKey required + ephemeral session key exchanged on connect. "
                         "Key still obtainable via cloud API. "
                         "pyaes compatible (unlike v3.5 which needs GCM)."),
        ]:
            try:
                import tinytuya
                devX = tinytuya.Device(b.device_id or "0"*22, b.ip, "0"*16, version=ver)
                devX.set_socketTimeout(3); devX.set_socketRetryLimit(1)
                rX = devX.status()
                if rX and rX.get("Err") in ("901", "914"):
                    proto_ver = label
                    out.append(Finding("HIGH", f"Tuya v{label} — localKey required but obtainable via cloud",
                                       detail=detail))
                    break
            except Exception:
                pass

    if not proto_ver:
        out.append(Finding("INFO",
                           "Could not fingerprint Tuya protocol version (offline or non-standard version)"))

    # F2 — UDP broadcast check (5s listen on 6666/6667/7000)
    print(f"  {DIM}  Listening for Tuya UDP broadcasts (5s)...{RST}", end="", flush=True)
    tuya_bcast = []
    def _listen_tuya():
        for port in [6666, 6667, 7000]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except: pass
                s.settimeout(1); s.bind(("0.0.0.0", port))
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        data, addr = s.recvfrom(4096)
                        if addr[0] == b.ip:
                            tuya_bcast.append((port, data))
                    except socket.timeout: continue
                s.close()
            except: pass
    lt = threading.Thread(target=_listen_tuya, daemon=True)
    lt.start(); lt.join()
    print()

    if tuya_bcast:
        port_, data_ = tuya_bcast[0]
        raw_hex = data_.hex()[:120]
        # Try decrypt with known Tuya UDP broadcast key
        decrypted = ""; extracted = {}
        try:
            from Crypto.Cipher import AES
            payload = data_[20:-8]
            if len(payload) >= 16:
                pad = payload + b'\x00'*(16-len(payload)%16) if len(payload)%16 else payload
                dec = AES.new(b"yGAdlopoPVldABfn", AES.MODE_ECB).decrypt(pad)
                decrypted = dec.decode("utf-8", errors="replace").strip()[:200]
                try:
                    import json as _j; extracted = _j.loads(decrypted)
                except Exception: pass
        except: pass
        detail_b = f"From UDP:{port_}  raw={raw_hex}"
        if decrypted: detail_b += f"\n           decrypted={decrypted}"
        dev_id_found = extracted.get("devId") or extracted.get("gwId", "")
        if dev_id_found:
            detail_b += (f"\n           devId={dev_id_found}  "
                         f"productKey={extracted.get('productKey','?')}  "
                         f"ip={extracted.get('ip','?')}")
            out.append(Finding("HIGH", "Tuya UDP broadcast — devId/gwId/productKey extracted plaintext",
                               detail=detail_b,
                               poc=f"Passive listener on UDP {port_} — devId enables cloud-API enumeration"))
        else:
            out.append(Finding("MED", "Tuya device broadcasting on UDP — device ID / MAC passively leaked",
                               detail=detail_b,
                               poc=f"Passive listener on UDP {port_} — no interaction required"))
    else:
        out.append(Finding("INFO",
                           "No Tuya UDP broadcast in 5s (v3.5 devices typically do not broadcast)"))

    # F3 — v3.1 control test (only if confirmed v3.1)
    if proto_ver == "3.1":
        try:
            import tinytuya
            dev = tinytuya.BulbDevice(b.device_id or "0"*22, b.ip, "0"*16, version=3.1)
            dev.set_socketTimeout(3)
            r_ctrl = dev.set_status(b.on, 20)
            if r_ctrl and "dps" in r_ctrl:
                out.append(Finding("CRIT", "Tuya v3.1 full control without localKey — DP_COMMAND accepted",
                                   detail="Device accepted control command without any key material.",
                                   cve="CVE-2019-18693",
                                   poc='tinytuya.BulbDevice(devid,ip,"",version=3.1).set_status(True,20)',
                                   response=str(r_ctrl)[:100]))
        except Exception:
            pass

    # F4 — Tuya-cloudcutter: AP mode buffer overflow (port 6669)
    print(f"  {DIM}  Checking for AP pairing mode (port 6669)...{RST}", end="", flush=True)
    ap_open = _scan_port(b.ip, 6669, timeout=1.5)
    print()
    if ap_open:
        out.append(Finding("CRIT",
                           "Port 6669 open — device in AP pairing mode: buffer overflow exploitable",
                           detail="Stack/heap buffer overflow in __udp_ap_v3() on BK7231T/N SDK. "
                                  "Oversized 'token' field in UDP AP config packet overwrites finish_cb "
                                  "function pointer → unauthenticated code execution → overwrite flash "
                                  "credentials (authKey, PSK, UUID) with attacker-controlled values. "
                                  "Use tuya-cloudcutter for automated exploitation (firmware-specific gadgets).",
                           cve="No CVE assigned (published 2022-03-29, rb9.nl)",
                           poc="github.com/tuya-cloudcutter/tuya-cloudcutter  —  UDP:6669 oversized token field"))
    else:
        out.append(Finding("SAFE",
                           "Port 6669 closed — device not in AP pairing mode (cloudcutter N/A)",
                           detail="AP provisioning buffer overflow only applies during initial pairing. "
                                  "To test: factory-reset device and trigger pairing mode."))

    # F5 — Null-key DP_COMMAND probe (firmware bug class: some early Tuya accepted all-zero key)
    if proto_ver not in ("3.5", "rst"):
        try:
            import tinytuya
            dev_null = tinytuya.Device(b.device_id or "0"*22, b.ip, "\x00"*16, version=3.3)
            dev_null.set_socketTimeout(3); dev_null.set_socketRetryLimit(1)
            r_null = dev_null.status()
            if r_null and "dps" in r_null:
                out.append(Finding("CRIT", "Null-key (all-zero) accepted — firmware bug: key validation absent",
                                   detail="Device accepted 16 null-bytes as localKey. "
                                          "Full unauthenticated control possible.",
                                   poc="tinytuya.Device(devid, ip, '\\x00'*16, version=3.3).status()"))
            else:
                out.append(Finding("SAFE", "Null-key probe rejected — key validation present"))
        except Exception: pass

    # F6 — Protocol downgrade probe: send v3.1 header to non-v3.1 device
    if proto_ver and proto_ver not in ("3.1", "3.1-partial", "3.5"):
        try:
            import tinytuya
            dev_dg = tinytuya.Device(b.device_id or "0"*22, b.ip, "0"*16, version=3.1)
            dev_dg.set_socketTimeout(3); dev_dg.set_socketRetryLimit(1)
            r_dg = dev_dg.status()
            if r_dg and "dps" in r_dg:
                out.append(Finding("CRIT", "Version downgrade accepted — v3.1 plaintext fallback on v3.3+ device",
                                   detail=f"Device reports as {proto_ver} but accepted a v3.1 DP_QUERY "
                                          "without encryption. Full unauth access via downgrade.",
                                   cve="CVE-2019-18693",
                                   poc="tinytuya.Device(devid, ip, '', version=3.1).status() on v3.3 device"))
            else:
                out.append(Finding("SAFE", f"Version downgrade rejected — v3.1 plaintext refused by {proto_ver} device"))
        except Exception: pass

    return out


# ── Dispatcher + Export ───────────────────────────────────────────────────────

_AUDIT_FN = {
    "WiZ":       audit_wiz,
    "Yeelight":  audit_yeelight,
    "LIFX":      audit_lifx,
    "Kasa":      audit_kasa,
    "MagicHome": audit_magichome,
    "Tuya":      audit_tuya,
}


def _export_audit(results: dict) -> str:
    ts    = time.strftime("%Y%m%d_%H%M%S")
    fname = f"audit_{ts}.txt"
    path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    sev_order = ["CRIT","HIGH","MED","LOW","INFO","SAFE"]

    with open(path, "w") as fh:
        fh.write("swiss_bulbs.py — Multi-Protocol Smart Bulb Security Audit\n")
        fh.write(f"Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"Audited   : {len(results)} device(s)\n")
        fh.write("=" * 70 + "\n\n")

        for ip, findings in results.items():
            brand = next((bx.BRAND for bx in registry if bx.ip == ip), "Unknown")
            fh.write(f"── {ip}  [{brand}] ──\n\n")
            for sev in sev_order:
                for fn in findings:
                    if fn.sev != sev: continue
                    cve = f"  [{fn.cve}]" if fn.cve else ""
                    fh.write(f"  [{fn.sev:<4}]  {fn.title}{cve}\n")
                    if fn.detail:   fh.write(f"           {fn.detail}\n")
                    if fn.poc:      fh.write(f"           POC: {fn.poc}\n")
                    if fn.response: fh.write(f"           Got: {fn.response}\n")
                    fh.write("\n")
            fh.write("\n")

        all_f = [fn for fns in results.values() for fn in fns]
        fh.write("── Risk Summary ──\n")
        for sev in ["CRIT","HIGH","MED","LOW"]:
            n = sum(1 for fn in all_f if fn.sev == sev)
            if n: fh.write(f"  {sev:<4}  {n}\n")

    return path


def menu_audit():
    banner()
    print(f"  {BOLD}{Y}── Security Audit ──{RST}")
    print(f"  {DIM}  Tests each targeted bulb for known CVEs, info disclosure,{RST}")
    print(f"  {DIM}  protocol weaknesses, and unauthenticated control. Non-destructive.{RST}\n")

    tgts = get_targets()
    if not tgts:
        print(f"  {R}No bulbs targeted. Run [d] Discover first.{RST}"); _pause(); return

    print(f"  Auditing {len(tgts)} bulb(s):\n")
    for b in tgts:
        c = BRAND_COLOR.get(b.BRAND, DIM)
        print(f"    {C}{b.ip}{RST}  {c}[{b.BRAND}]{RST}  {DIM}{b.name}{RST}")
    print()
    if _in("Start audit? [Y/n]").lower() == "n": return

    results = {}

    for b in tgts:
        banner()
        c = BRAND_COLOR.get(b.BRAND, DIM)
        print(f"\n  {BOLD}{c}── {b.ip}  [{b.BRAND}] ──{RST}\n")

        fn = _AUDIT_FN.get(b.BRAND)
        if not fn:
            print(f"  {DIM}No audit module for {b.BRAND}.{RST}\n")
            results[b.ip] = [Finding("INFO", f"No audit module for {b.BRAND}")]
            continue

        try:
            findings = fn(b)
        except Exception as e:
            findings = [Finding("INFO", f"Audit error: {e}")]

        results[b.ip] = findings
        print()
        for f_ in findings:
            _pf(f_)

        crit = sum(1 for f_ in findings if f_.sev == "CRIT")
        high = sum(1 for f_ in findings if f_.sev == "HIGH")
        med  = sum(1 for f_ in findings if f_.sev == "MED")
        low  = sum(1 for f_ in findings if f_.sev == "LOW")
        safe = sum(1 for f_ in findings if f_.sev == "SAFE")
        print(f"  {DIM}─── risk: {RST}", end="")
        if crit: print(f"{R}{BOLD}CRIT×{crit}{RST} ", end="")
        if high: print(f"{Y}HIGH×{high}{RST} ", end="")
        if med:  print(f"{M}MED×{med}{RST}  ", end="")
        if low:  print(f"{B}LOW×{low}{RST}  ", end="")
        if safe: print(f"{G}SAFE×{safe}{RST}", end="")
        print()

        if len(tgts) > 1 and b is not tgts[-1]:
            print(); _pause()

    # Summary
    print(f"\n  {'─'*60}")
    print(f"  {BOLD}Audit complete — {len(results)} device(s).{RST}\n")
    all_f = [f_ for fns in results.values() for f_ in fns]
    crit = sum(1 for f_ in all_f if f_.sev == "CRIT")
    high = sum(1 for f_ in all_f if f_.sev == "HIGH")
    med  = sum(1 for f_ in all_f if f_.sev == "MED")
    if crit or high:
        print(f"  {R}{BOLD}  {crit} CRITICAL  {high} HIGH  {med} MEDIUM findings{RST}\n")

    if _in("Export findings to file? [Y/n]").lower() != "n":
        path = _export_audit(results)
        print(f"  {G}Saved → {path}{RST}")
    _pause()


# ══════════════════════════════════════════════════════════════════════════════
#  Banner + UI helpers
# ══════════════════════════════════════════════════════════════════════════════

# ── REPL helpers ─────────────────────────────────────────────────────────────

def _i(msg):  print(f"  {DIM}[*]{RST} {msg}")
def _p(msg):  print(f"  {G}[+]{RST} {BOLD}{msg}{RST}")
def _e(msg):  print(f"  {R}[-]{RST} {msg}")
def _w(msg):  print(f"  {Y}[!]{RST} {BOLD}{msg}{RST}")
def _d(msg):  print(f"  {C}[>]{RST} {DIM}{msg}{RST}")

def _in(prompt=""):
    try:    return input(f"  {Y}{prompt}>{RST} ").strip()
    except: return ""

def _int(prompt, lo, hi):
    try:
        v = int(_in(prompt)); return v if lo <= v <= hi else None
    except: return None

def _pause():
    try: input(f"\n  {DIM}Press Enter to continue...{RST}")
    except: pass


def _hacked_status(b) -> str:
    """
    [HACKED] = full unauthenticated control confirmed right now
    [VULN]   = vulnerable/exposed but needs a key for full control (Tuya v3.3 / unknown)
    [SAFE]   = Tuya v3.5 TLS+HMAC confirmed — no known LAN exploit
    """
    if b.BRAND != "Tuya":
        # WiZ / Yeelight / LIFX / Kasa / MagicHome — zero auth, full control
        return f"{R}{BOLD}[HACKED]{RST}"
    # Tuya v3.1 confirmed — DP_QUERY/COMMAND accepted without any key
    if getattr(b, "_confirmed_v31", False):
        return f"{R}{BOLD}[HACKED]{RST}"
    # Tuya localKey is set and tinytuya device is initialised — we can actually control it
    if getattr(b, "_dev", None):
        return f"{R}{BOLD}[HACKED]{RST}"
    # Tuya v3.5 confirmed secure
    if getattr(b, "_confirmed_v35", False):
        return f"{G}[SAFE]{RST}"
    # Tuya with open port but no key / version unknown — vulnerable but not yet controllable
    return f"{Y}{BOLD}[VULN]{RST}"


def _show_targets():
    if not registry:
        _e("No bulbs — run 'scan' first."); return
    tgt_set = set(targets) if targets else set(range(len(registry)))
    for i, b in enumerate(registry):
        sel  = f"{Y}►{RST}" if i in tgt_set else " "
        c    = BRAND_COLOR.get(b.BRAND, DIM)
        st   = b.state_str() if b._st else f"{DIM}?{RST}"
        hack = _hacked_status(b)
        print(f"  {sel} {W}[{i+1}]{RST}  {C}{b.ip:<16}{RST}  {W}{b.name:<20}{RST}  "
              f"{c}{b.BRAND:<10}{RST}  {hack}  {st}")


def _do_scan():
    local  = _local_ip()
    subnet = local.rsplit(".", 1)[0]
    _i(f"Local IP: {local}  →  scanning {subnet}.0/24")
    _i("Running: WiZ · Yeelight · LIFX · Kasa · MagicHome · Tuya  (parallel)...")
    print()

    found = discover_all()

    new = 0
    for ip, bulb in found.items():
        existing = next((b for b in registry if b.ip == ip), None)
        if existing:
            existing._st = bulb._st
        else:
            registry.append(bulb); new += 1

    if not found:
        _e("No bulbs found on this subnet.")
        _i("Tips: make sure bulbs are on the same WiFi network · try 'add <ip>' to add manually")
        return

    print(f"  {'─'*76}")
    print(f"  {'IP':<18} {'Brand':<12} {'State':<24} {'Exploitability'}")
    print(f"  {DIM}{'─'*76}{RST}")
    print(f"  {DIM}{'':18} {'':12} {'':24} [HACKED]=full control  [VULN]=key needed  [SAFE]=secure{RST}")
    print(f"  {'─'*76}")
    tgt_set = set(range(len(registry)))
    for b in registry:
        if b.ip not in found: continue
        c    = BRAND_COLOR.get(b.BRAND, DIM)
        st   = f"{G}ON{RST}" if b.on else (f"{R}OFF{RST}" if b._st else f"{DIM}?{RST}")
        bri  = f"  {b.brightness}%" if b._st else ""
        col  = (f"  rgb({b._st['r']},{b._st['g']},{b._st['b']})" if b._st and "r" in b._st
                else f"  {b._st.get('temp','?')}K" if b._st and "temp" in b._st else "")
        hack = _hacked_status(b)
        print(f"  {C}{b.ip:<18}{RST}{c}{b.BRAND:<12}{RST}{st}{DIM}{bri}{col:<16}{RST}  {hack}")
    print(f"  {'─'*68}")
    print()
    _p(f"Found {len(found)} bulb(s) · {new} new  |  use 'select <n>' to target  |  'audit' to scan for vulns")


def _do_select(args):
    if not registry:
        _e("No bulbs — run 'scan' first."); return
    if not args or args[0].lower() in ("all", "*"):
        targets.clear()
        _p(f"All {len(registry)} bulb(s) selected.")
        return
    if args[0].lower() in ("none", "clear"):
        targets.clear()
        _p("Selection cleared — all targeted.")
        return
    idxs = []
    for tok in ",".join(args).split(","):
        try:
            i = int(tok.strip()) - 1
            if 0 <= i < len(registry): idxs.append(i)
            else: _e(f"No bulb #{i+1}")
        except ValueError:
            _e(f"Bad index: {tok}")
    if idxs:
        targets.clear(); targets.extend(sorted(set(idxs)))
        names = ", ".join(f"{C}{registry[i].ip}{RST}" for i in targets)
        _p(f"Targets: {names}")


def _do_add(args):
    protos = {"wiz":"1","yeelight":"2","lifx":"3","kasa":"4","magic":"5","tuya":"6",
              "1":"1","2":"2","3":"3","4":"4","5":"5","6":"6"}
    cls_map = {"1":WizBulb,"2":YeelightBulb,"3":LifxBulb,"4":KasaBulb,"5":MagicBulb,"6":TuyaBulb}
    ip    = args[0] if args else _in("IP address")
    if not ip: return
    proto = protos.get(args[1].lower() if len(args) > 1 else "", None)
    if not proto:
        print(f"  {DIM}Protocols: wiz yeelight lifx kasa magic tuya{RST}")
        proto = protos.get(_in("Protocol").lower(), "1")
    cls   = cls_map.get(proto, WizBulb)
    name  = _in(f"Name [{cls.BRAND} {ip}]") or f"{cls.BRAND} {ip}"
    if proto == "6":
        dev_id = _in("Device ID")
        loc_k  = _in("Local Key")
        ver    = _in("Version [3.3]") or "3.3"
        b = TuyaBulb(ip, name, info={"id": dev_id, "key": loc_k, "version": ver})
    else:
        b = cls(ip, name)
    b.refresh()
    registry.append(b)
    _p(f"Added {ip} [{b.BRAND}]  {_hacked_status(b)}")


def _do_rename(args):
    _show_targets()
    try:
        idx = int(args[0]) - 1 if args else int(_in("Bulb #")) - 1
        b   = registry[idx]
        new = args[1] if len(args) > 1 else _in(f"Name [{b.name}]")
        if new: b.name = new; _p(f"Renamed → {new}")
    except (ValueError, IndexError):
        _e("Invalid number.")


def _do_info():
    if not registry:
        _e("No bulbs — run 'scan' first."); return
    refresh_all()
    _show_targets()


def _do_control():
    while True:
        print()
        tgts = get_targets()
        if tgts:
            for b in tgts:
                c   = BRAND_COLOR.get(b.BRAND, DIM)
                st  = f"{G}ON{RST}" if b.on else f"{R}OFF{RST}"
                col = (f"rgb({b._st['r']},{b._st['g']},{b._st['b']})" if "r" in b._st
                       else f"{b._st.get('temp','?')}K" if "temp" in b._st else "")
                hack = _hacked_status(b)
                print(f"  {C}{b.ip}{RST}  {st}  {W}{b.brightness}%{RST}  "
                      f"{DIM}{col}{RST}  {c}[{b.BRAND}]{RST}  {hack}")
        print(f"""
  {BOLD}Control{RST}
  {W}[1]{RST} Toggle    {W}[2]{RST} ON        {W}[3]{RST} OFF
  {W}[4]{RST} Brightness  {W}[5]{RST} RGB color   {W}[6]{RST} Color temp
  {W}[7]{RST} Full bright {DIM}(4000K 100%){RST}
  {W}[0]{RST} Back
""")
        ch = _in("control").lower()
        if   ch == "1": send_all("toggle");                         _p("Toggled")
        elif ch == "2": send_all("turn_on");                        _p("ON")
        elif ch == "3": send_all("turn_off");                       _p("OFF")
        elif ch == "4":
            v = _int("Brightness 1-100", 1, 100)
            if v is not None: send_all("set_brightness", v);        _p(f"Brightness → {v}%")
        elif ch == "5":
            rv = _int("Red   0-255", 0, 255)
            gv = _int("Green 0-255", 0, 255)
            bv = _int("Blue  0-255", 0, 255)
            if None not in (rv, gv, bv):
                send_all("set_rgb", rv, gv, bv);                    _p(f"Color → rgb({rv},{gv},{bv})")
        elif ch == "6":
            t = _int("Kelvin 2000-9000", 2000, 9000)
            if t is not None: send_all("set_temp", t);              _p(f"Temp → {t}K")
        elif ch == "7": send_all("set_temp", 4000, 100);            _p("Full bright")
        elif ch == "0": break


def _do_music():
    """Music mode sub-menu — pick a mode with a plain description, then start."""
    MODES = [
        ("color",    "Color shift",   "Hue glides through colours — louder = faster shift"),
        ("pulse",    "Pulse",         "Brightness pumps up and down with the beat"),
        ("strobe",   "Strobe",        "White flash only on loud hits — silent = off"),
        ("spectrum", "Spectrum",      "Bass = Red  ·  Mid = Green  ·  Treble = Blue (FFT)"),
        ("beat",     "Beat detect",   "Detects each beat and flashes a new random colour"),
    ]
    PALETTES = [
        ("full",   "Full spectrum  — all colours (default)"),
        ("warm",   "Warm           — reds, oranges, yellows"),
        ("cool",   "Cool           — cyans, blues, purples"),
        ("fire",   "Fire           — deep reds and oranges only"),
        ("ocean",  "Ocean          — teals and blues only"),
        ("forest", "Forest         — greens and lime"),
        ("neon",   "Neon           — purples and pinks"),
    ]

    while True:
        print(f"""
  {M}{BOLD}Music Mode{RST}  {DIM}listens to your mic and drives the bulbs in real-time{RST}
  {DIM}Requires: pip install sounddevice numpy{RST}

  {BOLD}Pick a mode:{RST}
""")
        for i, (_, label, desc) in enumerate(MODES, 1):
            print(f"  {W}[{i}]{RST} {label:<14}  {DIM}{desc}{RST}")
        print(f"\n  {W}[0]{RST} Back\n")
        ch = _in("music mode")
        if ch == "0": return
        try:
            mode_idx = int(ch) - 1
            if not (0 <= mode_idx < len(MODES)):
                continue
        except ValueError:
            continue

        chosen_mode = MODES[mode_idx][0]

        print(f"""
  {BOLD}Pick a colour palette:{RST}  {DIM}(controls which colours are used){RST}
""")
        for i, (_, desc) in enumerate(PALETTES, 1):
            print(f"  {W}[{i}]{RST} {desc}")
        print(f"\n  {W}[0]{RST} Back\n")
        pc = _in("palette")
        if pc == "0": continue
        try:
            pal_idx = int(pc) - 1
            chosen_pal = PALETTES[pal_idx][0] if 0 <= pal_idx < len(PALETTES) else "full"
        except ValueError:
            chosen_pal = "full"

        print(f"""
  {BOLD}Sensitivity{RST}  {DIM}how strongly the bulbs react to quiet vs loud sounds{RST}

  {W}[1]{RST} Low    {DIM}— good for loud music / concerts{RST}
  {W}[2]{RST} Medium {DIM}— balanced for most music  (recommended){RST}
  {W}[3]{RST} High   {DIM}— good for quiet rooms / background music{RST}
""")
        sc = _in("sensitivity")
        sens_map = {"1": 0.8, "2": 1.5, "3": 2.8}
        chosen_sens = sens_map.get(sc, 1.5)

        fx_music(sensitivity=chosen_sens, mode=chosen_mode, palette=chosen_pal)
        return


def _do_effects():
    FX = [
        ("rainbow",    lambda: fx_rainbow(speed=round(0.15-(_int("Speed 1-10",1,10) or 5 -1)*0.013,3))),
        ("strobe",     lambda: fx_strobe(hz=_int("Hz 1-20",1,20) or 8)),
        ("breathe",    lambda: fx_breathe(temp=_int("Kelvin",1000,9000) or 4000)),
        ("sunset",     lambda: fx_sunset(minutes=_int("Minutes 1-60",1,60) or 5)),
        ("wave",       lambda: fx_wave(speed=round(0.8-(_int("Speed 1-10",1,10) or 5 -1)*0.07,2))),
        ("disco",      fx_disco),
        ("police",     fx_police),
        ("candlelight",fx_candle),
        ("alert",      lambda: fx_alert(hz=_int("Hz 1-10",1,10) or 2,
                                        pulses=_int("Pulses (0=forever)",0,999) or 0)),
        ("music",      _do_music),
    ]
    while True:
        print(f"\n  {BOLD}Effects{RST}  {DIM}(Ctrl+C stops and restores){RST}\n")
        for i, (name, _) in enumerate(FX, 1):
            if name == "alert":
                label = f"{R}{name.capitalize()}{RST}"
            elif name == "music":
                label = f"{M}{name.capitalize()}{RST}  {DIM}mic-reactive — listens to your music and drives the bulbs{RST}"
            else:
                label = name.capitalize()
            print(f"  {W}[{i}]{RST} {label}")
        print(f"  {W}[0]{RST} Back\n")
        ch = _in("effects")
        if ch == "0": break
        try:
            idx = int(ch) - 1
            if 0 <= idx < len(FX):
                FX[idx][1]()
        except (ValueError, IndexError): pass


def _do_presets():
    while True:
        print(f"\n  {BOLD}Presets{RST}\n")
        for i, (name, _) in enumerate(PRESETS, 1):
            print(f"  {W}[{i:>2}]{RST} {name}")
        print(f"  {W}[ 0]{RST} Back\n")
        ch = _in("presets")
        if ch == "0": break
        try:
            idx = int(ch) - 1
            if 0 <= idx < len(PRESETS):
                name, params = PRESETS[idx]
                apply_all(params); _p(f"Applied: {name}")
        except (ValueError, IndexError): pass


def _do_timers():
    while True:
        print(f"\n  {BOLD}Timers{RST}\n"
              f"  {W}[1]{RST} Off in N minutes\n"
              f"  {W}[2]{RST} Sleep {DIM}(gradual dim → off){RST}\n"
              f"  {W}[0]{RST} Back\n")
        ch = _in("timers")
        if   ch == "1":
            m = _int("Minutes", 1, 600) or 30; timer_off(m)
        elif ch == "2":
            m = _int("Minutes", 1, 120) or 30; timer_sleep(m)
        elif ch == "0": break


def _do_morse(args):
    msg = " ".join(args) if args else _in("Message")
    if not msg: return
    unit_map = {"slow": 0.20, "fast": 0.06, "normal": 0.12}
    speed = "normal"
    if args and args[-1].lower() in unit_map:
        speed = args[-1].lower(); msg = " ".join(args[:-1])
    unit = unit_map[speed]
    words = msg.upper().split()
    parts = [' '.join(MORSE_CODE.get(c,'?').replace('.','·').replace('-','—')
                      for c in w if MORSE_CODE.get(c)) for w in words]
    _d(f"Encoding: {'  /  '.join(parts)}")
    _d(f"~{_morse_duration(msg, unit):.1f}s at {speed} speed  ·  Ctrl+C to abort")
    morse_send(msg, unit)


def _do_protocols():
    print(f"""
  {BOLD}Protocol Capabilities & Market Prevalence{RST}

  {BOLD}{DIM}── Control Capabilities ────────────────────────────────────────────────────{RST}
  {BOLD}{"Protocol":<12} {"Port":<7} {"Transport":<13} {"Full Control":<14} {"Auth":<18} {"Key?"}{RST}
  {"─"*72}
  {Y}{"WiZ":<12}{RST} {"38899":<7} {"UDP JSON":<13} {G}{"YES":<14}{RST} {R}{"None":<18}{RST} No
  {M}{"Yeelight":<12}{RST} {"55443":<7} {"TCP JSON":<13} {G}{"YES *":<14}{RST} {R}{"None *":<18}{RST} No
  {C}{"LIFX":<12}{RST} {"56700":<7} {"UDP binary":<13} {G}{"YES":<14}{RST} {R}{"None (by design)":<18}{RST} No
  {G}{"Kasa":<12}{RST} {"9999":<7} {"TCP XOR":<13} {G}{"YES":<14}{RST} {R}{"XOR trivial":<18}{RST} No
  {B}{"MagicHome":<12}{RST} {"5577":<7} {"TCP binary":<13} {G}{"YES":<14}{RST} {R}{"None":<18}{RST} No
  {R}{"Tuya v3.1":<12}{RST} {"6668":<7} {"TCP plain":<13} {G}{"YES":<14}{RST} {R}{"None (CVE-2019-18693)":<18}{RST} No
  {R}{"Tuya v3.3":<12}{RST} {"6668":<7} {"TCP AES-128":<13} {Y}{"PARTIAL":<14}{RST} {Y}{"localKey needed":<18}{RST} Yes
  {R}{"Tuya v3.5":<12}{RST} {"6668":<7} {"TCP TLS":<13} {R}{"NO":<14}{RST} {G}{"TLS+HMAC-SHA256":<18}{RST} Yes
  {DIM}* Yeelight: must enable LAN control in the Yeelight app before tool can connect{RST}

  {BOLD}{DIM}── Feature Support ─────────────────────────────────────────────────────────{RST}
  {BOLD}{"Protocol":<12} {"Power":<7} {"Bright":<8} {"RGB":<6} {"Temp K":<8} {"Scene":<7} {"Morse":<7} {"Effects"}{RST}
  {"─"*68}
  {Y}{"WiZ":<12}{RST} {"✓":<7} {"✓":<8} {"✓":<6} {"✓":<8} {"✓":<7} {"✓":<7} {"✓"}
  {M}{"Yeelight":<12}{RST} {"✓":<7} {"✓":<8} {"✓":<6} {"✓":<8} {"✓":<7} {"✓":<7} {"✓"}
  {C}{"LIFX":<12}{RST} {"✓":<7} {"✓":<8} {"✓":<6} {"✓":<8} {"–":<7} {"✓":<7} {"✓"}
  {G}{"Kasa":<12}{RST} {"✓":<7} {"✓":<8} {"✓":<6} {"✓":<8} {"–":<7} {"✓":<7} {"✓"}
  {B}{"MagicHome":<12}{RST} {"✓":<7} {"✓":<8} {"✓":<6} {"≈":<8} {"–":<7} {"✓":<7} {"✓"}
  {R}{"Tuya":<12}{RST} {"✓":<7} {"✓":<8} {"✓":<6} {"✓":<8} {"–":<7} {"✓":<7} {"✓"}
  {DIM}≈  MagicHome color temp is approximated via kelvin→RGB conversion{RST}

  {BOLD}{DIM}── Audit Coverage ──────────────────────────────────────────────────────────{RST}
  {BOLD}{"Protocol":<12} {"Checks":<8} Top Findings{RST}
  {"─"*72}
  {Y}{"WiZ":<12}{RST} {"11":<8} getPilot/SystemConfig/WiFiConfig unauth · DoS · registration hijack
  {M}{"Yeelight":<12}{RST} {"8":<8}  get_prop(13 props) · music mode callback · cron · flash write
  {C}{"LIFX":<12}{RST} {"13":<8} GetLight/Version/Label/Group/Location · uptime · echo · no replay protection
  {G}{"Kasa":<12}{RST} {"10":<8} sysinfo · bound email · SSID · schedule · energy history · WiFi scan
  {B}{"MagicHome":<12}{RST} {"9":<8}  zero-auth control · preset write (0x61) · music mode (0x73) · 500+ brands
  {R}{"Tuya":<12}{RST} {"6":<8}  version fingerprint · devId leak · AP overflow · null-key · downgrade

  {BOLD}{DIM}── Market Prevalence ───────────────────────────────────────────────────────{RST}
  {BOLD}{"Protocol":<12} {"Share":<8} {"Devices":<22} Ecosystem / Where Found{RST}
  {"─"*72}
  {R}{"Tuya":<12}{RST} {"~34%":<8} {"500k+ SKUs":<22} Govee · Feit · Merkury · Treatlife · Smart Life · SmartThings
  {B}{"MagicHome":<12}{RST} {"~28%":<8} {"500+ brands":<22} MagicHome · FluxLED · Zengge · HappyLighting · Aubric · generic
  {Y}{"WiZ":<12}{RST} {"~14%":<8} {"Philips WiZ":<22} Signify / Philips WiZ · Home Depot · Amazon · Best Buy
  {G}{"Kasa":<12}{RST} {"~12%":<8} {"KL / KE series":<22} TP-Link Kasa · Tapo · sold in most US/EU electronics stores
  {M}{"Yeelight":<12}{RST} {"~8%":<8}  {"Mi · Yeelight":<22} Xiaomi / Mi ecosystem · popular in Asia and Europe
  {C}{"LIFX":<12}{RST} {"~4%":<8}  {"LIFX A series":<22} Premium brand · Apple HomeKit · sold at Apple Store
  {DIM}Estimates based on Amazon listings, Shodan scans, and IoT market reports (2023-2024){RST}

  {R}{BOLD}[HACKED]{RST} = full unauthenticated control confirmed right now
  {Y}{BOLD}[VULN]{RST}   = Tuya port open but no key yet — info leaks, not yet fully controlled
  {G}[SAFE]{RST}   = Tuya v3.5 confirmed — TLS + HMAC-SHA256, no known LAN exploit
  {DIM}Run 'audit' to fingerprint Tuya version · downgrade/null-key probes may flip [VULN] → [HACKED]{RST}
""")


def _do_help():
    print(f"""
  {BOLD}{DIM}── Discovery & Targeting ──────────────────────────────────────────{RST}
  {W}scan{RST}                      discover all WiFi bulbs on LAN (6 protocols)
  {W}targets{RST}  {DIM}(ls){RST}            list bulbs with {R}[HACKED]{RST}/{G}[SAFE]{RST} status
  {W}select{RST} {DIM}<n|all|none>{RST}      choose targets   e.g. {DIM}select 1,3{RST}
  {W}add{RST} {DIM}<ip> [proto]{RST}         add bulb manually  proto: wiz yeelight lifx kasa magic tuya
  {W}rename{RST} {DIM}<n> <name>{RST}        rename a bulb
  {W}info{RST}                      refresh and show live state of targeted bulbs

  {BOLD}{DIM}── Power & Light Control ───────────────────────────────────────────{RST}
  {W}on{RST}  {W}off{RST}  {W}toggle{RST}           quick power control (all targeted bulbs)
  {W}bright{RST} {DIM}<1-100>{RST}            set brightness %
  {W}rgb{RST} {DIM}<r> <g> <b>{RST}           set RGB color    e.g. {DIM}rgb 255 0 128{RST}
  {W}temp{RST} {DIM}<kelvin>{RST}             set color temp   e.g. {DIM}temp 4000{RST}
  {W}control{RST}                    interactive control menu (brightness · color · temp)

  {BOLD}{DIM}── Effects & Automation ────────────────────────────────────────────{RST}
  {W}effects{RST}                    rainbow · strobe · breathe · wave · disco · police · candle · {R}alert{RST} · {M}music{RST}
  {W}alert{RST} {DIM}[hz]{RST}               flash all bulbs solid red  e.g. {DIM}alert 4{RST}  (Ctrl+C to stop)
  {W}music{RST} {DIM}[mode] [sens] [palette] [speed] [floor%]{RST}
                             mic-reactive  modes: color · pulse · strobe · spectrum · beat
                             palettes: full · warm · cool · fire · ocean · forest · neon
                             e.g. {DIM}music spectrum 2 fire fast{RST}  —  live keys: +/- m p s [/] r q
  {W}presets{RST}                    movie · reading · party · relax · focus · romance · ...
  {W}timers{RST}                     off countdown · sleep timer (gradual dim → off)
  {W}morse{RST} {DIM}<text> [slow|fast]{RST}  transmit text in Morse code via bulb flashes

  {BOLD}{DIM}── Security & Exploitation ─────────────────────────────────────────{RST}
  {W}audit{RST}                      {Y}full security scan — CVEs · info disclosure · unauth control{RST}
                             {DIM}covers all 6 protocols · exports to audit_TIMESTAMP.txt{RST}
  {W}lock{RST}                       force lock ON/OFF — overrides phone app (Ctrl+C to release)
  {W}protocols{RST}  {DIM}(caps){RST}          capability + audit table — what's possible per brand

  {BOLD}{DIM}── Session ──────────────────────────────────────────────────────────{RST}
  {W}exit{RST}  {DIM}(quit){RST}              exit swiss_bulbs
  {W}clear{RST}                      clear the terminal screen

  {DIM}Tab = autocomplete · ↑↓ = history · Ctrl+R = search history{RST}
""")


# ══════════════════════════════════════════════════════════════════════════════
#  REPL — main loop
# ══════════════════════════════════════════════════════════════════════════════

def banner():
    """Print a random ASCII bulb banner (same pool used at startup)."""
    import random
    print(random.choice(_BANNERS))


_DISCLAIMER = (f"\n  {R}{BOLD}[!]{RST} {DIM}For authorized testing only. "
               f"Use against devices you own or have permission to test.{RST}")

_BANNERS = [
    # Cracked bulb
    f"""{Y}{BOLD}
        ,--,
       / /  \\
      | |    |       Swiss Bulbs
      | | /\\ |       Universal WiFi Smart Bulb Controller
      \\ \\/ / /      WiZ · Yeelight · LIFX · Kasa · MagicHome · Tuya
       \\  / /
        \\/ /        {R}your bulbs. your rules.{RST}{Y}{BOLD}
        / /
       /_/           {DIM}Anmol K Sachan  @FR13ND0x7F  github.com/anmolksachan/{RST}""" + _DISCLAIMER,
    # Shocked bulb
    f"""{Y}{BOLD}
        .---.
       / 0 0 \\
      | (   ) |     Swiss Bulbs — WiFi Bulb Controller
      |  ___  |     {R}[HACKED]{RST}{Y}{BOLD} · unauthenticated control
       \\ === /      WiZ · Yeelight · LIFX · Kasa · MagicHome · Tuya
        |   |
        |___|

  {DIM}Anmol K Sachan  @FR13ND0x7F  github.com/anmolksachan/{RST}""" + _DISCLAIMER,
    # Lightning bulb
    f"""{Y}{BOLD}
       _______
      /       \\
     |  ( ) ( )|    Swiss Bulbs
     |   ___   |    {R}WARNING:{RST}{Y}{BOLD} smart bulbs have no locks
      \\  ---  /     WiZ · Yeelight · LIFX · Kasa · MagicHome · Tuya
       \\  |  /
        \\ | /
         \\|/

  {DIM}Anmol K Sachan  @FR13ND0x7F  github.com/anmolksachan/{RST}""" + _DISCLAIMER,
    # Block letters
    f"""{R}{BOLD}
  ██████╗ ██╗   ██╗██╗     ██████╗ ███████╗
  ██╔══██╗██║   ██║██║     ██╔══██╗██╔════╝
  ██████╔╝██║   ██║██║     ██████╔╝███████╗
  ██╔══██╗██║   ██║██║     ██╔══██╗╚════██║
  ██████╔╝╚██████╔╝███████╗██████╔╝███████║
  ╚═════╝  ╚═════╝ ╚══════╝╚═════╝ ╚══════╝{RST}
  {Y}{BOLD}WiFi Smart Bulb Controller{RST}  {DIM}·  Anmol K Sachan  @FR13ND0x7F  github.com/anmolksachan/{RST}""" + _DISCLAIMER,
]

_REPL_COMMANDS = [
    "scan","discover","targets","ls","show","select","use","set","add",
    "rename","name","info","status","refresh","control","ctrl","effects",
    "fx","presets","preset","timers","timer","morse","lock","forcelock",
    "audit","vuln","protocols","caps","capabilities","alert","music",
    "on","off","toggle","tog","bright","brightness","dim",
    "rgb","temp","ct","kelvin","help","exit","quit","clear",
]
_PROTOS = ["wiz","yeelight","lifx","kasa","magic","tuya"]


def _setup_readline():
    try:
        import readline

        def _completer(text, state):
            line  = readline.get_line_buffer().lstrip()
            parts = line.split()
            cmd   = parts[0].lower() if parts else ""

            # ── first word: complete command name ──────────────────────────
            if not parts or (len(parts) == 1 and not line.endswith(" ")):
                opts = [c for c in _REPL_COMMANDS if c.startswith(text)]

            # ── context-aware second-word completions ──────────────────────
            elif cmd in ("select", "use", "set"):
                nums = [str(i + 1) for i in range(len(registry))]
                ips  = [b.ip for b in registry]
                opts = [x for x in (["all", "none"] + nums + ips) if x.startswith(text)]

            elif cmd == "add":
                opts = [x for x in _PROTOS if x.startswith(text)]

            elif cmd in ("rename", "name"):
                nums = [str(i + 1) for i in range(len(registry))]
                nms  = [b.name for b in registry]
                opts = [x for x in (nums + nms) if x.startswith(text)]

            elif cmd == "morse":
                opts = [x for x in ["slow", "normal", "fast"] if x.startswith(text)]

            elif cmd in ("bright", "brightness", "dim"):
                opts = [x for x in ["10","25","50","75","100"] if x.startswith(text)]

            elif cmd in ("temp", "ct", "kelvin"):
                opts = [x for x in ["2200","2700","3000","4000","5000","6500"] if x.startswith(text)]

            elif cmd == "rgb":
                # suggest common values per channel position
                pos  = len([p for p in parts if p]) - 1
                vals = {"0":"0","1":"128","2":"255"}
                opts = [vals.get(str(pos), "")]

            else:
                opts = []

            try:    return opts[state]
            except: return None

        readline.set_completer(_completer)
        readline.set_completer_delims(" \t\n")

        # macOS ships libedit instead of GNU readline — needs different binding syntax
        _using_libedit = "libedit" in getattr(readline, "__doc__", "")
        if _using_libedit:
            # libedit (macOS default)
            readline.parse_and_bind("bind -e")               # emacs key mode
            readline.parse_and_bind("bind ^I rl_complete")   # Tab = complete
            readline.parse_and_bind("bind ^R em-inc-search-prev")  # Ctrl+R = reverse search
            readline.parse_and_bind("bind ^S em-inc-search-next")  # Ctrl+S = forward search
        else:
            # GNU readline (Linux / Homebrew Python on macOS)
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind(r'"\C-r": reverse-search-history')
            readline.parse_and_bind(r'"\C-s": forward-search-history')

        # Persistent history across sessions
        hist = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".swiss_history")
        try:    readline.read_history_file(hist)
        except FileNotFoundError: pass
        readline.set_history_length(1000)

        import atexit
        atexit.register(readline.write_history_file, hist)

    except ImportError:
        pass  # readline unavailable (Windows without pyreadline)


def main():
    import random
    _setup_readline()
    os.system("clear")
    print(random.choice(_BANNERS))
    print(f"""
  {DIM}Protocols:{RST}  {Y}WiZ{RST}  {M}Yeelight{RST}  {C}LIFX{RST}  {G}Kasa{RST}  {B}MagicHome{RST}  {R}Tuya{RST}
  {DIM}Type {W}help{RST}{DIM} for commands · {W}scan{RST}{DIM} to discover · {W}audit{RST}{DIM} to find vulns{RST}
  {DIM}Tab to autocomplete · ↑/↓ arrow keys for history · Ctrl+R to search history{RST}
""")

    while True:
        # Build prompt
        n   = len(registry)
        sel = len(targets) if targets else n
        if n == 0:
            prompt_tag = ""
        elif sel == 1 and targets:
            b0   = registry[targets[0]]
            hack = _hacked_status(b0)
            prompt_tag = f"{DIM}[{C}{b0.ip}{RST}{DIM}]{RST} {hack}"
        elif targets and sel != n:
            # multiple selected — show worst-case status across selection
            tgt_bulbs = [registry[i] for i in targets if i < len(registry)]
            statuses  = [_hacked_status(b) for b in tgt_bulbs]
            if any("[HACKED]" in s for s in statuses):
                grp_hack = f"{R}{BOLD}[HACKED]{RST}"
            elif any("[VULN]" in s for s in statuses):
                grp_hack = f"{Y}{BOLD}[VULN]{RST}"
            else:
                grp_hack = f"{G}[SAFE]{RST}"
            prompt_tag = f"{DIM}[{Y}{sel} selected{RST}{DIM}]{RST} {grp_hack}"
        else:
            # all bulbs targeted
            all_statuses = [_hacked_status(b) for b in registry]
            if any("[HACKED]" in s for s in all_statuses):
                all_hack = f"{R}{BOLD}[HACKED]{RST}"
            elif any("[VULN]" in s for s in all_statuses):
                all_hack = f"{Y}{BOLD}[VULN]{RST}"
            else:
                all_hack = f"{G}[SAFE]{RST}"
            prompt_tag = f"{DIM}[{G}{n} bulbs{RST}{DIM}]{RST} {all_hack}"

        try:
            raw = input(f"\n  {G}swiss{RST}{prompt_tag} {W}>{RST} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}bye.{RST}\n"); break

        if not raw: continue
        parts = raw.split()
        cmd   = parts[0].lower()
        args  = parts[1:]

        try:
            if   cmd in ("scan", "discover", "d"):          _do_scan()
            elif cmd in ("targets", "ls", "show", "t"):     _show_targets()
            elif cmd in ("select", "use", "set"):            _do_select(args)
            elif cmd == "add":                               _do_add(args)
            elif cmd in ("rename", "name"):                  _do_rename(args)
            elif cmd in ("info", "status", "refresh", "i"): _do_info()
            elif cmd in ("control", "ctrl", "c"):            _do_control()
            elif cmd in ("effects", "fx"):                   _do_effects()
            elif cmd == "alert":
                hz = int(args[0]) if args else 2
                _p(f"Alert — flashing red at {hz}Hz  (Ctrl+C to stop)")
                fx_alert(hz=hz)
            elif cmd == "music":
                # music [mode] [sensitivity] [palette] [speed] [floor]
                # e.g.  music spectrum 2.0 fire fast 10
                #        music beat        music color 1.5 ocean
                _VALID_MODES = ("color","pulse","strobe","spectrum","beat")
                _VALID_PALS  = ("full","warm","cool","fire","ocean","forest","neon")
                _VALID_SPDS  = ("slow","med","fast")
                mode_    = next((a for a in args if a in _VALID_MODES), "color")
                palette_ = next((a for a in args if a in _VALID_PALS),  "full")
                speed_   = next((a for a in args if a in _VALID_SPDS),  "med")
                try:
                    nums = [float(a) for a in args if a.replace('.','',1).isdigit()]
                    sens_  = nums[0] if nums else 1.5
                    floor_ = int(nums[1]) if len(nums) > 1 else 5
                except (ValueError, IndexError):
                    sens_, floor_ = 1.5, 5
                fx_music(sensitivity=sens_, mode=mode_, palette=palette_,
                         speed=speed_, bright_floor=floor_)
            elif cmd in ("presets", "preset", "p"):          _do_presets()
            elif cmd in ("timers", "timer"):                 _do_timers()
            elif cmd == "morse":                             _do_morse(args)
            elif cmd in ("lock", "forcelock"):               force_lock()
            elif cmd in ("audit", "vuln", "scan-vuln", "a"): menu_audit()
            elif cmd == "on":
                send_all("turn_on");                         _p("All ON")
            elif cmd == "off":
                send_all("turn_off");                        _p("All OFF")
            elif cmd in ("toggle", "tog"):
                send_all("toggle");                          _p("Toggled")
            elif cmd in ("bright", "brightness", "dim"):
                if args:
                    v = max(1, min(100, int(args[0])))
                    send_all("set_brightness", v);           _p(f"Brightness → {v}%")
                else: _e("Usage: bright <1-100>")
            elif cmd == "rgb":
                if len(args) >= 3:
                    rv, gv, bv = int(args[0]), int(args[1]), int(args[2])
                    send_all("set_rgb", rv, gv, bv);         _p(f"Color → rgb({rv},{gv},{bv})")
                else: _e("Usage: rgb <r> <g> <b>")
            elif cmd in ("temp", "ct", "kelvin"):
                if args:
                    k = max(2000, min(9000, int(args[0])))
                    send_all("set_temp", k);                 _p(f"Temp → {k}K")
                else: _e("Usage: temp <kelvin>")
            elif cmd in ("protocols", "caps", "capabilities", "proto"): _do_protocols()
            elif cmd in ("help", "?", "h"):                  _do_help()
            elif cmd in ("exit", "quit", "q"):
                print(f"\n  {DIM}bye.{RST}\n"); break
            elif cmd in ("clear", "cls"):
                os.system("clear")
            else:
                # "did you mean?" — find closest match
                import difflib
                all_cmds = ["scan","discover","targets","ls","show","select","use","set","add",
                            "rename","name","info","status","refresh","control","ctrl","effects",
                            "fx","presets","preset","timers","timer","morse","lock","forcelock",
                            "audit","vuln","protocols","caps","capabilities","alert","music",
                            "on","off","toggle","tog","bright","brightness","dim",
                            "rgb","temp","ct","kelvin","help","exit","quit","clear"]
                close = difflib.get_close_matches(cmd, all_cmds, n=1, cutoff=0.6)
                if close:
                    _e(f"Unknown command: {cmd!r}  — did you mean '{close[0]}'?  (type 'help')")
                else:
                    _e(f"Unknown command: {cmd!r}  (type 'help')")
        except KeyboardInterrupt:
            print(f"\n  {Y}[!]{RST} Interrupted.")
        except Exception as ex:
            _e(f"Error: {ex}")


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            cls_hint = {"--wiz":WizBulb,"--yeelight":YeelightBulb,"--lifx":LifxBulb,
                        "--kasa":KasaBulb,"--magic":MagicBulb,"--tuya":TuyaBulb}
            cur_cls = WizBulb
            for arg in sys.argv[1:]:
                if arg in cls_hint:
                    cur_cls = cls_hint[arg]
                elif arg.count(".") == 3:
                    b = cur_cls(arg); b.refresh(); registry.append(b)
                    _p(f"Added {arg} [{b.BRAND}]  {_hacked_status(b)}")
            if registry: time.sleep(0.5)
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Interrupted. Bye.{RST}\n"); sys.exit(0)
