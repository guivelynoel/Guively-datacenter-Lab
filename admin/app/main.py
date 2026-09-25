import json
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Equipment, Point


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Guively Virtual Data Center Control Center",
    version="0.4.0"
)

# ---------------------------------------------------------
# LIVE TELEMETRY CACHE
# ---------------------------------------------------------

telemetry_state = {}
mqtt_connected = False

FIELD_MAP = {
    "temperature": "temperature_c",
    "current": "current_a",
    "speed": "rpm",
    "motor_speed": "rpm"
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------
# API MODELS
# ---------------------------------------------------------

class EquipmentCreate(BaseModel):
    equipment_id: str
    name: str
    equipment_type: str
    location: str = ""
    protocol: str = "MQTT"
    enabled: bool = True


class EquipmentUpdate(BaseModel):
    name: str
    equipment_type: str
    location: str = ""
    protocol: str = "MQTT"
    enabled: bool = True


class PointCreate(BaseModel):
    key: str
    display_name: str
    point_type: str = "analog"
    unit: str = ""

    normal_value: Optional[float] = None

    warning_low: Optional[float] = None
    warning_high: Optional[float] = None

    critical_low: Optional[float] = None
    critical_high: Optional[float] = None

    alarm_enabled: bool = True

    binary_zero_label: Optional[str] = None
    binary_one_label: Optional[str] = None

    state_labels: Optional[str] = None

    enabled: bool = True


# ---------------------------------------------------------
# MQTT
# ---------------------------------------------------------

def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_connected

    mqtt_connected = True

    print(
        f"[CONTROL CENTER] MQTT connected: {reason_code}",
        flush=True
    )

    client.subscribe("dc1/#")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    global mqtt_connected

    mqtt_connected = False

    print(
        f"[CONTROL CENTER] MQTT disconnected: {reason_code}",
        flush=True
    )


def on_message(client, userdata, msg):

    try:
        payload = json.loads(
            msg.payload.decode()
        )

        device_id = payload.get(
            "device_id"
        )

        if not device_id:
            return

        telemetry_state[device_id] = {
            "topic": msg.topic,
            "received_at": int(time.time()),
            "data": payload
        }

    except Exception as exc:

        print(
            f"[CONTROL CENTER] Invalid telemetry: {exc}",
            flush=True
        )


def mqtt_worker():

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="vdc-control-center"
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    while True:

        try:

            print(
                "[CONTROL CENTER] Connecting to MQTT...",
                flush=True
            )

            client.connect(
                "mqtt",
                1883,
                60
            )

            client.loop_forever()

        except Exception as exc:

            print(
                f"[CONTROL CENTER] MQTT retry: {exc}",
                flush=True
            )

            time.sleep(3)


@app.on_event("startup")
def start_mqtt():

    thread = threading.Thread(
        target=mqtt_worker,
        daemon=True
    )

    thread.start()


# ---------------------------------------------------------
# ALARM ENGINE
# ---------------------------------------------------------

def telemetry_value(point, data):

    candidates = [
        point.key,
        FIELD_MAP.get(point.key)
    ]

    for candidate in candidates:

        if candidate and candidate in data:

            value = data[candidate]

            if isinstance(
                value,
                (int, float)
            ):
                return float(value)

    return None


def evaluate_analog(point, value):

    if value is None:

        return {
            "severity": "NO_DATA",
            "alarm": False,
            "message": "No live telemetry"
        }


    if not point.alarm_enabled:

        return {
            "severity": "NORMAL",
            "alarm": False,
            "message": "Alarm disabled"
        }


    if (
        point.critical_low is not None
        and value <= point.critical_low
    ):

        return {
            "severity": "CRITICAL",
            "alarm": True,
            "condition": "LOW-LOW",
            "message":
                f"LOW-LOW: {value:g} <= {point.critical_low:g}"
        }


    if (
        point.critical_high is not None
        and value >= point.critical_high
    ):

        return {
            "severity": "CRITICAL",
            "alarm": True,
            "condition": "HIGH-HIGH",
            "message":
                f"HIGH-HIGH: {value:g} >= {point.critical_high:g}"
        }


    if (
        point.warning_low is not None
        and value <= point.warning_low
    ):

        return {
            "severity": "WARNING",
            "alarm": True,
            "condition": "LOW",
            "message":
                f"LOW: {value:g} <= {point.warning_low:g}"
        }


    if (
        point.warning_high is not None
        and value >= point.warning_high
    ):

        return {
            "severity": "WARNING",
            "alarm": True,
            "condition": "HIGH",
            "message":
                f"HIGH: {value:g} >= {point.warning_high:g}"
        }


    return {
        "severity": "NORMAL",
        "alarm": False,
        "condition": "NORMAL",
        "message": "Within configured operating range"
    }


@app.get("/api/status")
def system_status(
    db: Session = Depends(get_db)
):

    devices = (
        db.query(Equipment)
        .all()
    )

    result = []

    severity_rank = {
        "NO_DATA": 0,
        "NORMAL": 1,
        "WARNING": 2,
        "CRITICAL": 3
    }


    for device in devices:

        telemetry = telemetry_state.get(
            device.equipment_id
        )

        data = (
            telemetry["data"]
            if telemetry
            else {}
        )

        point_results = []

        device_severity = "NO_DATA"


        for point in device.points:

            if point.point_type != "analog":
                continue

            value = telemetry_value(
                point,
                data
            )

            evaluation = evaluate_analog(
                point,
                value
            )

            severity = evaluation[
                "severity"
            ]

            if (
                severity_rank[severity]
                >
                severity_rank[device_severity]
            ):
                device_severity = severity


            point_results.append({

                "key": point.key,

                "name":
                    point.display_name,

                "unit":
                    point.unit,

                "value":
                    value,

                "severity":
                    severity,

                "alarm":
                    evaluation["alarm"],

                "condition":
                    evaluation.get(
                        "condition"
                    ),

                "message":
                    evaluation["message"],

                "limits": {

                    "low_low":
                        point.critical_low,

                    "low":
                        point.warning_low,

                    "high":
                        point.warning_high,

                    "high_high":
                        point.critical_high
                }
            })


        age = None

        if telemetry:

            age = (
                int(time.time())
                -
                telemetry["received_at"]
            )


        result.append({

            "equipment_id":
                device.equipment_id,

            "name":
                device.name,

            "protocol":
                device.protocol,

            "severity":
                device_severity,

            "telemetry_age_seconds":
                age,

            "mqtt_topic":
                telemetry["topic"]
                if telemetry
                else None,

            "points":
                point_results
        })


    return {
        "mqtt_connected":
            mqtt_connected,

        "timestamp":
            int(time.time()),

        "equipment":
            result
    }


@app.get("/api/alarms")
def active_alarms(
    db: Session = Depends(get_db)
):

    status = system_status(db)

    alarms = []

    for device in status["equipment"]:

        for point in device["points"]:

            if point["alarm"]:

                alarms.append({

                    "equipment_id":
                        device["equipment_id"],

                    "equipment_name":
                        device["name"],

                    **point
                })

    return {
        "count": len(alarms),
        "alarms": alarms
    }


# ---------------------------------------------------------
# EQUIPMENT CRUD
# ---------------------------------------------------------

@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "vdc-control-center",
        "version": "0.4.0",
        "mqtt_connected": mqtt_connected
    }


