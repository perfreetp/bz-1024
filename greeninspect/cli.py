import os
import sys
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm, IntPrompt
from rich import print as rprint

from .storage import Storage, DEFAULT_DB_PATH
from .models import Plant, Inspection, Photo, Task, PlantStatus, TaskType, TaskStatus, Shift, ShiftHandover
from .utils import (
    console, import_plants_from_csv, print_plants_table, print_inspections_table,
    print_tasks_table, print_photos_table, determine_plant_status,
    generate_tasks_from_inspection, parse_date, days_between,
    print_shifts_table, print_handovers_table,
)

app = typer.Typer(
    name="greeninspect",
    help="园区绿植巡检命令行工具 - 供现场养护队长批量处理巡检记录",
    no_args_is_help=True,
    add_completion=False,
)


def get_storage(db_path: str = "") -> Storage:
    path = db_path or DEFAULT_DB_PATH
    return Storage(path)


def check_initialized(storage: Storage):
    counts = storage.count_plants()
    if counts.get("总计", 0) == 0:
        console.print(Panel(
            "[bold red]数据库中暂无绿植数据！[/bold red]\n"
            "请先使用 [cyan]greeninspect init --import <文件路径>[/cyan] 导入绿植清单",
            title="提示", border_style="red"
        ))


# ============================================================
# init 命令
# ============================================================
@app.command("init", help="初始化数据库并导入绿植清单(支持CSV/Excel)")
def init_cmd(
    import_file: Optional[str] = typer.Option(
        None, "--import", "-i", help="绿植清单文件路径(.csv/.xlsx/.xls)"
    ),
    sample: bool = typer.Option(
        False, "--sample", "-s", help="生成示例导入模板文件"
    ),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
    force: bool = typer.Option(False, "--force", "-f", help="重新初始化(删除旧数据)"),
):
    """初始化数据库，支持导入绿植清单"""
    storage = get_storage(db_path)

    if force and os.path.exists(storage.db_path):
        os.remove(storage.db_path)
        storage = get_storage(db_path)
        console.print("[green]✓ 已重新初始化数据库(force模式)[/green]")

    if sample:
        template_path = os.path.join(os.getcwd(), "绿植导入模板.csv")
        import pandas as pd
        template = pd.DataFrame([
            {"编号": "P001", "名称": "香樟", "品种": "樟科常绿乔木", "区域": "A区-东门",
             "位置": "入口花坛左侧", "种植日期": "2020-03-15", "巡检周期(天)": 7, "状态": "健康", "备注": ""},
            {"编号": "P002", "名称": "桂花树", "品种": "木犀科", "区域": "A区-东门",
             "位置": "办公楼南侧", "种植日期": "2019-10-01", "巡检周期(天)": 7, "状态": "健康", "备注": ""},
        ])
        template.to_csv(template_path, index=False, encoding="utf-8-sig")
        console.print(f"[green]✓ 已生成示例模板: {template_path}[/green]")
        return

    if import_file:
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("正在导入绿植清单...", total=None)
                plants = import_plants_from_csv(import_file)
                count = storage.batch_add_plants(plants)
                progress.update(task, completed=True)

            console.print(Panel(
                f"[bold green]成功导入 {count} 株绿植[/bold green]\n"
                f"共解析记录: {len(plants)} 条\n"
                f"数据库路径: {storage.db_path}",
                title="导入完成", border_style="green"
            ))

            counts = storage.count_plants()
            areas_table = "\n".join(
                f"  [cyan]{str(k):<12}[/cyan] {v:>5} 株"
                for k, v in counts.items() if k is not None
            )
            console.print(Panel(areas_table, title="按区域统计", border_style="blue"))

        except FileNotFoundError as e:
            console.print(f"[red]✗ 文件未找到: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]✗ 导入失败: {e}[/red]")
            raise typer.Exit(1)
        return

    console.print(Panel(
        "[bold green]✓ 数据库初始化完成[/bold green]\n\n"
        "使用方式:\n"
        "  1. [cyan]greeninspect init --sample[/cyan]       生成示例导入模板\n"
        "  2. [cyan]greeninspect init --import 清单.csv[/cyan]  导入绿植清单\n\n"
        f"数据库路径: {storage.db_path}",
        title="greeninspect 初始化", border_style="green"
    ))


