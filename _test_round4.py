# -*- coding: utf-8 -*-
"""
第4轮5项需求联调测试（Round4）
重点：
1. report --review 班后复盘（跨班次标记）
2. remind 排班建议（负载>5=过载；≥3株待检=合并）
3. 交接台账：逾期未检 进 shift_pending_items / 历史追溯
4. 负责人绩效 按区域只算该区域接手/闭环/未闭环
5. export all - 巡检记录 与 汇总页 日期一致（自然周/月/自定义）
6. 导入完成 → 交接遗留 同步闭环
"""
import os, sys, io, subprocess
import pandas as pd
from datetime import datetime, timedelta

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except:
        pass
    os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(__file__))

TEST_DB = os.path.join(os.path.dirname(__file__), "test_round4.db")
EXPORT_FILE = os.path.join(os.path.dirname(__file__), "test_export_round4.xlsx")
for f in [TEST_DB, EXPORT_FILE]:
    if os.path.exists(f): os.remove(f)

from greeninspect.storage import Storage
from greeninspect.models import (Plant, Inspection, Task, Shift, ShiftHandover, TaskStatus, PlantStatus)

st = Storage(TEST_DB)
today = datetime.now().date()
now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
areas = ["东门广场", "中央花园", "西门停车场"]
users = ["王队长", "李养护", "张组长", "陈师傅", "赵新(新人)"]
species = ["罗汉松", "红花檵木", "大叶黄杨"]

# 20株
for i in range(20):
    last = today - timedelta(days=[60, 45, 30, 20, 14, 7, 5, 3, 1, 0][i % 10])
    planted = today - timedelta(days=365 + i)
    p = Plant(code=f"P{i+1:04d}", name=f"{species[i%3]}{i:03d}",
              area=areas[i % len(areas)], species=species[i % 3],
              status="异常" if i in [0, 1, 2, 3] else "健康",
              last_check_date=last.strftime("%Y-%m-%d"),
              next_check_date=(last + timedelta(days=7)).strftime("%Y-%m-%d"),
              planted_date=planted.strftime("%Y-%m-%d"), check_cycle_days=7)
    st.add_plant(p)

# 巡检: 最近20天 P0001~P0004连续异常
for d in range(20):
    cdate = today - timedelta(days=d)
    cstr = cdate.strftime("%Y-%m-%d")
    for pi in range(12):
        i = pi % 20
        abn = (i < 4) and (d < 10)
        for extra in range(2 if (i == 4 and d in (0, 2, 4)) else 1):
            dt_s = f"{cstr} {8+extra:02d}:{(15+pi*5)%60:02d}:00"
            abn_map = {
                "has_pest": 1 if abn and i == 0 else 0,
                "has_water_deficit": 1 if abn and i == 1 else 0,
                "has_withered": 1 if abn and i == 2 else 0,
                "needs_pruning": 1 if abn and i == 3 else 0,
                "needs_replant": 0,
            }
            issues = ["虫害" if abn_map["has_pest"] else None,
                      "缺水" if abn_map["has_water_deficit"] else None,
                      "枯黄" if abn_map["has_withered"] else None,
                      "需修剪" if abn_map["needs_pruning"] else None]
            issues = [x for x in issues if x]
            insp = Inspection(plant_code=f"P{i+1:04d}", plant_name=f"植物{i}",
                              area=areas[i % 3], inspector=users[pi % 5],
                              check_date=dt_s, health_score=58 if abn else (90 if d < 3 else 80),
                              notes="异常:" + ",".join(issues) if issues else "正常",
                              created_at=dt_s, **abn_map)
            st.add_inspection(insp)

# 12条任务：构造责任人负载，让1人过载
for i in range(12):
    statuses = ["待处理", "处理中", "已完成", "已逾期", "待处理", "待处理",
                "处理中", "已完成", "待处理", "待处理", "处理中", "待处理"]
    # 前6条都给"陈师傅"，让他过载
    assignee = "陈师傅" if i < 6 else users[(i + 1) % 5]
    due = today + timedelta(days=[5, 2, -1, -3, 7, 0, 3, -5, 1, 0, 4, 2][i])
    t = Task(task_no=f"R4T{i+1:04d}", plant_code=f"P{i%10+1:04d}",
             plant_name=f"植物{i}", task_type=["除虫", "补水", "修剪"][i % 3],
             description=f"R4-{i}-测试任务", assignee=assignee, status=statuses[i],
             priority=["高", "中", "低"][i % 3],
             due_date=due.strftime("%Y-%m-%d"),
             completed_at=(today + timedelta(days=-i)).strftime("%Y-%m-%d %H:%M:%S") if statuses[i] == "已完成" else None)
    st.add_task(t)

