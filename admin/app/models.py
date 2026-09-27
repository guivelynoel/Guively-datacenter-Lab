import time

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True)
    equipment_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    equipment_type = Column(String, nullable=False)
    location = Column(String, default="")
    protocol = Column(String, default="MQTT")
    enabled = Column(Boolean, default=True)

    points = relationship(
        "Point",
        back_populates="equipment",
        cascade="all, delete-orphan"
    )


class Point(Base):
    __tablename__ = "points"

    id = Column(Integer, primary_key=True)
    equipment_pk = Column(
        Integer,
        ForeignKey("equipment.id"),
        nullable=False
    )

    key = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    point_type = Column(String, default="analog")
    unit = Column(String, default="")

    normal_value = Column(Float, nullable=True)
    critical_low = Column(Float, nullable=True)
    warning_low = Column(Float, nullable=True)
    warning_high = Column(Float, nullable=True)
    critical_high = Column(Float, nullable=True)

    alarm_enabled = Column(Boolean, default=True)

    binary_zero_label = Column(String, nullable=True)
    binary_one_label = Column(String, nullable=True)
    state_labels = Column(String, nullable=True)

    enabled = Column(Boolean, default=True)

    equipment = relationship(
        "Equipment",
        back_populates="points"
    )


class InvestigationCase(Base):
    __tablename__ = "investigation_cases"

    id = Column(Integer, primary_key=True)
    case_number = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, default="OPEN", nullable=False)

    alarm_id = Column(String, nullable=False, index=True)
    equipment_id = Column(String, nullable=False)
    equipment_name = Column(String, default="")
    point_key = Column(String, nullable=False)
    point_name = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    condition = Column(String, default="")
    alarm_value = Column(Float, nullable=True)
    alarm_unit = Column(String, default="")
    alarm_threshold = Column(Float, nullable=True)

    opened_at = Column(Integer, default=lambda: int(time.time()), nullable=False)
    acknowledged_at = Column(Integer, nullable=True)
    closed_at = Column(Integer, nullable=True)

    initial_evidence = Column(Text, default="[]")
    impact = Column(Text, default="[]")
    evidence = Column(Text, default="[]")
    diagnosis = Column(Text, default="")
    procedure_checks = Column(Text, default="[]")
    action_escalation = Column(Text, default="[]")
    recovery = Column(Text, default="[]")
    lessons_learned = Column(Text, default="[]")
    reviewed_items = Column(Text, default="[]")

    work_orders = relationship(
        "WorkOrder",
        back_populates="case",
        cascade="all, delete-orphan"
    )


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True)
    case_id = Column(
        Integer,
        ForeignKey("investigation_cases.id"),
        nullable=False,
        index=True
    )
    disposition = Column(String, nullable=False)
    work_order_number = Column(String, nullable=True, unique=True)
    external_reference = Column(String, nullable=True)
    priority = Column(String, default="P3")
    assigned_group = Column(String, default="Facilities")
    status = Column(String, default="OPEN")
    impact = Column(Text, default="")
    requested_work = Column(Text, default="")
    notes = Column(Text, default="")
    reason = Column(Text, default="")
    created_at = Column(Integer, default=lambda: int(time.time()), nullable=False)
    completed_at = Column(Integer, nullable=True)

    case = relationship(
        "InvestigationCase",
        back_populates="work_orders"
    )