@app.get("/api/equipment")
def list_equipment(
    db: Session = Depends(get_db)
):

    return db.query(
        Equipment
    ).all()


@app.post("/api/equipment")
def create_equipment(
    data: EquipmentCreate,
    db: Session = Depends(get_db)
):

    existing = (
        db.query(Equipment)
        .filter(
            Equipment.equipment_id
            ==
            data.equipment_id
        )
        .first()
    )

    if existing:

        raise HTTPException(
            409,
            "Equipment ID already exists"
        )

    equipment = Equipment(
        **data.model_dump()
    )

    db.add(equipment)
    db.commit()
    db.refresh(equipment)

    return equipment


@app.get("/api/equipment/{equipment_id}")
def get_equipment(
    equipment_id: str,
    db: Session = Depends(get_db)
):

    equipment = (
        db.query(Equipment)
        .filter(
            Equipment.equipment_id
            ==
            equipment_id
        )
        .first()
    )

    if not equipment:

        raise HTTPException(
            404,
            "Equipment not found"
        )

    return equipment


@app.put("/api/equipment/{equipment_id}")
def update_equipment(
    equipment_id: str,
    data: EquipmentUpdate,
    db: Session = Depends(get_db)
):

    equipment = (
        db.query(Equipment)
        .filter(
            Equipment.equipment_id
            ==
            equipment_id
        )
        .first()
    )

    if not equipment:

        raise HTTPException(
            404,
            "Equipment not found"
        )

    for key, value in (
        data.model_dump().items()
    ):

        setattr(
            equipment,
            key,
            value
        )

    db.commit()
    db.refresh(equipment)

    return equipment


