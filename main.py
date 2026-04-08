import network
import time
import math
import usocket
import ssl
import ujson
import _thread
from machine import Pin, PWM

# ================= CONFIG =================

WIFI_SSID = "ovadia-levy"
WIFI_PASS = "01122007"

FIREBASE_HOST = "button-link-e80a1-default-rtdb.europe-west1.firebasedatabase.app"
FIREBASE_PATH = "/status.json"
DEVICE = "A"   # CHANGE TO "B" ON OTHER DEVICE

GITHUB_USER = "Niro54"
GITHUB_REPO = "love_button"
GITHUB_BRANCH = "main"
OTA_URL = "https://raw.githubusercontent.com/" + GITHUB_USER + "/" + GITHUB_REPO + "/" + GITHUB_BRANCH + "/main.py"
VERSION_URL = "https://raw.githubusercontent.com/" + GITHUB_USER + "/" + GITHUB_REPO + "/" + GITHUB_BRANCH + "/version.txt"
LOCAL_VERSION_FILE = "versions.txt"

# ================= PINS ===================

button = Pin(15, Pin.IN, Pin.PULL_UP)
led_pwm = PWM(Pin(4), freq=5000)
led_pwm.duty(0)

# ================= SHARED STATE =================

current_led = "idle"
blink_start_ms = time.ticks_ms()
BLINK_PERIOD_MS = 1500
FADE_DURATION_MS = 800
state_lock = _thread.allocate_lock()

# ================= WIFI ===================

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)
    print("Connecting to WiFi...")
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(15):
        if wlan.isconnected():
            print("WiFi connected")
            print("IP:", wlan.ifconfig()[0])
            return wlan
        time.sleep(1)
    raise RuntimeError("WiFi FAILED.")

wlan = connect_wifi()

# ================= OTA UPDATE =================

def get_local_version():
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    except:
        return "0"

def check_and_update():
    import urequests, machine
    try:
        print("Checking for OTA update...")
        r = urequests.get(VERSION_URL)
        remote_version = r.text.strip()
        r.close()

        local_version = get_local_version()
        print("Local:", local_version, "Remote:", remote_version)

        if remote_version != local_version:
            print("New version found! Downloading...")
            r = urequests.get(OTA_URL)
            new_code = r.text
            r.close()

            with open("main.py", "w") as f:
                f.write(new_code)

            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(remote_version)

            print("Update done! Rebooting...")
            time.sleep(1)
            machine.reset()
        else:
            print("Already up to date, version", local_version)

    except Exception as e:
        print("OTA failed:", e)

check_and_update()

# ================= FIREBASE REST =================

def set_state(state):
    import urequests
    payload = {
        "state": state,
        "last_sender": DEVICE,
        "timestamp": time.time()
    }
    try:
        url = "https://" + FIREBASE_HOST + FIREBASE_PATH
        r = urequests.patch(url, json=payload)
        r.close()
    except Exception as e:
        print("SET failed:", e)

def fire_and_forget(state):
    _thread.start_new_thread(set_state, (state,))

# ================= LED =================

def set_led(mode):
    global current_led, blink_start_ms
    with state_lock:
        if mode != current_led:
            current_led = mode
            blink_start_ms = time.ticks_ms()

def led_loop():
    global current_led
    while True:
        with state_lock:
            mode = current_led
            start = blink_start_ms

        if mode == "fading":
            now = time.ticks_ms()
            pos = (time.ticks_diff(now, start) % BLINK_PERIOD_MS) / BLINK_PERIOD_MS
            brightness = (math.sin(pos * 2 * math.pi - math.pi / 2) + 1) / 2
            led_pwm.duty(int(brightness * 1023))

        elif mode == "fade_in":
            now = time.ticks_ms()
            t = time.ticks_diff(now, start) / FADE_DURATION_MS
            if t >= 1.0:
                led_pwm.duty(1023)
                with state_lock:
                    current_led = "solid"
            else:
                brightness = math.sin(t * math.pi / 2)
                led_pwm.duty(int(brightness * 1023))

        elif mode == "fade_out":
            now = time.ticks_ms()
            t = time.ticks_diff(now, start) / FADE_DURATION_MS
            if t >= 1.0:
                led_pwm.duty(0)
                with state_lock:
                    current_led = "idle"
            else:
                brightness = math.cos(t * math.pi / 2)
                led_pwm.duty(int(brightness * 1023))

        elif mode == "solid":
            led_pwm.duty(1023)

        else:
            led_pwm.duty(0)

        time.sleep_ms(10)

_thread.start_new_thread(led_loop, ())

# ================= FIREBASE SSE STREAMING =================

def open_sse():
    addr = usocket.getaddrinfo(FIREBASE_HOST, 443)[0][-1]
    sock = usocket.socket()
    sock.connect(addr)
    sock = ssl.wrap_socket(sock, server_hostname=FIREBASE_HOST)
    request = (
        "GET " + FIREBASE_PATH + " HTTP/1.1\r\n"
        "Host: " + FIREBASE_HOST + "\r\n"
        "Accept: text/event-stream\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
    )
    sock.write(request.encode())
    print("SSE connection opened")
    return sock

def skip_http_headers(sock):
    while True:
        line = b""
        while True:
            ch = sock.read(1)
            if ch == b"\n":
                break
            if ch and ch != b"\r":
                line += ch
        if line == b"":
            break

def read_sse_line(sock):
    line = b""
    while True:
        ch = sock.read(1)
        if not ch:
            return None
        if ch == b"\n":
            return line.decode().strip()
        if ch != b"\r":
            line += ch

def handle_state(state):
    print("Firebase state:", state)
    if state == "sent_from_A" and DEVICE == "B":
        set_led("fading")
    elif state == "sent_from_B" and DEVICE == "A":
        set_led("fading")
    elif state == "sent_from_A" and DEVICE == "A":
        set_led("fade_in")
    elif state == "sent_from_B" and DEVICE == "B":
        set_led("fade_in")
    elif state == "idle":
        set_led("fade_out")

def sse_loop():
    while True:
        try:
            sock = open_sse()
            skip_http_headers(sock)
            print("Listening for Firebase events...")

            event_type = None
            while True:
                line = read_sse_line(sock)
                if line is None:
                    print("SSE connection dropped, reconnecting...")
                    break
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:") and event_type in ("put", "patch"):
                    raw = line[5:].strip()
                    try:
                        parsed = ujson.loads(raw)
                        data = parsed.get("data", {})
                        if isinstance(data, dict):
                            state = data.get("state", "idle")
                            handle_state(state)
                    except:
                        pass
                    event_type = None

            sock.close()

        except Exception as e:
            print("SSE error:", e)
            time.sleep(2)

_thread.start_new_thread(sse_loop, ())

# ================= MAIN LOOP: button only =================

last_button = 1
print("Running as device", DEVICE)

while True:
    val = button.value()
    if val == 0 and last_button == 1:
        print("Button pressed")
        with state_lock:
            led_mode = current_led

        if led_mode in ("fading", "solid", "fade_in"):
            set_led("fade_out")
            fire_and_forget("idle")
        else:
            set_led("fade_in")
            if DEVICE == "A":
                fire_and_forget("sent_from_A")
            else:
                fire_and_forget("sent_from_B")

        time.sleep_ms(300)

    last_button = val
    time.sleep_ms(50)