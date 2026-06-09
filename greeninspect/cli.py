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
        raise typer.Exit(1)


# ============================================================
# 日期范围辅助：自然周 / 自然月 / 自定义
# ============================================================
def resolve_date_range(
    date_from: str = "", date_to: str = "",
    today: bool = False, week: bool = False, month: bool = False,
    natural_week: bool = False, natural_month: bool = False,
    default_days: int = 7,
):
    """统一解析日期范围：支持今日/本周/本月/自然周(周一~今日)/自然月(1号~今日)"""
    now = datetime.now()
    if today:
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), "今日"
    if week:  # 最近7天
        return (now - timedelta(days=6)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), "最近7天"
    if month:  # 最近30天
        return (now - timedelta(days=29)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), "最近30天"
    if natural_week:  # 自然周 周一~今日
        weekday = now.weekday()  # 0=周一
        s = now - timedelta(days=weekday)
        return s.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), f"自然周(第{now.isocalendar()[1]}周)"
    if natural_month:  # 自然月 1号~今日
        s = now.replace(day=1)
        return s.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), f"{now.year}年{now.month}月(自然月)"
    if date_from or date_to:
        df = date_from or "2000-01-01"
        dt = date_to or now.strftime("%Y-%m-%d")
        return df, dt, f"{df} ~ {dt}"
    # 默认：最近 N 天
    return (now - timedelta(days=default_days - 1)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), f"最近{default_days}天"


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
@app.command("task", help="养护任务管理:生成、指派、批量更新、查询逾期、批量导入完成结果")
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
    import_completion: str = typer.Option("", "--import", help="从Excel/CSV批量导入任务完成结果(任务号、处理人、完成时间、备注)"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """养护任务管理"""
    storage = get_storage(db_path)
    storage.update_overdue_tasks()
    check_initialized(storage)

    # ===== 批量导入完成结果 =====
    if import_completion:
        path = import_completion
        if not os.path.isfile(path):
            console.print(f"[red]✗ 文件不存在: {path}[/red]")
            raise typer.Exit(1)
        try:
            import pandas as pd
            ext2 = os.path.splitext(path)[1].lower()
            if ext2 in (".xlsx", ".xls"):
                df2 = pd.read_excel(path, dtype=str)
            elif ext2 == ".csv":
                try:
                    df2 = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
                except UnicodeDecodeError:
                    df2 = pd.read_csv(path, dtype=str, encoding="gbk")
            else:
                console.print(f"[red]✗ 仅支持 .xlsx/.csv 文件[/red]")
                raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]✗ 读取文件失败: {e}[/red]")
            raise typer.Exit(1)

        # 列名映射 - 智能识别常见表头
        df2.columns = [str(c).strip() for c in df2.columns]
        col_map = {}
        task_no_candidates = ["任务号", "任务编号", "任务Id", "task_no", "taskNo", "任务编码", "编号"]
        processor_candidates = ["处理人", "完成人", "执行人", "assignee", "处理人姓名"]
        completed_at_candidates = ["完成时间", "处理时间", "completion_time", "completedAt", "completed_at"]
        result_candidates = ["备注", "处理结果", "处理说明", "result", "处理备注", "完成结果"]
        for c in df2.columns:
            cc = c.lower()
            if "task" in c.lower() or any(x in c for x in task_no_candidates) or (c.lower() in ("id", "no", "编号") and "任务" in str(df2.columns)):
                if "task_no" not in col_map and (any(x.lower() in cc for x in ("task",)) or c in task_no_candidates):
                    col_map["task_no"] = c
                elif "task_no" not in col_map and c in ("编号", "ID"):
                    col_map["task_no"] = c
            if any(x.lower() in cc for x in ("processor", "assignee", "处理人", "完成人", "执行人")):
                if "processor" not in col_map:
                    col_map["processor"] = c
            if any(x.lower() in cc for x in ("completed_at", "completion", "完成时间", "处理时间")):
                if "completed_at" not in col_map:
                    col_map["completed_at"] = c
            if any(x.lower() in cc for x in ("result", "备注", "说明")):
                if "result" not in col_map:
                    col_map["result"] = c

        # 如果task_no还没识别到，找第一个含"任务"或"task"的列
        if "task_no" not in col_map:
            for c in df2.columns:
                if "任务" in c or "task" in c.lower() or c.lower() in ("no", "id"):
                    col_map["task_no"] = c
                    break
        if "task_no" not in col_map and len(df2.columns) > 0:
            col_map["task_no"] = df2.columns[0]

        console.print(Panel(
            f"📂 识别到列映射: \n" + "\n".join(
                f"  {k:15s} → {v}" for k, v in col_map.items()
            ) + f"\n\n共 {len(df2)} 行待导入",
            title="📥 导入任务完成结果", border_style="cyan"
        ))

        records = []
        for _, r in df2.iterrows():
            rec = {}
            for k, v in col_map.items():
                val = r.get(v, "")
                if pd.isna(val):
                    val = ""
                rec[k] = str(val).strip()
            records.append(rec)
        result = storage.import_task_completion(records)

        # 展示结果 - 三类清单分开
        from rich.table import Table
        total_read = result.get("total", len(records))
        console.print(Panel(
            f"📥 读取文件: {total_read} 行  |  去重后: {len(result.get('success_list', [])) + len(result.get('failed_list', []))} 条任务\n"
            f"[green]✅ 成功更新: {result['success']}[/green]   "
            f"[yellow]○ 跳过(空任务号): {result['skipped']}[/yellow]   "
            f"[red]❌ 失败(不存在/异常): {result['failed']}[/red]",
            title="导入完成", border_style=("green" if result["failed"] == 0 else "yellow")
        ))

        # 1. 成功清单Table
        success_list = result.get("success_list", [])
        if success_list:
            ok_t = Table(show_header=True, header_style="bold green")
            ok_t.add_column("任务号", style="cyan")
            ok_t.add_column("处理人")
            ok_t.add_column("新状态", style="green")
            ok_t.add_column("完成时间", style="dim")
            ok_t.add_column("备注", style="dim", max_width=30)
            for s in success_list[:15]:
                ok_t.add_row(s["task_no"], s["processor"], s["status"],
                             s["completed_at"], (s.get("result") or "")[:30])
            console.print(Panel(ok_t, title=f"✅ 成功更新 ({len(success_list)}条)", border_style="green", expand=False))
            if len(success_list) > 15:
                console.print(f"[dim green]  ... 还有 {len(success_list)-15} 条成功记录省略[/dim green]")

        # 2. 跳过清单(空任务号等)
        skipped_list = result.get("skipped_list", [])
        if skipped_list:
            sk_t = Table(show_header=True, header_style="bold yellow")
            sk_t.add_column("原行号", style="dim")
            sk_t.add_column("跳过原因", style="yellow")
            sk_t.add_column("原始内容", style="dim", max_width=40)
            for s in skipped_list[:10]:
                raw_preview = ", ".join(f"{k}={v}" for k, v in list(s.get("raw", {}).items())[:3])
                sk_t.add_row(str(s.get("row_index", "?")), s.get("reason", ""), raw_preview)
            console.print(Panel(sk_t, title=f"○ 跳过记录 ({len(skipped_list)}条)", border_style="yellow", expand=False))

        # 3. 失败清单
        failed_list = result.get("failed_list", [])
        if failed_list:
            fl_t = Table(show_header=True, header_style="bold red")
            fl_t.add_column("序号", style="dim")
            fl_t.add_column("任务号", style="red")
            fl_t.add_column("失败原因", style="red")
            for f in failed_list[:10]:
                fl_t.add_row(str(f.get("row_index", "?")), f.get("task_no", "?"), f.get("reason", ""))
            console.print(Panel(fl_t, title=f"❌ 失败记录 ({len(failed_list)}条)", border_style="red", expand=False))
            if len(failed_list) > 10:
                console.print(f"[dim red]  ... 还有 {len(failed_list)-10} 条失败记录省略[/dim red]")
            console.print("[yellow]💡 失败的任务请检查任务号拼写是否正确，或先用 task --list 确认[/yellow]")

        # 立即刷新逾期状态
        storage.update_overdue_tasks()
        return

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
@app.command("report", help="汇总报告:按日期汇总、异常摘要、区域负责人视角、连续异常、管理看板、交接清单")
def report_cmd(
    date_from: str = typer.Option("", "--from", "-f", help="开始日期 YYYY-MM-DD"),
    date_to: str = typer.Option("", "--to", "-t", help="结束日期 YYYY-MM-DD"),
    today: bool = typer.Option(False, "--today", help="仅查看今日"),
    week: bool = typer.Option(False, "--week", help="最近7天(滚动)"),
    month: bool = typer.Option(False, "--month", help="最近30天(滚动)"),
    natural_week: bool = typer.Option(False, "--natural-week", help="自然周(本周一至今)"),
    natural_month: bool = typer.Option(False, "--natural-month", help="自然月(本月1号至今)"),
    area: str = typer.Option("", "--area", "-a", help="按区域筛选"),
    summary: bool = typer.Option(False, "--summary", "-s", help="生成异常摘要"),
    handover: bool = typer.Option(False, "--handover", "-h", help="打印交接班清单"),
    area_view: bool = typer.Option(False, "--area-view", help="区域负责人视角:按区域+责任人+任务状态汇总"),
    kpi: bool = typer.Option(False, "--kpi", help="管理看板:巡检覆盖率/任务完成率/逾期风险/责任人排名(issue1)"),
    performance: bool = typer.Option(False, "--performance", help="负责人绩效考核:工作量/闭环率/处理时长/综合得分(issue2)"),
    abnormal_view: bool = typer.Option(False, "--abnormal", help="连续多天异常植株和重复问题分析"),
    abnormal_days: int = typer.Option(7, "--abnormal-days", help="连续异常分析的时间窗口(天)"),
    min_occurrences: int = typer.Option(2, "--min-occur", help="最小异常天数才算连续"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """巡检报告汇总"""
    storage = get_storage(db_path)
    storage.update_overdue_tasks()
    check_initialized(storage)

    # 统一日期解析
    df, dt, range_label = resolve_date_range(
        date_from=date_from, date_to=date_to,
        today=today, week=week, month=month,
        natural_week=natural_week, natural_month=natural_month,
        default_days=7,
    )
    if not parse_date(df) or not parse_date(dt):
        console.print("[red]✗ 日期格式错误，请使用 YYYY-MM-DD[/red]")
        raise typer.Exit(1)

    insps = storage.get_inspections_by_area_and_date(area, df, dt)

    # 管理看板
    if kpi:
        _print_kpi_dashboard(storage, df, dt, range_label, area)
        return

    # 负责人绩效考核
    if performance:
        _print_performance_dashboard(storage, df, dt, range_label, area)
        return

    # 区域负责人视角
    if area_view:
        _print_area_leader_view(storage, df, dt, area)
        return

    # 连续异常植株 + 重复问题（支持自然周/月）
    if abnormal_view:
        _print_abnormal_trend_view(storage, abnormal_days, min_occurrences, area, df, dt)
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
# 连续异常 & 重复问题分析视角（新版按天去重）
# ============================================================
def _print_abnormal_trend_view(storage: Storage, days: int, min_occur: int, area_filter: str,
                               date_from: str = "", date_to: str = ""):
    """展示连续多日异常植株和重复问题"""
    from collections import defaultdict
    from rich.table import Table as T
    from rich import box

    df, dt, label = resolve_date_range(date_from, date_to, default_days=days)

    console.print(Panel(
        f"[bold cyan]统计区间:[/bold cyan] {label} ({df} ~ {dt})\n"
        f"[bold cyan]判定阈值:[/bold cyan] 异常天数 ≥ {min_occur} 天\n"
        f"[bold cyan]区域筛选:[/bold cyan] {area_filter or '全部'}",
        title="🔥 连续异常植株 & 重复问题分析（按天去重）", border_style="red", expand=False
    ))

    # 1. 连续异常植株
    consec = storage.get_consecutive_abnormal_plants(days=days, min_occurrences=min_occur,
                                                      start_date=df, end_date=dt)
    if area_filter:
        consec = [c for c in consec if c["area"] == area_filter]

    if consec:
        t1 = T(title=f"反复异常植株TOP ({len(consec)}株 · 建议重点养护)", box=box.ROUNDED)
        t1.add_column("编号", style="cyan", no_wrap=True)
        t1.add_column("名称", style="green")
        t1.add_column("区域", style="blue")
        t1.add_column("异常\n天数", justify="right")
        t1.add_column("持续\n跨度", justify="right", style="bold magenta")
        t1.add_column("首次", style="yellow")
        t1.add_column("最近", style="yellow")
        t1.add_column("平均\n分", justify="right")
        t1.add_column("最近评分", justify="right")
        t1.add_column("最常见", style="magenta")
        t1.add_column("最近问题", style="white")
        t1.add_column("已派\n任务", justify="center")
        t1.add_column("问题明细", style="dim")
        for c in consec:
            sc = "red" if c["avg_score"] < 60 else ("yellow" if c["avg_score"] < 80 else "green")
            last_sc = "red" if c["last_score"] < 60 else ("yellow" if c["last_score"] < 80 else "green")
            task_badge = f"[green]✓{c['task_count']}[/green]" if c["has_task"] == "是" else "[red]✗无[/red]"
            t1.add_row(
                c["plant_code"], c["plant_name"][:6] if c["plant_name"] else "-", c["area"] or "-",
                f"[bold red]{c['abnormal_days']}[/bold red]",
                f"[bold magenta]{c['calendar_duration']}天[/bold magenta]",
                c["first_date"], c["last_date"],
                f"[{sc}]{c['avg_score']}[/{sc}]",
                f"[{last_sc}]{c['last_score']}[/{last_sc}]",
                f"[bold]{c['top_issue']}[/bold]",
                c["last_issue"],
                task_badge,
                c["issue_detail"] or "-"
            )
        console.print(t1)
    else:
        console.print(Panel("[green]✓ 区间内未发现反复异常的植株，养护状况良好[/green]",
                              title="连续异常排查", border_style="green"))

    # 2. 重复问题统计（按日期过滤）
    repeat = storage.get_repeat_issue_summary(days=days, start_date=df, end_date=dt)
    if area_filter:
        repeat = [r for r in repeat if r["area"] == area_filter]

    if repeat:
        t2 = T(title=f"重复问题排行 TOP15（{label}）", box=box.ROUNDED)
        t2.add_column("排名", justify="right", style="cyan")
        t2.add_column("区域", style="blue")
        t2.add_column("责任人", style="green")
        t2.add_column("问题类型", style="magenta")
        t2.add_column("总次", justify="right")
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
        if any(c["top_issue"] == "需补苗" for c in consec):
            suggestions.append("🌱 补苗需求集中，建议检查选址或更换耐候品种")
        unassigned = [c for c in consec if c["has_task"] == "否"]
        if unassigned:
            codes = "、".join(c["plant_code"] for c in unassigned[:5])
            suggestions.append(f"📋 以下植株异常未派任务: {codes}{'等' if len(unassigned) > 5 else ''}，请尽快处理")
        low_perf = [r for r in repeat if r["done_rate"] < 60 and r["overdue"] > 0]
        if low_perf:
            names = "、".join(sorted(set(r["assignee"] for r in low_perf)))
            suggestions.append(f"👥 责任人任务积压: {names}，建议增派支援或重新分配")
        if suggestions:
            console.print(Panel("\n".join(f"  • {s}" for s in suggestions),
                                  title="📋 养护优化建议", border_style="yellow"))


# ============================================================
# 管理看板 KPI Dashboard (issue1)
# ============================================================
def _print_kpi_dashboard(storage: Storage, df: str, dt: str, range_label: str, area_filter: str):
    """展示巡检覆盖率、任务完成率、逾期风险、责任人排名"""
    from rich.table import Table as T
    from rich import box
    from collections import defaultdict

    data = storage.get_kpi_dashboard(df, dt, area_filter)
    o = data["overall"]

    # ===== 顶部总体卡片 =====
    def _pct(v, style="bold"):
        color = "green" if v >= 80 else ("yellow" if v >= 50 else "red")
        return f"[{color}][{style}]{v}%[/{style}][/{color}]"

    coverage_color = "green" if o["coverage_rate"] >= 80 else ("yellow" if o["coverage_rate"] >= 50 else "red")
    task_color = "green" if o["task_completion_rate"] >= 80 else ("yellow" if o["task_completion_rate"] >= 50 else "red")
    risk_color = "green" if o["overdue_risk_rate"] < 10 else ("yellow" if o["overdue_risk_rate"] < 30 else "red")
    score_color = "green" if o["avg_score"] >= 80 else ("yellow" if o["avg_score"] >= 60 else "red")

    console.print(Panel(
        f"[bold]统计区间:[/bold] {range_label} ({df} ~ {dt})\n"
        f"[bold]区域筛选:[/bold] {area_filter or '全部'}",
        title="📊 园区绿植养护 · 管理看板", border_style="cyan", expand=False,
    ))

    # 大数字概览
    overview = T(box=box.ROUNDED, show_lines=False, padding=(0,2))
    overview.add_column("巡检覆盖率", style=coverage_color, justify="center", header_style="bold")
    overview.add_column("任务完成率", style=task_color, justify="center", header_style="bold")
    overview.add_column("平均评分", style=score_color, justify="center", header_style="bold")
    overview.add_column("异常率", style="bold", justify="center", header_style="bold")
    overview.add_column("逾期风险率", style=risk_color, justify="center", header_style="bold")
    overview.add_row(
        f"[bold]{o['coverage_rate']}%[/bold]\n[dim]已检{o['checked_plants']}/{o['total_plants']}株[/dim]",
        f"[bold]{o['task_completion_rate']}%[/bold]\n[dim]已完{o['task_done']}/{o['task_total']}项[/dim]",
        f"[bold]{o['avg_score']}[/bold]\n[dim]共{o['inspection_count']}次巡检[/dim]",
        f"{_pct(o['abnormal_rate'])}\n[dim]{o['inspection_count'] - int(o['inspection_count']*(100-o['abnormal_rate'])/100)}异常次[/dim]",
        f"[bold]{o['overdue_risk_rate']}%[/bold]\n[dim]逾期风险{o['overdue_risk_count']}株[/dim]",
    )
    console.print(overview)

    # ===== 各区域维度 =====
    if data["by_area"]:
        t = T(title=f"各区域养护KPI明细（{len(data['by_area'])}个区域）", box=box.ROUNDED)
        t.add_column("区域", style="cyan", no_wrap=True)
        t.add_column("植株\n总数", justify="right")
        t.add_column("已检", justify="right")
        t.add_column("巡检覆\n盖率", justify="right", header_style="bold")
        t.add_column("巡检\n次数", justify="right")
        t.add_column("平均\n评分", justify="right")
        t.add_column("新建\n任务", justify="right")
        t.add_column("完成率", justify="right", header_style="bold")
        t.add_column("当前\n逾期", justify="right")
        t.add_column("风险\n等级", justify="center")
        for row in data["by_area"]:
            cov_st = "green" if row["coverage"] >= 80 else ("yellow" if row["coverage"] >= 50 else "red")
            tr_st = "green" if row["task_rate"] >= 80 else ("yellow" if row["task_rate"] >= 50 else "red")
            sc_st = "green" if row["avg_score"] >= 80 else ("yellow" if row["avg_score"] >= 60 else "red")
            t.add_row(
                row["area"],
                str(row["plants_total"]),
                str(row["checked"]),
                f"[{cov_st}]{row['coverage']}%[/{cov_st}]",
                str(row["inspections"]),
                f"[{sc_st}]{row['avg_score']}[/{sc_st}]" if row["avg_score"] else "-",
                str(row["task_created"]),
                f"[{tr_st}]{row['task_rate']}%[/{tr_st}]" if row["task_created"] else "-",
                f"[bold red]{row['overdue_now']}[/bold red]" if row["overdue_now"] > 0 else "0",
                row["risk_level"],
            )
        console.print(t)

    # ===== 责任人排名 =====
    if data["assignee_ranking"]:
        t2 = T(title=f"责任人综合排名TOP10（完成率50%+评分30%+异常20%）",
               box=box.ROUNDED)
        t2.add_column("排名", justify="center", style="bold cyan")
        t2.add_column("责任人", style="green")
        t2.add_column("指派\n任务", justify="right")
        t2.add_column("已完成", justify="right", style="green")
        t2.add_column("逾期", justify="right", style="bold red")
        t2.add_column("任务\n完成率", justify="right")
        t2.add_column("参与\n巡检", justify="right")
        t2.add_column("巡检\n平均分", justify="right")
        t2.add_column("综合\n得分", justify="right", style="bold magenta")
        for p in data["assignee_ranking"][:10]:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(p["rank"], f"  {p['rank']}.")
            tr_st = "green" if p["task_rate"] >= 80 else ("yellow" if p["task_rate"] >= 50 else "red")
            sc_st = "green" if p["avg_inspection_score"] >= 80 else ("yellow" if p["avg_inspection_score"] >= 60 else "red")
            t2.add_row(
                medal, p["user"],
                str(p["total"]), str(p["done"]), str(p["overdue"]),
                f"[{tr_st}]{p['task_rate']}%[/{tr_st}]" if p["total"] else "-",
                str(p["insp_count"]),
                f"[{sc_st}]{p['avg_inspection_score']}[/{sc_st}]" if p["avg_inspection_score"] else "-",
                str(p["composite_score"]),
            )
        console.print(t2)

    # ===== 运营建议 =====
    tips = []
    low_cov = [x["area"] for x in data["by_area"] if x["coverage"] < 60]
    if low_cov:
        tips.append(f"🔍 区域巡检覆盖不足: {', '.join(low_cov)}，请优先安排")
    high_risk = [x["area"] for x in data["by_area"] if "🔴" in x["risk_level"]]
    if high_risk:
        tips.append(f"⚠️ 逾期高风险区域: {', '.join(high_risk)}，请立即处理")
    low_task = [p["user"] for p in data["assignee_ranking"] if p["total"] and p["task_rate"] < 50]
    if low_task:
        tips.append(f"👤 任务积压责任人: {', '.join(low_task[:5])}，建议增派支援")
    if o["overdue_risk_count"] > 5:
        tips.append(f"📅 全园区共有 {o['overdue_risk_count']} 株逾期未检，建议启动专项巡检")
    if tips:
        console.print(Panel("\n".join(f"  • {t}" for t in tips),
                              title="🚨 运营优先级建议", border_style="yellow"))


def _print_performance_dashboard(storage: Storage, df: str, dt: str, range_label: str, area_filter: str):
    """负责人绩效考核看板(issue2_new): 工作量/闭环率/处理时长/综合得分"""
    from rich.table import Table

    data = storage.get_assignee_performance(df, dt, area_filter)
    people = data["people"]
    if not people:
        console.print(Panel("暂无负责人绩效数据，请先运行巡检和任务",
                            title="📊 负责人绩效考核", border_style="yellow"))
        return

    # 顶部汇总
    total_created = sum(p["created_tasks"] for p in people)
    total_done = sum(p["completed_tasks"] + p["pending_closed"] for p in people)
    total_pending = sum(p["pending_open"] for p in people)
    total_overdue = sum(p["overdue_remaining"] for p in people)
    all_avg_hours = [p["avg_task_hours"] for p in people if p["avg_task_hours"] > 0]
    avg_h = round(sum(all_avg_hours) / len(all_avg_hours), 1) if all_avg_hours else 0

    # 颜色
    def _score_color(score):
        if score >= 85: return "green"
        if score >= 70: return "cyan"
        if score >= 55: return "yellow"
        return "red"

    console.print(Panel(
        f"📅 统计区间: [bold cyan]{range_label}[/bold cyan]\n"
        f"👥 参与人数: [bold]{len(people)}[/bold]   "
        f"📝 指派任务总数: [bold]{total_created}[/bold]   "
        f"✅ 已完成: [bold green]{total_done}[/bold green]\n"
        f"⏳ 遗留未闭环: [bold yellow]{total_pending}[/bold yellow]   "
        f"🔴 逾期剩余: [bold red]{total_overdue}[/bold red]   "
        f"⏱️ 平均处理时长: [bold]{avg_h}h[/bold]",
        title=f"📊 负责人绩效考核 ({area_filter or '全园区'})",
        border_style="bold magenta"
    ))

    # 核心表格
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}
    t = Table(show_header=True, header_style="bold magenta", expand=True)
    t.add_column("排名", width=6, justify="center")
    t.add_column("责任人", style="bold cyan")
    t.add_column("指派任务", justify="right", width=8)
    t.add_column("完成任务", justify="right", width=8)
    t.add_column("接手遗留", justify="right", width=8)
    t.add_column("已闭环\n遗留", justify="right", width=8)
    t.add_column("遗留未\n闭环", justify="right", width=8)
    t.add_column("遗留闭\n环率%", justify="right", width=8)
    t.add_column("逾期\n剩余", justify="right", width=7)
    t.add_column("平均处理\n时长(h)", justify="right", width=8)
    t.add_column("巡检\n次数", justify="right", width=6)
    t.add_column("巡检\n均分", justify="right", width=6)
    t.add_column("综合得分", justify="right", width=8)

    for p in people:
        rank_txt = medal.get(p["rank"], f"#{p['rank']}")
        sc_color = _score_color(p["composite_score"])
        closed_rate_color = "green" if p["pending_closed_rate"] >= 80 else ("yellow" if p["pending_closed_rate"] >= 50 else "red")
        overdue_color = "red" if p["overdue_remaining"] > 2 else ("yellow" if p["overdue_remaining"] > 0 else "green")

        def _c(v, c):
            return f"[{c}]{v}[/{c}]"

        t.add_row(
            rank_txt, p["user"],
            str(p["created_tasks"]),
            _c(p["completed_tasks"], "green" if p["completed_tasks"] else "dim"),
            str(p["pending_taken"]),
            _c(str(p["pending_closed"]), "green" if p["pending_closed"] else "dim"),
            _c(str(p["pending_open"]), "yellow" if p["pending_open"] else "dim"),
            _c(str(p["pending_closed_rate"]), closed_rate_color),
            _c(str(p["overdue_remaining"]), overdue_color),
            _c(str(p["avg_task_hours"]), "cyan" if p["avg_task_hours"] else "dim"),
            str(p["insp_count"]),
            _c(str(p["avg_inspection_score"]), "green" if p["avg_inspection_score"] >= 85 else ("yellow" if p["avg_inspection_score"] >= 70 else "red")) if p["avg_inspection_score"] else "-",
            _c(str(p["composite_score"]), sc_color),
        )
    console.print(t)

    # 末位提醒 + 绩效亮点
    good = [f"{p['user']}({p['composite_score']})" for p in people[:3]]
    bad = [f"{p['user']}(逾期{p['overdue_remaining']}, 闭环{p['pending_closed_rate']}%)"
           for p in people[-3:] if p["composite_score"] < 70 or p["overdue_remaining"] > 2]
    tips = [f"⭐ TOP绩效: {', '.join(good)}"]
    if bad:
        tips.append(f"⚠️ 需跟进: {', '.join(bad)}")
    if sum(p["overdue_remaining"] for p in people) > 5:
        tips.append(f"🔴 逾期总数较多，请启动积压专项清理")
    if tips:
        console.print(Panel("\n".join(f"  • {t}" for t in tips),
                            title="💡 考核提示", border_style="magenta"))


# ============================================================
# 提醒命令 Remind (issue4)
# ============================================================
@app.command("remind", help="队长每日早会:列出明天/3天/本周即将到检植株和到期任务")
def remind_cmd(
    scope: str = typer.Option("3days", "--scope", "-s", help="范围: tomorrow(明天)|3days(3天)|week(本周)"),
    by_area: bool = typer.Option(True, "--by-area/--by-assignee", help="按区域分组展示或按责任人分组"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    storage = get_storage(db_path)
    check_initialized(storage)
    storage.update_overdue_tasks()

    data = storage.get_upcoming_reminders(scope=scope)
    summary = data["summary"]
    pd_c = data.get("priority_distribution", {}).get("checks", {})
    pd_t = data.get("priority_distribution", {}).get("tasks", {})
    from rich.table import Table as T
    from rich import box

    console.print(Panel(
        f"[bold]提醒范围:[/bold] {data['scope_label']}  ({data['start_date']} ~ {data['end_date']})\n"
        f"[bold]🔴 P0 最高优先:[/bold] [red]逾期未检{pd_c.get('P0',0)}株 / 已逾期任务{pd_t.get('P0',0)}项[/red]\n"
        f"[bold]🟠 P1 今天处理:[/bold] [yellow]今到期检{pd_c.get('P1',0)}株 / 今到期任务{pd_t.get('P1',0)}项 + 异常未派{pd_c.get('P2',0)}[/yellow]\n"
        f"🟡 P2 跟进: 连续异常 {pd_c.get('P2',0)}株   🟢 P3 正常: {pd_c.get('P3',0)}株/{pd_t.get('P3',0)}项\n"
        f"📋 总计 待检{summary['checks_count']}株·任务{summary['tasks_count']}项  [bold]紧急合计{summary.get('urgent_p0_p1',0)}项[/bold]",
        title="⏰ 今日早会养护优先级提醒", border_style="magenta", expand=False,
    ))

    if by_area:
        for area in sorted(data["by_area"].keys()):
            entries = data["by_area"][area]
            cs = entries["checks"]
            ts = entries["tasks"]
            if not cs and not ts:
                continue
            # 本区域P0/P1数量
            area_p0 = sum(1 for c in cs if c.get("priority_level") == "P0") + sum(1 for t in ts if t.get("priority_level") == "P0")
            area_p1 = sum(1 for c in cs if c.get("priority_level") == "P1") + sum(1 for t in ts if t.get("priority_level") == "P1")
            console.print(f"\n[bold cyan]▌[/bold cyan] [bold]{area}[/bold]  "
                          f"待检[yellow]{len(cs)}[/yellow]株 · 任务[yellow]{len(ts)}[/yellow]项"
                          + (f"  [red]🚨P0={area_p0}[/red]" if area_p0 else "")
                          + (f"  [yellow]⚠️P1={area_p1}[/yellow]" if area_p1 else ""))
            if cs:
                t1 = T(box=box.SIMPLE_HEAVY, show_header=True)
                t1.add_column("优先级", width=14)
                t1.add_column("到期", width=10)
                t1.add_column("编号", style="cyan")
                t1.add_column("名称", style="green")
                t1.add_column("位置", style="dim")
                t1.add_column("状态", no_wrap=True)
                t1.add_column("优先级原因", style="dim", max_width=20)
                for c in cs[:10]:
                    dl = c["days_left"]
                    p_lvl = c.get("priority_level", "P3")
                    p_emo = c.get("priority_emoji", "🟢")
                    p_reason = c.get("priority_reason", "")
                    p_style = {"P0": "bold red", "P1": "bold yellow", "P2": "yellow", "P3": "dim"}.get(p_lvl, "")
                    pr_badge = f"[{p_style}]{p_emo}{p_lvl}[/{p_style}]"
                    badge = "[red]⚠️逾期[/red]" if c["is_overdue"] else (
                        "[yellow]今天[/yellow]" if dl == 0 else f"{dl}天后")
                    p = c["plant"]
                    status_style = "red" if p.status != "健康" else "green"
                    t1.add_row(pr_badge, badge, p.code, p.name or "", p.location or "",
                               f"[{status_style}]{p.status}[/{status_style}]",
                               p_reason)
                console.print(t1)
                if len(cs) > 10:
                    console.print(f"[dim]  ... 还有 {len(cs) - 10} 株待检，使用 list --area \"{area}\" --pending 查看全部[/dim]")
            if ts:
                t2 = T(box=box.SIMPLE_HEAVY, show_header=True)
                t2.add_column("优先级", width=14)
                t2.add_column("到期", width=10)
                t2.add_column("任务号", style="magenta")
                t2.add_column("类型", style="white")
                t2.add_column("责任人", style="green")
                t2.add_column("状态", no_wrap=True)
                t2.add_column("描述", style="dim", max_width=20)
                for t in ts[:10]:
                    dl = t["days_left"]
                    p_lvl = t.get("priority_level", "P3")
                    p_emo = t.get("priority_emoji", "🟢")
                    p_reason = t.get("priority_reason", "")
                    p_style = {"P0": "bold red", "P1": "bold yellow", "P2": "yellow", "P3": "dim"}.get(p_lvl, "")
                    pr_badge = f"[{p_style}]{p_emo}{p_lvl}[/{p_style}]"
                    badge = "[red]⚠️逾期[/red]" if dl < 0 else (
                        "[yellow]今天[/yellow]" if dl == 0 else f"{dl}天后")
                    tk = t["task"]
                    status_style = "red" if dl < 0 else ("yellow" if dl <= 1 else "white")
                    t2.add_row(pr_badge, badge, tk.task_no, tk.task_type, tk.assignee or "未指派",
                               f"[{status_style}]{tk.status}[/{status_style}]",
                               (f"{p_reason} | " if p_reason else "") + (tk.description or ""))
                console.print(t2)
    else:
        # 按责任人分组任务
        if summary["tasks_count"]:
            console.print(f"\n[bold]📋 到期任务（按责任人分组）[/bold]")
            for user in sorted(data["by_assignee"].keys()):
                t_list = data["by_assignee"][user]
                if not t_list: continue
                overdue_cnt = sum(1 for x in t_list if x["days_left"] < 0)
                p0_cnt = sum(1 for x in t_list if x.get("priority_level") == "P0")
                p1_cnt = sum(1 for x in t_list if x.get("priority_level") == "P1")
                console.print(f"\n  [green]▌[/green] {user}: 共 {len(t_list)} 项"
                              + (f"  [red]🚨P0{p0_cnt}/逾期{overdue_cnt}[/red]" if overdue_cnt or p0_cnt else "")
                              + (f"  [yellow]P1{p1_cnt}[/yellow]" if p1_cnt else ""))
                for x in t_list[:8]:
                    dl = x["days_left"]
                    tk = x["task"]
                    p_lvl = x.get("priority_level", "P3")
                    p_emo = x.get("priority_emoji", "🟢")
                    tag = f"[red]⚠️{abs(dl)}天前到期[/red]" if dl < 0 else f"{dl}天后到期"
                    console.print(f"    • {p_emo}{p_lvl} {tag}  {tk.task_no} {tk.task_type} "
                                  f"[dim]({tk.area})[/dim] {tk.description[:30] if tk.description else ''}")

    console.print(f"\n[dim]提示: 每天早上执行 [cyan]remind --scope 3days[/cyan] 生成今日优先清单；紧急按P0→P1→P2→P3处理[/dim]")


# ============================================================
# 历史追溯命令 Trace (issue5_new)
# ============================================================
@app.command("trace", help="历史追溯:输入植株编号或任务号，查看巡检/派单/交接/重指派/闭环全流程")
def trace_cmd(
    plant_code: str = typer.Option("", "--plant", "-p", help="植株编号"),
    task_no: str = typer.Option("", "--task", "-t", help="任务编号"),
    task_id: Optional[int] = typer.Option(None, "--task-id", help="任务ID(可选)"),
    limit: int = typer.Option(30, "--limit", "-n", help="最多显示事件条数"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """历史追溯命令 - 快速了解某株/某任务的完整处理链路"""
    storage = get_storage(db_path)
    check_initialized(storage)

    if not plant_code and not task_no and not task_id:
        console.print("[yellow]⚠ 请指定 [bold]--plant 植株编号[/bold] 或 [bold]--task 任务编号[/bold]，例如:\n"
                      "  greeninspect trace --plant P0001\n"
                      "  greeninspect trace --task T20260601-001[/yellow]")
        raise typer.Exit(1)

    result = storage.get_entity_trace(plant_code=plant_code, task_no=task_no, task_id=task_id)
    if result["entity_type"] == "unknown":
        console.print(f"[red]✗ 未找到任何记录。[/red] 请检查编号是否正确")
        raise typer.Exit(1)

    # 头部基本信息
    from rich.table import Table as T2

    header_lines = []
    if result.get("plant"):
        p = result["plant"]
        header_lines.append(
            f"[bold cyan]🌿 植株档案:[/bold cyan] {p['code']} / {p['name']}\n"
            f"      品种: {p.get('species','-')}  区域: {p.get('area','-')}  当前状态: [{ 'green' if p.get('status')=='健康' else 'red'}]{p.get('status','-')}[/{ 'green' if p.get('status')=='健康' else 'red'}]"
            f"  上次巡检: {p.get('last_check_date','-')}  下次巡检: {p.get('next_check_date','-')}"
        )
    if result.get("task"):
        tk = result["task"]
        st_color = {"已完成": "green", "已闭环": "green", "待处理": "yellow",
                    "处理中": "cyan", "已逾期": "red"}.get(tk.get("status", ""), "white")
        header_lines.append(
            f"[bold magenta]📝 任务档案:[/bold magenta] {tk['task_no']} / [{tk['task_type']}]\n"
            f"      责任人: {tk.get('assignee') or '未指派'}  截止: {tk.get('due_date','-')}  "
            f"状态: [{st_color}]{tk.get('status','-')}[/{st_color}]  优先级: {tk.get('priority','-')}\n"
            f"      描述: {(tk.get('description') or '')[:200]}"
        )
    console.print(Panel("\n\n".join(header_lines),
                        title=f"🔍 历史追溯 ({result['entity_type']})",
                        border_style="bold cyan"))

    # 汇总统计
    s = result["summary"]
    console.print(Panel(
        f"共 {result['events_count']} 条事件   |   "
        f"🔍巡检记录: {s['inspection_count']}   📋任务: {s['task_count']}   "
        f"🤝交接班: {s['handover_count']}   🔁重指派: {s['reassign_count']}   ✅闭环: {s['closed_count']}\n"
        f"当前状态: [{'green' if not s['is_open_loop'] else 'yellow'}]"
        f"{'✅ 已闭环' if not s['is_open_loop'] else '🟡 仍有未完成事项/任务在进行'}",
        title="📊 事件汇总", border_style="dim"
    ))

    # 事件时间线
    from rich import box
    events = result["events"][:limit]
    tbl = T2(box=box.SIMPLE, show_header=True, expand=True)
    tbl.add_column("时间", width=22)
    tbl.add_column("事件类型", width=16)
    tbl.add_column("事件内容", overflow="fold")

    for e in events:
        lvl_color = {
            "ERROR": "bold red", "SUCCESS": "green", "WARN": "yellow",
            "INFO": "cyan", "STATUS": "dim"
        }.get(e.get("level", "INFO"), "white")
        type_text = f"[{lvl_color}]{e['type']}[/{lvl_color}]"
        t = e.get("time") or ""
        if len(t) > 22:
            t = t[:22]
        tbl.add_row(t or "-", type_text, e.get("content") or "")

    console.print(tbl)
    if len(result["events"]) > limit:
        console.print(f"[dim]... 还有 {len(result['events']) - limit} 条事件省略显示，可加 --limit {len(result['events'])} 查看全部[/dim]")
@app.command("export", help="导出巡检表(Excel/CSV)、任务清单、植株档案、汇总分析")
def export_cmd(
    output: str = typer.Option("巡检导出.xlsx", "--output", "-o", help="输出文件路径(.xlsx/.csv)"),
    date_from: str = typer.Option("", "--from", "-f", help="开始日期 YYYY-MM-DD"),
    date_to: str = typer.Option("", "--to", "-t", help="结束日期 YYYY-MM-DD"),
    today: bool = typer.Option(False, "--today", help="仅导出今日数据"),
    week: bool = typer.Option(False, "--week", help="最近7天"),
    natural_week: bool = typer.Option(False, "--natural-week", help="自然周(本周一至今)"),
    natural_month: bool = typer.Option(False, "--natural-month", help="自然月(本月1号至今)"),
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

    df, dt, range_label = resolve_date_range(
        date_from=date_from, date_to=date_to,
        today=today, week=week, natural_week=natural_week, natural_month=natural_month,
        default_days=30,
    )
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
            from collections import defaultdict

            # Sheet: 任务矩阵（区间内+当前状态）
            tasks = storage.get_tasks()
            if area:
                tasks = [t for t in tasks if t.area == area]
            matrix = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
            for t in tasks:
                tc = (t.created_at or "")[:10]
                # 取区间内创建或当前未完成的
                in_range = (not tc) or (df <= tc <= dt)
                if not in_range and t.status == TaskStatus.COMPLETED.value:
                    continue
                ar = t.area or "未分区域"
                u = t.assignee or "未指派"
                matrix[ar][u][t.status] += 1
                matrix[ar][u]["合计"] += 1

            m_rows = []
            for ar in sorted(matrix.keys()):
                for u in sorted(matrix[ar].keys()):
                    d = matrix[ar][u]
                    total_row = d.get("合计", 0)
                    rate = round(d.get("已完成", 0) / total_row * 100, 1) if total_row else 0
                    m_rows.append({
                        "统计区间": f"{df} ~ {dt}",
                        "区域": ar, "责任人": u,
                        "待处理": d.get("待处理", 0),
                        "处理中": d.get("处理中", 0),
                        "已逾期": d.get("已逾期", 0),
                        "已完成": d.get("已完成", 0),
                        "合计": total_row,
                        "完成率%": rate,
                    })
            if m_rows:
                sheets["区域责任人任务汇总"] = pd.DataFrame(m_rows)

            # Sheet: 连续异常植株（按天去重，日期过滤）
            consec = storage.get_consecutive_abnormal_plants(days=abnormal_days, min_occurrences=2,
                                                               start_date=df, end_date=dt)
            if area:
                consec = [c for c in consec if c["area"] == area]
            if consec:
                sheets["连续异常植株(按天去重)"] = pd.DataFrame(consec)

            # Sheet: 重复问题排行（日期过滤）
            repeat = storage.get_repeat_issue_summary(days=abnormal_days, start_date=df, end_date=dt)
            if area:
                repeat = [r for r in repeat if r["area"] == area]
            if repeat:
                sheets["重复问题排行"] = pd.DataFrame(repeat)

            # Sheet: KPI看板（管理视角）
            kpi = storage.get_kpi_dashboard(df, dt, area)
            if kpi["by_area"]:
                overall_row = [{
                    "统计区间": f"{df} ~ {dt}",
                    "植株总数": kpi["overall"]["total_plants"],
                    "已检植株数": kpi["overall"]["checked_plants"],
                    "巡检覆盖率%": kpi["overall"]["coverage_rate"],
                    "巡检次数": kpi["overall"]["inspection_count"],
                    "平均评分": kpi["overall"]["avg_score"],
                    "异常率%": kpi["overall"]["abnormal_rate"],
                    "区间新建任务": kpi["overall"]["task_total"],
                    "任务完成数": kpi["overall"]["task_done"],
                    "任务完成率%": kpi["overall"]["task_completion_rate"],
                    "当前逾期风险株": kpi["overall"]["overdue_risk_count"],
                    "逾期风险率%": kpi["overall"]["overdue_risk_rate"],
                }]
                sheets["KPI_管理汇总"] = pd.DataFrame(overall_row)
                sheets["KPI_各区域明细"] = pd.DataFrame(kpi["by_area"])
                if kpi["assignee_ranking"]:
                    ranking_rows = []
                    for p in kpi["assignee_ranking"]:
                        ranking_rows.append({
                            "排名": p.get("rank", ""),
                            "责任人": p["user"],
                            "指派任务数": p["total"],
                            "已完成": p["done"],
                            "已逾期": p["overdue"],
                            "任务完成率%": p["task_rate"],
                            "参与巡检次数": p["insp_count"],
                            "巡检平均分": p["avg_inspection_score"],
                            "综合得分": p["composite_score"],
                        })
                    sheets["KPI_责任人排名"] = pd.DataFrame(ranking_rows)

            # Sheet: 负责人绩效考核 (issue2_new)
            perf = storage.get_assignee_performance(df, dt, area)
            if perf.get("people"):
                perf_rows = []
                for p in perf["people"]:
                    perf_rows.append({
                        "统计区间": f"{df} ~ {dt}",
                        "排名": p.get("rank", ""),
                        "责任人": p["user"],
                        "指派任务数": p["created_tasks"],
                        "完成任务数": p["completed_tasks"],
                        "接手遗留数": p["pending_taken"],
                        "遗留已闭环": p["pending_closed"],
                        "遗留未闭环": p["pending_open"],
                        "遗留闭环率%": p["pending_closed_rate"],
                        "逾期剩余数": p["overdue_remaining"],
                        "平均处理时长(h)": p["avg_task_hours"],
                        "巡检参与次数": p["insp_count"],
                        "巡检平均分": p["avg_inspection_score"],
                        "综合绩效得分": p["composite_score"],
                    })
                sheets["负责人绩效考核"] = pd.DataFrame(perf_rows)

            # Sheet: 各区域健康度（按日期过滤）
            insps2 = storage.get_inspections_by_area_and_date(area or "", df, dt)
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
                    "统计区间": f"{df} ~ {dt}",
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
@app.command("shift", help="交接班工作流:开/关班次、交班、接班确认、遗留事项重指派/补处理、闭环率查询")
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
    filter: str = typer.Option("", "--filter", help="班次详情筛选: 未闭环 | 已闭环 | 全部(默认)"),
    pending: bool = typer.Option(False, "--pending", help="查看所有班次遗留的待办任务和异常植株"),
    limit: int = typer.Option(20, "--limit", help="列表显示条数"),
    reassign: int = typer.Option(0, "--reassign", help="遗留事项ID - 重新指派给新人(需配合 --assignee)"),
    assignee: str = typer.Option("", "--assignee", help="重新指派/完成处理的责任人姓名"),
    resolve: int = typer.Option(0, "--resolve", help="遗留事项ID - 补处理结果(需配合 --result 和 --by)"),
    result: str = typer.Option("", "--result", help="遗留事项处理结果/说明"),
    by: str = typer.Option("", "--by", help="实际处理人姓名"),
    db_path: str = typer.Option("", "--db", help="自定义数据库路径"),
):
    """交接班工作流管理"""
    storage = get_storage(db_path)
    storage.update_overdue_tasks()
    check_initialized(storage)

    # ===== 遗留事项重指派 =====
    if reassign > 0:
        if not assignee:
            console.print("[red]✗ 请配合 --assignee 指定新的责任人[/red]")
            raise typer.Exit(1)
        ok = storage.update_pending_item(reassign, new_assignee=assignee)
        if ok:
            console.print(f"[green]✓ 遗留事项 #{reassign} 已重新指派给: {assignee}[/green]")
        else:
            console.print(f"[red]✗ 遗留事项 #{reassign} 不存在[/red]")
        return

    # ===== 遗留事项补处理结果 =====
    if resolve > 0:
        if not result:
            console.print("[red]✗ 请配合 --result 提供处理结果/说明[/red]")
            raise typer.Exit(1)
        processor = by or assignee or "现场处理"
        ok = storage.update_pending_item(resolve, process_result=result, processed_by=processor,
                                         new_status="已闭环")
        if ok:
            console.print(f"[green]✓ 遗留事项 #{resolve} 已闭环: {result} (处理人: {processor})[/green]")
        else:
            console.print(f"[red]✗ 遗留事项 #{resolve} 不存在[/red]")
        return

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

        # 收集本班次进行中/待处理任务 & 非健康植株 & 当前逾期未检植株
        pending_tasks = storage.get_tasks()
        pending_tasks = [t for t in pending_tasks if t.status in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value, TaskStatus.OVERDUE.value)]
        abnormal_plants = storage.search_plants()
        abnormal_plants = [p for p in abnormal_plants if p.status != PlantStatus.HEALTHY.value]
        overdue_plants = storage.get_overdue_plants()

        task_ids = ",".join(str(t.id) for t in pending_tasks if t.id)
        plant_codes = ",".join(p.code for p in abnormal_plants)
        overdue_codes = ",".join(p.code for p in overdue_plants)

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
            overdue_plant_codes=overdue_codes,
            unfinished_reason=reason_text,
            remarks=remarks_text,
            confirmed=0,
        )
        hid = storage.create_handover(ho)
        ho.id = hid

        # 打印交接清单
        _print_shift_handover_form(shift, ho, pending_tasks, abnormal_plants, overdue_plants)
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

            # 独立遗留事项闭环统计(支持筛选)
            status_filter_val = filter.strip() if filter else ""
            closed = storage.get_shift_closed_loop_status(detail, status_filter=status_filter_val)
            if status_filter_val:
                console.print(f"[dim cyan]💡 当前筛选条件: {status_filter_val} (仅显示匹配项)[/dim cyan]")
            if closed["items_total"] > 0:
                rate = closed["closed_rate"]
                risk = "🟢" if rate >= 90 else ("🟡" if rate >= 60 else "🔴")
                kpi_title = f"📋 班次遗留事项闭环统计" + (f" [{status_filter_val}]" if status_filter_val else "")
                kpi_pan = Panel(
                    f"[bold]{risk} 事项总数: {closed['items_total']}[/bold]   "
                    f"[green]已闭环: {closed['items_closed']}[/green]   "
                    f"[yellow]待处理: {closed['items_total'] - closed['items_closed']}[/yellow]   "
                    f"[bold]闭环率: {rate:.1f}%[/bold]",
                    title=kpi_title,
                    border_style=("green" if rate >= 90 else ("yellow" if rate >= 60 else "red"))
                )
                console.print(kpi_pan)

                # 遗留事项表格
                from rich.table import Table
                tbl = Table(show_header=True, header_style="bold cyan", expand=True)
                tbl.add_column("ID", style="dim", width=6)
                tbl.add_column("类型", width=8)
                tbl.add_column("编号/任务号", style="cyan")
                tbl.add_column("标题", overflow="fold")
                tbl.add_column("原责任人")
                tbl.add_column("当前责任人", style="bold")
                tbl.add_column("状态", width=8)
                tbl.add_column("处理结果", overflow="fold")
                tbl.add_column("处理人")
                tbl.add_column("处理时间", style="dim", width=18)
                for it in closed["items"]:
                    status_color = {
                        "已闭环": "[green]",
                        "处理中": "[yellow]",
                        "重新指派": "[cyan]",
                        "待处理": "[red]",
                    }.get(it["status"], "")
                    end_c = "[/]" if status_color else ""
                    tbl.add_row(
                        str(it["id"]),
                        it["item_type"],
                        it["ref_code"] or "-",
                        it["title"] or "-",
                        it["original_assignee"] or "-",
                        it["current_assignee"] or "-",
                        f"{status_color}{it['status']}{end_c}",
                        it.get("process_result") or "-",
                        it.get("processed_by") or "-",
                        it.get("processed_at") or "-",
                    )
                console.print(tbl)
                if rate < 100:
                    console.print("[dim]💡 可用: shift --reassign <ID> --assignee <新人>   |   shift --resolve <ID> --result <处理结果> --by <处理人>[/dim]")

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


def _print_shift_handover_form(shift: Shift, ho: ShiftHandover, tasks: List[Task], plants: List[Plant],
                               overdue_plants: List = None):
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

    console.print(f"\n[bold red]【零、逾期未检植株】共 {len(overdue_plants or [])} 株 (高风险,接班优先处理)[/bold red]")
    if overdue_plants:
        from rich.table import Table as T2
        from rich import box
        t0 = T2(box=box.SIMPLE)
        t0.add_column("编号", style="cyan")
        t0.add_column("名称")
        t0.add_column("区域")
        t0.add_column("品种")
        t0.add_column("上次巡检")
        t0.add_column("下次巡检")
        t0.add_column("状态", style="bold red")
        for p in overdue_plants[:20]:
            t0.add_row(p.code, p.name, p.area or "-", p.species or "-",
                       p.last_check_date or "-", p.next_check_date or "-", p.status)
        console.print(t0)
        if len(overdue_plants) > 20:
            console.print(f"[dim]  还有 {len(overdue_plants)-20} 株省略显示, 完整清单见 [cyan]list --overdue[/cyan][/dim]")
    else:
        console.print("  ✓ (无逾期未检,班次完成度优秀)")

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
