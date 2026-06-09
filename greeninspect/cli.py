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
from .models import Plant, Inspection, Photo, Task, PlantStatus, TaskType, TaskStatus
from .utils import (
    console, import_plants_from_csv, print_plants_table, print_inspections_table,
    print_tasks_table, print_photos_table, determine_plant_status,
    generate_tasks_from_inspection, parse_date, days_between,
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
@app.command("report", help="汇总报告:按日期汇总、异常摘要、打印交接清单")
def report_cmd(
    date_from: str = typer.Option("", "--from", "-f", help="开始日期 YYYY-MM-DD"),
    date_to: str = typer.Option("", "--to", "-t", help="结束日期 YYYY-MM-DD"),
    today: bool = typer.Option(False, "--today", help="仅查看今日"),
    week: bool = typer.Option(False, "--week", help="最近7天"),
    month: bool = typer.Option(False, "--month", help="最近30天"),
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    summary: bool = typer.Option(False, "--summary", "-s", help="生成异常摘要"),
    handover: bool = typer.Option(False, "--handover", "-h", help="打印交接班清单"),
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
# export 命令
# ============================================================
@app.command("export", help="导出巡检表(Excel/CSV)、任务清单、植株档案")
def export_cmd(
    output: str = typer.Option("巡检导出.xlsx", "--output", "-o", help="输出文件路径(.xlsx/.csv)"),
    date_from: str = typer.Option("", "--from", "-f", help="开始日期 YYYY-MM-DD"),
    date_to: str = typer.Option("", "--to", "-t", help="结束日期 YYYY-MM-DD"),
    today: bool = typer.Option(False, "--today", help="仅导出今日数据"),
    week: bool = typer.Option(False, "--week", help="最近7天"),
    data_type: str = typer.Option(
        "inspections", "--type",
        help="导出类型: inspections(巡检记录)/tasks(任务)/plants(植株档案)/all(全部)"
    ),
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    status: str = typer.Option("", "--status", "-s", help="按状态筛选"),
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
