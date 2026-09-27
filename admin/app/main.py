import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import List, Optional

import paho.mqtt.client as mqtt
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Equipment, InvestigationCase, Point, WorkOrder


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Guively Virtual Data Center Control Center",
    version="0.5.0"
)

telemetry_state = {}
telemetry_history = defaultdict(lambda: deque(maxlen=600))
mqtt_connected = False
mqtt_client = None

alarm_first_seen = {}
alarm_acknowledged_at = {}

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


class CaseUpdate(BaseModel):
    impact: Optional[List[str]] = None
    evidence: Optional[List[str]] = None
    diagnosis: Optional[str] = None
    procedure_checks: Optional[List[str]] = None
    action_escalation: Optional[List[str]] = None
    recovery: Optional[List[str]] = None
    lessons_learned: Optional[List[str]] = None
    reviewed_items: Optional[List[str]] = None


class SimulatorCommand(BaseModel):
    mode: str


class WorkOrderCreate(BaseModel):
    disposition: str
    external_reference: Optional[str] = None
    priority: str = "P3"
    assigned_group: str = "Facilities"
    status: str = "OPEN"
    impact: str = ""
    requested_work: str = ""
    notes: str = ""
    reason: str = ""


def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = True
    print(f"[CONTROL CENTER] MQTT connected: {reason_code}", flush=True)
    client.subscribe("dc1/#")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = False
    print(f"[CONTROL CENTER] MQTT disconnected: {reason_code}", flush=True)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        device_id = payload.get("device_id")
        if not device_id:
            return

        received_at = int(time.time())
        telemetry_state[device_id] = {
            "topic": msg.topic,
            "received_at": received_at,
            "data": payload
        }
        telemetry_history[device_id].append({
            "timestamp": received_at,
            "data": payload
        })

    except Exception as exc:
        print(f"[CONTROL CENTER] Invalid telemetry: {exc}", flush=True)


def mqtt_worker():
    global mqtt_client
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="vdc-control-center"
    )
    mqtt_client = client
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    while True:
        try:
            print("[CONTROL CENTER] Connecting to MQTT...", flush=True)
            client.connect("mqtt", 1883, 60)
            client.loop_forever()
        except Exception as exc:
            print(f"[CONTROL CENTER] MQTT retry: {exc}", flush=True)
            time.sleep(3)


@app.on_event("startup")
def start_mqtt():
    thread = threading.Thread(target=mqtt_worker, daemon=True)
    thread.start()


def telemetry_value(point, data):
    candidates = [point.key, FIELD_MAP.get(point.key)]
    for candidate in candidates:
        if candidate and candidate in data:
            value = data[candidate]
            if isinstance(value, (int, float)):
                return float(value)
    return None


def evaluate_analog(point, value):
    if value is None:
        return {
            "severity": "NO_DATA",
            "alarm": False,
            "condition": "NO_DATA",
            "message": "No live telemetry"
        }

    if not point.alarm_enabled:
        return {
            "severity": "NORMAL",
            "alarm": False,
            "condition": "NORMAL",
            "message": "Alarm disabled"
        }

    if point.critical_low is not None and value <= point.critical_low:
        return {
            "severity": "CRITICAL",
            "alarm": True,
            "condition": "LOW-LOW",
            "message": f"LOW-LOW: {value:g} <= {point.critical_low:g}"
        }

    if point.critical_high is not None and value >= point.critical_high:
        return {
            "severity": "CRITICAL",
            "alarm": True,
            "condition": "HIGH-HIGH",
            "message": f"HIGH-HIGH: {value:g} >= {point.critical_high:g}"
        }

    if point.warning_low is not None and value <= point.warning_low:
        return {
            "severity": "WARNING",
            "alarm": True,
            "condition": "LOW",
            "message": f"LOW: {value:g} <= {point.warning_low:g}"
        }

    if point.warning_high is not None and value >= point.warning_high:
        return {
            "severity": "WARNING",
            "alarm": True,
            "condition": "HIGH",
            "message": f"HIGH: {value:g} >= {point.warning_high:g}"
        }

    return {
        "severity": "NORMAL",
        "alarm": False,
        "condition": "NORMAL",
        "message": "Within configured operating range"
    }


def threshold_for(point, condition):
    return {
        "LOW-LOW": point.critical_low,
        "LOW": point.warning_low,
        "HIGH": point.warning_high,
        "HIGH-HIGH": point.critical_high
    }.get(condition)


