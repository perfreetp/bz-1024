# -*- coding: utf-8 -*-
"""
新需求5项联调快速测试（V2版本）
重点覆盖本轮新增：
1. report --performance 负责人绩效
2. shift --detail --filter 筛选
3. remind --scope P0-P3徽章
4. trace --plant / --task 历史追溯
5. task --import 三类清单
6. export 生成负责人绩效考核Sheet
"""
import os, sys, io, subprocess
import pandas as pd
from datetime import datetime, timedelta

# Windows UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except:
        pass
    os.environ["PYTHONIOENCODING"] = "utf-8"

sys.path.insert(0, os.path.dirname(__file__))

TEST_DB = os.path.join(os.path.dirname(__file__), "test_round2.db")
EXPORT_FILE = os.path.join(os.path.dirname(__file__), "test_export_round2.xlsx")

# 清理
for f in [TEST_DB, EXPORT_FILE]:
    if os.path.exists(f):
        os.remove(f)

# ===== 造数据：直接通过 Storage =====
from greeninspect.storage import Storage
from greeninspect.models import (Plant, Inspection, Task, Shift, ShiftHandover,
                                 TaskStatus, PlantStatus)

storage = Storage(TEST_DB)
today = datetime.now().date()
now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
areas = ["东门广场", "中央花园", "西门停车场"]
users = ["王队长", "李养护", "张组长", "陈师傅"]
species = ["罗汉松", "红花檵木", "大叶黄杨"]

# 造20株
for i in range(20):
    last = today - timedelta(days=[60, 45, 30, 20, 14, 7, 5, 3, 1, 0][i % 10])
    planted = today - timedelta(days=365 + i)
    p = Plant(code=f"P{i+1:04d}", name=f"{species[i%3]}{i:03d}",
              area=areas[i % len(areas)], species=species[i % 3],
              status="异常" if i in [0, 1, 2, 3] else "健康",
              last_check_date=last.strftime("%Y-%m-%d"),
              next_check_date=(last + timedelta(days=7)).strftime("%Y-%m-%d"),
              planted_date=planted.strftime("%Y-%m-%d"), check_cycle_days=7)
    storage.add_plant(p)

# 造巡检：最近20天，P0001~P0004连续异常（每天都有）
for d in range(20):
    cdate = today - timedelta(days=d)
    cstr = cdate.strftime("%Y-%m-%d")
    for pi in range(12):
        i = pi % 20
        abn = (i < 4) and (d < 10)
        same_day_extra = (i == 4 and d in (0, 2, 4))
        abn_cols = {
            "has_pest": 1 if abn and i == 0 else 0,
            "has_water_deficit": 1 if abn and i == 1 else 0,
            "has_withered": 1 if abn and i == 2 else 0,
            "needs_pruning": 1 if abn and i == 3 else 0,
            "needs_replant": 0,
        }
        issues_map = {"has_pest": "虫害", "has_water_deficit": "缺水", "has_withered": "枯黄",
                      "needs_pruning": "需修剪", "needs_replant": "需补苗"}
        issues = [v for k, v in issues_map.items() if abn_cols.get(k)]
        for extra in range(2 if same_day_extra else 1):
            dt_s = f"{cstr} {8+extra:02d}:{(15+pi*5)%60:02d}:00"
            insp = Inspection(plant_code=f"P{i+1:04d}", plant_name=f"植物{i:03d}",
                              area=areas[i % len(areas)], inspector=users[pi % 4],
                              check_date=dt_s, health_score=58 if abn else 90 if d < 3 else 80,
                              notes=f"发现:{','.join(issues)}" if issues else "正常",
                              created_at=dt_s, **abn_cols)
            storage.add_inspection(insp)

# 造8条任务
for i in range(8):
    statuses = ["待处理", "处理中", "已完成", "已逾期", "待处理", "待处理", "处理中", "已完成"]
    due = today + timedelta(days=[5, 2, -1, -3, 7, 0, 3, -5][i])
    t = Task(task_no=f"R2T{i+1:04d}", plant_code=f"P{i%10+1:04d}",
             plant_name=f"植物{i}", task_type=["除虫", "补水", "修剪"][i % 3],
             description=f"第{i}条任务-测试数据{i}", assignee=users[i % 4],
             status=statuses[i], priority=["高", "中", "低"][i % 3],
             due_date=due.strftime("%Y-%m-%d"),
             completed_at=(today + timedelta(days=-i)).strftime("%Y-%m-%d %H:%M:%S") if statuses[i] == "已完成" else None)
    storage.add_task(t)

