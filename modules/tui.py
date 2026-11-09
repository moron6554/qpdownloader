import os
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Prompt, Confirm
from rich.live import Live
import questionary

from modules.config_handler import fetch_from_config, save_to_config
from modules.dictionaries import IGCSE, ALevel, OLevel
from modules.file_handler import (
    NO_COMPONENT_TYPES,
    clear_temp_files,
    compile_pdf,
    download_paper,
)

console = Console()

class CAIE_TUI:
    def __init__(self):
        self.version = "v3.1-TUI"
        self.paper_types = {
            "Question Papers": "qp",
            "Mark Schemes": "ms",
            "Grade Thresholds": "gt",
            "Examiners' Reports": "er",
        }

    def display_header(self):
        console.clear()
        console.print(Panel.fit(
            "[bold purple]CAIE Downloader[/bold purple] [fg.grey]v3.1-TUI[/fg.grey]\n"
            "[fg.grey]Past Papers · PapaCambridge & DynamicPapers[/fg.grey]",
            border_style="purple",
            padding=(1, 2)
        ))

    def get_subject_components(self, subject):
        try:
            with open("components.txt", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(f"{subject}:"):
                        return line.split(":")[1].strip()
        except Exception:
            pass
        return ""

    def run(self):
        self.display_header()

        # 1. Subject Code
        subject = questionary.text(
            "Enter Subject Code (4 digits):",
            validate=lambda text: len(text) == 4 and text.isnumeric()
        ).ask()

        if subject not in IGCSE and subject not in ALevel and subject not in OLevel:
            console.print("[bold red]Error:[/bold red] Subject code not found in dictionaries.")
            return

        # 2. Year Range
        start_year = questionary.text("Start Year (e.g. 22):").ask()
        end_year = questionary.text("End Year (e.g. 24):").ask()
        
        try:
            sy = int(start_year) % 100 if int(start_year) > 99 else int(start_year)
            ey = int(end_year) % 100 if int(end_year) > 99 else int(end_year)
        except ValueError:
            console.print("[bold red]Error:[/bold red] Years must be numeric.")
            return

        # 3. Sessions
        sessions_list = [
            ("Feb/March", "m"),
            ("May/June", "s"),
            ("Oct/Nov", "w"),
        ]
        selected_sessions_labels = questionary.checkbox(
            "Select Exam Sessions:",
            choices=[label for label, code in sessions_list]
        ).ask()

        if not selected_sessions_labels:
            console.print("[bold red]Error:[/bold red] Select at least one session.")
            return

        selected_sessions = [code for label, code in sessions_list if label in selected_sessions_labels]

        # 4. Paper Type
        paper_type_label = questionary.select(
            "Select Paper Type:",
            choices=list(self.paper_types.keys())
        ).ask()
        paper_type = self.paper_types[paper_type_label]

        # 5. Component Codes
        components = ""
        if paper_type not in NO_COMPONENT_TYPES:
            default_comps = self.get_subject_components(subject)
            components = questionary.text(
                "Enter Component Codes (comma-separated):",
                default=default_comps
            ).ask()
            
            if not components:
                console.print("[bold red]Error:[/bold red] Components are required for this paper type.")
                return
            
            comp_list = [c.strip() for c in components.split(",") if c.strip()]
            for c in comp_list:
                if not (c.isnumeric() and len(c) == 2):
                    console.print(f"[bold red]Error:[/bold red] '{c}' is not a valid 2-digit code.")
                    return
        else:
            console.print(f"[info] {paper_type_label} do not require component codes.[/info]")

        # --- Processing ---
        console.print("\n[bold purple]Preparing download queue...[/bold purple]\n")
        
        tasks = []
        for yr in range(sy, ey + 1):
            ys = f"{yr:02d}"
            for scode in selected_sessions:
                if scode == "m" and yr < 16:
                    continue
                if paper_type in NO_COMPONENT_TYPES:
                    tasks.append((subject, scode, ys, paper_type, None))
                else:
                    for c in [x.strip() for x in components.split(",") if x.strip()]:
                        tasks.append((subject, scode, ys, paper_type, c))

        if not tasks:
            console.print("[bold red]No papers matched your criteria.[/bold red]")
            return

        # Temp Folder Management
        from modules.file_handler import TEMPPATH
        if os.path.exists(TEMPPATH) and os.listdir(TEMPPATH):
            if Confirm.ask("Temp folder is not empty. Clear it before starting?"):
                clear_temp_files()

        # Download with Progress and Result Tracking
        results = [] # List of (filename, success, source)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            overall_task = progress.add_task("[cyan]Overall Progress", total=len(tasks))
            
            for sub_, scode, ys, pt_, comp in tasks:
                filename = f"{sub_}_{scode}{ys}_{pt_}" + (f"_{comp}" if comp else "") + ".pdf"
                desc = filename
                progress.update(overall_task, description=f"[cyan]Downloading {desc}...")
                
                def tui_log(msg):
                    if "✓" in msg:
                        progress.console.print(f"[green]{msg}[/green]")
                    elif "✗" in msg:
                        progress.console.print(f"[red]{msg}[/red]")
                    else:
                        progress.console.print(f"[grey]{msg}[/grey]")

                success, source = download_paper(sub_, scode, ys, pt_, comp, tui_log)
                results.append((filename, success, source))
                progress.advance(overall_task)

        # --- Pre-Compilation Report ---
        console.print("\n[bold purple]Download Summary Report[/bold purple]")
        report_table = Table(show_header=True, header_style="bold magenta")
        report_table.add_column("Filename", style="dim")
        report_table.add_column("Status", justify="center")
        report_table.add_column("Source", justify="center")

        downloaded_count = 0
        for filename, success, source in results:
            if success:
                status = "[green]✓ Found[/green]"
                src = f"[blue]{source}[/blue]"
                downloaded_count += 1
            else:
                status = "[red]✗ Missing[/red]"
                src = "[grey]N/A[/grey]"
            report_table.add_row(filename, status, src)

        console.print(report_table)

        if downloaded_count == 0:
            console.print("\n[bold red]No files were downloaded. Compilation cancelled.[/bold red]")
            return

        # Compilation Mode
        console.print("\n[bold purple]Compilation Mode[/bold purple]")
        comp_mode = questionary.select(
            "How do you want to compile your papers?",
            choices=[
                "Single Combined PDF (All in one)",
                "Yearly Bundles (One PDF per year)",
                "Interleaved (QP followed by MS for each paper)",
                "Separate PDFs (One PDF per Paper Type)"
            ],
            default="Single Combined PDF (All in one)"
        ).ask()
        
        if comp_mode == "Single Combined PDF (All in one)":
            sort_by = questionary.select(
                "Sort papers by:",
                choices=["Year", "Session", "Component"],
                default="Year"
            ).ask().lower()
            
            sort_order = questionary.select(
                "Sort order:",
                choices=["Descending (Newest First)", "Ascending (Oldest First)"],
                default="Descending (Newest First)"
            ).ask()
            order_val = "desc" if "Descending" in sort_order else "asc"
            mode_val = "year" if sort_by == "year" else "session" if sort_by == "session" else "component"
        
        elif comp_mode == "Yearly Bundles (One PDF per year)":
            sort_order = questionary.select(
                "Sort order (within each year):",
                choices=["Descending (Newest First)", "Ascending (Oldest First)"],
                default="Descending (Newest First)"
            ).ask()
            order_val = "desc" if "Descending" in sort_order else "asc"
            mode_val = "yearly"
        
        elif comp_mode == "Interleaved (QP followed by MS for each paper)":
            sort_order = questionary.select(
                "Sort order:",
                choices=["Descending (Newest First)", "Ascending (Oldest First)"],
                default="Descending (Newest First)"
            ).ask()
            order_val = "desc" if "Descending" in sort_order else "asc"
            mode_val = "interleaved"
        
        else: # Separate PDFs
            mode_val = "separate"
            sort_by = "year" 
            sort_order = "desc"
            order_val = "desc"

        # Save Location
        if mode_val == "yearly":
            last_dir = fetch_from_config("last_download_directory")
            save_dir = Prompt.ask("Enter save directory for yearly bundles", 
                                   default=last_dir)
            if not os.path.isdir(save_dir):
                try:
                    os.makedirs(save_dir, exist_ok=True)
                except Exception as e:
                    console.print(f"[bold red]Error creating directory: {e}[/bold red]")
                    return
            save_path = save_dir
            
            compiled_any = False
            yearly_ok = True
            year_range = range(ey, sy - 1, -1) if order_val == "desc" else range(sy, ey + 1)
            for yr in year_range:
                ys_str = f"{yr:02d}"
                for t_code in ["qp", "ms", "gt", "er"]:
                    # Fixed filter: match the year and type regardless of session letter
                    year_type_files = [f for f, success, src in results if success and f"{ys_str}_{t_code}" in f]
                    if not year_type_files:
                        continue
                    
                    type_filename = f"{subject}_Year_{yr}_{t_code}.pdf"
                    year_type_save_path = os.path.join(save_path, type_filename)
                    ok_single = compile_pdf(
                        year_type_save_path,
                        files_to_merge=year_type_files,
                        sort_by="component",
                        sort_order=order_val,
                        delete_blanks=fetch_from_config("remove_blank") == "Y",
                        delete_additional=fetch_from_config("remove_additional") == "Y",
                        delete_formulae=fetch_from_config("remove_formula") == "Y",
                        log_cb=lambda m: console.print(f"  [{ys_str} {t_code}] {m}")
                    )
                    if ok_single:
                        compiled_any = True
                    else:
                        yearly_ok = False
            ok = yearly_ok if compiled_any else False
        elif mode_val == "separate":
            last_dir = fetch_from_config("last_download_directory")
            save_dir = Prompt.ask("Enter save directory for separate PDFs", 
                                   default=last_dir)
            if not os.path.isdir(save_dir):
                try:
                    os.makedirs(save_dir, exist_ok=True)
                except Exception as e:
                    console.print(f"[bold red]Error creating directory: {e}[/bold red]")
                    return
            save_path = save_dir
            
            compiled_any = False
            separate_ok = True
            type_codes = ["qp", "ms", "gt", "er"]
            for t_code in type_codes:
                type_files = [f for f, success, src in results if success and (f"_{t_code}_" in f or f.endswith(f"_{t_code}.pdf"))]
                if not type_files:
                    continue
                type_filename = f"{subject}_{t_code}_Combined.pdf"
                type_save_path = os.path.join(save_path, type_filename)
                ok_single = compile_pdf(
                    type_save_path,
                    files_to_merge=type_files,
                    sort_by="year",
                    sort_order=order_val,
                    delete_blanks=fetch_from_config("remove_blank") == "Y",
                    delete_additional=fetch_from_config("remove_additional") == "Y",
                    delete_formulae=fetch_from_config("remove_formula") == "Y",
                    log_cb=lambda m: console.print(f"  [{t_code}] {m}")
                )
                if ok_single:
                    compiled_any = True
                else:
                    separate_ok = False
            ok = separate_ok if compiled_any else False
        else:
            pt_labels = {v: k for k, v in self.paper_types.items()}
            default_name = f"{subject} {pt_labels.get(paper_type, 'Papers')} 20{sy:02d}-20{ey:02d}.pdf"
            last_dir = fetch_from_config("last_download_directory")
            save_path = Prompt.ask("Enter save path (or press Enter for default)", 
                                    default=os.path.join(last_dir, default_name))
            
            if os.path.isdir(save_path):
                save_path = os.path.join(save_path, default_name)

            from modules.file_handler import TEMPPATH
            files_to_merge = [f for f in os.listdir(TEMPPATH) if f.lower().endswith(".pdf") and f != ".gitignore"]
            ok = compile_pdf(
                save_path,
                files_to_merge=files_to_merge,
                sort_by=mode_val,
                sort_order=order_val,
                delete_blanks=fetch_from_config("remove_blank") == "Y",
                delete_additional=fetch_from_config("remove_additional") == "Y",
                delete_formulae=fetch_from_config("remove_formula") == "Y",
                log_cb=lambda m: console.print(f"  {m}")
            )


        if ok:
            last_dir = save_path if mode_val == "yearly" else os.path.dirname(save_path)
            save_to_config("last_download_directory", last_dir)
            console.print(f"\n[bold green]✓ Successfully saved to: {save_path}[/bold green]")
            if Confirm.ask("Keep individual PDF files in temp folder?"):
                console.print("[info] Files preserved.[/info]")
            else:
                clear_temp_files()
                console.print("[info] Temp files cleared.[/info]")
        else:
            console.print("\n[bold red]Compilation failed. No pages were found.[/bold red]")

if __name__ == "__main__":
    app = CAIE_TUI()
    app.run()