# ============================================================
# list 命令
# ============================================================
@app.command("list", help="列出绿植/待巡检点/逾期项，支持按区域筛选")
def list_cmd(
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    pending: bool = typer.Option(False, "--pending", "-p", help="仅显示待巡检(到检)的植株"),
    overdue: bool = typer.Option(False, "--overdue", "-o", help="仅显示逾期未巡检的植株"),
    keyword: str = typer.Option("", "--search", "-s", help="按编号/名称/位置搜索"),
    status: str = typer.Option("", "--status", help="按状态筛选(健康/缺水/枯黄/虫害/需修剪/需补苗)"),
    stats: bool = typer.Option(False, "--stats", help="仅显示统计信息"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """列出绿植、待巡检点或逾期项"""
    storage = get_storage(db_path)
    check_initialized(storage)

    if stats:
        counts = storage.count_plants()
        pending_count = len(storage.get_pending_check_plants(area))
        overdue_count = len(storage.get_overdue_plants(area))

        areas = storage.get_all_areas()
        area_stats = []
        for ar in areas:
            total = counts.get(ar, 0)
            p = len(storage.get_pending_check_plants(ar))
            o = len(storage.get_overdue_plants(ar))
            area_stats.append(f"  [cyan]{ar:<12}[/cyan] 总计: {total:>4}  待检: [yellow]{p:>4}[/yellow]  逾期: [red]{o:>4}[/red]")

        console.print(Panel(
            "\n".join(area_stats) + f"\n\n[bold]全局统计[/bold]:\n"
            f"  植株总数: [cyan]{counts.get('总计', 0)}[/cyan]\n"
            f"  待巡检数: [yellow]{pending_count}[/yellow]\n"
            f"  逾期数:   [red]{overdue_count}[/red]",
            title="巡检统计", border_style="blue"
        ))
        return

    plants = []
    title = "绿植列表"

    if overdue:
        plants = storage.get_overdue_plants(area)
        title = f"逾期未巡检植株 (共{len(plants)}株)"
    elif pending:
        plants = storage.get_pending_check_plants(area)
        title = f"待巡检植株 (共{len(plants)}株)"
    else:
        plants = storage.search_plants(keyword=keyword, area=area, status=status)
        if keyword:
            title = f"搜索结果 (共{len(plants)}株)"
        elif area:
            title = f"{area} 区域绿植 (共{len(plants)}株)"
        elif status:
            title = f"状态为【{status}】的绿植 (共{len(plants)}株)"

    # 逾期标识
    today_str = datetime.now().strftime("%Y-%m-%d")
    for p in plants:
        if p.next_check_date and p.next_check_date < today_str:
            over = days_between(p.next_check_date)
            p.name = f"[red]⚠逾期{abs(over)}天[/red] {p.name}"

    print_plants_table(plants, title)


# ============================================================
# scan 命令
# ============================================================
@app.command("scan", help="录入编号或扫码定位植株(交互式批量)")
def scan_cmd(
    code: Optional[str] = typer.Argument(None, help="直接输入植株编号，不进入交互模式"),
    area: str = typer.Option("", "--area", "-a", help="限定区域(可减少输入)"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """扫描/录入编号定位植株，可进入交互模式批量扫描"""
    storage = get_storage(db_path)
    check_initialized(storage)

    if code:
        plant = storage.get_plant_by_code(code)
        if plant:
            _print_plant_detail(storage, plant)
        else:
            console.print(f"[red]✗ 未找到编号 [{code}] 的植株[/red]")
        return

    console.print(Panel(
        "[bold]进入批量扫码/录入模式[/bold]\n"
        "  输入编号后回车 → 显示植株详情\n"
        "  输入 [cyan]c[/cyan] + 编号 → 直接进入该植株的 check 巡检录入\n"
        "  输入 [yellow]q[/yellow] 或 [yellow]exit[/yellow] → 退出",
        title="scan 扫码模式", border_style="cyan"
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[cyan]请输入或扫描编号[/cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]已退出扫描模式[/yellow]")
            break

        if user_input.lower() in ("q", "quit", "exit", ""):
            console.print("[yellow]已退出扫描模式[/yellow]")
            break

        shortcut = False
        target_code = user_input
        if user_input.lower().startswith("c") and len(user_input) > 1:
            shortcut = True
            target_code = user_input[1:].strip()

        if area and target_code and not target_code.upper().startswith(area.upper()):
            target_code = f"{area.upper()}-{target_code}"

        plant = storage.get_plant_by_code(target_code)
        if not plant:
            alt = storage.search_plants(keyword=target_code, area=area)
            if len(alt) == 1:
                plant = alt[0]
                console.print(f"[yellow]提示: 匹配到编号 {plant.code}[/yellow]")
            elif len(alt) > 1:
                console.print(f"[yellow]找到 {len(alt)} 个匹配项，请输入更精确的编号:[/yellow]")
                for p in alt[:10]:
                    console.print(f"  {p.code}  {p.name}  ({p.area})")
                continue
            else:
                console.print(f"[red]✗ 未找到编号 [{target_code}] 的植株[/red]")
                continue

        if shortcut:
            _do_interactive_check(storage, plant.code)
        else:
            _print_plant_detail(storage, plant)


def _print_plant_detail(storage: Storage, plant: Plant):
    console.print(Panel(
        f"[bold cyan]编号:[/bold cyan] {plant.code}\n"
        f"[bold green]名称:[/bold green] {plant.name}  |  品种: {plant.species or '-'}\n"
        f"[bold blue]区域:[/bold blue] {plant.area or '-'}  |  位置: {plant.location or '-'}\n"
        f"[bold]种植日期:[/bold] {plant.planted_date or '-'}  |  巡检周期: {plant.check_cycle_days}天\n"
        f"[bold]上次巡检:[/bold] {plant.last_check_date or '[yellow]从未[/yellow]'}\n"
        f"[bold]下次巡检:[/bold] {plant.next_check_date or '[yellow]待安排[/yellow]'}\n"
        f"[bold]当前状态:[/bold] {plant.status}  |  备注: {plant.notes or '-'}",
        title=f"植株详情 {plant.code}", border_style="cyan"
    ))

    history = storage.get_plant_history(plant.code)
    if history:
        console.print(f"\n[bold]最近巡检记录 (共{len(history)}条):[/bold]")
        print_inspections_table(history[:5], title="")

    photos = storage.get_photos_by_plant(plant.code)
    if photos:
        console.print(f"\n[bold]照片记录 (共{len(photos)}张):[/bold]")
        print_photos_table(photos[:5], title="")

    if Confirm.ask("是否立即录入巡检记录？", default=False):
        _do_interactive_check(storage, plant.code)


# ============================================================
# check 命令
# ============================================================
@app.command("check", help="巡检录入:填写长势评分、标记异常、搜索历史")
def check_cmd(
    code: str = typer.Argument(..., help="植株编号"),
    score: Optional[int] = typer.Option(None, "--score", help="长势评分(0-100)"),
    inspector: str = typer.Option("", "--inspector", "-n", help="巡检人姓名"),
    water: bool = typer.Option(False, "--water/--no-water", help="是否缺水"),
    withered: bool = typer.Option(False, "--withered/--no-withered", help="是否枯黄"),
    pest: bool = typer.Option(False, "--pest/--no-pest", help="是否有虫害"),
    prune: bool = typer.Option(False, "--prune/--no-prune", help="是否需修剪"),
    replant: bool = typer.Option(False, "--replant/--no-replant", help="是否需补苗"),
    notes: str = typer.Option("", "--notes", help="巡检备注"),
    no_task: bool = typer.Option(False, "--no-task", help="不自动生成养护任务"),
    history: bool = typer.Option(False, "--history", help="仅查看该植株的历史记录，不录入"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="交互式填写巡检表"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """巡检录入或查看历史记录"""
    storage = get_storage(db_path)
    check_initialized(storage)

    plant = storage.get_plant_by_code(code)
    if not plant:
        console.print(f"[red]✗ 未找到编号 [{code}] 的植株[/red]")
        raise typer.Exit(1)

    if history:
        hlist = storage.get_plant_history(code)
        console.print(f"[bold]植株 {plant.code} {plant.name} 共 {len(hlist)} 条巡检记录:[/bold]")
        print_inspections_table(hlist, title="")
        if hlist:
            for h in hlist[:3]:
                if h.id:
                    photos = storage.get_photos_by_inspection(h.id)
                    if photos:
                        print_photos_table(photos, title=f"巡检 #{h.id} 照片")
        return

    if interactive:
        _do_interactive_check(storage, code)
        return

    # 命令行参数模式
    if score is None:
        score = 80
        console.print(f"[yellow]提示: 未指定评分，默认使用 {score}[/yellow]")

    if not (0 <= score <= 100):
        console.print("[red]✗ 评分必须在 0-100 之间[/red]")
        raise typer.Exit(1)

    insp = Inspection(
        plant_code=code,
        plant_name=plant.name,
        area=plant.area,
        inspector=inspector,
        health_score=score,
        has_water_deficit=water,
        has_withered=withered,
        has_pest=pest,
        needs_pruning=prune,
        needs_replant=replant,
        notes=notes,
    )
    _save_inspection(storage, plant, insp, no_task)


def _do_interactive_check(storage: Storage, code: str):
    plant = storage.get_plant_by_code(code)
    if not plant:
        console.print(f"[red]✗ 未找到编号 [{code}] 的植株[/red]")
        return

    console.print(f"\n[bold]=== 巡检录入: {plant.code} {plant.name} ({plant.area}) ===[/bold]")

    inspector = Prompt.ask("巡检人姓名", default="队长")
    score = IntPrompt.ask("长势评分 (0-100)", default=80)
    while not (0 <= score <= 100):
        console.print("[red]评分必须在 0-100 之间[/red]")
        score = IntPrompt.ask("长势评分 (0-100)", default=80)

    water = Confirm.ask("是否缺水？", default=False)
    withered = Confirm.ask("是否出现枯黄？", default=False)
    pest = Confirm.ask("是否有虫害？", default=False)
    prune = Confirm.ask("是否需要修剪？", default=False)
    replant = Confirm.ask("是否需要补苗？", default=False)
    notes = Prompt.ask("备注信息(可留空)", default="")

    insp = Inspection(
        plant_code=code,
        plant_name=plant.name,
        area=plant.area,
        inspector=inspector,
        health_score=score,
        has_water_deficit=water,
        has_withered=withered,
        has_pest=pest,
        needs_pruning=prune,
        needs_replant=replant,
        notes=notes,
    )
    _save_inspection(storage, plant, insp, no_task=False)

    if Confirm.ask("是否追加照片路径？", default=False):
        while True:
            fp = Prompt.ask("照片文件路径(输入 q 结束)", default="q")
            if fp.lower() == "q" or fp.strip() == "":
                break
            desc = Prompt.ask("照片描述(可选)", default="")
            from .models import Photo as P
            storage.add_photo(P(
                inspection_id=insp.id, plant_code=code,
                file_path=fp, description=desc,
            ))
            console.print(f"[green]✓ 已添加照片: {fp}[/green]")


def _save_inspection(storage: Storage, plant: Plant, insp: Inspection, no_task: bool):
    insp_id = storage.add_inspection(insp)
    insp.id = insp_id

    new_status = determine_plant_status(insp)
    storage.update_plant_after_check(plant.code, new_status, insp.health_score)

    flags = []
    if insp.has_water_deficit:
        flags.append("缺水")
    if insp.has_withered:
        flags.append("枯黄")
    if insp.has_pest:
        flags.append("虫害")
    if insp.needs_pruning:
        flags.append("需修剪")
    if insp.needs_replant:
        flags.append("需补苗")

    console.print(Panel(
        f"[bold green]✓ 巡检记录已保存[/bold green] (ID: {insp_id})\n\n"
        f"  植株: {plant.code} {plant.name}\n"
        f"  评分: {insp.health_score}/100\n"
        f"  异常: {'、'.join(flags) if flags else '无'}\n"
        f"  新状态: {new_status}\n"
        f"  下次巡检: {(datetime.now() + timedelta(days=plant.check_cycle_days)).strftime('%Y-%m-%d')}",
        title="巡检录入成功", border_style="green"
    ))

    if not no_task and (flags or insp.health_score < 70):
        tasks = generate_tasks_from_inspection(insp)
        if tasks:
            for t in tasks:
                storage.add_task(t)
            print_tasks_table(tasks, title=f"已自动生成 {len(tasks)} 条养护任务")


# ============================================================
# photo 命令
# ============================================================
@app.command("photo", help="追加照片路径到巡检记录或植株档案")
def photo_cmd(
    code: str = typer.Argument(..., help="植株编号"),
    file_path: Optional[str] = typer.Argument(None, help="照片文件路径"),
    insp_id: Optional[int] = typer.Option(None, "--insp", help="关联的巡检记录ID"),
    description: str = typer.Option("", "--desc", "-d", help="照片描述"),
    taken_at: str = typer.Option("", "--date", help="拍摄时间(YYYY-MM-DD)"),
    list_all: bool = typer.Option(False, "--list", "-l", help="列出该植株的所有照片"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """追加照片路径，或查询已有照片"""
    storage = get_storage(db_path)
    check_initialized(storage)

    if list_all:
        plant = storage.get_plant_by_code(code)
        if not plant:
            console.print(f"[red]✗ 未找到编号 [{code}] 的植株[/red]")
            raise typer.Exit(1)
        photos = storage.get_photos_by_plant(code)
        console.print(f"[bold]植株 {code} 共 {len(photos)} 张照片记录:[/bold]")
        print_photos_table(photos, title="")
        return

    if not file_path:
        console.print("[red]✗ 请提供照片文件路径，或使用 --list 查看已有照片[/red]")
        raise typer.Exit(1)

    plant = storage.get_plant_by_code(code)
    if not plant:
        console.print(f"[red]✗ 未找到编号 [{code}] 的植株[/red]")
        raise typer.Exit(1)

    real_taken = parse_date(taken_at) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    photo = Photo(
        inspection_id=insp_id,
        plant_code=code,
        file_path=os.path.abspath(file_path) if os.path.exists(file_path) else file_path,
        description=description,
        taken_at=real_taken,
    )
    pid = storage.add_photo(photo)
    console.print(Panel(
        f"[green]✓ 照片记录已添加 (ID: {pid})[/green]\n"
        f"  植株: {code} {plant.name}\n"
        f"  路径: {photo.file_path}\n"
        f"  描述: {description or '-'}\n"
        f"  拍摄时间: {real_taken}",
        title="照片添加成功", border_style="green"
    ))


# ============================================================
# task 命令
# ============================================================
@app.command("task", help="养护任务管理:生成、指派、批量更新、查询逾期")
def task_cmd(
    list_mode: bool = typer.Option(False, "--list", "-l", help="列出所有任务"),
    assignee: str = typer.Option("", "--assignee", "-u", help="筛选/指派责任人"),
    status: str = typer.Option("", "--status", "-s", help="按状态筛选(待处理/处理中/已完成/已逾期)"),
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    overdue: bool = typer.Option(False, "--overdue", "-o", help="仅显示逾期任务"),
    task_id: Optional[int] = typer.Option(None, "--id", help="指定任务ID进行操作"),
    assign: bool = typer.Option(False, "--assign", help="指派责任人(需配合 --id 和 --assignee)"),
    set_status: str = typer.Option("", "--set-status", help="更新任务状态(需配合 --id)"),
    batch_ids: str = typer.Option("", "--batch-ids", help="批量更新的任务ID列表(逗号分隔,如 1,2,3)"),
    batch_status: str = typer.Option("", "--batch-status", help="批量更新的目标状态"),
    create: bool = typer.Option(False, "--create", "-c", help="手动创建任务(交互)"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """养护任务管理"""
    storage = get_storage(db_path)
    storage.update_overdue_tasks()
    check_initialized(storage)

    # 创建任务
    if create:
        code = Prompt.ask("植株编号")
        plant = storage.get_plant_by_code(code)
        if not plant:
            console.print(f"[red]✗ 未找到编号 [{code}] 的植株[/red]")
            raise typer.Exit(1)

        ttype = Prompt.ask(
            "任务类型",
            choices=[TaskType.WATERING.value, TaskType.PRUNING.value, TaskType.REPLANT.value,
                     TaskType.PEST_CONTROL.value, TaskType.FERTILIZE.value],
            default=TaskType.WATERING.value,
        )
        desc = Prompt.ask("任务描述", default=f"【{plant.name}】{ttype}")
        user = Prompt.ask("责任人", default="")
        prio = Prompt.ask("优先级(高/中/低)", choices=["高", "中", "低"], default="中")
        due_days = IntPrompt.ask("要求完成天数", default=3)
        due_date = (datetime.now() + timedelta(days=due_days)).strftime("%Y-%m-%d")

        task = Task(
            plant_code=code, plant_name=plant.name, area=plant.area,
            task_type=ttype, description=desc, assignee=user,
            priority=prio, due_date=due_date,
        )
        tid = storage.add_task(task)
        console.print(f"[green]✓ 任务已创建 (ID: {tid}, 任务号: {task.task_no})[/green]")
        return

    # 指派
    if assign and task_id and assignee:
        if storage.assign_task(task_id, assignee):
            console.print(f"[green]✓ 任务 #{task_id} 已指派给 {assignee}[/green]")
        else:
            console.print(f"[red]✗ 任务 #{task_id} 不存在[/red]")
        return

    # 更新单个任务状态
    if task_id and set_status:
        valid = [TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value,
                 TaskStatus.COMPLETED.value, TaskStatus.OVERDUE.value]
        if set_status not in valid:
            console.print(f"[red]✗ 状态必须是: {'/'.join(valid)}[/red]")
            raise typer.Exit(1)
        if storage.update_task_status(task_id, set_status):
            console.print(f"[green]✓ 任务 #{task_id} 状态已更新为: {set_status}[/green]")
        else:
            console.print(f"[red]✗ 任务 #{task_id} 不存在[/red]")
        return

    # 批量更新状态
    if batch_ids and batch_status:
        ids = [int(x.strip()) for x in batch_ids.split(",") if x.strip().isdigit()]
        cnt = storage.batch_update_task_status(ids, batch_status)
        console.print(f"[green]✓ 已批量更新 {cnt}/{len(ids)} 条任务状态为: {batch_status}[/green]")
        return

    # 列出任务
    tasks = storage.get_tasks(status=status, assignee=assignee, area=area, overdue_only=overdue)
    title_parts = []
    if overdue:
        title_parts.append("逾期任务")
    if status:
        title_parts.append(f"状态:{status}")
    if assignee:
        title_parts.append(f"责任人:{assignee}")
    if area:
        title_parts.append(f"区域:{area}")
    title = f"养护任务列表 (共{len(tasks)}条) {' | '.join(title_parts)}"
    print_tasks_table(tasks, title=title)

    # 汇总统计
    status_counts = {}
    for t in tasks:
        status_counts[t.status] = status_counts.get(t.status, 0) + 1
    if status_counts:
        summary = "  ".join(
            f"[{('bold red' if k == '已逾期' else ('bold green' if k == '已完成' else 'bold yellow'))}]{k}: {v}[/{'bold red' if k == '已逾期' else ('bold green' if k == '已完成' else 'bold yellow')}]"
            for k, v in status_counts.items()
        )
        console.print(Panel(summary, title="统计", border_style="cyan"))


# ============================================================
# report 命令
# ============================================================
@app.command("report", help="汇总报告:按日期汇总、异常摘要、区域负责人视角、连续异常、交接清单")
def report_cmd(
    date_from: str = typer.Option("", "--from", "-f", help="开始日期 YYYY-MM-DD"),
    date_to: str = typer.Option("", "--to", "-t", help="结束日期 YYYY-MM-DD"),
    today: bool = typer.Option(False, "--today", help="仅查看今日"),
    week: bool = typer.Option(False, "--week", help="最近7天"),
    month: bool = typer.Option(False, "--month", help="最近30天"),
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    summary: bool = typer.Option(False, "--summary", "-s", help="生成异常摘要"),
    handover: bool = typer.Option(False, "--handover", "-h", help="打印交接班清单"),
    area_view: bool = typer.Option(False, "--area-view", help="区域负责人视角:按区域+责任人+任务状态汇总"),
    abnormal_view: bool = typer.Option(False, "--abnormal", help="连续多天异常植株和重复问题分析"),
    abnormal_days: int = typer.Option(7, "--abnormal-days", help="连续异常分析的时间窗口(天)"),
    min_occurrences: int = typer.Option(2, "--min-occur", help="最小异常出现次数才算连续"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """巡检报告汇总"""
    storage = get_storage(db_path)
    storage.update_overdue_tasks()
    check_initialized(storage)

    now = datetime.now()
    if today:
        date_from = now.strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif week:
        date_from = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif month:
        date_from = (now - timedelta(days=29)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    else:
        if not date_from:
            date_from = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        if not date_to:
            date_to = now.strftime("%Y-%m-%d")

    df = parse_date(date_from)
    dt = parse_date(date_to)
    if not df or not dt:
        console.print("[red]✗ 日期格式错误，请使用 YYYY-MM-DD[/red]")
        raise typer.Exit(1)

    insps = storage.get_inspections_by_area_and_date(area, df, dt)

    # 新增：区域负责人视角
    if area_view:
        _print_area_leader_view(storage, df, dt, area)
        return

    # 新增：连续异常植株 + 重复问题
    if abnormal_view:
        _print_abnormal_trend_view(storage, abnormal_days, min_occurrences, area)
        return

    if handover:
        _print_handover_report(storage, df, dt, area)
        return

    if summary:
        _print_anomaly_summary(storage, insps, df, dt, area)
        return

    # 默认：按日期汇总报告
    _print_daily_report(storage, insps, df, dt, area)


def _print_daily_report(storage: Storage, insps: List[Inspection], df: str, dt: str, area: str):
    console.print(Panel(
        f"[bold cyan]时间范围:[/bold cyan] {df} ~ {dt}\n"
        f"[bold cyan]区域筛选:[/bold cyan] {area or '全部'}",
        title="园区绿植巡检汇总报告", border_style="green", expand=False
    ))

    # 按日期分组
    by_date = {}
    by_area = {}
    total_score = 0
    abnormal_count = 0
    for insp in insps:
        d = insp.check_date[:10]
        by_date.setdefault(d, []).append(insp)
        by_area.setdefault(insp.area or "未分类", []).append(insp)
        total_score += insp.health_score
        if (insp.has_water_deficit or insp.has_withered or insp.has_pest
                or insp.needs_pruning or insp.needs_replant or insp.health_score < 70):
            abnormal_count += 1

    total = len(insps)
    avg_score = round(total_score / total, 1) if total else 0
    abnormal_rate = round(abnormal_count / total * 100, 1) if total else 0

    # 总体统计
    from rich.table import Table
    from rich import box
    t1 = Table(box=box.ROUNDED, show_header=True)
    t1.add_column("指标", style="cyan", no_wrap=True)
    t1.add_column("数值", justify="right", style="bold")
    t1.add_row("巡检次数", str(total))
    t1.add_row("覆盖植株", str(len(set(i.plant_code for i in insps))))
    t1.add_row("异常次数", f"[red]{abnormal_count}[/red]")
    t1.add_row("异常率", f"[red]{abnormal_rate}%[/red]")
    t1.add_row("平均评分", f"[{'green' if avg_score >= 80 else ('yellow' if avg_score >= 60 else 'red')}]{avg_score}[/{'green' if avg_score >= 80 else ('yellow' if avg_score >= 60 else 'red')}]")
    pending = len(storage.get_pending_check_plants(area))
    overdue = len(storage.get_overdue_plants(area))
    t1.add_row("待巡检植株", f"[yellow]{pending}[/yellow]")
    t1.add_row("逾期未巡检", f"[red]{overdue}[/red]")
    console.print(t1)

    # 按日期趋势
    if by_date:
        t2 = Table(title="巡检日期分布", box=box.ROUNDED)
        t2.add_column("日期", style="cyan")
        t2.add_column("巡检数", justify="right")
        t2.add_column("异常数", justify="right", style="red")
        t2.add_column("平均分", justify="right")
        for d in sorted(by_date.keys()):
            lst = by_date[d]
            ab = sum(1 for i in lst if (i.has_water_deficit or i.has_withered or i.has_pest
                    or i.needs_pruning or i.needs_replant or i.health_score < 70))
            avg = round(sum(i.health_score for i in lst) / len(lst), 1)
            t2.add_row(d, str(len(lst)), str(ab), str(avg))
        console.print(t2)

    # 异常明细
    abnormal_insps = [i for i in insps if (i.has_water_deficit or i.has_withered or i.has_pest
            or i.needs_pruning or i.needs_replant or i.health_score < 70)]
    if abnormal_insps:
        console.print(f"\n[bold]异常明细 (共{len(abnormal_insps)}条):[/bold]")
        print_inspections_table(abnormal_insps, title="")


def _print_anomaly_summary(storage: Storage, insps: List[Inspection], df: str, dt: str, area: str):
    anomaly_types = {
        "缺水(💧)": [i for i in insps if i.has_water_deficit],
        "枯黄(🍂)": [i for i in insps if i.has_withered],
        "虫害(🐛)": [i for i in insps if i.has_pest],
        "需修剪(✂️)": [i for i in insps if i.needs_pruning],
        "需补苗(🌱)": [i for i in insps if i.needs_replant],
        "低评分(<70)": [i for i in insps if i.health_score < 70],
    }

    console.print(Panel(
        f"时间范围: {df} ~ {dt}  |  区域: {area or '全部'}",
        title="异常情况摘要", border_style="red", expand=False
    ))

    from rich.table import Table
    from rich import box

    t = Table(box=box.ROUNDED)
    t.add_column("异常类型", style="bold red", no_wrap=True)
    t.add_column("发生次数", justify="right", style="yellow")
    t.add_column("涉及植株", justify="right")
    t.add_column("典型植株", style="cyan")

    total_anomaly = 0
    for name, lst in anomaly_types.items():
        if lst:
            codes = sorted(set(i.plant_code for i in lst))
            total_anomaly += len(lst)
            sample = "、".join(codes[:3]) + ("..." if len(codes) > 3 else "")
            t.add_row(name, str(len(lst)), str(len(codes)), sample)
    console.print(t)

    # 待处理任务
    pending_tasks = storage.get_tasks(status=TaskStatus.PENDING.value, area=area)
    in_progress = storage.get_tasks(status=TaskStatus.IN_PROGRESS.value, area=area)
    overdue_tasks = storage.get_tasks(overdue_only=True, area=area)

    console.print(Panel(
        f"待处理任务: [yellow]{len(pending_tasks)}[/yellow]  |  "
        f"处理中: [blue]{len(in_progress)}[/blue]  |  "
        f"已逾期: [red]{len(overdue_tasks)}[/red]",
        title="当前任务积压", border_style="yellow"
    ))

    if overdue_tasks:
        print_tasks_table(overdue_tasks, title=f"⚠️ 逾期任务 ({len(overdue_tasks)}条，需立即处理)")


def _print_handover_report(storage: Storage, df: str, dt: str, area: str):
    console.print("\n" + "=" * 70)
    console.print(" " * 20 + "[bold]园区绿植养护交接班清单[/bold]")
    console.print("=" * 70)
    console.print(f"交班日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"巡检周期: {df} ~ {dt}  |  区域: {area or '全部'}")
    console.print("-" * 70)

    insps = storage.get_inspections_by_area_and_date(area, df, dt)
    console.print(f"\n[bold]【一、本期巡检完成情况】[/bold]")
    total = len(insps)
    done_codes = set(i.plant_code for i in insps)
    console.print(f"  完成巡检次数: {total}")
    console.print(f"  覆盖植株数: {len(done_codes)}")

    pending = storage.get_pending_check_plants(area)
    overdue = storage.get_overdue_plants(area)
    console.print(f"  仍待巡检: [yellow]{len(pending)}[/yellow] 株")
    if overdue:
        console.print(f"  [red]逾期未巡检: {len(overdue)} 株 (重点处理！)[/red]")
        for p in overdue[:10]:
            console.print(f"    - {p.code} {p.name} ({p.area}) 应检: {p.next_check_date}")

    console.print(f"\n[bold]【二、本期异常发现汇总】[/bold]")
    water = [i for i in insps if i.has_water_deficit]
    withered = [i for i in insps if i.has_withered]
    pest = [i for i in insps if i.has_pest]
    prune = [i for i in insps if i.needs_pruning]
    replant = [i for i in insps if i.needs_replant]
    console.print(f"  缺水: {len(water)}  |  枯黄: {len(withered)}  |  虫害: {len(pest)}")
    console.print(f"  需修剪: {len(prune)}  |  需补苗: {len(replant)}")

    console.print(f"\n[bold]【三、待办养护任务】[/bold]")
    all_pending = storage.get_tasks(area=area)
    all_pending = [t for t in all_pending if t.status in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value, TaskStatus.OVERDUE.value)]
    by_assignee = {}
    for t in all_pending:
        key = t.assignee or "[未指派]"
        by_assignee.setdefault(key, []).append(t)

    for user, tasks in sorted(by_assignee.items()):
        console.print(f"\n  ▶ 责任人: [cyan]{user}[/cyan] (共 {len(tasks)} 项)")
        for t in tasks:
            mark = "[red]⚠逾期[/red]" if t.status == TaskStatus.OVERDUE.value else ""
            console.print(f"    {t.task_no}  {t.task_type:<4}  {t.plant_code} {t.plant_name:<10}  "
                          f"{t.area}  截止:{t.due_date or '-'}  {t.status} {mark}")

    console.print(f"\n[bold]【四、交接签名】[/bold]")
    console.print(f"\n  交班人签字: _______________    接班人签字: _______________")
    console.print(f"  交接时间:   _______________")
    console.print("\n" + "=" * 70 + "\n")


# ============================================================
# 区域负责人视角
# ============================================================
def _print_area_leader_view(storage: Storage, df: str, dt: str, area_filter: str):
    """按区域、责任人、任务状态汇总展示"""
    from collections import defaultdict
    from rich.table import Table as T
    from rich import box

    console.print(Panel(
        f"[bold cyan]时间范围:[/bold cyan] {df} ~ {dt}\n"
        f"[bold cyan]区域:[/bold cyan] {area_filter or '全部'}",
        title="区域负责人视角汇总", border_style="green", expand=False
    ))

    # 1. 按区域 x 责任人 x 状态 汇总任务
    tasks = storage.get_tasks()
    if area_filter:
        tasks = [t for t in tasks if t.area == area_filter]

    # 过滤日期范围内创建的任务（或者所有未完成的+已完成）
    # 这里取全部任务展示
    matrix = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for t in tasks:
        area = t.area or "未分区域"
        assignee = t.assignee or "未指派"
        matrix[area][assignee][t.status] += 1
        matrix[area][assignee]["合计"] += 1

    if matrix:
        t1 = T(title="任务矩阵: 区域 × 责任人 × 状态", box=box.ROUNDED)
        t1.add_column("区域", style="cyan", no_wrap=True)
        t1.add_column("责任人", style="magenta")
        t1.add_column("待处理", justify="right", style="yellow")
        t1.add_column("处理中", justify="right", style="blue")
        t1.add_column("已逾期", justify="right", style="bold red")
        t1.add_column("已完成", justify="right", style="green")
        t1.add_column("总计", justify="right", style="bold")

        for area in sorted(matrix.keys()):
            for assignee in sorted(matrix[area].keys()):
                d = matrix[area][assignee]
                t1.add_row(
                    area, assignee,
                    str(d.get("待处理", 0)),
                    str(d.get("处理中", 0)),
                    f"[bold red]{d.get('已逾期', 0)}[/bold red]",
                    str(d.get("已完成", 0)),
                    f"[bold]{d.get('合计', 0)}[/bold]"
                )
                area = ""  # 合并区域列显示
        console.print(t1)

    # 2. 责任人绩效概览（按责任人汇总）
    perf = defaultdict(lambda: {"总任务": 0, "已完成": 0, "待处理": 0, "逾期": 0})
    for t in tasks:
        a = t.assignee or "未指派"
        perf[a]["总任务"] += 1
        if t.status == TaskStatus.COMPLETED.value:
            perf[a]["已完成"] += 1
        elif t.status == TaskStatus.OVERDUE.value:
            perf[a]["逾期"] += 1
        else:
            perf[a]["待处理"] += 1

    if perf:
        t2 = T(title="责任人绩效(全部任务)", box=box.ROUNDED)
        t2.add_column("责任人", style="green")
        t2.add_column("总任务", justify="right")
        t2.add_column("已完成", justify="right", style="green")
        t2.add_column("待处理", justify="right", style="yellow")
        t2.add_column("已逾期", justify="right", style="bold red")
        t2.add_column("完成率", justify="right", style="bold")
        for a in sorted(perf.keys(), key=lambda x: -perf[x]["总任务"]):
            p = perf[a]
            rate = round(p["已完成"] / p["总任务"] * 100, 1) if p["总任务"] else 0
            rate_style = "green" if rate >= 80 else ("yellow" if rate >= 50 else "red")
            t2.add_row(a, str(p["总任务"]), str(p["已完成"]), str(p["待处理"]),
                       f"[bold red]{p['逾期']}[/bold red]",
                       f"[{rate_style}]{rate}%[/{rate_style}]")
        console.print(t2)

    # 3. 各区域健康度（巡检平均评分 + 植株状态分布）
    inspections = storage.get_inspections_by_area_and_date(area_filter or "", df, dt)
    by_area = defaultdict(list)
    for i in inspections:
        by_area[i.area or "未分区域"].append(i)

    all_plants = storage.search_plants(area=area_filter)
    status_by_area = defaultdict(lambda: defaultdict(int))
    for p in all_plants:
        status_by_area[p.area or "未分区域"][p.status] += 1

    t3 = T(title="区域健康度一览", box=box.ROUNDED)
    t3.add_column("区域", style="cyan")
    t3.add_column("巡检次数", justify="right")
    t3.add_column("覆盖植株", justify="right")
    t3.add_column("平均评分", justify="right")
    t3.add_column("健康株", justify="right", style="green")
    t3.add_column("异常株", justify="right", style="red")
    t3.add_column("逾期株", justify="right", style="bold red")

    areas = sorted(set(list(by_area.keys()) + list(status_by_area.keys())))
    for ar in areas:
        lst = by_area.get(ar, [])
        score = round(sum(i.health_score for i in lst) / len(lst), 1) if lst else "-"
        score_style = ""
        if isinstance(score, float):
            score_style = "green" if score >= 80 else ("yellow" if score >= 60 else "red")
            score = f"[{score_style}]{score}[/{score_style}]"
        statuses = status_by_area.get(ar, {})
        healthy = statuses.get("健康", 0)
        total_area = sum(statuses.values())
        abnormal = total_area - healthy
        overdue_ar = len(storage.get_overdue_plants(ar))
        t3.add_row(ar, str(len(lst)), str(len(set(i.plant_code for i in lst))),
                   str(score), str(healthy), f"[red]{abnormal}[/red]",
                   f"[bold red]{overdue_ar}[/bold red]")
    console.print(t3)


# ============================================================
# 连续异常 & 重复问题分析视角
# ============================================================
def _print_abnormal_trend_view(storage: Storage, days: int, min_occur: int, area_filter: str):
    """展示连续多日异常植株和重复问题"""
    from collections import defaultdict
    from rich.table import Table as T
    from rich import box

    console.print(Panel(
        f"分析窗口: 最近 [bold yellow]{days}[/bold yellow] 天\n"
        f"判定阈值: 异常次数 ≥ [bold yellow]{min_occur}[/bold yellow] 次\n"
        f"区域筛选: {area_filter or '全部'}",
        title="连续异常植株 & 重复问题分析", border_style="red", expand=False
    ))

    # 1. 连续异常植株
    consec = storage.get_consecutive_abnormal_plants(days=days, min_occurrences=min_occur)
    if area_filter:
        consec = [c for c in consec if c["area"] == area_filter]

    if consec:
        t1 = T(title=f"反复出现异常的植株 ({len(consec)}株，建议重点养护)", box=box.ROUNDED)
        t1.add_column("编号", style="cyan", no_wrap=True)
        t1.add_column("名称", style="green")
        t1.add_column("区域", style="blue")
        t1.add_column("异常次数", justify="right", style="bold red")
        t1.add_column("首次异常", style="yellow")
        t1.add_column("最近异常", style="yellow")
        t1.add_column("平均评分", justify="right")
        t1.add_column("主要问题", style="magenta")
        t1.add_column("问题明细", style="white")
        for c in consec:
            sc_style = "red" if c["avg_score"] < 60 else ("yellow" if c["avg_score"] < 80 else "white")
            t1.add_row(
                c["plant_code"], c["plant_name"] or "-", c["area"] or "-",
                f"[bold red]{c['abnormal_days']}[/bold red]",
                c["first_date"], c["last_date"],
                f"[{sc_style}]{c['avg_score']}[/{sc_style}]",
                f"[bold]{c['top_issue']}[/bold]",
                c["issue_detail"] or "-"
            )
        console.print(t1)
    else:
        console.print(Panel("[green]✓ 未发现反复异常的植株，养护状况良好[/green]",
                              title="连续异常排查", border_style="green"))

    # 2. 重复问题统计（按区域+责任人+任务类型）
    repeat = storage.get_repeat_issue_summary(days=days)
    if area_filter:
        repeat = [r for r in repeat if r["area"] == area_filter]

    if repeat:
        t2 = T(title=f"重复问题排行 TOP（按任务数）", box=box.ROUNDED)
        t2.add_column("排名", justify="right", style="cyan")
        t2.add_column("区域", style="blue")
        t2.add_column("责任人", style="green")
        t2.add_column("问题类型", style="magenta")
        t2.add_column("总次数", justify="right")
        t2.add_column("已完成", justify="right", style="green")
        t2.add_column("待处理", justify="right", style="yellow")
        t2.add_column("已逾期", justify="right", style="bold red")
        t2.add_column("完成率", justify="right")
        for idx, r in enumerate(repeat[:15], 1):
            rate_style = "green" if r["done_rate"] >= 80 else ("yellow" if r["done_rate"] >= 50 else "red")
            t2.add_row(
                str(idx), r["area"], r["assignee"], r["task_type"],
                str(r["total"]), str(r["done"]), str(r["pending"]),
                f"[bold red]{r['overdue']}[/bold red]",
                f"[{rate_style}]{r['done_rate']}%[/{rate_style}]"
            )
        console.print(t2)

    # 3. 根因分析建议
    if consec or repeat:
        suggestions = []
        if any(c["top_issue"] == "缺水" for c in consec):
            suggestions.append("💧 部分植株反复缺水，建议优化浇水排班或增加自动灌溉装置")
        if any(c["top_issue"] == "虫害" for c in consec):
            suggestions.append("🐛 反复出现虫害，建议开展区域统一消杀，清理病枝病叶")
        if any(c["top_issue"] == "枯黄" for c in consec):
            suggestions.append("🍂 多次枯黄，建议检查土壤肥力、排水和光照条件")
        if any(c["top_issue"] == "需修剪" for c in consec):
            suggestions.append("✂️ 修剪需求频繁，建议缩短该区域修剪周期")
        low_perf = [r for r in repeat if r["done_rate"] < 60 and r["overdue"] > 0]
        if low_perf:
            names = ", ".join(set(r["assignee"] for r in low_perf))
            suggestions.append(f"👥 以下责任人任务积压较多: {names}，建议增派支援或重新分配")
        if suggestions:
            console.print(Panel("\n".join(f"  • {s}" for s in suggestions),
                                  title="📋 养护优化建议", border_style="yellow"))


# ============================================================
# export 命令
# ============================================================
@app.command("export", help="导出巡检表(Excel/CSV)、任务清单、植株档案、汇总分析")
def export_cmd(
    output: str = typer.Option("巡检导出.xlsx", "--output", "-o", help="输出文件路径(.xlsx/.csv)"),
    date_from: str = typer.Option("", "--from", "-f", help="开始日期 YYYY-MM-DD"),
    date_to: str = typer.Option("", "--to", "-t", help="结束日期 YYYY-MM-DD"),
    today: bool = typer.Option(False, "--today", help="仅导出今日数据"),
    week: bool = typer.Option(False, "--week", help="最近7天"),
    data_type: str = typer.Option(
        "inspections", "--type",
        help="导出类型: inspections/tasks/plants/all(含汇总)"
    ),
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    status: str = typer.Option("", "--status", "-s", help="按状态筛选"),
    with_summary: bool = typer.Option(True, "--summary/--no-summary", help="是否包含汇总Sheet(仅.xlsx)"),
    abnormal_days: int = typer.Option(14, "--abnormal-days", help="连续异常分析的时间窗口(天)"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """导出数据到 Excel 或 CSV"""
    storage = get_storage(db_path)
    check_initialized(storage)

    import pandas as pd

    now = datetime.now()
    if today:
        date_from = now.strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif week:
        date_from = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    else:
        if not date_from:
            date_from = "2000-01-01"
        if not date_to:
            date_to = now.strftime("%Y-%m-%d")

    ext = os.path.splitext(output)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        output += ".xlsx"
        ext = ".xlsx"

    sheets = {}

    if data_type in ("inspections", "all"):
        insps = storage.get_inspections_by_area_and_date(area, date_from, date_to)
        rows = []
        for i in insps:
            if area and i.area != area:
                continue
            rows.append({
                "巡检时间": i.check_date,
                "植株编号": i.plant_code,
                "植株名称": i.plant_name,
                "区域": i.area,
                "巡检人": i.inspector,
                "长势评分": i.health_score,
                "缺水": "是" if i.has_water_deficit else "否",
                "枯黄": "是" if i.has_withered else "否",
                "虫害": "是" if i.has_pest else "否",
                "需修剪": "是" if i.needs_pruning else "否",
                "需补苗": "是" if i.needs_replant else "否",
                "备注": i.notes,
            })
        sheets["巡检记录"] = pd.DataFrame(rows)

    if data_type in ("tasks", "all"):
        tasks = storage.get_tasks(status=status, area=area)
        rows = []
        for t in tasks:
            rows.append({
                "任务号": t.task_no,
                "任务类型": t.task_type,
                "植株编号": t.plant_code,
                "植株名称": t.plant_name,
                "区域": t.area,
                "任务描述": t.description,
                "责任人": t.assignee,
                "优先级": t.priority,
                "截止日期": t.due_date,
                "状态": t.status,
                "完成时间": t.completed_at,
                "创建时间": t.created_at,
            })
        sheets["养护任务"] = pd.DataFrame(rows)

    if data_type in ("plants", "all"):
        plants = storage.search_plants(area=area, status=status)
        rows = []
        for p in plants:
            rows.append({
                "编号": p.code,
                "名称": p.name,
                "品种": p.species,
                "区域": p.area,
                "位置": p.location,
                "种植日期": p.planted_date,
                "上次巡检": p.last_check_date,
                "下次巡检": p.next_check_date,
                "巡检周期(天)": p.check_cycle_days,
                "状态": p.status,
                "备注": p.notes,
            })
        sheets["植株档案"] = pd.DataFrame(rows)

    # 汇总分析Sheet (仅 xlsx, data_type=all 或 with_summary=True)
    if with_summary and ext != ".csv":
        try:
            # Sheet1: 任务矩阵 区域x责任人x状态
            from collections import defaultdict
            tasks = storage.get_tasks()
            if area:
                tasks = [t for t in tasks if t.area == area]
            matrix = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
            for t in tasks:
                ar = t.area or "未分区域"
                u = t.assignee or "未指派"
                matrix[ar][u][t.status] += 1
                matrix[ar][u]["合计"] += 1

            m_rows = []
            for ar in sorted(matrix.keys()):
                for u in sorted(matrix[ar].keys()):
                    d = matrix[ar][u]
                    rate = round(d.get("已完成", 0) / d.get("合计", 1) * 100, 1)
                    m_rows.append({
                        "区域": ar, "责任人": u,
                        "待处理": d.get("待处理", 0),
                        "处理中": d.get("处理中", 0),
                        "已逾期": d.get("已逾期", 0),
                        "已完成": d.get("已完成", 0),
                        "合计": d.get("合计", 0),
                        "完成率%": rate,
                    })
            if m_rows:
                sheets["区域责任人任务汇总"] = pd.DataFrame(m_rows)

            # Sheet2: 连续异常植株
            consec = storage.get_consecutive_abnormal_plants(days=abnormal_days, min_occurrences=2)
            if area:
                consec = [c for c in consec if c["area"] == area]
            if consec:
                sheets["连续异常植株"] = pd.DataFrame(consec)

            # Sheet3: 重复问题类型排行
            repeat = storage.get_repeat_issue_summary(days=abnormal_days)
            if area:
                repeat = [r for r in repeat if r["area"] == area]
            if repeat:
                sheets["重复问题排行"] = pd.DataFrame(repeat)

            # Sheet4: 各区域健康度
            insps2 = storage.get_inspections_by_area_and_date(area or "", date_from, date_to)
            by_area2 = defaultdict(list)
            for i in insps2:
                by_area2[i.area or "未分区域"].append(i)
            all_p = storage.search_plants(area=area)
            st_area = defaultdict(lambda: defaultdict(int))
            for p in all_p:
                st_area[p.area or "未分区域"][p.status] += 1
            health_rows = []
            for ar in sorted(set(list(by_area2.keys()) + list(st_area.keys()))):
                lst = by_area2.get(ar, [])
                avg = round(sum(i.health_score for i in lst) / len(lst), 1) if lst else None
                sts = st_area.get(ar, {})
                total_ar = sum(sts.values())
                ov = len(storage.get_overdue_plants(ar))
                health_rows.append({
                    "区域": ar,
                    "巡检次数": len(lst),
                    "覆盖植株": len(set(i.plant_code for i in lst)),
                    "平均评分": avg,
                    "健康株": sts.get("健康", 0),
                    "异常株": total_ar - sts.get("健康", 0),
                    "逾期未检": ov,
                    "植株总数": total_ar,
                })
            if health_rows:
                sheets["区域健康度汇总"] = pd.DataFrame(health_rows)

            # Sheet5: 交接班记录摘要
            handovers = storage.get_handovers(limit=100)
            if handovers:
                ho_rows = []
                for h in handovers:
                    pending_cnt = len([x for x in h.pending_task_ids.split(",") if x.strip()]) if h.pending_task_ids else 0
                    abn_cnt = len([x for x in h.abnormal_plant_codes.split(",") if x.strip()]) if h.abnormal_plant_codes else 0
                    ho_rows.append({
                        "交接单ID": h.id, "班次号": h.shift_no,
                        "交班人": h.handover_from, "接班人": h.handover_to,
                        "交接时间": h.handover_time,
                        "遗留任务数": pending_cnt,
                        "异常植株数": abn_cnt,
                        "未完成原因": h.unfinished_reason,
                        "备注": h.remarks,
                        "是否已确认": "是" if h.confirmed else "否",
                        "确认时间": h.confirmed_at,
                    })
                sheets["交接班汇总"] = pd.DataFrame(ho_rows)

        except Exception as ex:
            console.print(f"[yellow]⚠ 汇总Sheet生成跳过: {ex}[/yellow]")

    if not sheets:
        console.print("[red]✗ 没有可导出的数据[/red]")
        raise typer.Exit(1)

    try:
        if ext == ".csv":
            first_key = next(iter(sheets))
            sheets[first_key].to_csv(output, index=False, encoding="utf-8-sig")
        else:
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                for name, df in sheets.items():
                    df.to_excel(writer, sheet_name=name, index=False)

        total_rows = sum(len(df) for df in sheets.values())
        console.print(Panel(
            f"[bold green]✓ 导出成功[/bold green]\n"
            f"  文件路径: {os.path.abspath(output)}\n"
            f"  工作表:   {', '.join(sheets.keys())}\n"
            f"  总记录数: {total_rows}",
            title="导出完成", border_style="green"
        ))
    except PermissionError:
        console.print(f"[red]✗ 无法写入文件，请确认 {output} 未被其他程序占用[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ 导出失败: {e}[/red]")
        raise typer.Exit(1)


# ============================================================
# shift 命令
# ============================================================
@app.command("shift", help="交接班工作流:开/关班次、交班、接班确认、查班次遗留待办")
def shift_cmd(
    start: bool = typer.Option(False, "--start", help="开启新班次"),
    end: bool = typer.Option(False, "--end", help="结束当前班次"),
    name: str = typer.Option("", "--name", help="班次名称(如白班/夜班/早班)"),
    leader: str = typer.Option("", "--leader", help="班次负责人/队长"),
    members: str = typer.Option("", "--members", help="班组成员(逗号分隔)"),
    handover: bool = typer.Option(False, "--handover", "-h", help="执行交班:生成交接单(需要先结束当前班次)"),
    handover_to: str = typer.Option("", "--to", help="接班人姓名"),
    handover_from: str = typer.Option("", "--from", help="交班人(默认取班次负责人)"),
    reason: str = typer.Option("", "--reason", help="未完成任务原因说明"),
    remarks: str = typer.Option("", "--remarks", help="交接备注"),
    confirm: int = typer.Option(0, "--confirm", help="确认接班的交接单ID"),
    confirmer: str = typer.Option("", "--confirmer", help="确认接班人姓名"),
    list_shifts: bool = typer.Option(False, "--list", "-l", help="列出班次历史"),
    list_handovers: bool = typer.Option(False, "--list-handovers", help="列出交接班记录"),
    detail: int = typer.Option(0, "--detail", help="查看某班次的交接详情(班次ID)"),
    pending: bool = typer.Option(False, "--pending", help="查看所有班次遗留的待办任务和异常植株"),
    limit: int = typer.Option(20, "--limit", help="列表显示条数"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """交接班工作流管理"""
    storage = get_storage(db_path)
    storage.update_overdue_tasks()
    check_initialized(storage)

    # 1. 开启班次
    if start:
        cur = storage.get_current_shift()
        if cur:
            console.print(f"[yellow]⚠ 当前已有进行中的班次 {cur.shift_no} ({cur.name})，请先交班或结束[/yellow]")
            return

        if not name:
            name = Prompt.ask("班次名称(如:白班/夜班/中班)", default=f"班次{datetime.now().strftime('%m%d')}")
        if not leader:
            leader = Prompt.ask("班次负责人/队长姓名", default="")
        if not members:
            members = Prompt.ask("班组成员(逗号分隔,可留空)", default="")

        s = Shift(
            name=name, leader=leader, members=members,
            start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status="进行中",
        )
        sid = storage.create_shift(s)
        s.id = sid
        console.print(Panel(
            f"[bold green]✓ 班次已开启[/bold green] (ID: {sid}, 班次号: {s.shift_no})\n\n"
            f"  班次名称: {name}\n"
            f"  负责人:   {leader or '-'}\n"
            f"  班组成员: {members or '-'}\n"
            f"  开始时间: {s.start_time}",
            title="开班次成功", border_style="green"
        ))
        return

    # 2. 结束班次
    if end:
        cur = storage.get_current_shift()
        if not cur:
            console.print("[yellow]⚠ 当前没有进行中的班次[/yellow]")
            return
        if storage.close_shift(cur.id):
            console.print(f"[green]✓ 班次 {cur.shift_no} 已结束[/green]")
        else:
            console.print(f"[red]✗ 结束班次失败[/red]")
        return

    # 3. 交班 (--handover)
    if handover:
        cur = storage.get_current_shift()
        if cur:
            if not Confirm.ask(f"当前班次 {cur.shift_no} ({cur.name}) 仍在进行，是否先结束再交接？", default=True):
                console.print("[yellow]已取消[/yellow]")
                return
            storage.close_shift(cur.id)

        shifts = storage.get_shifts(status="已结束", limit=1)
        if not shifts:
            console.print("[red]✗ 没有可交接的已结束班次[/red]")
            raise typer.Exit(1)
        shift = shifts[0]

        from_ = handover_from or shift.leader
        if not from_:
            from_ = Prompt.ask("交班人姓名", default="交班人")
        to_ = handover_to
        if not to_:
            to_ = Prompt.ask("接班人姓名", default="接班人")

        # 收集本班次进行中/待处理任务 & 非健康植株
        pending_tasks = storage.get_tasks()
        pending_tasks = [t for t in pending_tasks if t.status in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value, TaskStatus.OVERDUE.value)]
        abnormal_plants = storage.search_plants()
        abnormal_plants = [p for p in abnormal_plants if p.status != PlantStatus.HEALTHY.value]

        task_ids = ",".join(str(t.id) for t in pending_tasks if t.id)
        plant_codes = ",".join(p.code for p in abnormal_plants)

        reason_text = reason
        if not reason_text and pending_tasks:
            reason_text = Prompt.ask("未完成任务原因说明(可留空)", default="")
        remarks_text = remarks
        if not remarks_text:
            remarks_text = Prompt.ask("其他交接备注(可留空)", default="")

        ho = ShiftHandover(
            shift_id=shift.id, shift_no=shift.shift_no,
            handover_from=from_, handover_to=to_,
            handover_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pending_task_ids=task_ids,
            abnormal_plant_codes=plant_codes,
            unfinished_reason=reason_text,
            remarks=remarks_text,
            confirmed=0,
        )
        hid = storage.create_handover(ho)
        ho.id = hid

        # 打印交接清单
        _print_shift_handover_form(shift, ho, pending_tasks, abnormal_plants)
        console.print(f"\n[bold green]✓ 交接单已生成 (ID: {hid})[/bold green]，请通知接班人执行 [cyan]python main.py shift --confirm {hid}[/cyan] 确认接班")
        return

    # 4. 确认接班
    if confirm > 0:
        ho = storage.get_handover_by_id(confirm)
        if not ho:
            console.print(f"[red]✗ 交接单 #{confirm} 不存在[/red]")
            raise typer.Exit(1)
        if ho.confirmed:
            console.print(f"[yellow]⚠ 交接单 #{confirm} 已于 {ho.confirmed_at} 确认过[/yellow]")
            return
        c = confirmer
        if not c:
            c = Prompt.ask("请输入接班人姓名确认", default=ho.handover_to or "")
        if storage.confirm_handover(confirm, c):
            console.print(f"[green]✓ 交接单 #{confirm} 已确认接班，接班人: {c}[/green]")
        else:
            console.print(f"[red]✗ 确认失败[/red]")
        return

    # 5. 班次详情
    if detail > 0:
        shift = storage.get_shift_by_id(detail)
        if not shift:
            console.print(f"[red]✗ 班次 #{detail} 不存在[/red]")
            raise typer.Exit(1)
        print_shifts_table([shift], title="班次详情")

        handovers = storage.get_handovers(shift_id=detail)
        if handovers:
            ho = handovers[0]
            print_handovers_table(handovers, title="")
            pending_tasks = storage.get_shift_pending_tasks(detail)
            abnormal_plants = storage.get_shift_abnormal_plants(detail)

            if pending_tasks:
                print_tasks_table(pending_tasks, title=f"该班次遗留待办任务 ({len(pending_tasks)}条)")
            if abnormal_plants:
                print_plants_table(abnormal_plants, title=f"该班次遗留异常植株 ({len(abnormal_plants)}株)")
            if ho.unfinished_reason:
                console.print(Panel(f"未完成原因: {ho.unfinished_reason}", title="未完成说明", border_style="yellow"))
            if ho.remarks:
                console.print(Panel(f"备注: {ho.remarks}", title="交接备注", border_style="blue"))
        else:
            console.print("[yellow]该班次暂无交接记录[/yellow]")
        return

    # 6. 查所有班次遗留待办
    if pending:
        all_handovers = storage.get_handovers(limit=limit)
        if not all_handovers:
            console.print("[yellow]暂无交接记录[/yellow]")
            return
        console.print(Panel(f"共 {len(all_handovers)} 条历史交接，以下展示遗留未完成的任务与异常植株:",
                              title="各交接班次遗留事项", border_style="cyan"))
        for ho in all_handovers:
            pending_tasks = storage.get_shift_pending_tasks(ho.shift_id)
            # 过滤掉已完成的
            pending_tasks = [t for t in pending_tasks if t.status != TaskStatus.COMPLETED.value]
            abnormal = storage.get_shift_abnormal_plants(ho.shift_id)
            abnormal = [p for p in abnormal if p.status != PlantStatus.HEALTHY.value]
            if not pending_tasks and not abnormal:
                continue
            shift = storage.get_shift_by_id(ho.shift_id)
            badge = "[bold green]✓已确认[/bold green]" if ho.confirmed else f"[bold yellow]⏳待确认[/bold yellow]"
            console.print(f"\n  [{('green' if ho.confirmed else 'yellow')}]====[/] [cyan]{shift.shift_no if shift else ho.shift_no}[/cyan] "
                          f"{shift.name if shift else ''} ({ho.handover_from}→{ho.handover_to}) [{badge}] {ho.handover_time}")
            if pending_tasks:
                print_tasks_table(pending_tasks, title=f"  遗留待办 ({len(pending_tasks)}条)")
            if abnormal:
                print_plants_table(abnormal, title=f"  异常植株 ({len(abnormal)}株)")
        return

    # 7. 列出交接记录
    if list_handovers:
        hos = storage.get_handovers(limit=limit)
        print_handovers_table(hos, title=f"交接班记录 (最近{len(hos)}条)")
        return

    # 8. 默认: 列出班次 + 当前状态
    if list_shifts:
        shifts = storage.get_shifts(limit=limit)
        print_shifts_table(shifts, title=f"班次历史 (最近{len(shifts)}条)")
        return

    # 默认行为: 显示当前班次概况
    cur = storage.get_current_shift()
    if cur:
        print_shifts_table([cur], title="当前进行中的班次")
    else:
        console.print(Panel(
            f"[yellow]当前无进行中的班次[/yellow]\n"
            f"使用 [cyan]shift --start[/cyan] 开启新班次\n"
            f"使用 [cyan]shift --list[/cyan] 查看班次历史",
            title="班次概况", border_style="yellow"
        ))
    unconfirmed = storage.get_handovers(unconfirmed_only=True, limit=limit)
    if unconfirmed:
        print_handovers_table(unconfirmed, title=f"⚠ 待确认交接单 ({len(unconfirmed)}条)")


def _print_shift_handover_form(shift: Shift, ho: ShiftHandover, tasks: List[Task], plants: List[Plant]):
    sep = "=" * 70
    console.print(f"\n{sep}")
    console.print(" " * 25 + "[bold]园区绿植养护 交接班单[/bold]")
    console.print(sep)
    console.print(f"班次号: {ho.shift_no}  |  班次: {shift.name}")
    console.print(f"交接时间: {ho.handover_time}")
    console.print(f"交班人: {ho.handover_from}  →  接班人: {ho.handover_to}")
    console.print(f"负责人: {shift.leader}  |  班组成员: {shift.members or '-'}")
    console.print(f"班次时段: {shift.start_time} ~ {shift.end_time or '-'}")
    console.print("-" * 70)

    console.print(f"\n[bold]【一、待办养护任务】共 {len(tasks)} 项[/bold]")
    if tasks:
        from rich.table import Table as T
        from rich import box
        t = T(box=box.SIMPLE)
        t.add_column("ID", style="cyan")
        t.add_column("类型")
        t.add_column("编号")
        t.add_column("名称")
        t.add_column("区域")
        t.add_column("责任人")
        t.add_column("优先级")
        t.add_column("截止")
        t.add_column("状态", style="bold")
        for tk in tasks:
            t.add_row(str(tk.id), tk.task_type, tk.plant_code, tk.plant_name,
                      tk.area or "-", tk.assignee or "-", tk.priority,
                      tk.due_date or "-", tk.status)
        console.print(t)
    else:
        console.print("  (无)")

    console.print(f"\n[bold]【二、异常植株】共 {len(plants)} 株[/bold]")
    if plants:
        print_plants_table(plants, title="")
    else:
        console.print("  (无)")

    console.print(f"\n[bold]【三、未完成原因】[/bold]")
    console.print(f"  {ho.unfinished_reason or '无'}")

    console.print(f"\n[bold]【四、交接备注】[/bold]")
    console.print(f"  {ho.remarks or '无'}")

    console.print(f"\n[bold]【五、交接签名】[/bold]")
    console.print(f"\n  交班人签字: _______________    接班人签字: _______________")
    console.print(f"  交接确认:   [ {'✓已确认' if ho.confirmed else '  待确认  '} ]")
    console.print(sep + "\n")


# ============================================================
# 入口
# ============================================================
def main():
    try:
        app()
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        console.print("\n[yellow]操作已取消[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