def make_alarm_id(equipment_id, point_key, condition):
    raw = f"{equipment_id}|{point_key}|{condition}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12].upper()


def json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def dump_list(value):
    return json.dumps(value or [])


def procedure_guidance(equipment_type, condition, severity):
    risk = "Potential equipment or service degradation. Confirm redundancy before any action."
    if severity == "CRITICAL":
        risk = "Critical operating limit exceeded. Treat as a potential service-impacting condition until verified."

    return {
        "condition": f"{condition} condition on {equipment_type or 'equipment'}",
        "risk": risk,
        "immediate_checks": [
            "Verify the alarm value and timestamp.",
            "Check correlated points and the recent trend.",
            "Confirm equipment run status and any dependent equipment.",
            "Check for additional alarms before changing anything."
        ],
        "safety_ppe": [
            "Follow the site SOP/EOP/MOP and JSA/JHA.",
            "Use PPE required by the hazard assessment.",
            "Do not open energized equipment or bypass interlocks unless trained and authorized.",
            "Apply LOTO and verify zero energy when the approved procedure requires it."
        ],
        "escalate_when": [
            "The condition is critical, worsening, or affects redundancy.",
            "The required action is outside your authorization or training.",
            "The procedure does not match the observed equipment state."
        ],
        "next_step": "Collect evidence, document your diagnosis, then escalate or create/link a work order if corrective work is required."
    }


def work_order_to_dict(wo):
    if wo is None:
        return None
    return {
        "id": wo.id,
        "case_id": wo.case_id,
        "disposition": wo.disposition,
        "work_order_number": wo.work_order_number,
        "external_reference": wo.external_reference,
        "priority": wo.priority,
        "assigned_group": wo.assigned_group,
        "status": wo.status,
        "impact": wo.impact,
        "requested_work": wo.requested_work,
        "notes": wo.notes,
        "reason": wo.reason,
        "created_at": wo.created_at,
        "completed_at": wo.completed_at
    }


def case_to_dict(case):
    wo = case.work_orders[-1] if case.work_orders else None
    return {
        "id": case.id,
        "case_number": case.case_number,
        "status": case.status,
        "alarm_id": case.alarm_id,
        "equipment_id": case.equipment_id,
        "equipment_name": case.equipment_name,
        "point_key": case.point_key,
        "point_name": case.point_name,
        "severity": case.severity,
        "condition": case.condition,
        "alarm_value": case.alarm_value,
        "alarm_unit": case.alarm_unit,
        "alarm_threshold": case.alarm_threshold,
        "opened_at": case.opened_at,
        "acknowledged_at": case.acknowledged_at,
        "closed_at": case.closed_at,
        "initial_evidence": json_list(case.initial_evidence),
        "impact": json_list(case.impact),
        "evidence": json_list(case.evidence),
        "diagnosis": case.diagnosis or "",
        "procedure_checks": json_list(case.procedure_checks),
        "action_escalation": json_list(case.action_escalation),
        "recovery": json_list(case.recovery),
        "lessons_learned": json_list(case.lessons_learned),
        "reviewed_items": json_list(case.reviewed_items),
        "work_order": work_order_to_dict(wo) if wo else None
    }


def get_case_or_404(db, case_id):
    case = db.query(InvestigationCase).filter(
        InvestigationCase.id == case_id
    ).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return case


def get_equipment_or_404(db, equipment_id):
    equipment = db.query(Equipment).filter(
        Equipment.equipment_id == equipment_id
    ).first()
    if not equipment:
        raise HTTPException(404, "Equipment not found")
    return equipment


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "vdc-control-center",
        "version": "0.5.0",
        "mqtt_connected": mqtt_connected
    }