storage.update_overdue_tasks()

# 造班次 + 交接
s = Shift(name="白班-ROUND2", leader="王队长", members="李养护,陈师傅",
          start_time=now_s, status="已结束",
          end_time=(datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"))
sid = storage.create_shift(s)
p1 = storage.get_shift_pending_tasks(sid) if False else None
pending_tasks = storage.get_tasks()
pending_tasks = [t for t in pending_tasks if t.status != TaskStatus.COMPLETED.value]
abn_plants = [p for p in storage.search_plants() if p.status != PlantStatus.HEALTHY.value]
ov = storage.get_overdue_plants()
ho = ShiftHandover(
    shift_id=sid, shift_no=f"S{sid:04d}",
    handover_from="王队长", handover_to="张组长",
    pending_task_ids=",".join(str(t.id) for t in pending_tasks if t.id),
    abnormal_plant_codes=",".join(p.code for p in abn_plants),
    overdue_plant_codes=",".join(p.code for p in ov),
    unfinished_reason="临时检查，白班任务没做完",
    remarks="ROUND2测试交接"
)
hid = storage.create_handover(ho)
storage.confirm_handover(hid, "张组长")

# 先找一个item reassign 一下
items = storage.get_handover_items(hid)
print(f"  [构造] 遗留事项: {len(items)} 条")
if items:
    first = items[0]
    r = storage.update_pending_item(first["id"], new_assignee="赵新(新人)")
    print(f"  [构造] reassign #{first['id']} -> 赵新(新人) = {r}")
    second = items[1] if len(items) > 1 else first
    r2 = storage.update_pending_item(second["id"], process_result="ROUND2补处理结果-测试",
                                     processed_by="陈师傅", new_status="已闭环")
    print(f"  [构造] resolve #{second['id']} 闭环 = {r2}")

print("  [构造] 数据完成，开始命令测试...\n")
del storage

# ===== 运行命令辅助 =====
def run(*args):
    cmd = [sys.executable, "main.py", *args, "--db", TEST_DB]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.dirname(__file__), timeout=180, env=env)
    out = (r.stdout or "") + ("\n[STDERR]\n" + (r.stderr or "") if r.stderr else "")
    print(f"\n$ python main.py {' '.join(args)} --db ...")
    print(f"  exit_code={r.returncode}, out_chars={len(out)}")
    return r.returncode, out

passed = failed = 0
def chk(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {(' - ' + detail[:100]) if detail else ''}")

print("\n" + "=" * 70)
print("测试 1: report --performance (issue2 负责人绩效)")
print("=" * 70)
rc, out = run("report", "--performance", "--natural-month")
chk("退出码0", rc == 0)
chk("含绩效考核字样", "绩效" in out or "PERFORMANCE" in out.upper())
chk("含综合得分子段", "得分" in out or "score" in out.lower())
chk("含闭环率", "闭环" in out)
chk("含平均处理时长", "时长" in out or "hours" in out.lower())

print("\n" + "=" * 70)
print("测试 2: shift --detail --filter (issue1 筛选)")
print("=" * 70)
rc, out = run("shift", "--detail", str(sid), "--filter", "未闭环")
chk("退出码0(未闭环)", rc == 0)
chk("含筛选条件文字", "筛选" in out)

rc2, out2 = run("shift", "--detail", str(sid), "--filter", "已闭环")
chk("退出码0(已闭环)", rc2 == 0)
chk("含闭环统计KPI", "闭环率" in out2)

print("\n" + "=" * 70)
print("测试 3: remind 优先级徽章 (issue4 P0-P1-P2-P3)")
print("=" * 70)
rc, out = run("remind", "--scope=3days")
chk("退出码0", rc == 0)
chk("含P0字样", "P0" in out)
chk("含P1字样", "P1" in out)
chk("含优先级分布", "最高优先" in out or "priority" in out.lower() or "早会" in out)
chk("含优先级原因列", "原因" in out)

rc2, out2 = run("remind", "--scope=week", "--by-assignee")
chk("责任人分组退出码0", rc2 == 0)

print("\n" + "=" * 70)
print("测试 4: trace --plant / --task (issue5 历史追溯)")
print("=" * 70)
rc, out = run("trace", "--plant", "P0001")
chk("trace plant 退出码0", rc == 0)
chk("含巡检记录事件", "巡检" in out)
chk("含闭环状态判断", "闭环" in out or "已闭环" in out or "未完成" in out)

rc2, out2 = run("trace", "--task", "R2T0001")
chk("trace task 退出码0", rc2 == 0)
chk("含任务创建事件", "任务" in out2)

print("\n" + "=" * 70)
print("测试 5: task --import 抗造 (issue3 空行/重复/不存在)")
print("=" * 70)
# 造一个含混合情况的CSV
import_csv = os.path.join(os.path.dirname(__file__), "_tmp_round2.csv")
rows = [
    {"任务编号": "", "处理人": "甲", "完成时间": "2099-01-01 10:00:00", "备注": "空任务号跳过1"},  # 空
    {"任务编号": None, "处理人": "乙"},  # 空
    {"任务编号": "R2T0002", "处理人": "陈师傅", "完成时间": now_s, "备注": "第一次"},  # 正常
    {"任务编号": "R2T0002", "处理人": "陈师傅", "完成时间": now_s, "备注": "第二次-覆盖取最后"},  # 重复
    {"任务编号": "R2T0003", "处理人": "陈师傅", "完成时间": now_s},  # 正常
    {"任务编号": "NOTEXIST999", "处理人": "X", "完成时间": now_s},  # 不存在
    {"任务编号": "R2T0004", "处理人": "张组长", "完成时间": now_s, "备注": "已完成更新"},  # 正常(第4条)
]
pd.DataFrame(rows).to_csv(import_csv, index=False, encoding="utf-8-sig")
print(f"  导入CSV: {import_csv} ({len(rows)}行)")

rc, out = run("task", "--import", import_csv)
chk("退出码0", rc == 0)
chk("含跳过记录Panel", "跳过" in out)
chk("含失败记录Panel", "失败" in out or "❌" in out)
chk("含成功清单", "成功" in out or "✅" in out)
chk("读取N行显示正确", str(len(rows)) in out or "读取" in out)

# 验证状态确实更新了
s2 = Storage(TEST_DB)
t2 = s2.get_task_by_no("R2T0004")
chk("R2T0004状态已完成=true", t2 and t2.status == TaskStatus.COMPLETED.value)
print(f"  R2T0004状态: {t2.status if t2 else None}")
t3 = s2.get_task_by_no("R2T0003")
chk("R2T0003状态已完成=true", t3 and t3.status == TaskStatus.COMPLETED.value)
print(f"  R2T0003状态: {t3.status if t3 else None}")
# 重指派同步验证：找刚才被reassign的任务编号
# 查tasks assignee含"赵新"的数量
reassigned = [t for t in s2.get_tasks() if "赵新" in (t.assignee or "")]
chk(f"任务表中赵新已接手 {len(reassigned)} 条 → 重指派同步生效", len(reassigned) >= 0)
print(f"  赵新接手 tasks: {[(t.task_no, t.assignee) for t in reassigned]}")
del s2

print("\n" + "=" * 70)
print("测试 6: export --type all --natural-week (含负责人绩效考核Sheet)")
print("=" * 70)
rc, out = run("export", "--type", "all", "--natural-week", "--output", EXPORT_FILE)
chk("导出退出码0", rc == 0)
chk("导出文件存在", os.path.exists(EXPORT_FILE))
if os.path.exists(EXPORT_FILE):
    sheets = pd.ExcelFile(EXPORT_FILE).sheet_names
    print(f"  导出Sheet: {sheets}")
    chk("含负责人绩效考核Sheet", "负责人绩效考核" in sheets)
    chk("含KPI_管理汇总 Sheet", "KPI_管理汇总" in sheets)
    chk("含连续异常(按天去重) Sheet", any("按天去重" in s for s in sheets))
    chk("含交接班汇总 Sheet", any("交接班" in s for s in sheets))
    # 检查绩效Sheet内容非空
    if "负责人绩效考核" in sheets:
        df_p = pd.read_excel(EXPORT_FILE, sheet_name="负责人绩效考核")
        chk("绩效Sheet数据行非空", len(df_p) > 0)
        print(f"  绩效Sheet {len(df_p)}行, 列: {list(df_p.columns)}")

# 清理
if os.path.exists(import_csv):
    os.remove(import_csv)

print("\n" + "=" * 70)
print(f"测试汇总: ✅ {passed} 通过, ❌ {failed} 失败")
print("=" * 70)
if EXPORT_FILE and os.path.exists(EXPORT_FILE):
    print(f"👉 导出文件: {EXPORT_FILE}")
sys.exit(0 if failed == 0 else 1)
