import sqlite3
import os
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

from .models import Plant, Inspection, Photo, Task, PlantStatus, TaskStatus, TaskType


DEFAULT_DB_PATH = os.path.join(os.getcwd(), ".greeninspect", "data.db")


class Storage:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
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

                CREATE INDEX IF NOT EXISTS idx_plants_area ON plants(area);
                CREATE INDEX IF NOT EXISTS idx_inspections_plant ON inspections(plant_code);
                CREATE INDEX IF NOT EXISTS idx_inspections_date ON inspections(check_date);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
                CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
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
        sql = "SELECT * FROM plants WHERE last_check_date IS NOT NULL AND next_check_date < ?"
        params = [today]
        if area:
            sql += " AND area = ?"
            params.append(area)
        sql += " ORDER BY next_check_date, area, code"
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
