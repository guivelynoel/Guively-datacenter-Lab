import json
import random
import time
import paho.mqtt.client as mqtt

BROKER = "mqtt"
PORT = 1883
DEVICE_ID = "MOTOR-01"
BASE_TOPIC = f"dc1/mechanical/motor/{DEVICE_ID}"

temperature = 48.0
current = 20.0
rpm = 1750

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

while True:
    try:
        client.connect(BROKER, PORT)
        print(f"[{DEVICE_ID}] Connected to MQTT broker")
        break
    except Exception as e:
        print(f"[{DEVICE_ID}] Waiting for MQTT: {e}")
        time.sleep(2)

client.loop_start()

try:
    while True:
        temperature += random.uniform(-0.3, 0.5)
        current = 20.0 + random.uniform(-1.5, 1.5)
        rpm = 1750 + random.randint(-20, 20)

        telemetry = {
            "device_id": DEVICE_ID,
            "temperature_c": round(temperature, 2),
            "current_a": round(current, 2),
            "rpm": rpm,
            "status": "RUNNING",
            "timestamp": int(time.time())
        }

        client.publish(
            f"{BASE_TOPIC}/telemetry",
            json.dumps(telemetry)
        )

        print(json.dumps(telemetry))
        time.sleep(2)

except KeyboardInterrupt:
    pass
finally:
    client.loop_stop()
    client.disconnect()
