from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum


class PlantStatus(str, Enum):
    HEALTHY = "健康"
    NEEDS_WATER = "缺水"
    WITHERED = "枯黄"
    PEST = "虫害"
    NEEDS_PRUNING = "需修剪"
    NEEDS_REPLANT = "需补苗"


class TaskStatus(str, Enum):
    PENDING = "待处理"
    IN_PROGRESS = "处理中"
    COMPLETED = "已完成"
    OVERDUE = "已逾期"


class TaskType(str, Enum):
    WATERING = "补水"
    PRUNING = "修剪"
    REPLANT = "补苗"
    PEST_CONTROL = "除虫"
    FERTILIZE = "施肥"


@dataclass
class Plant:
    id: Optional[int] = None
    code: str = ""
    name: str = ""
    species: str = ""
    area: str = ""
    location: str = ""
    planted_date: Optional[str] = None
    last_check_date: Optional[str] = None
    next_check_date: Optional[str] = None
    check_cycle_days: int = 7
    status: str = PlantStatus.HEALTHY.value
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class Inspection:
    id: Optional[int] = None
    plant_code: str = ""
    plant_name: str = ""
    area: str = ""
    inspector: str = ""
    check_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    health_score: int = 100
    has_water_deficit: bool = False
    has_withered: bool = False
    has_pest: bool = False
    needs_pruning: bool = False
    needs_replant: bool = False
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class Photo:
    id: Optional[int] = None
    inspection_id: Optional[int] = None
    plant_code: str = ""
    file_path: str = ""
    description: str = ""
    taken_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class Task:
    id: Optional[int] = None
    task_no: str = ""
    plant_code: str = ""
    plant_name: str = ""
    area: str = ""
    task_type: str = ""
    description: str = ""
    assignee: str = ""
    priority: str = "中"
    due_date: Optional[str] = None
    status: str = TaskStatus.PENDING.value
    completed_at: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class Shift:
    id: Optional[int] = None
    shift_no: str = ""
    name: str = ""
    leader: str = ""
    members: str = ""
    start_time: str = ""
    end_time: Optional[str] = None
    status: str = "进行中"
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class ShiftHandover:
    id: Optional[int] = None
    shift_id: int = 0
    shift_no: str = ""
    handover_from: str = ""
    handover_to: str = ""
    handover_time: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    pending_task_ids: str = ""
    abnormal_plant_codes: str = ""
    unfinished_reason: str = ""
    remarks: str = ""
    confirmed: int = 0
    confirmed_at: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
