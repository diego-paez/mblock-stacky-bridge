# Stacky link test: runs ON the CyberPi (mBlock 5 -> Python -> Upload mode).
# Answers two questions:
#   1. Does an uploaded (child's) program receive messages from a BLE/USB host?
#   2. Can the program push events back to the host by itself?
# Pair with: scripts/link_test.py on the Mac (later: the CoreS3).
import cyberpi
import event  # Makeblock's own programs use @event.is_press, not @cyberpi.event.is_press
import time

cyberpi.console.clear()
cyberpi.console.println("stacky test")
cyberpi.led.on(0, 0, 60, "all")

count = 0


@cyberpi.event.upload_broadcast("ping")
def on_ping():
    global count
    count += 1
    v = cyberpi.upload_broadcast.get("ping")
    cyberpi.console.println("ping " + str(v))
    cyberpi.led.on(0, 80, 0, "all")
    cyberpi.upload_broadcast.set("pong", str(v))


@cyberpi.event.upload_broadcast("face")
def on_face():
    cyberpi.console.println("face " + str(cyberpi.upload_broadcast.get("face")))


@event.is_press("a")
def on_a():
    cyberpi.upload_broadcast.set("button", "a-event")
    cyberpi.console.println("sent a-event")


@cyberpi.event.start
def loop():
    # Periodic unsolicited event: tests device -> host without any request
    # Also poll button B, to tell apart "events broken" from "button not pressed"
    last = 0
    while True:
        if time.time() - last >= 2:
            cyberpi.upload_broadcast.set("alive", str(int(time.time())))
            last = time.time()
        if cyberpi.controller.is_press("b"):
            cyberpi.upload_broadcast.set("button", "b-poll")
            cyberpi.console.println("sent b-poll")
            while cyberpi.controller.is_press("b"):
                time.sleep(0.02)
        time.sleep(0.02)
