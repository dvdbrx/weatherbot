import time
from datetime import datetime
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.align import Align
from rich.text import Text

from bot_v2 import load_state, load_all_markets

def generate_dashboard() -> Layout:
    state = load_state()
    markets = load_all_markets()
    
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    closed_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "closed"]
    
    bal = state.get("balance", 0.0)
    start = state.get("starting_balance", 10000.0)
    wins_count = state.get("wins", 0)
    losses_count = state.get("losses", 0)
    total = wins_count + losses_count
    ret_pct = (bal - start) / start * 100 if start else 0
    
    # Calculate Unrealized PnL
    total_unrealized = 0.0
    for m in open_pos:
        pos = m["position"]
        current_price = pos.get("entry_price", 0.0)
        for o in m.get("all_outcomes", []):
            if o["market_id"] == pos["market_id"]:
                current_price = o.get("price", current_price)
                break
        pnl = (current_price - pos["entry_price"]) * pos.get("shares", 0.0)
        total_unrealized += pnl
    
    unrealized_str = f"[green]+${total_unrealized:.2f}[/green]" if total_unrealized >= 0 else f"[red]-${abs(total_unrealized):.2f}[/red]"
    
    # Create Overview Panel
    overview_text = Text()
    overview_text.append(f"Balance: ${bal:,.2f} ", style="bold cyan")
    overview_text.append(f"({'+' if ret_pct>=0 else ''}{ret_pct:.1f}%) | ", style="green" if ret_pct>=0 else "red")
    overview_text.append(f"Unrealized PnL: ", style="bold")
    overview_text.append_text(Text.from_markup(unrealized_str))
    overview_text.append(f" | Trades: {total} (W: {wins_count} / L: {losses_count}) | ", style="bold")
    if total > 0:
        overview_text.append(f"Win Rate: {wins_count/total:.0%}", style="bold magenta")
    else:
        overview_text.append("No trades yet", style="dim")
        
    overview_panel = Panel(Align.center(overview_text), title="[bold]Overview[/bold]", border_style="blue")
    
    # Create Open Positions Table
    open_table = Table(expand=True, title="Open Positions", show_header=True, header_style="bold yellow")
    open_table.add_column("City/Date", style="cyan")
    open_table.add_column("Bucket", style="magenta")
    open_table.add_column("Entry", justify="right")
    open_table.add_column("Current", justify="right")
    open_table.add_column("PnL", justify="right")
    
    # Sort open_pos by unrealized PnL
    def get_pnl(m):
        pos = m["position"]
        c = pos.get("entry_price", 0.0)
        for o in m.get("all_outcomes", []):
            if o["market_id"] == pos["market_id"]:
                c = o.get("price", c)
                break
        return (c - pos["entry_price"]) * pos.get("shares", 0.0)

    for m in sorted(open_pos, key=get_pnl, reverse=True):
        pos = m["position"]
        unit_sym = "F" if m.get("unit") == "F" else "C"
        label = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit_sym}"
        current_price = pos.get("entry_price", 0.0)
        for o in m.get("all_outcomes", []):
            if o["market_id"] == pos["market_id"]:
                current_price = o.get("price", current_price)
                break
        pnl = (current_price - pos["entry_price"]) * pos.get("shares", 0.0)
        pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.2f}[/red]"
        open_table.add_row(
            f"{m.get('city_name')} {m.get('date')}",
            label,
            f"${pos.get('entry_price', 0):.3f}",
            f"${current_price:.3f}",
            pnl_str
        )
        
    if not open_pos:
        open_table.add_row("No open positions", "", "", "", "")
        
    # Create Closed Positions Table
    closed_table = Table(expand=True, title="Recent Closed Positions", show_header=True, header_style="bold cyan")
    closed_table.add_column("City/Date", style="cyan")
    closed_table.add_column("Result", justify="center")
    closed_table.add_column("PnL", justify="right")
    
    for m in sorted(closed_pos, key=lambda x: x["position"].get("closed_at") or 0, reverse=True)[:15]:
        pos = m["position"]
        pnl = pos.get("pnl", 0.0)
        if pnl is None:
            pnl = 0.0
        pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl > 0 else f"[red]-${abs(pnl):.2f}[/red]"
        res = "WON" if pnl > 0 else ("LOST" if pnl < 0 else "FLAT")
        res_color = "bold green" if pnl > 0 else ("bold red" if pnl < 0 else "dim")
        closed_table.add_row(
            f"{m.get('city_name')} {m.get('date')}",
            f"[{res_color}]{res}[/]",
            pnl_str
        )
        
    if not closed_pos:
        closed_table.add_row("No closed positions", "", "")
        
    # Build Layout
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="overview", size=3),
        Layout(name="tables")
    )
    layout["tables"].split_row(
        Layout(name="open", ratio=6),
        Layout(name="closed", ratio=4)
    )
    
    layout["header"].update(Panel(Align.center(f"[bold white]WeatherBot Live Dashboard[/bold white] | Last Update: {datetime.now().strftime('%H:%M:%S')}"), style="on blue"))
    layout["overview"].update(overview_panel)
    layout["open"].update(Panel(open_table, border_style="yellow"))
    layout["closed"].update(Panel(closed_table, border_style="cyan"))
    
    return layout

if __name__ == "__main__":
    try:
        with Live(generate_dashboard(), refresh_per_second=2) as live:
            while True:
                time.sleep(2)
                live.update(generate_dashboard())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