@app.get("/api/status")
def system_status(db: Session = Depends(get_db)):
    devices = db.query(Equipment).all()
    result = []

    severity_rank = {
        "NO_DATA": 0,
        "NORMAL": 1,
        "WARNING": 2,
        "CRITICAL": 3
    }

    for device in devices:
        telemetry = telemetry_state.get(device.equipment_id)
        data = telemetry["data"] if telemetry else {}
        point_results = []
        device_severity = "NO_DATA"

        for point in device.points:
            if point.point_type != "analog" or not point.enabled:
                continue

            value = telemetry_value(point, data)
            evaluation = evaluate_analog(point, value)
            severity = evaluation["severity"]

            if severity_rank[severity] > severity_rank[device_severity]:
                device_severity = severity

            point_results.append({
                "key": point.key,
                "name": point.display_name,
                "unit": point.unit,
                "value": value,
                "severity": severity,
                "alarm": evaluation["alarm"],
                "condition": evaluation.get("condition"),
                "message": evaluation["message"],
                "threshold": threshold_for(point, evaluation.get("condition")),
                "limits": {
                    "low_low": point.critical_low,
                    "low": point.warning_low,
                    "high": point.warning_high,
                    "high_high": point.critical_high
                }
            })

        age = None
        if telemetry:
            age = int(time.time()) - telemetry["received_at"]

        result.append({
            "equipment_id": device.equipment_id,
            "name": device.name,
            "equipment_type": device.equipment_type,
            "location": device.location,
            "protocol": device.protocol,
            "severity": device_severity,
            "telemetry_age_seconds": age,
            "mqtt_topic": telemetry["topic"] if telemetry else None,
            "simulation_mode": data.get("simulation_mode"),
            "points": point_results
        })

    return {
        "mqtt_connected": mqtt_connected,
        "timestamp": int(time.time()),
        "equipment": result
    }


@app.get("/api/alarms")
def active_alarms(db: Session = Depends(get_db)):
    status = system_status(db)
    alarms = []
    active_ids = set()
    now = int(time.time())

    for device in status["equipment"]:
        for point in device["points"]:
            if not point["alarm"]:
                continue

            alarm_id = make_alarm_id(
                device["equipment_id"],
                point["key"],
                point["condition"]
            )
            active_ids.add(alarm_id)
            first_seen = alarm_first_seen.setdefault(alarm_id, now)

            alarms.append({
                "alarm_id": alarm_id,
                "equipment_id": device["equipment_id"],
                "equipment_name": device["name"],
                "equipment_type": device["equipment_type"],
                "location": device["location"],
                "first_seen": first_seen,
                "acknowledged_at": alarm_acknowledged_at.get(alarm_id),
                **point
            })

    for alarm_id in list(alarm_first_seen):
        if alarm_id not in active_ids:
            alarm_first_seen.pop(alarm_id, None)
            alarm_acknowledged_at.pop(alarm_id, None)

    alarms.sort(
        key=lambda a: (
            0 if a["severity"] == "CRITICAL" else 1,
            a["first_seen"]
        )
    )

    return {
        "count": len(alarms),
        "alarms": alarms
    }


@app.post("/api/alarms/{alarm_id}/acknowledge")
def acknowledge_alarm(alarm_id: str, db: Session = Depends(get_db)):
    alarms = active_alarms(db)["alarms"]
    alarm = next((a for a in alarms if a["alarm_id"] == alarm_id), None)
    if not alarm:
        raise HTTPException(404, "Active alarm not found")

    ts = int(time.time())
    alarm_acknowledged_at[alarm_id] = ts
    return {"alarm_id": alarm_id, "acknowledged_at": ts}


@app.get("/api/trends/{equipment_id}")
def trend_data(
    equipment_id: str,
    point: str,
    limit: int = 120,
    db: Session = Depends(get_db)
):
    get_equipment_or_404(db, equipment_id)
    limit = max(10, min(limit, 600))
    history = list(telemetry_history.get(equipment_id, []))[-limit:]

    series = []
    for sample in history:
        data = sample["data"]
        candidates = [point, FIELD_MAP.get(point)]
        value = None
        for key in candidates:
            if key and isinstance(data.get(key), (int, float)):
                value = float(data[key])
                break
        if value is not None:
            series.append({
                "timestamp": sample["timestamp"],
                "value": value
            })

    return {
        "equipment_id": equipment_id,
        "point": point,
        "count": len(series),
        "series": series
    }


VALID_MOTOR_MODES = {
    "NORMAL",
    "OVERHEAT",
    "OVERCURRENT",
    "UNDERVOLTAGE",
    "BEARING_FAULT",
    "VIBRATION",
    "STOPPED"
}


@app.post("/api/simulator/{equipment_id}/mode")
def set_simulator_mode(equipment_id: str, data: SimulatorCommand):
    mode = data.mode.upper().strip()
    if equipment_id != "MOTOR-01":
        raise HTTPException(400, "Browser fault injection is currently implemented for MOTOR-01")
    if mode not in VALID_MOTOR_MODES:
        raise HTTPException(400, f"Unsupported simulator mode: {mode}")
    if not mqtt_client or not mqtt_connected:
        raise HTTPException(503, "MQTT is not connected")

    topic = f"dc1/mechanical/motor/{equipment_id}/command"
    info = mqtt_client.publish(topic, mode)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise HTTPException(503, "Failed to publish simulator command")

    return {
        "equipment_id": equipment_id,
        "mode": mode,
        "topic": topic
    }


