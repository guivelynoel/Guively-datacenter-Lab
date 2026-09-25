import json
import random
import time
import paho.mqtt.client as mqtt

DEVICE_ID = "MOTOR-01"

TELEMETRY_TOPIC = f"dc1/mechanical/motor/{DEVICE_ID}/telemetry"
COMMAND_TOPIC = f"dc1/mechanical/motor/{DEVICE_ID}/command"

mode = "NORMAL"

# ---------------------------------------------------------
# MOTOR STATE
# ---------------------------------------------------------

state = {
    "temperature_c": 50.0,
    "current_a": 20.0,
    "rpm": 1750.0,
    "vibration": 2.0,
    "motor_voltage": 480.0,
    "vfd_frequency": 60.0,
    "bearing_temperature": 55.0,
    "winding_temperature": 70.0,
    "power": 12.0,
}


def approach(current, target, rate=0.20, noise=0.0):
    """
    Smoothly move a value toward a target.
    """
    value = current + ((target - current) * rate)

    if noise:
        value += random.uniform(-noise, noise)

    return value


def reset_normal():
    global state

    state.update({
        "temperature_c": 50.0,
        "current_a": 20.0,
        "rpm": 1750.0,
        "vibration": 2.0,
        "motor_voltage": 480.0,
        "vfd_frequency": 60.0,
        "bearing_temperature": 55.0,
        "winding_temperature": 70.0,
        "power": 12.0,
    })


# ---------------------------------------------------------
# MQTT CALLBACKS
# ---------------------------------------------------------

def on_connect(client, userdata, flags, reason_code, properties):
    print(
        f"[{DEVICE_ID}] MQTT CONNECTED: {reason_code}",
        flush=True
    )

    client.subscribe(COMMAND_TOPIC)

    print(
        f"[{DEVICE_ID}] COMMAND TOPIC: {COMMAND_TOPIC}",
        flush=True
    )


def on_message(client, userdata, msg):
    global mode

    command = msg.payload.decode().strip().upper()

    valid_modes = {
        "NORMAL",
        "OVERHEAT",
        "OVERCURRENT",
        "UNDERVOLTAGE",
        "BEARING_FAULT",
        "VIBRATION",
        "STOPPED",
    }

    if command not in valid_modes:
        print(
            f"[{DEVICE_ID}] UNKNOWN COMMAND: {command}",
            flush=True
        )
        return

    mode = command

    if mode == "NORMAL":
        reset_normal()

    print(
        f"[{DEVICE_ID}] SIMULATION MODE -> {mode}",
        flush=True
    )


# ---------------------------------------------------------
# SIMULATION
# ---------------------------------------------------------

def simulate_normal():

    state["temperature_c"] = approach(
        state["temperature_c"], 50.0, .25, .20
    )

    state["current_a"] = approach(
        state["current_a"], 20.0, .30, .20
    )

    state["rpm"] = approach(
        state["rpm"], 1750.0, .30, 2.0
    )

    state["vibration"] = approach(
        state["vibration"], 2.0, .30, .05
    )

    state["motor_voltage"] = approach(
        state["motor_voltage"], 480.0, .30, 1.0
    )

    state["vfd_frequency"] = approach(
        state["vfd_frequency"], 60.0, .30, .05
    )

    state["bearing_temperature"] = approach(
        state["bearing_temperature"], 55.0, .25, .15
    )

    state["winding_temperature"] = approach(
        state["winding_temperature"], 70.0, .25, .20
    )

    state["power"] = approach(
        state["power"], 12.0, .30, .10
    )


def simulate_overheat():

    # Correlated thermal/electrical degradation
    state["temperature_c"] += random.uniform(2.0, 2.8)
    state["winding_temperature"] += random.uniform(1.8, 2.5)
    state["bearing_temperature"] += random.uniform(.8, 1.3)

    state["current_a"] = approach(
        state["current_a"], 27.0, .15, .20
    )

    state["power"] = approach(
        state["power"], 16.0, .15, .10
    )

    state["vibration"] = approach(
        state["vibration"], 4.8, .08, .05
    )

    state["rpm"] = approach(
        state["rpm"], 1740.0, .10, 2.0
    )

    state["motor_voltage"] = approach(
        state["motor_voltage"], 478.0, .20, 1.0
    )

    state["vfd_frequency"] = approach(
        state["vfd_frequency"], 60.0, .20, .05
    )


def simulate_overcurrent():

    state["current_a"] += random.uniform(1.0, 1.7)

    state["power"] = approach(
        state["power"], 18.5, .20, .15
    )

    state["winding_temperature"] += random.uniform(.5, 1.0)

    state["temperature_c"] += random.uniform(.2, .6)

    state["vibration"] = approach(
        state["vibration"], 2.8, .10, .05
    )


