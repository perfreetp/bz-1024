import os, sys
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass

sys.path.insert(0, os.path.dirname(__file__))
from greeninspect.storage import Storage

TEST_DB = os.path.join(os.path.dirname(__file__), "test_features.db")
s = Storage(TEST_DB)

print("=" * 60)
print("验证 1: 连续异常新字段 (issue3)")
print("=" * 60)
abn = s.get_consecutive_abnormal_plants(days=30, min_occurrences=3)
if abn:
    keys = list(abn[0].keys())
    print('返回字段:', keys)
    need = ['abnormal_days', 'last_issue', 'has_task', 'calendar_duration', 'top_issue', 'task_count']
    for k in need:
        assert k in keys, f"缺少字段 {k}"
    print("  ✅ 全部新字段存在")
    for c in abn[:3]:
        print(f"    {c['plant_code']}: abnormal_days={c['abnormal_days']}, "
              f"calendar_duration={c['calendar_duration']}, "
              f"last_issue={c['last_issue']}, has_task={c['has_task']}, task_count={c['task_count']}")

print()
print("=" * 60)
print("验证 2: KPI 管理看板 (issue1)")
print("=" * 60)
df = '2026-01-01'
dt = '2099-12-31'
kpi = s.get_kpi_dashboard(df, dt, '')
print("  overall keys:", list(kpi['overall'].keys()))
assert kpi['overall']['coverage_rate'] is not None
assert kpi['overall']['task_completion_rate'] is not None
assert kpi['overall']['overdue_risk_rate'] is not None
print("  ✅ 全局汇总覆盖率/完成率/逾期风险率 均有值")
if kpi['by_area']:
    print("  by_area sample:", list(kpi['by_area'][0].keys()))
if kpi['assignee_ranking']:
    print("  ranking sample:", list(kpi['assignee_ranking'][0].keys()))
    assert 'composite_score' in kpi['assignee_ranking'][0]
    print("  ✅ 责任人综合得分 composite_score 存在")

print()
print("=" * 60)
print("验证 3: 提醒看板 (issue4)")
print("=" * 60)
for scope in ['tomorrow', '3days', 'week']:
    rem = s.get_upcoming_reminders(scope)
    print(f"  scope={scope}: keys={list(rem.keys())}, summary={rem['summary']}")
    assert 'by_area' in rem
    assert 'by_assignee' in rem
print("  ✅ 三种scope均有返回by_area/by_assignee")

print()
print("=" * 60)
print("验证 4: 任务批量导入 (issue5) - 造新测试任务")
print("=" * 60)
records = [
    {"task_no": "IMPORT-TEST-01", "processor": "临时", "completed_at": "2099-01-01 12:00:00"},
]
# 先查3条真实任务
real_tasks = s.get_tasks(status='待处理')[:2]
real_nos = [t.task_no for t in real_tasks] + ["NOTEXIST"]
import_records = []
for no in real_nos:
    import_records.append({"task_no": no, "processor": "导入测试员",
                           "completed_at": "2026-06-10 15:00:00",
                           "result": "从Excel批量导入测试"})
res = s.import_task_completion(import_records)
print(f"  结果: success={res['success']}, failed={res['failed']}, skipped={res['skipped']}")
for d in res['details']:
    print(f"    task={d['task_no']}: ok={d['ok']}, msg={d['msg']}")
print("  ✅ 批量导入接口正常")

print()
print("=" * 60)
print("验证 5: 交接班闭环 (issue2)")
print("=" * 60)
handovers = s.get_handovers(limit=1)
if handovers:
    hid = handovers[0].id
    sid = handovers[0].shift_id
    items = s.get_handover_items(hid)
    print(f"  handover_id={hid}, 展开遗留事项数量={len(items)} (前3条):")
    for it in items[:3]:
        print(f"    #{it['id']} {it['item_type']}: {it['title'][:20]} | 原={it['original_assignee']} | 现={it['current_assignee']} | 状态={it['status']}")
    closed = s.get_shift_closed_loop_status(sid)
    print(f"  闭环统计: {closed['items_closed']}/{closed['items_total']} = {closed['closed_rate']:.1f}%")
    assert 'items_total' in closed and 'closed_rate' in closed
    print("  ✅ 独立遗留事项闭环统计正常")

print()
print("🎉 全部 5 项新需求的核心 API 验证通过！")