@app.get("/api/cases")
def list_cases(db: Session = Depends(get_db)):
    cases = db.query(InvestigationCase).order_by(
        InvestigationCase.id.desc()
    ).all()
    return [case_to_dict(case) for case in cases]


@app.get("/api/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    equipment = db.query(Equipment).filter(
        Equipment.equipment_id == case.equipment_id
    ).first()
    data = case_to_dict(case)
    data["procedure"] = procedure_guidance(
        equipment.equipment_type if equipment else "",
        case.condition,
        case.severity
    )
    return data


@app.post("/api/alarms/{alarm_id}/cases")
def create_case_from_alarm(alarm_id: str, db: Session = Depends(get_db)):
    alarms = active_alarms(db)["alarms"]
    alarm = next((a for a in alarms if a["alarm_id"] == alarm_id), None)
    if not alarm:
        raise HTTPException(404, "Active alarm not found")

    existing = db.query(InvestigationCase).filter(
        InvestigationCase.alarm_id == alarm_id,
        InvestigationCase.status == "OPEN"
    ).first()
    if existing:
        return case_to_dict(existing)

    next_number = (db.query(InvestigationCase).count() + 1)
    case_number = f"CASE-{next_number:03d}"

    initial_evidence = [
        (
            f"Alarm {alarm['condition']} on {alarm['equipment_id']} / "
            f"{alarm['name']}: {alarm['value']:g} {alarm['unit'] or ''}."
        ).strip(),
        f"Severity: {alarm['severity']}.",
        f"Alarm first observed at {alarm['first_seen']}."
    ]

    impact = [
        f"Potential impact to {alarm['equipment_name']} operation.",
        "No confirmed downstream impact yet — verify redundancy and dependent equipment."
    ]

    evidence = [
        (
            f"Live {alarm['name']}: {alarm['value']:g} "
            f"{alarm['unit'] or ''}; threshold: "
            f"{alarm['threshold'] if alarm['threshold'] is not None else 'configured limit'}."
        ).strip(),
        "Review the recent trend and at least one correlated signal.",
        "Check equipment state, alarm history, and dependent equipment."
    ]

    procedure_checks = [
        "Confirm the applicable SOP/EOP/MOP before making changes.",
        "Review JSA/JHA, authorization level, PPE, and LOTO requirements.",
        "Compare the observed condition with the one-line, schematic, or P&ID when applicable."
    ]

    action_escalation = [
        "Acknowledge the alarm and document the initial assessment.",
        "Escalate if the condition is critical, worsening, or outside your authorization.",
        "Create or link a work order when corrective maintenance is required."
    ]

    case = InvestigationCase(
        case_number=case_number,
        alarm_id=alarm_id,
        equipment_id=alarm["equipment_id"],
        equipment_name=alarm["equipment_name"],
        point_key=alarm["key"],
        point_name=alarm["name"],
        severity=alarm["severity"],
        condition=alarm["condition"],
        alarm_value=alarm["value"],
        alarm_unit=alarm["unit"] or "",
        alarm_threshold=alarm["threshold"],
        opened_at=int(time.time()),
        acknowledged_at=alarm.get("acknowledged_at"),
        initial_evidence=dump_list(initial_evidence),
        impact=dump_list(impact),
        evidence=dump_list(evidence),
        diagnosis="",
        procedure_checks=dump_list(procedure_checks),
        action_escalation=dump_list(action_escalation),
        recovery=dump_list([]),
        lessons_learned=dump_list([]),
        reviewed_items=dump_list([])
    )

    db.add(case)
    db.commit()
    db.refresh(case)
    return case_to_dict(case)


@app.put("/api/cases/{case_id}")
def update_case(
    case_id: int,
    data: CaseUpdate,
    db: Session = Depends(get_db)
):
    case = get_case_or_404(db, case_id)
    payload = data.model_dump(exclude_unset=True)

    list_fields = {
        "impact",
        "evidence",
        "procedure_checks",
        "action_escalation",
        "recovery",
        "lessons_learned",
        "reviewed_items"
    }

    for key, value in payload.items():
        setattr(case, key, dump_list(value) if key in list_fields else value)

    db.commit()
    db.refresh(case)
    return case_to_dict(case)


