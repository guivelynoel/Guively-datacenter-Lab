from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String
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

    # Point identity
    key = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    point_type = Column(String, default="analog")

    # Engineering unit
    unit = Column(String, default="")

    # Analog values
    normal_value = Column(Float, nullable=True)

    # LOW-LOW
    critical_low = Column(Float, nullable=True)

    # LOW
    warning_low = Column(Float, nullable=True)

    # HIGH
    warning_high = Column(Float, nullable=True)

    # HIGH-HIGH
    critical_high = Column(Float, nullable=True)

    # Alarm configuration
    alarm_enabled = Column(Boolean, default=True)

    # Binary point labels
    binary_zero_label = Column(String, nullable=True)
    binary_one_label = Column(String, nullable=True)

    # Multistate labels stored as text:
    # HAND,OFF,AUTO
    state_labels = Column(String, nullable=True)

    enabled = Column(Boolean, default=True)

    equipment = relationship(
        "Equipment",
        back_populates="points"
    )