def simulate_undervoltage():

    state["motor_voltage"] -= random.uniform(4.0, 7.0)

    state["current_a"] = approach(
        state["current_a"], 26.0, .15, .20
    )

    state["rpm"] = approach(
        state["rpm"], 1650.0, .12, 2.0
    )

    state["power"] = approach(
        state["power"], 10.0, .15, .10
    )


def simulate_bearing_fault():

    state["bearing_temperature"] += random.uniform(1.5, 2.2)

    state["vibration"] += random.uniform(.25, .45)

    state["temperature_c"] += random.uniform(.15, .35)

    state["current_a"] = approach(
        state["current_a"], 22.0, .10, .15
    )


def simulate_vibration():

    state["vibration"] += random.uniform(.35, .60)

    state["bearing_temperature"] += random.uniform(.4, .8)

    state["rpm"] += random.uniform(-8.0, 8.0)


def simulate_stopped():

    state["rpm"] = approach(
        state["rpm"], 0.0, .50, 0
    )

    state["current_a"] = approach(
        state["current_a"], 0.0, .50, 0
    )

    state["power"] = approach(
        state["power"], 0.0, .50, 0
    )

    state["vfd_frequency"] = approach(
        state["vfd_frequency"], 0.0, .50, 0
    )

    state["temperature_c"] = approach(
        state["temperature_c"], 35.0, .08, .10
    )

    state["bearing_temperature"] = approach(
        state["bearing_temperature"], 35.0, .08, .10
    )

    state["winding_temperature"] = approach(
        state["winding_temperature"], 40.0, .08, .10
    )

    state["vibration"] = approach(
        state["vibration"], 0.1, .40, .02
    )


def simulate():

    if mode == "NORMAL":
        simulate_normal()

    elif mode == "OVERHEAT":
        simulate_overheat()

    elif mode == "OVERCURRENT":
        simulate_overcurrent()

    elif mode == "UNDERVOLTAGE":
        simulate_undervoltage()

    elif mode == "BEARING_FAULT":
        simulate_bearing_fault()

    elif mode == "VIBRATION":
        simulate_vibration()

    elif mode == "STOPPED":
        simulate_stopped()


# ---------------------------------------------------------
# MQTT CLIENT
# ---------------------------------------------------------

client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="motor-01-simulator"
)

client.on_connect = on_connect
client.on_message = on_message

print(
    f"[{DEVICE_ID}] Starting full motor analytics simulator...",
    flush=True
)

while True:
    try:
        client.connect("mqtt", 1883, 60)
        break

    except Exception as exc:
        print(
            f"[{DEVICE_ID}] MQTT unavailable: {exc}",
            flush=True
        )
        time.sleep(3)

client.loop_start()


# ---------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------

while True:

    simulate()

    running = mode != "STOPPED"

    payload = {
        "device_id": DEVICE_ID,

        # Analog telemetry
        "temperature_c": round(state["temperature_c"], 2),
        "current_a": round(state["current_a"], 2),
        "rpm": round(state["rpm"]),
        "vibration": round(state["vibration"], 2),
        "motor_voltage": round(state["motor_voltage"], 2),
        "vfd_frequency": round(state["vfd_frequency"], 2),
        "bearing_temperature": round(
            state["bearing_temperature"], 2
        ),
        "winding_temperature": round(
            state["winding_temperature"], 2
        ),
        "power": round(state["power"], 2),

        # Operational states
        "run_command": 1 if running else 0,
        "run_status": 1 if running else 0,
        "motor_fault": 0 if mode == "NORMAL" else 1,
        "motor_overload": 1 if mode == "OVERCURRENT" else 0,
        "vfd_fault": 0,
        "communication_status": 1,
        "hoa_mode": 2,
        "vfd_status": 2 if running else 0,

        # Simulator metadata
        "simulation_mode": mode,
        "timestamp": int(time.time())
    }

    client.publish(
        TELEMETRY_TOPIC,
        json.dumps(payload)
    )

    print(
        f"[{DEVICE_ID}] "
        f"{mode:<14} | "
        f"T={payload['temperature_c']:>6}C | "
        f"I={payload['current_a']:>5}A | "
        f"V={payload['motor_voltage']:>6}V | "
        f"RPM={payload['rpm']:>4} | "
        f"VIB={payload['vibration']:>4} | "
        f"BRG={payload['bearing_temperature']:>5}C | "
        f"WDG={payload['winding_temperature']:>5}C | "
        f"P={payload['power']:>5}kW",
        flush=True
    )

    time.sleep(2)