@app.post("/api/cases/{case_id}/work-order")
def set_work_order(
    case_id: int,
    data: WorkOrderCreate,
    db: Session = Depends(get_db)
):
    case = get_case_or_404(db, case_id)
    disposition = data.disposition.upper()

    if disposition not in {"CREATE", "LINK", "NONE"}:
        raise HTTPException(400, "Disposition must be CREATE, LINK, or NONE")

    if disposition == "LINK" and not data.external_reference:
        raise HTTPException(400, "External work-order reference is required")

    if disposition == "NONE" and not data.reason.strip():
        raise HTTPException(400, "Reason is required when no corrective work is needed")

    existing = case.work_orders[-1] if case.work_orders else None
    if existing:
        wo = existing
    else:
        wo = WorkOrder(case_id=case.id, disposition=disposition)
        db.add(wo)

    wo.disposition = disposition
    wo.external_reference = data.external_reference
    wo.priority = data.priority
    wo.assigned_group = data.assigned_group
    wo.status = data.status
    wo.impact = data.impact
    wo.requested_work = data.requested_work
    wo.notes = data.notes
    wo.reason = data.reason

    if disposition == "CREATE" and not wo.work_order_number:
        next_number = db.query(WorkOrder).count() + 1
        wo.work_order_number = f"WO-{next_number:03d}"
    elif disposition != "CREATE":
        wo.work_order_number = None

    if wo.status.upper() in {"DONE", "COMPLETED", "CLOSED"}:
        wo.completed_at = int(time.time())
    else:
        wo.completed_at = None

    db.commit()
    db.refresh(wo)
    return work_order_to_dict(wo)


@app.post("/api/cases/{case_id}/close")
def close_case(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)

    if not case.work_orders:
        raise HTTPException(
            400,
            "Choose Create Work Order, Link Existing Work Order, or No Corrective Work Required first"
        )

    if not json_list(case.recovery):
        raise HTTPException(
            400,
            "Document recovery verification before closing the case"
        )

    case.status = "CLOSED"
    case.closed_at = int(time.time())
    db.commit()
    db.refresh(case)
    return case_to_dict(case)


@app.get("/api/equipment")
def list_equipment(db: Session = Depends(get_db)):
    return db.query(Equipment).all()


@app.post("/api/equipment")
def create_equipment(data: EquipmentCreate, db: Session = Depends(get_db)):
    existing = db.query(Equipment).filter(
        Equipment.equipment_id == data.equipment_id
    ).first()
    if existing:
        raise HTTPException(409, "Equipment ID already exists")

    equipment = Equipment(**data.model_dump())
    db.add(equipment)
    db.commit()
    db.refresh(equipment)
    return equipment


@app.get("/api/equipment/{equipment_id}")
def get_equipment(equipment_id: str, db: Session = Depends(get_db)):
    return get_equipment_or_404(db, equipment_id)


@app.put("/api/equipment/{equipment_id}")
def update_equipment(
    equipment_id: str,
    data: EquipmentUpdate,
    db: Session = Depends(get_db)
):
    equipment = get_equipment_or_404(db, equipment_id)

    for key, value in data.model_dump().items():
        setattr(equipment, key, value)

    db.commit()
    db.refresh(equipment)
    return equipment


@app.delete("/api/equipment/{equipment_id}")
def delete_equipment(equipment_id: str, db: Session = Depends(get_db)):
    equipment = get_equipment_or_404(db, equipment_id)
    db.delete(equipment)
    db.commit()
    return {"deleted": equipment_id}


@app.get("/api/equipment/{equipment_id}/points")
def list_points(equipment_id: str, db: Session = Depends(get_db)):
    equipment = get_equipment_or_404(db, equipment_id)
    return equipment.points


@app.post("/api/equipment/{equipment_id}/points")
def create_point(
    equipment_id: str,
    data: PointCreate,
    db: Session = Depends(get_db)
):
    equipment = get_equipment_or_404(db, equipment_id)

    point = Point(
        equipment_pk=equipment.id,
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
    point = db.query(Point).filter(Point.id == point_id).first()
    if not point:
        raise HTTPException(404, "Point not found")

    for key, value in data.model_dump().items():
        setattr(point, key, value)

    db.commit()
    db.refresh(point)
    return point


@app.delete("/api/points/{point_id}")
def delete_point(point_id: int, db: Session = Depends(get_db)):
    point = db.query(Point).filter(Point.id == point_id).first()
    if not point:
        raise HTTPException(404, "Point not found")

    db.delete(point)
    db.commit()
    return {"deleted": point_id}


@app.get("/", response_class=HTMLResponse)
def control_center():
    return Path("/app/app/templates/index.html").read_text()
