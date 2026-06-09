import csv
import os
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from .models import Plant, Inspection, Task, Photo, PlantStatus, TaskType, TaskStatus

console = Console()


def parse_date(date_str: str) -> Optional[str]:
    if not date_str or str(date_str).strip() == "" or str(date_str).lower() == "nan":
        return None
    date_str = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        ts = pd.to_datetime(date_str)
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def parse_int(val, default: int = 0) -> int:
    if val is None or str(val).strip() == "" or str(val).lower() == "nan":
        return default
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return default


def parse_bool(val) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y", "是", "有", "需", "需要", "√", "✓", "对")


def _clean_str(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "nat"):
        return ""
    return s


def import_plants_from_csv(file_path: str) -> List[Plant]:
    plants = []
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(file_path)
    else:
        df = pd.read_csv(file_path, encoding="utf-8-sig")

    col_map = _detect_columns(df.columns.tolist())

    for _, row in df.iterrows():
        code = _clean_str(row.get(col_map.get("code", "code"), ""))
        name = _clean_str(row.get(col_map.get("name", "name"), ""))
        if not code or not name:
            continue

        status_val = _clean_str(row.get(col_map.get("status", "status"), PlantStatus.HEALTHY.value))
        plant = Plant(
            code=code,
            name=name,
            species=_clean_str(row.get(col_map.get("species", "species"), "")),
            area=_clean_str(row.get(col_map.get("area", "area"), "")),
            location=_clean_str(row.get(col_map.get("location", "location"), "")),
            planted_date=parse_date(row.get(col_map.get("planted_date", "planted_date"))),
            last_check_date=parse_date(row.get(col_map.get("last_check_date", "last_check_date"))),
            next_check_date=parse_date(row.get(col_map.get("next_check_date", "next_check_date"))),
            check_cycle_days=parse_int(row.get(col_map.get("check_cycle_days", "check_cycle_days")), 7),
            status=status_val or PlantStatus.HEALTHY.value,
            notes=_clean_str(row.get(col_map.get("notes", "notes"), "")),
        )
        if plant.check_cycle_days <= 0:
            plant.check_cycle_days = 7
        plants.append(plant)

    return plants


def _detect_columns(columns: List[str]) -> dict:
    mapping = {}
    col_lower = {str(c).strip().lower(): c for c in columns}
    aliases = {
        "code": ["编号", "编码", "植株编号", "绿植编号", "code", "id", "no", "no."],
        "name": ["名称", "绿植名称", "植物名称", "name", "plant_name"],
        "species": ["品种", "种类", "物种", "species", "type"],
        "area": ["区域", "片区", "园区", "area", "zone", "region"],
        "location": ["位置", "地点", "具体位置", "location", "address", "position"],
        "planted_date": ["种植日期", "种植时间", "栽种日期", "planted_date", "plant_date"],
        "last_check_date": ["上次巡检", "上次巡检日期", "last_check", "last_check_date"],
        "next_check_date": ["下次巡检", "下次巡检日期", "next_check", "next_check_date"],
        "check_cycle_days": ["巡检周期", "周期天数", "cycle_days", "check_cycle", "days"],
        "status": ["状态", "健康状态", "status", "health"],
        "notes": ["备注", "说明", "notes", "remark", "remarks"],
    }
    for key, alias_list in aliases.items():
        for alias in alias_list:
            if alias.lower() in col_lower:
                mapping[key] = col_lower[alias.lower()]
                break
    return mapping


def print_plants_table(plants: List[Plant], title: str = "绿植列表"):
    if not plants:
        console.print(Panel("[yellow]暂无数据[/yellow]", title=title, border_style="yellow"))
        return

    table = Table(title=title, box=box.ROUNDED, show_lines=False)
    table.add_column("编号", style="cyan", no_wrap=True)
    table.add_column("名称", style="green")
    table.add_column("品种", style="magenta")
    table.add_column("区域", style="blue")
    table.add_column("位置", style="white")
    table.add_column("上次巡检", style="yellow")
    table.add_column("下次巡检", style="yellow")
    table.add_column("状态", style="bold")

    for p in plants:
        status_style = _status_style(p.status)
        table.add_row(
            p.code, p.name, p.species or "-", p.area or "-", p.location or "-",
            p.last_check_date or "-", p.next_check_date or "-",
            f"[{status_style}]{p.status}[/{status_style}]"
        )
    console.print(table)


def print_inspections_table(insps: List[Inspection], title: str = "巡检记录", show_notes: bool = False):
    if not insps:
        console.print(Panel("[yellow]暂无巡检记录[/yellow]", title=title, border_style="yellow"))
        return

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("时间", style="cyan", no_wrap=True)
    table.add_column("编号", style="blue")
    table.add_column("名称", style="green")
    table.add_column("区域", style="magenta")
    table.add_column("巡检人", style="white")
    table.add_column("评分", justify="right", style="yellow")
    table.add_column("异常", style="bold red")

    for i in insps:
        flags = []
        if i.has_water_deficit:
            flags.append("💧缺水")
        if i.has_withered:
            flags.append("🍂枯黄")
        if i.has_pest:
            flags.append("🐛虫害")
        if i.needs_pruning:
            flags.append("✂️修剪")
        if i.needs_replant:
            flags.append("🌱补苗")
        flags_str = "、".join(flags) if flags else "✓正常"
        score_style = "green" if i.health_score >= 80 else ("yellow" if i.health_score >= 60 else "red")
        table.add_row(
            i.check_date, i.plant_code, i.plant_name or "-", i.area or "-",
            i.inspector or "-", f"[{score_style}]{i.health_score}[/{score_style}]",
            flags_str
        )
    console.print(table)