@app.delete("/api/equipment/{equipment_id}")
def delete_equipment(
    equipment_id: str,
    db: Session = Depends(get_db)
):

    equipment = (
        db.query(Equipment)
        .filter(
            Equipment.equipment_id
            ==
            equipment_id
        )
        .first()
    )

    if not equipment:

        raise HTTPException(
            404,
            "Equipment not found"
        )

    db.delete(equipment)
    db.commit()

    return {
        "deleted":
            equipment_id
    }


# ---------------------------------------------------------
# POINT CRUD
# ---------------------------------------------------------

@app.get("/api/equipment/{equipment_id}/points")
def list_points(
    equipment_id: str,
    db: Session = Depends(get_db)
):

    equipment = (
        db.query(Equipment)
        .filter(
            Equipment.equipment_id
            ==
            equipment_id
        )
        .first()
    )

    if not equipment:

        raise HTTPException(
            404,
            "Equipment not found"
        )

    return equipment.points


@app.post("/api/equipment/{equipment_id}/points")
def create_point(
    equipment_id: str,
    data: PointCreate,
    db: Session = Depends(get_db)
):

    equipment = (
        db.query(Equipment)
        .filter(
            Equipment.equipment_id
            ==
            equipment_id
        )
        .first()
    )

    if not equipment:

        raise HTTPException(
            404,
            "Equipment not found"
        )

    point = Point(
        equipment_pk=
            equipment.id,

        **data.model_dump()
    )

    db.add(point)
    db.commit()
    db.refresh(point)

    return point


@app.put("/api/points/{point_id}")
def update_point(
    point_id: int,
    data: PointCreate,
    db: Session = Depends(get_db)
):

    point = (
        db.query(Point)
        .filter(
            Point.id
            ==
            point_id
        )
        .first()
    )

    if not point:

        raise HTTPException(
            404,
            "Point not found"
        )

    for key, value in (
        data.model_dump().items()
    ):

        setattr(
            point,
            key,
            value
        )

    db.commit()
    db.refresh(point)

    return point


@app.delete("/api/points/{point_id}")
def delete_point(
    point_id: int,
    db: Session = Depends(get_db)
):

    point = (
        db.query(Point)
        .filter(
            Point.id
            ==
            point_id
        )
        .first()
    )

    if not point:

        raise HTTPException(
            404,
            "Point not found"
        )

    db.delete(point)
    db.commit()

    return {
        "deleted":
            point_id
    }


@app.get("/", response_class=HTMLResponse)
def control_center():

    return Path(
        "/app/app/templates/index.html"
    ).read_text()
