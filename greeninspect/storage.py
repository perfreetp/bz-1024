import sqlite3
import os
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

from .models import Plant, Inspection, Photo, Task, PlantStatus, TaskStatus, TaskType, Shift, ShiftHandover


DEFAULT_DB_PATH = os.path.join(os.getcwd(), ".greeninspect", "data.db")


def _resolve_db_path(db_path: str) -> str:
    if not db_path:
        return DEFAULT_DB_PATH
    if not os.path.isabs(db_path):
        db_path = os.path.abspath(db_path)
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return db_path


class Storage:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = _resolve_db_path(db_path)
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            c = conn.cursor()
            c.executescript("""
                CREATE TABLE IF NOT EXISTS plants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    species TEXT,
                    area TEXT,
                    location TEXT,
                    planted_date TEXT,
                    last_check_date TEXT,
                    next_check_date TEXT,
                    check_cycle_days INTEGER DEFAULT 7,
                    status TEXT DEFAULT '健康',
                    notes TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS inspections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plant_code TEXT NOT NULL,
                    plant_name TEXT,
                    area TEXT,
                    inspector TEXT,
                    check_date TEXT,
                    health_score INTEGER DEFAULT 100,
                    has_water_deficit INTEGER DEFAULT 0,
                    has_withered INTEGER DEFAULT 0,
                    has_pest INTEGER DEFAULT 0,
                    needs_pruning INTEGER DEFAULT 0,
                    needs_replant INTEGER DEFAULT 0,
                    notes TEXT,
                    created_at TEXT,
                    FOREIGN KEY (plant_code) REFERENCES plants(code)
                );

                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inspection_id INTEGER,
                    plant_code TEXT,
                    file_path TEXT,
                    description TEXT,
                    taken_at TEXT,
                    created_at TEXT,
                    FOREIGN KEY (inspection_id) REFERENCES inspections(id)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_no TEXT UNIQUE,
                    plant_code TEXT,
                    plant_name TEXT,
                    area TEXT,
                    task_type TEXT,
                    description TEXT,
                    assignee TEXT,
                    priority TEXT DEFAULT '中',
                    due_date TEXT,
                    status TEXT DEFAULT '待处理',
                    completed_at TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_no TEXT UNIQUE NOT NULL,
                    name TEXT,
                    leader TEXT,
                    members TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    status TEXT DEFAULT '进行中',
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS shift_handovers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_id INTEGER NOT NULL,
                    shift_no TEXT,
                    handover_from TEXT,
                    handover_to TEXT,
                    handover_time TEXT,
                    pending_task_ids TEXT,
                    abnormal_plant_codes TEXT,
                    overdue_plant_codes TEXT,
                    unfinished_reason TEXT,
                    remarks TEXT,
                    confirmed INTEGER DEFAULT 0,
                    confirmed_at TEXT,
                    created_at TEXT,
                    FOREIGN KEY (shift_id) REFERENCES shifts(id)
                );

                CREATE TABLE IF NOT EXISTS shift_pending_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    handover_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL,
                    ref_id INTEGER,
                    ref_code TEXT,
                    title TEXT,
                    original_assignee TEXT,
                    current_assignee TEXT,
                    reason TEXT,
                    status TEXT DEFAULT '待处理',
                    process_result TEXT,
                    processed_by TEXT,
                    processed_at TEXT,
                    created_at TEXT,
                    FOREIGN KEY (handover_id) REFERENCES shift_handovers(id)
                );

                CREATE INDEX IF NOT EXISTS idx_plants_area ON plants(area);
                CREATE INDEX IF NOT EXISTS idx_inspections_plant ON inspections(plant_code);
                CREATE INDEX IF NOT EXISTS idx_inspections_date ON inspections(check_date);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
                CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
                CREATE INDEX IF NOT EXISTS idx_shifts_status ON shifts(status);
                CREATE INDEX IF NOT EXISTS idx_handovers_shift ON shift_handovers(shift_id);
            """)

    def is_initialized(self) -> bool:
        try:
            with self._get_conn() as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM plants")
                return True
        except Exception:
            return False

    # ==================== Plants ====================
    def add_plant(self, plant: Plant) -> int:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO plants (code, name, species, area, location, planted_date,
                    last_check_date, next_check_date, check_cycle_days, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plant.code, plant.name, plant.species, plant.area, plant.location,
                plant.planted_date, plant.last_check_date, plant.next_check_date,
                plant.check_cycle_days, plant.status, plant.notes, plant.created_at
            ))
            return c.lastrowid

    def batch_add_plants(self, plants: List[Plant]) -> int:
        count = 0
        for p in plants:
            try:
                self.add_plant(p)
                count += 1
            except sqlite3.IntegrityError:
                continue
        return count

    def get_plant_by_code(self, code: str) -> Optional[Plant]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM plants WHERE code = ?", (code,))
            row = c.fetchone()
            return self._row_to_plant(row) if row else None

    def search_plants(self, keyword: str = "", area: str = "", status: str = "") -> List[Plant]:
        sql = "SELECT * FROM plants WHERE 1=1"
        params = []
        if keyword:
            sql += " AND (code LIKE ? OR name LIKE ? OR species LIKE ? OR location LIKE ?)"
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw, kw])
        if area:
            sql += " AND area = ?"
            params.append(area)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY area, code"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_plant(r) for r in c.fetchall()]

    def get_pending_check_plants(self, area: str = "") -> List[Plant]:
        today = datetime.now().strftime("%Y-%m-%d")
        sql = "SELECT * FROM plants WHERE (next_check_date IS NULL OR next_check_date <= ?)"
        params = [today]
        if area:
            sql += " AND area = ?"
            params.append(area)
        sql += " ORDER BY area, code"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_plant(r) for r in c.fetchall()]

    def get_overdue_plants(self, area: str = "") -> List[Plant]:
        today = datetime.now().strftime("%Y-%m-%d")
        _cycle = "COALESCE(NULLIF(check_cycle_days, 0), 7)"
        sql = f"""
            SELECT * FROM plants WHERE (
                (next_check_date IS NOT NULL AND date(next_check_date) < date(?))
                OR (next_check_date IS NULL AND last_check_date IS NOT NULL 
                    AND date(last_check_date, '+' || {_cycle} || ' days') < date(?))
                OR (last_check_date IS NULL AND planted_date IS NOT NULL
                    AND date(planted_date, '+' || {_cycle} || ' days') < date(?))
            )
        """
        params = [today, today, today]
        if area:
            sql += " AND area = ?"
            params.append(area)
        sql += f" ORDER BY COALESCE(next_check_date, date(COALESCE(last_check_date, planted_date), '+' || {_cycle} || ' days')), area, code"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_plant(r) for r in c.fetchall()]

    def get_all_areas(self) -> List[str]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT DISTINCT area FROM plants WHERE area IS NOT NULL AND area != '' ORDER BY area")
            return [r["area"] for r in c.fetchall()]

    def update_plant_after_check(self, code: str, new_status: str, health_score: int):
        today = datetime.now().strftime("%Y-%m-%d")
        cycle = 7
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT check_cycle_days FROM plants WHERE code = ?", (code,))
            row = c.fetchone()
            if row and row["check_cycle_days"]:
                cycle = row["check_cycle_days"]
        next_date = (datetime.now() + timedelta(days=cycle)).strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                UPDATE plants SET last_check_date = ?, next_check_date = ?, status = ?
                WHERE code = ?
            """, (today, next_date, new_status, code))

    def get_plant_history(self, code: str) -> List[Inspection]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM inspections WHERE plant_code = ? ORDER BY check_date DESC", (code,))
            return [self._row_to_inspection(r) for r in c.fetchall()]

    def count_plants(self) -> Dict[str, int]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT area, COUNT(*) as cnt FROM plants GROUP BY area ORDER BY area")
            rows = c.fetchall()
            result = {r["area"]: r["cnt"] for r in rows}
            c.execute("SELECT COUNT(*) as total FROM plants")
            result["总计"] = c.fetchone()["total"]
            return result

    # ==================== Inspections ====================
    def add_inspection(self, insp: Inspection) -> int:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO inspections (plant_code, plant_name, area, inspector, check_date,
                    health_score, has_water_deficit, has_withered, has_pest, needs_pruning,
                    needs_replant, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                insp.plant_code, insp.plant_name, insp.area, insp.inspector, insp.check_date,
                insp.health_score, 1 if insp.has_water_deficit else 0,
                1 if insp.has_withered else 0, 1 if insp.has_pest else 0,
                1 if insp.needs_pruning else 0, 1 if insp.needs_replant else 0,
                insp.notes, insp.created_at
            ))
            return c.lastrowid

    def get_inspection_by_id(self, insp_id: int) -> Optional[Inspection]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM inspections WHERE id = ?", (insp_id,))
            row = c.fetchone()
            return self._row_to_inspection(row) if row else None

    def get_inspections_by_date(self, date_from: str, date_to: str) -> List[Inspection]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT * FROM inspections WHERE date(check_date) BETWEEN date(?) AND date(?)
                ORDER BY check_date DESC
            """, (date_from, date_to))
            return [self._row_to_inspection(r) for r in c.fetchall()]

    def get_inspections_by_area_and_date(self, area: str, date_from: str, date_to: str) -> List[Inspection]:
        sql = """
            SELECT * FROM inspections WHERE date(check_date) BETWEEN date(?) AND date(?)
        """
        params = [date_from, date_to]
        if area:
            sql += " AND area = ?"
            params.append(area)
        sql += " ORDER BY check_date DESC"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_inspection(r) for r in c.fetchall()]

    # ==================== Photos ====================
    def add_photo(self, photo: Photo) -> int:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO photos (inspection_id, plant_code, file_path, description, taken_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                photo.inspection_id, photo.plant_code, photo.file_path,
                photo.description, photo.taken_at, photo.created_at
            ))
            return c.lastrowid

    def get_photos_by_plant(self, plant_code: str) -> List[Photo]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM photos WHERE plant_code = ? ORDER BY taken_at DESC", (plant_code,))
            return [self._row_to_photo(r) for r in c.fetchall()]

    def get_photos_by_inspection(self, inspection_id: int) -> List[Photo]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM photos WHERE inspection_id = ? ORDER BY taken_at", (inspection_id,))
            return [self._row_to_photo(r) for r in c.fetchall()]

    # ==================== Tasks ====================
    def add_task(self, task: Task) -> int:
        import random
        if not task.task_no:
            task.task_no = f"T{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(10, 99)}"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO tasks (task_no, plant_code, plant_name, area, task_type, description,
                    assignee, priority, due_date, status, completed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_no, task.plant_code, task.plant_name, task.area,
                task.task_type, task.description, task.assignee, task.priority,
                task.due_date, task.status, task.completed_at, task.created_at
            ))
            return c.lastrowid

    def get_tasks(self, status: str = "", assignee: str = "", area: str = "", overdue_only: bool = False) -> List[Task]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if assignee:
            sql += " AND assignee = ?"
            params.append(assignee)
        if area:
            sql += " AND area = ?"
            params.append(area)
        if overdue_only:
            today = datetime.now().strftime("%Y-%m-%d")
            sql += " AND due_date IS NOT NULL AND date(due_date) < date(?) AND status != '已完成'"
            params.append(today)
        sql += " ORDER BY due_date, priority, created_at DESC"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_task(r) for r in c.fetchall()]

    def get_task(self, task_id: int) -> Optional[Task]:
        """按ID获取单个任务"""
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            r = c.fetchone()
            return self._row_to_task(r) if r else None

    def get_task_by_no(self, task_no: str) -> Optional[Task]:
        """按任务编号获取任务"""
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM tasks WHERE task_no = ?", (task_no,))
            r = c.fetchone()
            return self._row_to_task(r) if r else None

    def update_task_status(self, task_id: int, status: str) -> bool:
        completed_at = None
        if status == TaskStatus.COMPLETED.value:
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                      (status, completed_at, task_id))
            return c.rowcount > 0

    def batch_update_task_status(self, task_ids: List[int], status: str) -> int:
        count = 0
        for tid in task_ids:
            if self.update_task_status(tid, status):
                count += 1
        return count

    def assign_task(self, task_id: int, assignee: str) -> bool:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (assignee, task_id))
            return c.rowcount > 0

    def get_all_assignees(self) -> List[str]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL AND assignee != '' ORDER BY assignee")
            return [r["assignee"] for r in c.fetchall()]

    def update_overdue_tasks(self):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                UPDATE tasks SET status = '已逾期'
                WHERE due_date IS NOT NULL AND date(due_date) < date(?)
                AND status IN ('待处理', '处理中')
            """, (today,))

    # ==================== Row Mappers ====================
    def _row_to_plant(self, row) -> Plant:
        return Plant(
            id=row["id"], code=row["code"], name=row["name"], species=row["species"],
            area=row["area"], location=row["location"], planted_date=row["planted_date"],
            last_check_date=row["last_check_date"], next_check_date=row["next_check_date"],
            check_cycle_days=row["check_cycle_days"] or 7,
            status=row["status"], notes=row["notes"], created_at=row["created_at"]
        )

    def _row_to_inspection(self, row) -> Inspection:
        return Inspection(
            id=row["id"], plant_code=row["plant_code"], plant_name=row["plant_name"],
            area=row["area"], inspector=row["inspector"], check_date=row["check_date"],
            health_score=row["health_score"],
            has_water_deficit=bool(row["has_water_deficit"]),
            has_withered=bool(row["has_withered"]),
            has_pest=bool(row["has_pest"]),
            needs_pruning=bool(row["needs_pruning"]),
            needs_replant=bool(row["needs_replant"]),
            notes=row["notes"], created_at=row["created_at"]
        )

    def _row_to_photo(self, row) -> Photo:
        return Photo(
            id=row["id"], inspection_id=row["inspection_id"], plant_code=row["plant_code"],
            file_path=row["file_path"], description=row["description"],
            taken_at=row["taken_at"], created_at=row["created_at"]
        )

    def _row_to_task(self, row) -> Task:
        return Task(
            id=row["id"], task_no=row["task_no"], plant_code=row["plant_code"],
            plant_name=row["plant_name"], area=row["area"], task_type=row["task_type"],
            description=row["description"], assignee=row["assignee"], priority=row["priority"],
            due_date=row["due_date"], status=row["status"], completed_at=row["completed_at"],
            created_at=row["created_at"]
        )

    # ==================== Shifts ====================
    def create_shift(self, shift: Shift) -> int:
        import random
        if not shift.shift_no:
            shift.shift_no = f"S{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(10, 99)}"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO shifts (shift_no, name, leader, members, start_time, end_time, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                shift.shift_no, shift.name, shift.leader, shift.members,
                shift.start_time, shift.end_time, shift.status, shift.created_at
            ))
            return c.lastrowid

    def get_current_shift(self) -> Optional[Shift]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM shifts WHERE status = '进行中' ORDER BY start_time DESC LIMIT 1")
            row = c.fetchone()
            return self._row_to_shift(row) if row else None

    def get_shift_by_id(self, shift_id: int) -> Optional[Shift]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,))
            row = c.fetchone()
            return self._row_to_shift(row) if row else None

    def get_shifts(self, status: str = "", limit: int = 50) -> List[Shift]:
        sql = "SELECT * FROM shifts WHERE 1=1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY start_time DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_shift(r) for r in c.fetchall()]

    def close_shift(self, shift_id: int) -> bool:
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("UPDATE shifts SET status = '已结束', end_time = ? WHERE id = ?",
                      (end_time, shift_id))
            return c.rowcount > 0

    # ==================== ShiftHandovers ====================
    def create_handover(self, handover: ShiftHandover) -> int:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO shift_handovers (shift_id, shift_no, handover_from, handover_to,
                    handover_time, pending_task_ids, abnormal_plant_codes,
                    unfinished_reason, remarks, confirmed, confirmed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                handover.shift_id, handover.shift_no, handover.handover_from,
                handover.handover_to, handover.handover_time, handover.pending_task_ids,
                handover.abnormal_plant_codes, handover.unfinished_reason,
                handover.remarks, handover.confirmed, handover.confirmed_at,
                handover.created_at
            ))
            return c.lastrowid

    def confirm_handover(self, handover_id: int, confirmer: str = "") -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            c = conn.cursor()
            if confirmer:
                c.execute("UPDATE shift_handovers SET confirmed = 1, confirmed_at = ?, handover_to = COALESCE(NULLIF(handover_to, ''), ?) WHERE id = ?",
                          (now, confirmer, handover_id))
            else:
                c.execute("UPDATE shift_handovers SET confirmed = 1, confirmed_at = ? WHERE id = ?",
                          (now, handover_id))
            return c.rowcount > 0

    def get_handovers(self, shift_id: int = 0, unconfirmed_only: bool = False,
                      limit: int = 50) -> List[ShiftHandover]:
        sql = """SELECT h.*, s.shift_no AS shift_no
                 FROM shift_handovers h
                 LEFT JOIN shifts s ON h.shift_id = s.id
                 WHERE 1=1"""
        params = []
        if shift_id > 0:
            sql += " AND h.shift_id = ?"
            params.append(shift_id)
        if unconfirmed_only:
            sql += " AND h.confirmed = 0"
        sql += " ORDER BY h.handover_time DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return [self._row_to_handover(r) for r in c.fetchall()]

    def get_handover_by_id(self, handover_id: int) -> Optional[ShiftHandover]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""SELECT h.*, s.shift_no AS shift_no
                         FROM shift_handovers h
                         LEFT JOIN shifts s ON h.shift_id = s.id
                         WHERE h.id = ?""", (handover_id,))
            row = c.fetchone()
            return self._row_to_handover(row) if row else None

    def get_shift_pending_tasks(self, shift_id: int) -> List[Task]:
        ho = self.get_handovers(shift_id=shift_id)
        if not ho:
            return []
        ids_str = ho[0].pending_task_ids
        if not ids_str:
            return []
        ids = [int(x.strip()) for x in ids_str.split(",") if x.strip().isdigit()]
        if not ids:
            return []
        sql = f"SELECT * FROM tasks WHERE id IN ({','.join('?' * len(ids))}) ORDER BY due_date"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, ids)
            return [self._row_to_task(r) for r in c.fetchall()]

    def get_shift_abnormal_plants(self, shift_id: int) -> List[Plant]:
        ho = self.get_handovers(shift_id=shift_id)
        if not ho:
            return []
        codes_str = ho[0].abnormal_plant_codes
        if not codes_str:
            return []
        codes = [x.strip() for x in codes_str.split(",") if x.strip()]
        if not codes:
            return []
        sql = f"SELECT * FROM plants WHERE code IN ({','.join('?' * len(codes))}) ORDER BY area, code"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, codes)
            return [self._row_to_plant(r) for r in c.fetchall()]

    # ==================== Analysis Helpers ====================
    def get_consecutive_abnormal_plants(self, days: int = 7, min_occurrences: int = 3,
                                         start_date: str = "", end_date: str = "") -> List[Dict]:
        """
        找出连续多日出现异常的植株（按天去重：同一天多次巡检只算1天）
        返回字段含：持续天数(calendar_duration)、最近问题(last_issue)、是否已有任务(has_task)
        """
        from collections import defaultdict
        today = datetime.now()
        # 优先级：显式日期范围 > days回溯
        if not start_date or not end_date:
            end_date = today.strftime("%Y-%m-%d")
            start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            c = conn.cursor()
            # Step1: 取时间范围内的异常巡检，同时 LEFT JOIN 查是否有对应任务
            c.execute("""
                SELECT DISTINCT i.plant_code, i.plant_name, i.area, date(i.check_date) AS check_date,
                       i.health_score, i.has_water_deficit, i.has_withered, i.has_pest,
                       i.needs_pruning, i.needs_replant, i.notes, i.created_at,
                       (SELECT COUNT(*) FROM tasks t
                         WHERE t.plant_code = i.plant_code AND t.created_at >= ?) AS task_count
                FROM inspections i
                WHERE date(i.check_date) BETWEEN date(?) AND date(?)
                  AND (i.health_score < 80 OR i.has_water_deficit = 1 OR i.has_withered = 1
                       OR i.has_pest = 1 OR i.needs_pruning = 1 OR i.needs_replant = 1)
                ORDER BY i.plant_code, i.check_date, i.created_at
            """, (start_date, start_date, end_date))
            rows = [dict(r) for r in c.fetchall()]

        by_plant = defaultdict(list)
        for r in rows:
            # 按 check_date 去重 - 同一天只保留最后一条（created_at最大的）
            key = r["check_date"]
            existing = next((x for x in by_plant[r["plant_code"]] if x["check_date"] == key), None)
            if existing is None:
                by_plant[r["plant_code"]].append(r)
            else:
                if (r["created_at"] or "") > (existing["created_at"] or ""):
                    by_plant[r["plant_code"]].remove(existing)
                    by_plant[r["plant_code"]].append(r)

        def _get_issues(rec):
            names = []
            if rec["has_water_deficit"]: names.append("缺水")
            if rec["has_withered"]: names.append("枯黄")
            if rec["has_pest"]: names.append("虫害")
            if rec["needs_pruning"]: names.append("需修剪")
            if rec["needs_replant"]: names.append("需补苗")
            if not names and rec["health_score"] < 80: names.append("低评分")
            return names

        result = []
        for code, records in by_plant.items():
            # 按日期排序
            records.sort(key=lambda x: x["check_date"])
            abnormal_days = len(records)  # 去重后的"异常天数"
            if abnormal_days < min_occurrences:
                continue

            # 日历跨度（从第一次到最后一次的天数+1）
            from datetime import datetime as _dt
            d1 = _dt.strptime(records[0]["check_date"], "%Y-%m-%d")
            d2 = _dt.strptime(records[-1]["check_date"], "%Y-%m-%d")
            calendar_duration = (d2 - d1).days + 1

            # 统计问题类型频次
            issue_counter = defaultdict(int)
            scores = []
            for rec in records:
                scores.append(rec["health_score"])
                for iss in _get_issues(rec):
                    issue_counter[iss] += 1
            top_issues = sorted(issue_counter.items(), key=lambda x: -x[1])

            # 最近一次的问题
            last_rec = records[-1]
            last_issues = _get_issues(last_rec)

            # 是否有任务
            has_task = "是" if any(r["task_count"] and r["task_count"] > 0 for r in records) else "否"
            task_cnt = sum(r["task_count"] or 0 for r in records)

            result.append({
                "plant_code": code,
                "plant_name": records[0]["plant_name"] or "",
                "area": records[0]["area"] or "",
                "abnormal_days": abnormal_days,                 # 实际异常天数(去重后)
                "calendar_duration": calendar_duration,         # 日历持续跨度
                "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
                "first_date": records[0]["check_date"],
                "last_date": records[-1]["check_date"],
                "top_issue": top_issues[0][0] if top_issues else "低评分",
                "issue_detail": "、".join(f"{k}×{v}" for k, v in top_issues),
                "last_issue": "、".join(last_issues) if last_issues else "-",
                "last_score": last_rec["health_score"],
                "has_task": has_task,
                "task_count": task_cnt,
                "analyze_window": f"{start_date} ~ {end_date}",
            })
        result.sort(key=lambda x: (-x["calendar_duration"], -x["abnormal_days"], x["avg_score"]))
        return result

    def get_repeat_issue_summary(self, days: int = 14, start_date: str = "", end_date: str = "") -> List[Dict]:
        """统计重复出现的问题类型，按区域+责任人+任务类型分组，支持自定义日期范围"""
        today = datetime.now()
        if not start_date or not end_date:
            end_date = today.strftime("%Y-%m-%d")
            start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        tasks = self.get_tasks()
        from collections import defaultdict
        group = defaultdict(lambda: defaultdict(int))
        for t in tasks:
            tc = (t.created_at or "")[:10]
            if not tc or tc < start_date or tc > end_date:
                continue
            key = (t.area or "未分区域", t.assignee or "未指派", t.task_type or "未分类")
            group[key]["total"] += 1
            if t.status == TaskStatus.COMPLETED.value:
                group[key]["done"] += 1
            else:
                group[key]["pending"] += 1
                if t.status == TaskStatus.OVERDUE.value:
                    group[key]["overdue"] += 1

        result = []
        for (area, assignee, ttype), cnt in group.items():
            total = cnt["total"]
            done = cnt.get("done", 0)
            result.append({
                "area": area,
                "assignee": assignee,
                "task_type": ttype,
                "total": total,
                "done": done,
                "pending": cnt.get("pending", 0),
                "overdue": cnt.get("overdue", 0),
                "done_rate": round(done / total * 100, 1) if total else 0,
                "analyze_window": f"{start_date} ~ {end_date}",
            })
        result.sort(key=lambda x: (-x["total"], x["area"]))
        return result

    # ==================== Shift Row Mappers ====================
    def _row_to_shift(self, row) -> Shift:
        return Shift(
            id=row["id"], shift_no=row["shift_no"], name=row["name"],
            leader=row["leader"], members=row["members"],
            start_time=row["start_time"], end_time=row["end_time"],
            status=row["status"], created_at=row["created_at"]
        )

    def _row_to_handover(self, row) -> ShiftHandover:
        return ShiftHandover(
            id=row["id"], shift_id=row["shift_id"], shift_no=row["shift_no"],
            handover_from=row["handover_from"], handover_to=row["handover_to"],
            handover_time=row["handover_time"],
            pending_task_ids=row["pending_task_ids"],
            abnormal_plant_codes=row["abnormal_plant_codes"],
            overdue_plant_codes=row["overdue_plant_codes"] if "overdue_plant_codes" in row.keys() else "",
            unfinished_reason=row["unfinished_reason"],
            remarks=row["remarks"],
            confirmed=bool(row["confirmed"]),
            confirmed_at=row["confirmed_at"],
            created_at=row["created_at"]
        )

    # ==================== 管理看板 KPI (issue1) ====================
    def get_kpi_dashboard(self, start_date: str, end_date: str, area: str = "") -> Dict:
        """
        返回管理看板核心指标：
        巡检覆盖率、任务完成率、逾期风险、责任人排名、各区域维度KPI
        """
        from collections import defaultdict
        result = {
            "start_date": start_date, "end_date": end_date, "area_filter": area,
            "by_area": [], "assignee_ranking": [], "overall": {},
        }
        today = datetime.now().strftime("%Y-%m-%d")

        # ===== 1. 全库植株总量（供覆盖率分母） =====
        all_plants = self.search_plants(area=area)
        total_plants = len(all_plants)
        area_plant_count = defaultdict(int)
        for p in all_plants:
            area_plant_count[p.area or "未分区域"] += 1

        # ===== 2. 区间内巡检记录 =====
        insps = self.get_inspections_by_area_and_date(area or "", start_date, end_date)
        insps_by_area = defaultdict(list)
        for i in insps:
            insps_by_area[i.area or "未分区域"].append(i)

        # ===== 3. 区间内任务（按 created_at 过滤） =====
        all_tasks = self.get_tasks()
        tasks_by_area = defaultdict(list)
        tasks_in_range = []
        for t in all_tasks:
            tc = (t.created_at or "")[:10]
            if tc and start_date <= tc <= end_date:
                if area and t.area != area:
                    continue
                tasks_in_range.append(t)
                tasks_by_area[t.area or "未分区域"].append(t)

        # ===== 4. 当前逾期风险 =====
        overdue_now = self.get_overdue_plants(area)

        # ===== 5. 全局汇总 =====
        checked_codes = set(i.plant_code for i in insps)
        coverage = round(len(checked_codes) / total_plants * 100, 1) if total_plants else 0
        task_total = len(tasks_in_range)
        task_done = sum(1 for t in tasks_in_range if t.status == TaskStatus.COMPLETED.value)
        task_overdue = sum(1 for t in tasks_in_range if t.status == TaskStatus.OVERDUE.value)
        task_rate = round(task_done / task_total * 100, 1) if task_total else 0

        avg_score = round(sum(i.health_score for i in insps) / len(insps), 1) if insps else 0
        abnormal_insp = sum(1 for i in insps if (i.health_score < 80 or i.has_water_deficit
                                                   or i.has_withered or i.has_pest
                                                   or i.needs_pruning or i.needs_replant))
        abnormal_rate = round(abnormal_insp / len(insps) * 100, 1) if insps else 0

        result["overall"] = {
            "total_plants": total_plants,
            "checked_plants": len(checked_codes),
            "inspection_count": len(insps),
            "coverage_rate": coverage,
            "avg_score": avg_score,
            "abnormal_rate": abnormal_rate,
            "task_total": task_total,
            "task_done": task_done,
            "task_overdue": task_overdue,
            "task_completion_rate": task_rate,
            "overdue_risk_count": len(overdue_now),
            "overdue_risk_rate": round(len(overdue_now) / total_plants * 100, 1) if total_plants else 0,
        }

        # ===== 6. 按区域维度 =====
        all_areas = sorted(set(list(insps_by_area.keys()) + list(tasks_by_area.keys()) + list(area_plant_count.keys())))
        for ar in all_areas:
            pl_cnt = area_plant_count.get(ar, 0)
            a_insps = insps_by_area.get(ar, [])
            a_codes = set(i.plant_code for i in a_insps)
            a_tasks = tasks_by_area.get(ar, [])
            a_total = len(a_tasks)
            a_done = sum(1 for t in a_tasks if t.status == TaskStatus.COMPLETED.value)
            a_overdue = sum(1 for t in a_tasks if t.status == TaskStatus.OVERDUE.value)
            a_score = round(sum(i.health_score for i in a_insps) / len(a_insps), 1) if a_insps else 0
            a_ov = len(self.get_overdue_plants(ar))
            result["by_area"].append({
                "area": ar,
                "plants_total": pl_cnt,
                "checked": len(a_codes),
                "coverage": round(len(a_codes) / pl_cnt * 100, 1) if pl_cnt else 0,
                "inspections": len(a_insps),
                "avg_score": a_score,
                "task_created": a_total,
                "task_done": a_done,
                "task_overdue": a_overdue,
                "task_rate": round(a_done / a_total * 100, 1) if a_total else 0,
                "overdue_now": a_ov,
                "risk_level": "🔴高" if a_ov >= 3 or (a_tasks and a_overdue / len(a_tasks) > 0.3)
                              else ("🟡中" if a_ov > 0 or a_overdue > 0 else "🟢低"),
            })

        # ===== 7. 责任人排名（取区间内有任务的人） =====
        u_perf = defaultdict(lambda: {"total": 0, "done": 0, "overdue": 0,
                                       "insp_count": 0, "avg_score": []})
        for t in tasks_in_range:
            u = t.assignee or "未指派"
            u_perf[u]["total"] += 1
            if t.status == TaskStatus.COMPLETED.value:
                u_perf[u]["done"] += 1
            if t.status == TaskStatus.OVERDUE.value:
                u_perf[u]["overdue"] += 1
        for i in insps:
            u = i.inspector or "未记录"
            u_perf[u]["insp_count"] += 1
            u_perf[u]["avg_score"].append(i.health_score)

        for u, p in u_perf.items():
            avg_s = round(sum(p["avg_score"]) / len(p["avg_score"]), 1) if p["avg_score"] else 0
            rate = round(p["done"] / p["total"] * 100, 1) if p["total"] else 0
            p["user"] = u
            p["task_rate"] = rate
            p["avg_inspection_score"] = avg_s
            p["composite_score"] = round(
                (rate * 0.5 + (100 - abnormal_rate) * 0.2
                 + (avg_s if avg_s else 70) * 0.3), 1
            ) if (rate or avg_s) else 0

        ranking = sorted([p for p in u_perf.values() if p["total"] + p["insp_count"] > 0],
                         key=lambda x: -x["composite_score"])
        for idx, p in enumerate(ranking, 1):
            p["rank"] = idx
            result["assignee_ranking"].append(p)
        return result

    # ==================== 提醒看板 (issue4) ====================
    def get_upcoming_reminders(self, scope: str = "3days") -> Dict:
        """
        scope: tomorrow(明天到期) | 3days(3天内) | week(本周内)
        返回: {'due_checks': 待检植株, 'due_tasks': 到期任务, 'scope_label': str, 'group_by_area': ...}
        """
        from collections import defaultdict
        today = datetime.now()
        if scope == "tomorrow":
            end = today + timedelta(days=1)
            label = "明天（1天内）"
        elif scope == "week":
            weekday = today.weekday()
            end = today + timedelta(days=(6 - weekday))
            label = f"本周剩余（{6 - weekday + 1}天）"
        else:
            end = today + timedelta(days=3)
            scope = "3days"
            label = "未来3天"
        start_str = today.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        # 待检植株：next_check_date 在范围内，或者 next 空但 last+周期 或 planted+周期 在范围内
        _cy = "COALESCE(NULLIF(check_cycle_days, 0), 7)"
        sql_plants = f"""
            SELECT * FROM plants WHERE (
                (next_check_date IS NOT NULL
                    AND date(next_check_date) BETWEEN date(?) AND date(?))
                OR (next_check_date IS NULL AND last_check_date IS NOT NULL
                    AND date(last_check_date, '+' || {_cy} || ' days') BETWEEN date(?) AND date(?))
                OR (next_check_date IS NULL AND last_check_date IS NULL
                    AND date(planted_date, '+' || {_cy} || ' days') BETWEEN date(?) AND date(?))
            )
        """
        params = [start_str, end_str, start_str, end_str, start_str, end_str]

        # 还要加上已经逾期的（红色警告）
        overdue = self.get_overdue_plants()
        overdue_codes = set(p.code for p in overdue)

        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql_plants, params)
            upcoming = [self._row_to_plant(r) for r in c.fetchall()]

        # 过滤重复（有些已逾期的也在范围内，保留但打标记）
        due_checks = []
        for p in upcoming:
            entry = {"plant": p, "days_left": None, "status": "即将到期", "is_overdue": False}
            # 计算距离最近到期的天数
            try:
                if p.next_check_date:
                    d = datetime.strptime(p.next_check_date[:10], "%Y-%m-%d")
                elif p.last_check_date:
                    cy = p.check_cycle_days or 7
                    d = datetime.strptime(p.last_check_date[:10], "%Y-%m-%d") + timedelta(days=cy)
                else:
                    cy = p.check_cycle_days or 7
                    d = datetime.strptime(p.planted_date[:10], "%Y-%m-%d") + timedelta(days=cy)
                left = (d - today).days
                entry["days_left"] = left
                if p.code in overdue_codes or left < 0:
                    entry["status"] = "⚠️已逾期"
                    entry["is_overdue"] = True
                elif left == 0:
                    entry["status"] = "今天到期"
                elif left == 1:
                    entry["status"] = "明天到期"
            except Exception:
                pass
            due_checks.append(entry)

        # 到期任务：due_date 在范围内（含已逾期）
        due_tasks = []
        for t in self.get_tasks():
            if t.status == TaskStatus.COMPLETED.value:
                continue
            if not t.due_date:
                continue
            try:
                d = datetime.strptime(t.due_date[:10], "%Y-%m-%d")
                left = (d - today).days
                if -100 <= left <= (end - today).days:  # 含已逾期100天内的
                    status = "⚠️已逾期" if left < 0 else (
                        "今天到期" if left == 0 else f"{left}天后到期")
                    due_tasks.append({"task": t, "days_left": left, "status": status})
            except Exception:
                pass

        # 按区域分组（优先级：先按区域，再按责任人）
        by_area = defaultdict(lambda: {"checks": [], "tasks": []})
        for c in due_checks:
            by_area[c["plant"].area or "未分区域"]["checks"].append(c)
        for t in due_tasks:
            by_area[t["task"].area or "未分区域"]["tasks"].append(t)

        # 按责任人人分组任务
        by_assignee = defaultdict(list)
        for t in due_tasks:
            by_assignee[t["task"].assignee or "未指派"].append(t)

        # 排序：已逾期在前，剩余天数升序
        due_checks.sort(key=lambda x: (0 if x["is_overdue"] else 1, x["days_left"] or 999))
        due_tasks.sort(key=lambda x: (0 if x["days_left"] < 0 else 1, x["days_left"]))

        return {
            "scope": scope, "scope_label": label,
            "start_date": start_str, "end_date": end_str,
            "due_checks": due_checks,
            "due_tasks": due_tasks,
            "by_area": by_area,
            "by_assignee": by_assignee,
            "summary": {
                "checks_count": len(due_checks),
                "checks_overdue": sum(1 for c in due_checks if c["is_overdue"]),
                "tasks_count": len(due_tasks),
                "tasks_overdue": sum(1 for t in due_tasks if t["days_left"] < 0),
            }
        }

    # ==================== 任务批量完成结果导入 (issue5) ====================
    def import_task_completion(self, records: List[Dict]) -> Dict:
        """
        records: 每条含 task_no, processor(处理人), completed_at(完成时间可选), result(备注/处理结果可选), new_status(可选，默认已完成)
        返回: {success, failed, skipped, details: [{'task_no','ok','msg'}]}
        """
        summary = {"success": 0, "failed": 0, "skipped": 0, "details": []}
        for rec in records:
            tno = (rec.get("task_no") or rec.get("任务编号") or rec.get("编号") or "").strip()
            processor = (rec.get("processor") or rec.get("处理人") or rec.get("完成人") or "").strip()
            completed_at = (rec.get("completed_at") or rec.get("完成时间") or rec.get("处理时间") or "").strip()
            result = (rec.get("result") or rec.get("备注") or rec.get("处理结果") or rec.get("说明") or "").strip()
            new_status = (rec.get("new_status") or rec.get("状态") or TaskStatus.COMPLETED.value).strip()

            if not tno:
                summary["skipped"] += 1
                summary["details"].append({"task_no": "(空)", "ok": False, "msg": "缺少任务编号，跳过"})
                continue
            if not completed_at:
                completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 找到任务
            with self._get_conn() as conn:
                c = conn.cursor()
                c.execute("SELECT * FROM tasks WHERE task_no = ?", (tno,))
                row = c.fetchone()
                if not row:
                    summary["failed"] += 1
                    summary["details"].append({"task_no": tno, "ok": False, "msg": "任务编号不存在"})
                    continue
                if processor:
                    c.execute("UPDATE tasks SET status = ?, completed_at = ?, assignee = COALESCE(NULLIF(assignee, ''), ?) WHERE task_no = ?",
                              (new_status, completed_at, processor, tno))
                else:
                    c.execute("UPDATE tasks SET status = ?, completed_at = ? WHERE task_no = ?",
                              (new_status, completed_at, tno))
                # 追加备注（合并现有任务说明）
                if result:
                    existing = row["description"] or ""
                    if result[:20] not in existing:
                        c.execute("UPDATE tasks SET description = ? WHERE task_no = ?",
                                  (f"{existing} | 完成说明:{result[:200]}" if existing else f"完成说明:{result[:200]}", tno))
            summary["success"] += 1
            summary["details"].append({"task_no": tno, "ok": True,
                                       "msg": f"状态更新为{new_status}, 处理人{processor or '未变'}"})
        return summary

    # ==================== 遗留事项独立表 (issue2) ====================
    def expand_handover_to_items(self, handover_id: int) -> int:
        """从handover的CSV字段展开成独立shift_pending_items（首次展开）"""
        ho = self.get_handover_by_id(handover_id)
        if not ho:
            return 0
        created = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            c = conn.cursor()
            # 避免重复展开
            c.execute("SELECT COUNT(*) AS cnt FROM shift_pending_items WHERE handover_id = ?", (handover_id,))
            if c.fetchone()["cnt"] > 0:
                return 0

            # 1. 展开任务遗留
            task_ids = [int(x.strip()) for x in (ho.pending_task_ids or "").split(",") if x.strip().isdigit()]
            for tid in task_ids:
                c.execute("SELECT task_no, task_type, description, assignee, area, plant_code FROM tasks WHERE id = ?", (tid,))
                r = c.fetchone()
                title = f"{r['task_type']}#{r['task_no']}" if r else f"任务#{tid}"
                desc = r["description"][:80] if r and r["description"] else ""
                c.execute("""INSERT INTO shift_pending_items
                    (handover_id, item_type, ref_id, ref_code, title, original_assignee, current_assignee, reason, status, created_at)
                    VALUES (?, '任务', ?, ?, ?, ?, ?, ?, '待处理', ?)""",
                          (handover_id, tid, r["plant_code"] if r else "",
                           f"{title} {desc}".strip(),
                           r["assignee"] if r else ho.handover_to,
                           r["assignee"] if r else ho.handover_to,
                           ho.unfinished_reason or "", now))
                created += 1

            # 2. 展开异常植株
            codes = [x.strip() for x in (ho.abnormal_plant_codes or "").split(",") if x.strip()]
            for code in codes:
                c.execute("SELECT name, area, status FROM plants WHERE code = ?", (code,))
                r = c.fetchone()
                title = f"{code} {r['name']}" if r else code
                c.execute("""INSERT INTO shift_pending_items
                    (handover_id, item_type, ref_code, title, current_assignee, reason, status, created_at)
                    VALUES (?, '异常株', ?, ?, ?, ?, '待处理', ?)""",
                          (handover_id, code, title,
                           ho.handover_to or "",
                           f"状态异常:{r['status']}" if r else "异常株", now))
                created += 1

            # 3. 展开逾期植株
            ov_codes = [x.strip() for x in (ho.overdue_plant_codes or "").split(",") if x.strip()]
            for code in ov_codes:
                c.execute("SELECT name, area, status FROM plants WHERE code = ?", (code,))
                r = c.fetchone()
                if r and code in codes:
                    continue  # 已在异常里
                title = f"{code} {r['name'] if r else ''} [逾期未检]"
                c.execute("""INSERT INTO shift_pending_items
                    (handover_id, item_type, ref_code, title, current_assignee, reason, status, created_at)
                    VALUES (?, '逾期未检', ?, ?, ?, '巡检周期已过', '待处理', ?)""",
                          (handover_id, code, title, ho.handover_to or "", now))
                created += 1
        return created

    def get_handover_items(self, handover_id: int) -> List[Dict]:
        """获取交接单下的独立遗留事项列表（含闭环状态）"""
        self.expand_handover_to_items(handover_id)
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM shift_pending_items WHERE handover_id = ? ORDER BY id", (handover_id,))
            return [dict(r) for r in c.fetchall()]

    def update_pending_item(self, item_id: int, *, new_assignee: str = "",
                            new_status: str = "", process_result: str = "",
                            processed_by: str = "") -> bool:
        """重新指派责任人、补处理结果、更新闭环状态"""
        updates, params = [], []
        if new_assignee:
            updates.append("current_assignee = ?")
            params.append(new_assignee)
        if new_status:
            updates.append("status = ?")
            params.append(new_status)
        if process_result:
            updates.append("process_result = COALESCE(process_result, '') || ?")
            params.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {process_result[:300]}; ")
        if processed_by:
            updates.append("processed_by = ?")
            params.append(processed_by)
        if new_status in ("已完成", "已闭环", "已处理"):
            updates.append("processed_at = COALESCE(processed_at, ?)")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if not updates:
            return False
        params.append(item_id)
        sql = f"UPDATE shift_pending_items SET {', '.join(updates)} WHERE id = ?"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            return c.rowcount > 0

    def get_shift_closed_loop_status(self, shift_id: int) -> Dict:
        """某班次下所有遗留事项的闭环统计"""
        handovers = self.get_handovers(shift_id=shift_id, limit=50)
        result = {"handovers": len(handovers),
                  "items_total": 0, "items_closed": 0, "items_pending": 0,
                  "items": [], "closed_rate": 0.0}
        for ho in handovers:
            items = self.get_handover_items(ho.id)
            for it in items:
                it_closed = (it["status"] or "") in ("已完成", "已闭环", "已处理", "已取消")
                result["items_total"] += 1
                if it_closed:
                    result["items_closed"] += 1
                else:
                    result["items_pending"] += 1
                it["_handover_id"] = ho.id
                it["_closed"] = it_closed
                result["items"].append(it)
        if result["items_total"]:
            result["closed_rate"] = round(result["items_closed"] / result["items_total"] * 100, 1)
        return result