def print_tasks_table(tasks: List[Task], title: str = "养护任务列表"):
    if not tasks:
        console.print(Panel("[yellow]暂无任务[/yellow]", title=title, border_style="yellow"))
        return

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("任务号", style="cyan", no_wrap=True)
    table.add_column("类型", style="magenta")
    table.add_column("编号", style="blue")
    table.add_column("名称", style="green")
    table.add_column("区域", style="white")
    table.add_column("责任人", style="yellow")
    table.add_column("优先级", justify="center")
    table.add_column("截止日期", style="cyan")
    table.add_column("状态", style="bold")

    for t in tasks:
        status_style = _task_status_style(t.status)
        prio_style = {"高": "bold red", "中": "bold yellow", "低": "bold green"}.get(t.priority, "white")
        table.add_row(
            t.task_no, t.task_type, t.plant_code, t.plant_name or "-",
            t.area or "-", t.assignee or "[dim]未指派[/dim]",
            f"[{prio_style}]{t.priority}[/{prio_style}]",
            t.due_date or "-", f"[{status_style}]{t.status}[/{status_style}]"
        )
    console.print(table)


def print_photos_table(photos: List[Photo], title: str = "照片记录"):
    if not photos:
        console.print(Panel("[yellow]暂无照片[/yellow]", title=title, border_style="yellow"))
        return

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("植株编号", style="blue")
    table.add_column("文件路径", style="white")
    table.add_column("描述", style="green")
    table.add_column("拍摄时间", style="yellow")

    for p in photos:
        table.add_row(
            str(p.id or "-"), p.plant_code or "-", p.file_path,
            p.description or "-", p.taken_at or "-"
        )
    console.print(table)


def _status_style(status: str) -> str:
    mapping = {
        PlantStatus.HEALTHY.value: "bold green",
        PlantStatus.NEEDS_WATER.value: "bold blue",
        PlantStatus.WITHERED.value: "bold yellow",
        PlantStatus.PEST.value: "bold red",
        PlantStatus.NEEDS_PRUNING.value: "bold magenta",
        PlantStatus.NEEDS_REPLANT.value: "bold cyan",
    }
    return mapping.get(status, "white")


def _task_status_style(status: str) -> str:
    mapping = {
        TaskStatus.PENDING.value: "bold yellow",
        TaskStatus.IN_PROGRESS.value: "bold blue",
        TaskStatus.COMPLETED.value: "bold green",
        TaskStatus.OVERDUE.value: "bold red",
    }
    return mapping.get(status, "white")


def determine_plant_status(insp: Inspection) -> str:
    priority = [
        (insp.needs_replant, PlantStatus.NEEDS_REPLANT.value),
        (insp.has_pest, PlantStatus.PEST.value),
        (insp.has_withered, PlantStatus.WITHERED.value),
        (insp.has_water_deficit, PlantStatus.NEEDS_WATER.value),
        (insp.needs_pruning, PlantStatus.NEEDS_PRUNING.value),
    ]
    for flag, status in priority:
        if flag:
            return status
    if insp.health_score >= 80:
        return PlantStatus.HEALTHY.value
    if insp.health_score >= 60:
        return PlantStatus.WITHERED.value
    return PlantStatus.NEEDS_REPLANT.value


def generate_tasks_from_inspection(insp: Inspection, assignee: str = "",
                                    priority: str = "中", due_days: int = 3) -> List[Task]:
    tasks = []
    base_date = datetime.now()
    due_date = (base_date + timedelta(days=due_days)).strftime("%Y-%m-%d")

    task_map = [
        (insp.has_water_deficit, TaskType.WATERING.value, f"【{insp.plant_name}】需要及时补水"),
        (insp.has_withered, TaskType.WATERING.value, f"【{insp.plant_name}】出现枯黄，需加强养护补水"),
        (insp.has_pest, TaskType.PEST_CONTROL.value, f"【{insp.plant_name}】发现虫害，需喷洒药剂"),
        (insp.needs_pruning, TaskType.PRUNING.value, f"【{insp.plant_name}】枝条过密，需修剪整形"),
        (insp.needs_replant, TaskType.REPLANT.value, f"【{insp.plant_name}】状态极差，需重新补苗"),
    ]
    for flag, ttype, desc in task_map:
        if flag:
            tasks.append(Task(
                plant_code=insp.plant_code,
                plant_name=insp.plant_name,
                area=insp.area,
                task_type=ttype,
                description=desc,
                assignee=assignee,
                priority=priority,
                due_date=due_date,
                status=TaskStatus.PENDING.value,
            ))
    return tasks


def days_between(d1: str, d2: Optional[str] = None) -> int:
    try:
        date1 = datetime.strptime(d1[:10], "%Y-%m-%d")
        if d2:
            date2 = datetime.strptime(d2[:10], "%Y-%m-%d")
        else:
            date2 = datetime.now()
        return (date2 - date1).days
    except Exception:
        return 0