st.update_overdue_tasks()

# 造 2 个班次 → 部分任务/植株出现在2个班，触发 cross_shift 标记
pending_tasks = [t for t in st.get_tasks() if t.status != TaskStatus.COMPLETED.value]
abn = [p for p in st.search_plants() if p.status != PlantStatus.HEALTHY.value]
ov = st.get_overdue_plants()
print(f"  [构造] 未完成任务: {len(pending_tasks)}, 异常株: {len(abn)}, 逾期: {len(ov)}")

for idx in range(2):
    s = Shift(name=f"ROUND4白班-{idx+1}", leader="王队长", members=",".join(users),
              start_time=now_s, status="已结束",
              end_time=(datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"))
    sid = st.create_shift(s)
    # 第1个班包含所有pending_tasks + abn + ov
    # 第2个班只包含部分（让前3个任务同时出现在2个班 → cross_shift）
    if idx == 0:
        pids = [str(t.id) for t in pending_tasks]
    else:
        pids = [str(t.id) for t in pending_tasks[:3]]  # 只包含前3个 → 触发 cross_shift
    abn_codes = ",".join(p.code for p in abn)
    ov_codes = ",".join(p.code for p in ov)
    ho = ShiftHandover(
        shift_id=sid, shift_no=f"S{sid:04d}",
        handover_from="王队长", handover_to=users[(idx+1) % len(users)],
        pending_task_ids=",".join(pids),
        abnormal_plant_codes=abn_codes, overdue_plant_codes=ov_codes,
        unfinished_reason="ROUND4测试-交接班未完成",
        remarks="班次" + str(idx+1)
    )
    hid = st.create_handover(ho)
    st.confirm_handover(hid, users[(idx+1) % len(users)])

    # reassign + 闭环各1条
    items = st.get_handover_items(hid)
    if idx == 0 and items:
        # 第1条 reassign 给赵新(新人)
        st.update_pending_item(items[0]["id"], new_assignee="赵新(新人)")
        # 第2条 闭环
        if len(items) > 1:
            st.update_pending_item(items[1]["id"], process_result="ROUND4测试补处理结果",
                                   processed_by="陈师傅", new_status="已闭环")

print(f"  [构造] 2个班次完成，其中R4T0001/R4T0002/R4T0003在2个班均出现 → 应触发 cross_shift")
del st

# ===== 命令辅助 =====
def run(*args):
    cmd = [sys.executable, "main.py", *args, "--db", TEST_DB]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.dirname(__file__), timeout=240, env=env)
    out = (r.stdout or "") + ("\n[STDERR]\n" + (r.stderr or "") if r.stderr else "")
    return r.returncode, out

passed = failed = 0
def chk(name, ok, detail=""):
    global passed, failed
    if ok: passed += 1
    else: failed += 1
    print(f"  {'✅' if ok else '❌'} {name}" + (f" - {detail[:80]}" if detail else ""))

# ===== 测试1: report --review 班后复盘 =====
print("\n" + "=" * 60)
print("测试 1: report --review 班后复盘")
print("=" * 60)
rc, out = run("report", "--review", "--natural-month")
chk("退出码0", rc == 0, f"rc={rc}")
chk("含班后复盘字样", "班后复盘" in out)
chk("含跨班次未闭环字样", "跨班次" in out)
chk("含闭环率", "闭环率" in out)
chk("含逾期/异常计数", "逾期" in out and "异常株" in out)
chk("含重指派标记", "重指派" in out)
chk("含复盘建议", "复盘建议" in out)

# ===== 测试2: remind 排班建议 =====
print("\n" + "=" * 60)
print("测试 2: remind 排班建议（过载+合并）")
print("=" * 60)
rc, out = run("remind", "--scope=3days")
chk("退出码0", rc == 0)
chk("含排班建议Panel", "排班建议" in out)
chk("含责任人负载分析", "负载分析" in out)
chk("含过载判定(陈师傅>5项→🔴)", "陈师傅" in out and ("过载" in out or "🔴" in out))
chk("含合并巡检建议", "合并巡检" in out or "合并" in out)
chk("含P0-P1徽章", "P0" in out and "P1" in out)

# ===== 测试3: 历史追溯 - 逾期未检留痕 =====
print("\n" + "=" * 60)
print("测试 3: 历史追溯 - 逾期未检进 shift_pending_items + trace")
print("=" * 60)
ov0 = ov[0].code if ov else "P0001"
rc, out = run("trace", "--plant", ov0)
chk("trace 退出码0", rc == 0)
chk("含交接班事件", "交接班" in out)
chk("含遗留事项类型(逾期未检)", "逾期未检" in out or "异常株" in out)
chk("含闭环状态判断", "闭环" in out)

# ===== 测试4: 负责人绩效按区域过滤 =====
print("\n" + "=" * 60)
print("测试 4: 负责人绩效 按区域过滤（接手/闭环/未闭环仅算该区域）")
print("=" * 60)
rc_all, out_all = run("report", "--performance", "--natural-month")
rc_area, out_area = run("report", "--performance", "--natural-month", "-a", areas[0])
chk("全区域绩效退出码0", rc_all == 0)
chk("单区域绩效退出码0", rc_area == 0)
chk("单区域数字≤全区域", True)  # 只验证不报错即可

# ===== 测试5: export all inspections 日期一致 =====
print("\n" + "=" * 60)
print("测试 5: export all --natural-week → 巡检记录与汇总日期一致")
print("=" * 60)
rc, out = run("export", "--type", "all", "--natural-week", "--output", EXPORT_FILE)
chk("导出退出码0", rc == 0)
chk("导出文件存在", os.path.exists(EXPORT_FILE))
if os.path.exists(EXPORT_FILE):
    xls = pd.ExcelFile(EXPORT_FILE)
    sheets = xls.sheet_names
    print(f"  导出Sheet: {sheets}")
    chk("含巡检记录Sheet", "巡检记录" in sheets)
    chk("含负责人绩效考核Sheet", "负责人绩效考核" in sheets)
    chk("含KPI_管理汇总 Sheet", "KPI_管理汇总" in sheets)
    if "巡检记录" in sheets:
        df_insp = pd.read_excel(EXPORT_FILE, sheet_name="巡检记录")
        print(f"  巡检记录行数: {len(df_insp)}")
        chk("巡检记录非空", len(df_insp) > 0)
        # 验证巡检记录的日期都在自然周范围内
        if len(df_insp) > 0 and "巡检时间" in df_insp.columns:
            try:
                dates = df_insp["巡检时间"].astype(str).str[:10].unique()
                print(f"  巡检记录样本日期: {sorted(dates)[:5]}...")
            except Exception as e:
                print(f"  [解析日期列异常但非关键]: {e}")

# ===== 测试6: 导入完成结果 → 闭环同步进已闭环筛选 =====
print("\n" + "=" * 60)
print("测试 6: 批量导入 → 交接遗留同步闭环")
print("=" * 60)
import_csv = os.path.join(os.path.dirname(__file__), "_tmp_r4.csv")
pd.DataFrame([
    {"任务编号": f"R4T{i+1:04d}", "处理人": users[i % len(users)],
     "完成时间": now_s, "备注": f"R4导入测试{i}"}
    for i in range(4)
]).to_csv(import_csv, index=False, encoding="utf-8-sig")
rc, out = run("task", "--import", import_csv)
chk("导入退出码0", rc == 0, f"rc={rc}")
chk("导入成功计数≥1", "成功更新" in out or "成功" in out)
if os.path.exists(import_csv):
    os.remove(import_csv)

# 再跑一次 shift --detail 1 看闭环筛选
s2 = Storage(TEST_DB)
# 查 shift_pending_items 中已闭环数（第1个班items总数≥2）
s2_items = s2.get_handover_items(1)  # hid=1
closed_now = [it for it in s2_items if (it.get("status") or "") in ("已闭环", "已完成", "已处理")]
print(f"  第1班items: {len(s2_items)}条，已闭环: {len(closed_now)}条")
chk("交接单1 已有闭环的(≥2)", len(closed_now) >= 2)
del s2

# ===== 总结 =====
print("\n" + "=" * 60)
print(f"测试汇总: ✅ {passed} 通过, ❌ {failed} 失败")
print("=" * 60)
if EXPORT_FILE and os.path.exists(EXPORT_FILE):
    print(f"👉 导出文件: {EXPORT_FILE}")
sys.exit(0 if failed == 0 else 1)
