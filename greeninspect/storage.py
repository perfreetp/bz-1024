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

    # ==================== 负责人绩效考核 (issue2_new) ====================
    def get_assignee_performance(self, start_date: str, end_date: str, area: str = "") -> Dict:
        """
        个人工作量 & 质量趋势统计（按自然周/月/自定义区间）
        返回: {'period_label', 'start_date', 'end_date', 'people': [...]}
        people每项字段:
          user, created_tasks(生成本人指派), completed_tasks(区间内完成),
          pending_handover_taken(接手遗留数), pending_handover_closed(已闭环遗留数),
          pending_handover_open(遗留未闭环), overdue_remaining(逾期未完成剩余),
          avg_hours_per_task(平均处理时长小时), insp_count(巡检次数),
          avg_inspection_score(平均巡检分), composite_score(综合绩效)
        """
        from collections import defaultdict

        # 初始化所有相关人员
        users = set()
        # 1. 任务中出现过的人
        with self._get_conn() as conn:
            c = conn.cursor()
            for col in ["assignee", "completed_by"]:
                if col == "completed_by":
                    continue  # tasks 没有 completed_by 字段, 用 completed_at 关联
                area_q = f"AND area = '{area}'" if area else ""
                c.execute(f"SELECT DISTINCT {col} FROM tasks WHERE {col} IS NOT NULL AND {col} != '' {area_q}")
                users.update(r[col] for r in c.fetchall())
            # 2. 巡检人
            area_q2 = f"AND area = '{area}'" if area else ""
            c.execute(f"SELECT DISTINCT inspector FROM inspections WHERE inspector IS NOT NULL AND inspector != '' {area_q2}")
            users.update(r["inspector"] for r in c.fetchall())
            # 3. 遗留事项责任人
            c.execute("SELECT DISTINCT current_assignee FROM shift_pending_items WHERE current_assignee IS NOT NULL AND current_assignee != ''")
            users.update(r["current_assignee"] for r in c.fetchall())
            c.execute("SELECT DISTINCT processed_by FROM shift_pending_items WHERE processed_by IS NOT NULL AND processed_by != ''")
            users.update(r["processed_by"] for r in c.fetchall())

        base = {"created_tasks": 0, "completed_tasks": 0,
                "pending_taken": 0, "pending_closed": 0, "pending_open": 0,
                "overdue_remaining": 0,
                "task_duration_hours": [],
                "insp_count": 0, "insp_scores": []}
        perf = {u: {**base, "user": u} for u in users if u}

        # A. 任务维度 (区间内 创建/完成)
        area_q = f"AND area = '{area}'" if area else ""
        with self._get_conn() as conn:
            c = conn.cursor()
            # A1: 区间内创建、且指配人的任务
            c.execute(f"""SELECT id, task_no, assignee, status, created_at, due_date, completed_at
                FROM tasks WHERE created_at >= ? AND created_at <= ? {area_q}""",
                      (start_date + " 00:00:00", end_date + " 23:59:59"))
            for r in c.fetchall():
                u = r["assignee"] or "未指派"
                if u in perf:
                    perf[u]["created_tasks"] += 1
                # 逾期未完成：状态未完成 且 已过截止
                if r["status"] not in ("已完成", "已闭环", "已处理") and r["due_date"] and r["due_date"] < end_date:
                    if u in perf:
                        perf[u]["overdue_remaining"] += 1

            # A2: 区间内完成 (completed_at 落在区间)
            c.execute(f"""SELECT id, task_no, assignee, status, created_at, due_date, completed_at
                FROM tasks WHERE completed_at >= ? AND completed_at <= ? {area_q}""",
                      (start_date + " 00:00:00", end_date + " 23:59:59"))
            for r in c.fetchall():
                u = r["assignee"] or "未指派"
                if u in perf:
                    perf[u]["completed_tasks"] += 1
                # 计算处理时长 (小时)
                try:
                    if r["created_at"] and r["completed_at"]:
                        d1 = datetime.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S")
                        d2 = datetime.strptime(r["completed_at"][:19], "%Y-%m-%d %H:%M:%S")
                        h = round((d2 - d1).total_seconds() / 3600, 2)
                        if h < 24 * 30:  # 合理值过滤
                            if u in perf:
                                perf[u]["task_duration_hours"].append(h)
                except Exception:
                    pass

            # B. 遗留事项维度（按区域过滤: 任务→tasks.area; 植株→plants.area）
            c.execute("""SELECT id, item_type, ref_id, ref_code, original_assignee, current_assignee,
                status, process_result, processed_by, processed_at, created_at
                FROM shift_pending_items
                WHERE created_at >= ? OR processed_at >= ? OR status != '已闭环'""",
                      (start_date + " 00:00:00", start_date + " 00:00:00"))
            pending_rows = c.fetchall()
            # 预查 area 映射
            task_area_cache, plant_area_cache = {}, {}
            if area:
                c2 = conn.cursor()
                for r in pending_rows:
                    if r["item_type"] == "任务" and r["ref_id"]:
                        tid = r["ref_id"]
                        if tid not in task_area_cache:
                            c2.execute("SELECT area FROM tasks WHERE id = ?", (tid,))
                            rr = c2.fetchone()
                            task_area_cache[tid] = rr["area"] if rr else ""
                    elif r["item_type"] in ("异常株", "逾期未检") and r["ref_code"]:
                        code = r["ref_code"]
                        if code not in plant_area_cache:
                            c2.execute("SELECT area FROM plants WHERE code = ?", (code,))
                            rr = c2.fetchone()
                            plant_area_cache[code] = rr["area"] if rr else ""
            for r in pending_rows:
                # --- area过滤判断 ---
                skip = False
                if area:
                    if r["item_type"] == "任务":
                        t_area = task_area_cache.get(r["ref_id"], "")
                        skip = (t_area != area)
                    elif r["item_type"] in ("异常株", "逾期未检"):
                        p_area = plant_area_cache.get(r["ref_code"], "")
                        skip = (p_area != area)
                if skip:
                    continue
                # B1: 被指派(接手)次数
                u = r["current_assignee"] or ""
                if u in perf:
                    perf[u]["pending_taken"] += 1
                # B2: 区间内闭环
                is_closed = (r["status"] or "") in ("已完成", "已闭环", "已处理", "已取消")
                if is_closed and r["processed_at"] and start_date <= r["processed_at"][:10] <= end_date:
                    pu = r["processed_by"] or u
                    if pu in perf:
                        perf[pu]["pending_closed"] += 1
                    # 也记入当前接手人
                    if u in perf and u != pu:
                        perf[u]["pending_closed"] += 1
                # B3: 未闭环
                if not is_closed:
                    if u in perf:
                        perf[u]["pending_open"] += 1

            # C. 巡检维度 (区间内)
            area_q3 = f"AND area = '{area}'" if area else ""
            c.execute(f"""SELECT inspector, health_score FROM inspections
                WHERE substr(check_date,1,10) >= ? AND substr(check_date,1,10) <= ? {area_q3}""",
                      (start_date, end_date))
            for r in c.fetchall():
                u = r["inspector"] or "未记录"
                if u in perf:
                    perf[u]["insp_count"] += 1
                    perf[u]["insp_scores"].append(r["health_score"] or 0)

        # D. 计算汇总字段
        final = []
        for u, p in perf.items():
            durations = p["task_duration_hours"]
            avg_h = round(sum(durations) / len(durations), 1) if durations else 0
            scores = p["insp_scores"]
            avg_s = round(sum(scores) / len(scores), 1) if scores else 0
            # 综合绩效: 完成数×40% + 闭环率×25% + 巡检均分×20% - 逾期×15%
            task_done_total = p["completed_tasks"] + p["pending_closed"]
            pending_total = p["pending_taken"] or 1
            closed_rate = round(p["pending_closed"] / pending_total * 100, 1) if p["pending_taken"] else 100
            composite = round(
                (min(task_done_total * 2, 100) * 0.4) +
                (closed_rate * 0.25) +
                ((avg_s if avg_s else 60) * 0.2) +
                (max(0, 100 - p["overdue_remaining"] * 10) * 0.15)
                , 1
            )
            final.append({
                "user": u,
                "created_tasks": p["created_tasks"],
                "completed_tasks": p["completed_tasks"],
                "pending_taken": p["pending_taken"],
                "pending_closed": p["pending_closed"],
                "pending_open": p["pending_open"],
                "pending_closed_rate": closed_rate,
                "overdue_remaining": p["overdue_remaining"],
                "avg_task_hours": avg_h,
                "insp_count": p["insp_count"],
                "avg_inspection_score": avg_s,
                "composite_score": composite,
            })
        final.sort(key=lambda x: -x["composite_score"])
        for idx, x in enumerate(final, 1):
            x["rank"] = idx
        return {
            "start_date": start_date, "end_date": end_date,
            "period_label": f"{start_date} ~ {end_date}",
            "people_count": len(final),
            "people": final
        }

    # ==================== 班后复盘看板 (issue4_round3) ====================
    def get_shift_review(self, start_date: str, end_date: str, area: str = "") -> Dict:
        """
        班后复盘：按班次把「逾期未检/异常株/遗留任务/重指派/闭环结果」串起来
        标记跨班次遗留问题（同一 ref_code/ref_id 出现在多个交接单）
        """
        from collections import defaultdict
        start_dt = start_date + " 00:00:00"
        end_dt = end_date + " 23:59:59"

        shifts = []
        # 全局统计：某植株/任务出现过几次（跨班次）
        entity_counter = defaultdict(list)  # key -> [shift_id,...]
        all_pending_entities = []

        with self._get_conn() as conn:
            c = conn.cursor()
            # 1. 先取区间内所有交接班（按 handover_time 升序）
            c.execute(f"""
                SELECT h.*, s.name AS shift_name, s.leader
                FROM shift_handovers h
                LEFT JOIN shifts s ON s.id = h.shift_id
                WHERE h.handover_time BETWEEN ? AND ?
                ORDER BY h.handover_time ASC, h.id ASC
            """, (start_dt, end_dt))
            handovers = [dict(r) for r in c.fetchall()]

            for h in handovers:
                # 展开遗留事项
                items = self.get_handover_items(h["id"])
                # ---- area过滤：基于items的区域归属判定 ----
                if area:
                    # 预查 area 映射
                    filtered = []
                    for it in items:
                        keep = False
                        if it["item_type"] == "任务" and it["ref_id"]:
                            try:
                                cc = conn.cursor()
                                cc.execute("SELECT area FROM tasks WHERE id = ?", (it["ref_id"],))
                                rr = cc.fetchone()
                                if rr and rr["area"] == area:
                                    keep = True
                            except:
                                pass
                        elif it["ref_code"]:
                            try:
                                cc = conn.cursor()
                                cc.execute("SELECT area FROM plants WHERE code = ?", (it["ref_code"],))
                                rr = cc.fetchone()
                                if rr and rr["area"] == area:
                                    keep = True
                            except:
                                pass
                        if keep:
                            filtered.append(it)
                    if not filtered:
                        continue  # 该handover在本区域无任何匹配项，跳过
                    items = filtered
                    matched_area = area
                else:
                    matched_area = "-"
                # 统计该班次KPI
                ov = [i for i in items if i["item_type"] == "逾期未检"]
                abn = [i for i in items if i["item_type"] == "异常株"]
                tasks = [i for i in items if i["item_type"] == "任务"]
                reassigned = [i for i in items if (i["current_assignee"] or "") != (i["original_assignee"] or "")
                              and i["original_assignee"]]
                closed = [i for i in items if (i["status"] or "") in ("已完成", "已闭环", "已处理")]
                open_items = [i for i in items if i not in closed]

                # 记录跨班次追踪
                for it in items:
                    key = None
                    if it["item_type"] == "任务" and it["ref_id"]:
                        key = ("TASK", it["ref_id"])
                    elif it["item_type"] in ("异常株", "逾期未检") and it["ref_code"]:
                        key = ("PLANT", it["ref_code"])
                    if key:
                        entity_counter[key].append(h["id"])
                        all_pending_entities.append((key, it, h))

                shifts.append({
                    "shift_id": h["shift_id"],
                    "shift_name": h.get("shift_name") or f"班次#{h['shift_id']}",
                    "handover_id": h["id"],
                    "shift_no": h.get("shift_no") or "",
                    "leader": h.get("leader") or "",
                    "handover_from": h["handover_from"],
                    "handover_to": h["handover_to"],
                    "handover_time": h["handover_time"],
                    "area": matched_area,
                    "confirmed": bool(h["confirmed"]),
                    "overdue_count": len(ov),
                    "abnormal_count": len(abn),
                    "task_count": len(tasks),
                    "total_pending": len(items),
                    "reassigned_count": len(reassigned),
                    "closed_count": len(closed),
                    "open_count": len(open_items),
                    "closed_rate": round(len(closed) / len(items) * 100, 1) if items else 0,
                    "items_overdue": ov,
                    "items_abnormal": abn,
                    "items_tasks": tasks,
                    "items_closed": closed,
                    "items_reassigned": reassigned,
                    "items": items,
                })

        # 统计跨班次的问题
        cross_shift = []  # [{key, key_label, count, shifts:[shift_id...], statuses:[]}]
        for key, shifts_list in entity_counter.items():
            if len(shifts_list) >= 2:
                related = [e for e in all_pending_entities if e[0] == key]
                statuses = list({e[1].get("status") or "待处理" for e in related})
                labels = {
                    "TASK": f"任务#{key[1]}",
                    "PLANT": f"植株{key[1]}",
                }
                final_status = "已闭环" if "已闭环" in statuses or "已完成" in statuses else (
                    "处理中" if any(s in ("处理中", "进行中") for s in statuses) else "未闭环")
                cross_shift.append({
                    "key_type": key[0],
                    "key_id": key[1],
                    "key_label": labels.get(key[0], str(key)),
                    "appear_in_shift_count": len(set(shifts_list)),
                    "appear_in_shifts": sorted(set(shifts_list)),
                    "all_statuses": statuses,
                    "final_status": final_status,
                    "related_events_count": len(related),
                })
        cross_shift.sort(key=lambda x: (-x["appear_in_shift_count"], x["key_label"]))

        # 汇总KPI
        total_pending = sum(s["total_pending"] for s in shifts)
        total_closed = sum(s["closed_count"] for s in shifts)
        total_reassigned = sum(s["reassigned_count"] for s in shifts)
        overdue_total = sum(s["overdue_count"] for s in shifts)
        abnormal_total = sum(s["abnormal_count"] for s in shifts)
        cross_shift_unclosed = sum(1 for c in cross_shift if c["final_status"] == "未闭环")

        return {
            "start_date": start_date, "end_date": end_date,
            "period_label": f"{start_date} ~ {end_date}",
            "area": area or "全部区域",
            "shifts_count": len(shifts),
            "total_pending": total_pending,
            "total_closed": total_closed,
            "total_open": total_pending - total_closed,
            "total_reassigned": total_reassigned,
            "overdue_total": overdue_total,
            "abnormal_total": abnormal_total,
            "overall_closed_rate": round(total_closed / total_pending * 100, 1) if total_pending else 0,
            "cross_shift_total": len(cross_shift),
            "cross_shift_unclosed": cross_shift_unclosed,
            "shifts": shifts,
            "cross_shift_issues": cross_shift,
        }

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

        # ---- 早会优先级打标：P0逾期 > P1今到期/异常未派 > P2连续异常 > P3正常 ----
        abn_set, abn_notask_set = set(), set()
        try:
            abn_list = self.get_consecutive_abnormal_plants(days=14, min_occurrences=2)
            abn_set = {a["plant_code"] for a in abn_list}
            abn_notask_set = {a["plant_code"] for a in abn_list if not a.get("has_task")}
        except Exception:
            pass

        def _score_check(c):
            days = c.get("days_left") or 999
            code = c["plant"].code
            if c.get("is_overdue") or days < 0:
                return 0, "P0", "🔴", "逾期未检(最高优先级)"
            if code in abn_notask_set and days <= 3:
                return 1, "P1", "🟠", "连续异常未派任务"
            if days == 0:
                return 1, "P1", "🟠", "今天到检"
            if code in abn_set:
                return 2, "P2", "🟡", "连续异常植株"
            return 3, "P3", "🟢", "正常到检"

        def _score_task(t):
            days = t.get("days_left") or 999
            if days < 0:
                return 0, "P0", "🔴", "已逾期"
            if days == 0:
                return 1, "P1", "🟠", "今天到期"
            return 3, "P3", "🟢", "正常到期"

        for c in due_checks:
            s, lvl, emo, reason = _score_check(c)
            c["priority_score"] = s
            c["priority_level"] = lvl
            c["priority_emoji"] = emo
            c["priority_reason"] = reason
        for t in due_tasks:
            s, lvl, emo, reason = _score_task(t)
            t["priority_score"] = s
            t["priority_level"] = lvl
            t["priority_emoji"] = emo
            t["priority_reason"] = reason

        # 按区域分组（优先级：先按区域，再按责任人）
        by_area = defaultdict(lambda: {"checks": [], "tasks": []})
        for c in sorted(due_checks, key=lambda x: (x["priority_score"], x.get("days_left") or 999)):
            by_area[c["plant"].area or "未分区域"]["checks"].append(c)
        for t in sorted(due_tasks, key=lambda x: (x["priority_score"], x.get("days_left") or 999)):
            by_area[t["task"].area or "未分区域"]["tasks"].append(t)

        # 按责任人人分组任务
        by_assignee = defaultdict(list)
        for t in sorted(due_tasks, key=lambda x: (x["priority_score"], x.get("days_left") or 999)):
            by_assignee[t["task"].assignee or "未指派"].append(t)

        # 最终排序：优先级+剩余天数二级排序(早会重点在上)
        due_checks.sort(key=lambda x: (x["priority_score"], x.get("days_left") or 999))
        due_tasks.sort(key=lambda x: (x["priority_score"], x.get("days_left") or 999))

        # 优先级分布统计
        p_count_c = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        p_count_t = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        for c in due_checks:
            p_count_c[c["priority_level"]] = p_count_c.get(c["priority_level"], 0) + 1
        for t in due_tasks:
            p_count_t[t["priority_level"]] = p_count_t.get(t["priority_level"], 0) + 1

        # ---- 排班建议 (issue2_round4) ----
        # 1) 责任人负载判定：统计每人待处理任务数，>5 则判定"过载"
        OVERLOAD_THRESHOLD = 5
        assignee_load = defaultdict(lambda: {"tasks": 0, "checks_ref": 0, "urgent": 0})
        for t in due_tasks:
            u = t["task"].assignee or "未指派"
            assignee_load[u]["tasks"] += 1
            if t["priority_level"] in ("P0", "P1"):
                assignee_load[u]["urgent"] += 1
        # 待检植株按责任人（通过区域默认巡检人，这里估算：区域植株数 / 总人数，按区域分散）
        area_inspector_hint = defaultdict(set)
        for c in due_checks:
            ar = c["plant"].area or "未分区域"
            area_inspector_hint[ar].add(ar)
        suggestions_by_assignee = {}
        for u, ld in assignee_load.items():
            tasks = ld["tasks"]
            level = "🟢正常负载"
            if tasks >= OVERLOAD_THRESHOLD:
                level = "🔴过载(建议分流)"
            elif tasks >= OVERLOAD_THRESHOLD - 1:
                level = "🟡临界负载"
            suggestions_by_assignee[u] = {
                "user": u,
                "task_count": tasks,
                "urgent_count": ld["urgent"],
                "load_level": level,
                "overload": tasks >= OVERLOAD_THRESHOLD,
                "suggestion": (
                    f"⚠️ 建议将 {u} 的{min(2, tasks - OVERLOAD_THRESHOLD + 1)}条低优先级任务分流给其他同事"
                    if tasks >= OVERLOAD_THRESHOLD
                    else ("可适当增加任务" if tasks <= 1 else "负载合理")
                ),
            }

        # 2) 区域合并巡检建议：同区域有 >= 3 株待检，建议一次巡检
        MERGE_THRESHOLD = 3
        suggestions_by_area = {}
        for ar, ag in by_area.items():
            check_cnt = len(ag["checks"])
            task_cnt = len(ag["tasks"])
            total = check_cnt + task_cnt
            merge = check_cnt >= MERGE_THRESHOLD
            suggestions_by_area[ar] = {
                "area": ar,
                "due_checks": check_cnt,
                "due_tasks": task_cnt,
                "total_work": total,
                "merge_suggested": merge,
                "reason": (
                    f"同区域有{check_cnt}株待检，建议合并巡检，减少来回跑动"
                    if merge else (
                        f"建议与相邻区域一起处理，提高效率" if total else "暂无工作"
                    )
                ),
            }

        # 3) 给每条 due_check / due_task 打建议处理顺序 (基于优先级+区域+责任人)
        order = 0
        for c in due_checks:
            order += 1
            c["suggestion_order"] = order
        order = 0
        for t in due_tasks:
            order += 1
            t["suggestion_order"] = order

        return {
            "scope": scope, "scope_label": label,
            "start_date": start_str, "end_date": end_str,
            "due_checks": due_checks,
            "due_tasks": due_tasks,
            "by_area": by_area,
            "by_assignee": by_assignee,
            "priority_distribution": {"checks": p_count_c, "tasks": p_count_t},
            "scheduling": {
                "overload_threshold": OVERLOAD_THRESHOLD,
                "merge_threshold": MERGE_THRESHOLD,
                "suggestions_by_assignee": dict(sorted(
                    suggestions_by_assignee.items(),
                    key=lambda kv: (-kv[1]["task_count"], -kv[1]["urgent_count"])
                )),
                "suggestions_by_area": suggestions_by_area,
                "overloaded_users": [u for u, v in suggestions_by_assignee.items() if v["overload"]],
                "merge_areas": [a for a, v in suggestions_by_area.items() if v["merge_suggested"]],
            },
            "summary": {
                "checks_count": len(due_checks),
                "checks_overdue": sum(1 for c in due_checks if c["is_overdue"]),
                "tasks_count": len(due_tasks),
                "tasks_overdue": sum(1 for t in due_tasks if t["days_left"] < 0),
                "urgent_p0_p1": p_count_c["P0"] + p_count_c["P1"] + p_count_t["P0"] + p_count_t["P1"],
                "overloaded_users_count": len([u for u, v in suggestions_by_assignee.items() if v["overload"]]),
                "merge_areas_count": len([a for a, v in suggestions_by_area.items() if v["merge_suggested"]]),
            }
        }

    # ==================== 任务批量完成结果导入 (issue5, issue3_new) ====================
    def import_task_completion(self, records: List[Dict]) -> Dict:
        """
        records: 每条含 task_no, processor(处理人), completed_at(完成时间可选), result(备注/处理结果可选), new_status(可选，默认已完成)
        抗造特性: 空任务号跳过、重复任务号取最后一条、不存在的任务号算失败，均不中断
        返回: {
          total, success, failed, skipped,
          success_list: [{task_no, task_id, processor, status, completed_at}],
          skipped_list: [{row_index, reason, raw}],
          failed_list:  [{row_index, task_no, reason, raw}],
          details: [{'task_no','ok','msg'}] (兼容旧格式)
        }
        """
        # 第1步：按 task_no 去重，保留最后一条
        seen = {}
        skip_list = []
        for idx, rec in enumerate(records):
            tno = (rec.get("task_no") or rec.get("任务编号") or rec.get("编号") or "").strip()
            if not tno:
                skip_list.append({"row_index": idx + 1, "reason": "任务编号为空", "raw": rec})
                continue
            seen[tno] = (idx, rec)
        dedup = [r for _, r in seen.values()]

        summary = {
            "total": len(records),
            "success": 0, "failed": 0, "skipped": len(skip_list),
            "success_list": [], "skipped_list": skip_list, "failed_list": [],
            "details": []
        }
        # 把之前跳过的空行记入兼容格式
        for s in skip_list:
            summary["details"].append({"task_no": "(空)", "ok": False, "msg": s["reason"]})

        # 第2步：逐条处理
        for row_index_in_dedup, rec in enumerate(dedup, 1):
            tno = (rec.get("task_no") or rec.get("任务编号") or rec.get("编号") or "").strip()
            processor = (rec.get("processor") or rec.get("处理人") or rec.get("完成人") or "").strip()
            completed_at = (rec.get("completed_at") or rec.get("完成时间") or rec.get("处理时间") or "").strip()
            result = (rec.get("result") or rec.get("备注") or rec.get("处理结果") or rec.get("说明") or "").strip()
            new_status = (rec.get("new_status") or rec.get("状态") or TaskStatus.COMPLETED.value).strip()

            if not completed_at:
                completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            task_id = None
            try:
                with self._get_conn() as conn:
                    c = conn.cursor()
                    c.execute("SELECT * FROM tasks WHERE task_no = ?", (tno,))
                    row = c.fetchone()
                    if not row:
                        summary["failed"] += 1
                        summary["failed_list"].append({
                            "row_index": row_index_in_dedup,
                            "task_no": tno, "reason": "任务编号不存在", "raw": rec
                        })
                        summary["details"].append({"task_no": tno, "ok": False, "msg": "任务编号不存在"})
                        continue

                    task_id = row["id"]
                    # 更新任务
                    if processor:
                        c.execute("UPDATE tasks SET status = ?, completed_at = ?, assignee = COALESCE(NULLIF(assignee, ''), ?) WHERE task_no = ?",
                                  (new_status, completed_at, processor, tno))
                    else:
                        c.execute("UPDATE tasks SET status = ?, completed_at = ? WHERE task_no = ?",
                                  (new_status, completed_at, tno))
                    # 追加备注（合并现有任务说明）
                    if result:
                        existing = row["description"] or ""
                        snippet = result[:200]
                        if snippet not in (existing or ""):
                            new_desc = f"{existing} | 完成说明:{snippet}" if existing else f"完成说明:{snippet}"
                            c.execute("UPDATE tasks SET description = ? WHERE task_no = ?", (new_desc, tno))

                    # 若该任务在某未闭环的遗留事项中，也同步 pending_item 的状态和处理人
                    if new_status in ("已完成", "已闭环", "已处理"):
                        c.execute("""UPDATE shift_pending_items
                            SET status = '已闭环',
                                process_result = COALESCE(process_result,'') || ?,
                                processed_by   = COALESCE(NULLIF(processed_by,''), ?),
                                processed_at   = COALESCE(processed_at, ?)
                            WHERE item_type = '任务' AND (ref_code = ? OR ref_id = ?)
                              AND status NOT IN ('已完成','已闭环','已处理','已取消')""",
                                  (f"[导入{datetime.now().strftime('%Y-%m-%d %H:%M')}] {result or '批量导入完成'}; ",
                                   processor or "批量导入", completed_at, tno, task_id))
            except Exception as e:
                summary["failed"] += 1
                summary["failed_list"].append({
                    "row_index": row_index_in_dedup,
                    "task_no": tno, "reason": f"异常: {str(e)[:80]}", "raw": rec
                })
                summary["details"].append({"task_no": tno, "ok": False, "msg": f"处理异常: {e}"})
                continue

            summary["success"] += 1
            summary["success_list"].append({
                "task_no": tno, "task_id": task_id,
                "processor": processor or "(未变)",
                "status": new_status, "completed_at": completed_at,
                "result": result
            })
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
                          (handover_id, tid, r["task_no"] if r else "",
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
        """重新指派责任人、补处理结果、更新闭环状态；同时同步真实tasks表"""
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

        # 先查item详情，后续同步tasks需要
        item_info = None
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT item_type, ref_id, ref_code FROM shift_pending_items WHERE id = ?", (item_id,))
            r = c.fetchone()
            if r:
                item_info = dict(r)

        params.append(item_id)
        sql = f"UPDATE shift_pending_items SET {', '.join(updates)} WHERE id = ?"
        rowcount = 0
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(sql, params)
            rowcount = c.rowcount

            # 同步：若item_type=任务，则同步真实 tasks 表
            if rowcount > 0 and item_info and item_info.get("item_type") == "任务":
                tno = item_info.get("ref_code")
                tid = item_info.get("ref_id")
                if tno or tid:
                    q = "SELECT id FROM tasks WHERE task_no = ? OR id = ? LIMIT 1"
                    c.execute(q, (tno or "", tid or 0))
                    task_row = c.fetchone()
                    if task_row:
                        real_tid = task_row["id"]
                        if new_assignee:
                            c.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (new_assignee, real_tid))
                        if new_status and new_status in ("已完成", "已闭环", "已处理"):
                            c.execute("""UPDATE tasks
                                SET status = '已完成',
                                    completed_at = COALESCE(completed_at, ?)
                                WHERE id = ?""",
                                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), real_tid))
        return rowcount > 0

    def get_shift_closed_loop_status(self, shift_id: int, status_filter: str = "") -> Dict:
        """
        某班次下所有遗留事项的闭环统计
        status_filter: ''(全部) | '未闭环' | '已闭环'
        """
        handovers = self.get_handovers(shift_id=shift_id, limit=50)
        result = {"handovers": len(handovers),
                  "items_total": 0, "items_closed": 0, "items_pending": 0,
                  "items": [], "closed_rate": 0.0}
        for ho in handovers:
            items = self.get_handover_items(ho.id)
            for it in items:
                it_closed = (it["status"] or "") in ("已完成", "已闭环", "已处理", "已取消")
                # 过滤
                if status_filter == "未闭环" and it_closed:
                    continue
                if status_filter == "已闭环" and not it_closed:
                    continue
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

    # ==================== 历史追溯查询 (issue5_new) ====================
    def get_entity_trace(self, *, plant_code: str = "", task_no: str = "",
                         task_id: Optional[int] = None) -> Dict:
        """
        按 植株编号 或 任务号/任务ID 聚合查询完整的处理时间线，方便现场解释：
        返回: {
            'entity_type': 'plant'|'task'|'unknown',
            'plant_info': {...}, 'task_info': {...},
            'events': [ {time, type, level, content, raw} ] 时间倒序
        }
        events.type: [巡检记录]|[派单生成]|[任务更新]|[交接班]|[重指派]|[闭环记录]
        """
        entity_type = "unknown"
        plant_info, task_info = None, None
        events = []

        # ---------- 步骤1：先查实体基本信息 ----------
        plant_code = (plant_code or "").strip()
        task_no = (task_no or "").strip()

        with self._get_conn() as conn:
            c = conn.cursor()

            # A. 查植株档案
            if plant_code:
                c.execute("SELECT * FROM plants WHERE code = ?", (plant_code,))
                r = c.fetchone()
                if r:
                    plant_info = dict(r)
                    entity_type = "plant"
                    events.append({
                        "time": r["planted_date"] or r["created_at"] or "",
                        "type": "档案录入", "level": "INFO",
                        "content": f"📋 植株建档: {r['code']} / {r['name']} / {r['species'] or '-'} / {r['area'] or '-'}",
                        "raw": "plants表"
                    })
                    events.append({
                        "time": (r["last_check_date"] or "") + " " + (r["status"] or ""),
                        "type": "当前状态", "level": "STATUS",
                        "content": f"🏷  当前状态: {r['status']}    上次巡检: {r['last_check_date'] or '未巡检'}    下次巡检: {r['next_check_date'] or '未设置'}",
                        "raw": "plants当前"
                    })

            # B. 查任务档案
            if task_no or task_id:
                if task_id:
                    c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                else:
                    c.execute("SELECT * FROM tasks WHERE task_no = ?", (task_no,))
                r = c.fetchone()
                if r:
                    task_info = dict(r)
                    entity_type = "task"
                    # 如果该任务关联某植株，同时获取plant_info
                    if r["plant_code"] and not plant_info:
                        c2 = conn.cursor()
                        c2.execute("SELECT * FROM plants WHERE code = ?", (r["plant_code"],))
                        pr = c2.fetchone()
                        if pr:
                            plant_info = dict(pr)
                    events.append({
                        "time": r["created_at"] or "",
                        "type": "任务创建", "level": "INFO",
                        "content": (
                            f"📝 创建任务#{r['task_no']} [{r['task_type']}] 指派人: {r['assignee'] or '未指派'} 截止: {r['due_date'] or '-'} 优先级:{r['priority']}\n"
                            f"        → 植株: {r['plant_code']} {r['plant_name'] or ''} / {r['area'] or '-'}\n"
                            f"        → 描述: {(r['description'] or '')[:200]}"
                        ),
                        "raw": "tasks create"
                    })
                    # 完成情况
                    if r["status"] in ("已完成", "已闭环"):
                        events.append({
                            "time": r["completed_at"] or "",
                            "type": "任务完成", "level": "SUCCESS",
                            "content": f"✅ 任务已完成 at {r['completed_at']}",
                            "raw": "tasks done"
                        })
                    elif r["status"] == TaskStatus.OVERDUE.value:
                        events.append({
                            "time": r["due_date"] or "",
                            "type": "任务逾期", "level": "ERROR",
                            "content": f"🔴 任务已逾期(截止: {r['due_date']})，当前状态: {r['status']}",
                            "raw": "tasks overdue"
                        })
                    else:
                        events.append({
                            "time": r["due_date"] or "",
                            "type": "任务进行中", "level": "WARN",
                            "content": f"🟡 当前状态: {r['status']}，截止: {r['due_date']}",
                            "raw": "tasks pending"
                        })

            # ---------- 步骤2：根据实体补充关联历史 ----------
            search_code = plant_code or (task_info and task_info.get("plant_code") or "")
            search_task_no = task_no or (task_info and task_info.get("task_no") or "")
            search_task_id = task_id or (task_info and task_info.get("id") or None)

            # A. 该植株所有巡检记录
            if search_code:
                c.execute("""SELECT * FROM inspections
                    WHERE plant_code = ? ORDER BY check_date DESC, created_at DESC""",
                          (search_code,))
                for r in c.fetchall():
                    issues = []
                    if r["has_pest"]: issues.append("虫害")
                    if r["has_water_deficit"]: issues.append("缺水")
                    if r["has_withered"]: issues.append("枯黄")
                    if r["needs_pruning"]: issues.append("需修剪")
                    if r["needs_replant"]: issues.append("需补苗")
                    lvl = "WARN" if issues or (r["health_score"] or 100) < 80 else "INFO"
                    events.append({
                        "time": r["check_date"] or r["created_at"] or "",
                        "type": "巡检记录",
                        "level": lvl,
                        "content": (
                            f"🔍 巡检评分: {r['health_score']}分  巡检人: {r['inspector'] or '-'}  区域: {r['area'] or '-'}\n"
                            f"        → 发现: {','.join(issues) if issues else '无异常'}   备注: {(r['notes'] or '')[:150]}"
                        ),
                        "raw": f"insp#{r['id']}"
                    })

            # B. 该植株/任务关联的任务记录
            where, args = [], []
            if search_code:
                where.append("plant_code = ?")
                args.append(search_code)
            if search_task_no:
                where.append("task_no = ?")
                args.append(search_task_no)
            if search_task_id:
                where.append("id = ?")
                args.append(search_task_id)
            if where:
                c.execute(f"SELECT * FROM tasks WHERE {' OR '.join(where)} ORDER BY created_at DESC", args)
                for r in c.fetchall():
                    if task_info and r["id"] == task_info["id"]:
                        continue  # 避免重复
                    events.append({
                        "time": r["created_at"] or "",
                        "type": f"任务-{r['status']}",
                        "level": "WARN" if r["status"] not in ("已完成",) else "SUCCESS",
                        "content": (
                            f"📋 任务#{r['task_no']} [{r['task_type']}] {r['status']}  指派人: {r['assignee'] or '未指派'}  截止: {r['due_date'] or '-'}\n"
                            f"        → {(r['description'] or '')[:200]}"
                        ),
                        "raw": f"task#{r['id']}"
                    })

            # C. 交接班遗留事项记录(含重指派、闭环)
            c_args = []
            where2 = []
            if search_task_no or search_task_id:
                where2.append("(item_type='任务' AND (ref_code = ? OR ref_id = ?))")
                c_args += [search_task_no or "", search_task_id or 0]
            if search_code:
                where2.append("(item_type IN ('异常株','逾期未检') AND ref_code = ?)")
                c_args.append(search_code)
            if where2:
                c.execute(f"""SELECT i.*, h.shift_no, h.handover_from, h.handover_to
                    FROM shift_pending_items i
                    LEFT JOIN shift_handovers h ON h.id = i.handover_id
                    WHERE {' OR '.join(where2)}
                    ORDER BY i.created_at DESC, i.id DESC""", c_args)
                for r in c.fetchall():
                    events.append({
                        "time": r["created_at"] or "",
                        "type": "交接班-遗留事项",
                        "level": "WARN",
                        "content": (
                            f"🤝 {r['shift_no'] or ''} 交接单#{r['handover_id']}: {r['handover_from'] or ''}→{r['handover_to'] or ''}\n"
                            f"        → 类型:{r['item_type']} 标题:{r['title']}\n"
                            f"        → 原责任人:{r['original_assignee'] or '-'} → 当前:{r['current_assignee'] or '-'}   状态:{r['status']}\n"
                            + (f"        → 处理结果:{(r['process_result'] or '')[:200]}" if r['process_result'] else "")
                            + (f"  (处理人:{r['processed_by'] or '-'} @{r['processed_at'] or '-'})" if r['processed_at'] else "")
                        ),
                        "raw": f"pending_item#{r['id']}"
                    })
                    # 若当前接手人 != 原，加一条"重指派"事件
                    if r["current_assignee"] and r["original_assignee"] and r["current_assignee"] != r["original_assignee"]:
                        events.append({
                            "time": r["processed_at"] or r["created_at"] or "",
                            "type": "任务重指派",
                            "level": "WARN",
                            "content": f"🔁 事项#{r['id']} 重新指派: {r['original_assignee']} → {r['current_assignee']}",
                            "raw": f"reassign#{r['id']}"
                        })
                    if r["status"] in ("已闭环", "已完成", "已处理"):
                        events.append({
                            "time": r["processed_at"] or "",
                            "type": "闭环记录",
                            "level": "SUCCESS",
                            "content": f"✅ 事项#{r['id']} 已闭环 by {r['processed_by'] or '-'} @ {r['processed_at'] or '-'}",
                            "raw": f"closed#{r['id']}"
                        })

            # D. 该植株的所有交接单出现记录（整体级）
            if search_code:
                c.execute("""SELECT h.* FROM shift_handovers h
                    WHERE h.abnormal_plant_codes LIKE ? OR h.overdue_plant_codes LIKE ?
                    ORDER BY h.handover_time DESC""",
                          (f"%{search_code}%", f"%{search_code}%"))
                for r in c.fetchall():
                    events.append({
                        "time": r["handover_time"] or "",
                        "type": "交接班-整体清单",
                        "level": "INFO",
                        "content": (
                            f"📦 {r['shift_no'] or ''} #{r['id']} 交接: {r['handover_from']}→{r['handover_to']} 确认:{r['confirmed']}\n"
                            f"        → 原因: {(r['unfinished_reason'] or '-')[:120]}  备注: {(r['remarks'] or '-')[:120]}"
                        ),
                        "raw": f"handover#{r['id']}"
                    })

        # ---------- 步骤3：按时间倒序排序 ----------
        def _sort_key(e):
            t = (e.get("time") or "").strip().replace(" ", "0")[:19]
            # 若时间为空则排最后
            if not t or t == "0":
                return "9999",
            return t
        events.sort(key=_sort_key, reverse=True)

        # ---------- 总结结论 ----------
        summary = {
            "inspection_count": sum(1 for e in events if e["type"].startswith("巡检")),
            "task_count": sum(1 for e in events if e["type"].startswith("任务") or e["type"] == "任务创建"),
            "handover_count": sum(1 for e in events if "交接班" in e["type"]),
            "reassign_count": sum(1 for e in events if e["type"] == "任务重指派"),
            "closed_count": sum(1 for e in events if e["type"] == "闭环记录"),
            "is_open_loop": any(e["level"] == "ERROR" or (e["type"] == "任务进行中") for e in events),
        }

        return {
            "entity_type": entity_type,
            "plant": plant_info,
            "task": task_info,
            "events_count": len(events),
            "summary": summary,
            "events": events,
        }
