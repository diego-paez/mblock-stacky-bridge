# Check 1: which communication modules does CyberPiOS MicroPython expose?
# Upload with mBlock 5 -> Python editor -> "Upload" mode (does not erase CyberPiOS).
# Results show on the CyberPi screen, 7 lines per page. Button A = next page.
import cyberpi
import sys

CANDIDATES = [
    "network", "espnow", "bluetooth", "ubluetooth", "ble",
    "socket", "usocket", "select", "uselect",
    "umqtt.simple", "umqtt.robust", "mqtt", "simple",
    "urequests", "requests", "websocket", "uwebsocket",
    "machine", "esp", "esp32", "_thread", "uasyncio", "asyncio",
    "json", "ujson", "struct", "ustruct", "os", "uos",
    "makeblock", "mbuild", "mbot2", "event", "time",
]

results = []
try:
    results.append("py " + ".".join(str(v) for v in sys.implementation.version[:3]))
except Exception:
    results.append("py " + str(sys.version)[:12])

for name in CANDIDATES:
    try:
        __import__(name)
        results.append("OK " + name)
    except Exception as e:
        results.append("-- " + name)

# Extra detail on the most important ones
def attrs(modname, names):
    try:
        m = __import__(modname)
        return [modname + "." + n for n in names if hasattr(m, n)]
    except Exception:
        return []

results += attrs("machine", ["UART", "Pin", "I2C", "SPI"])
results += attrs("network", ["WLAN", "AP_IF", "STA_IF"])
results += attrs("bluetooth", ["BLE", "UUID"])
results += attrs("espnow", ["ESPNow"])
results += attrs("socket", ["socket", "getaddrinfo"])
results += attrs("cyberpi", ["wifi", "wifi_broadcast", "cloud", "upload_broadcast", "broadcast", "ble", "mesh"])

for r in results:
    print(r)  # also goes to USB serial if anything is listening

PAGE = 7
page = 0
pages = (len(results) + PAGE - 1) // PAGE
while True:
    cyberpi.console.clear()
    cyberpi.console.println("p%d/%d A=next" % (page + 1, pages))
    for line in results[page * PAGE:(page + 1) * PAGE]:
        cyberpi.console.println(line)
    while not cyberpi.controller.is_press("a"):
        pass
    while cyberpi.controller.is_press("a"):
        pass
    page = (page + 1) % pages
