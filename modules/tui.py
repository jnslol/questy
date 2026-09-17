import datetime
import threading
import asyncio

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, TabPane, TabbedContent

from .accounts import add_account, load_accounts, remove_account
from .api import ApiError, Client
from .filters import STATUS_FILTERS, matches_row
from .quest import SUPPORTED_TASK_TYPES, parse_quests_response
from .runner import run_account


TYPE_FILTER_KEYS = tuple(SUPPORTED_TASK_TYPES)


def mask_token(token):
    if len(token) <= 12:
        return "***"
    return token[:6] + "..." + token[-4:]


def filter_quest_rows(rows, status=("actionable", "completed"), types=()):
    return [row for row in rows if matches_row(row, status, types)]


class AccountDialog(ModalScreen):
    CSS = """
    AccountDialog { align: center middle; }
    #account-dialog { width: 70; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    #account-dialog Input { margin: 1 0; }
    #account-dialog-actions { height: 3; align: right middle; }
    """

    def compose(self):
        with Container(id="account-dialog"):
            yield Label("Add account")
            yield Input(placeholder="Authorization token", id="account-token", password=True)
            yield Input(placeholder="Label (optional)", id="account-label")
            with Horizontal(id="account-dialog-actions"):
                yield Button("Cancel", id="account-cancel")
                yield Button("Add", id="account-submit", variant="primary")

    @on(Button.Pressed, "#account-submit")
    def submit(self):
        token = self.query_one("#account-token", Input).value.strip()
        label = self.query_one("#account-label", Input).value.strip() or None
        if not token:
            return
        self.dismiss((token, label))

    @on(Button.Pressed, "#account-cancel")
    def cancel(self):
        self.dismiss(None)


class FilterDialog(ModalScreen):
    CSS = """
    FilterDialog { align: center middle; }
    #filter-dialog { width: 90; height: auto; max-height: 80%; padding: 1 2; border: thick $accent; background: $surface; }
    #filter-statuses { height: 3; }
    #filter-types { height: 6; }
    #filter-dialog Button { margin: 0 1; }
    #filter-dialog-actions { height: 3; align: right middle; }
    """

    def __init__(self, status, types):
        super().__init__()
        self.status = {status} if isinstance(status, str) else set(status)
        self.types = set(types)

    def compose(self):
        with Container(id="filter-dialog"):
            yield Label("Quest filters")
            yield Label("Status")
            with Horizontal(id="filter-statuses"):
                for status in STATUS_FILTERS:
                    yield Button(status.title(), id=f"dialog-status-{status}")
            yield Label("Task types")
            with Vertical(id="filter-types"):
                with Horizontal():
                    for task_type in TYPE_FILTER_KEYS[:3]:
                        yield Button(task_type, id=f"dialog-type-{task_type}", classes="dialog-task-type")
                with Horizontal():
                    for task_type in TYPE_FILTER_KEYS[3:]:
                        yield Button(task_type, id=f"dialog-type-{task_type}", classes="dialog-task-type")
            with Horizontal(id="filter-dialog-actions"):
                yield Button("Clear types", id="dialog-clear-types")
                yield Button("Cancel", id="dialog-cancel")
                yield Button("Apply", id="dialog-apply", variant="primary")

    def on_mount(self):
        self._refresh_buttons()

    def _refresh_buttons(self):
        for status in STATUS_FILTERS:
            self.query_one(f"#dialog-status-{status}", Button).variant = (
                "primary" if status in self.status else "default"
            )
        for task_type in TYPE_FILTER_KEYS:
            self.query_one(f"#dialog-type-{task_type}", Button).variant = (
                "primary" if task_type in self.types else "default"
            )

    @on(Button.Pressed, "#dialog-apply")
    def apply(self):
        self.dismiss((self.status, set(self.types)))

    @on(Button.Pressed, "#dialog-cancel")
    def cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, "#dialog-clear-types")
    def clear_types(self):
        self.types.clear()
        self._refresh_buttons()

    @on(Button.Pressed, "#dialog-status-all, #dialog-status-actionable, #dialog-status-completed, #dialog-status-expired, #dialog-status-unsupported")
    def select_status(self, event):
        status = event.button.id.removeprefix("dialog-status-")
        if status == "all":
            self.status = {"all"}
        else:
            self.status.discard("all")
            if status in self.status:
                self.status.remove(status)
            else:
                self.status.add(status)
        self._refresh_buttons()

    @on(Button.Pressed, ".dialog-task-type")
    def select_type(self, event):
        task_type = event.button.id.removeprefix("dialog-type-")
        if task_type in self.types:
            self.types.remove(task_type)
        else:
            self.types.add(task_type)
        self._refresh_buttons()


class QuestyTui(App):
    TITLE = "Questy"
    BINDINGS = [
        Binding("l", "load_quests", "Load quests"),
        Binding("r", "run", "Run"),
        Binding("c", "cancel_run", "Cancel"),
        Binding("d", "delete_account", "Delete account"),
        Binding("q", "quit", "Quit", priority=True),
    ]

    CSS = """
    #accounts-toolbar, #quests-toolbar { height: 3; align: right bottom; }
    #accounts { height: 8; border: solid $accent; }
    #account-tabs { height: 1fr; border: solid $accent; }
    #log { height: 10; border: solid $accent; }
    #accounts-toolbar Button, #quests-toolbar Button { margin: 0 1; }
    """

    def __init__(self, accounts_file="accounts.json"):
        super().__init__()
        self.accounts_file = accounts_file
        self.cancel_event = threading.Event()
        self.runners = []
        self.quest_rows = []
        self.quest_state = {}
        self.live_state = {}
        self.account_tab_ids = {}
        self._tabs_rebuild_running = False
        self._tabs_rebuild_pending = False
        self.status_filter = {"actionable", "completed"}
        self.type_filter = set()

    def compose(self):
        yield Header()
        with Horizontal(id="accounts-toolbar"):
            yield Button("Add account", id="add-account", variant="primary")
        yield DataTable(id="accounts", cursor_type="row")
        with Horizontal(id="quests-toolbar"):
            yield Button("Quest settings", id="quest-settings", variant="primary")
        with TabbedContent(id="account-tabs"):
            yield TabPane("Loading...", id="account-empty")
        yield RichLog(id="log", markup=False, wrap=True)
        yield Footer()

    def on_mount(self):
        accounts_table = self.query_one("#accounts", DataTable)
        accounts_table.add_columns("label", "user", "token", "status")
        self.refresh_accounts()
        self._request_account_tab_rebuild()
        self.set_interval(1, self._tick_live_progress)
        self.call_after_refresh(self.action_load_quests)

    @property
    def accounts(self):
        return load_accounts(self.accounts_file)

    def _label(self, account, index):
        return account.get("label") or f"account-{index}"

    def refresh_accounts(self, statuses=None):
        statuses = statuses or {}
        table = self.query_one("#accounts", DataTable)
        table.clear()
        for index, account in enumerate(self.accounts):
            label = self._label(account, index)
            user = account.get("display_name") or account.get("username") or "unknown"
            table.add_row(label, user, mask_token(account.get("token", "")), statuses.get(label, ""), key=label)

    def log_line(self, message):
        self.query_one("#log", RichLog).write(str(message).rstrip())

    def _account_percent(self, account):
        values = [
            state for state in self.quest_state.values()
            if state["account"] == account
            and state["status"] in {"actionable", "active", "running", "completed"}
            and state["target"] > 0
        ]
        if not values:
            return 0
        total = sum(state["target"] for state in values)
        current = sum(min(state["progress"], state["target"]) for state in values)
        return round(current / total * 100)

    def _quest_row(self, state):
        percent = round(min(state["progress"], state["target"]) / state["target"] * 100) if state["target"] else 0
        return (state["name"], f"{state['progress']:.0f}/{state['target']:.0f}s - {percent}%", state["status"])

    def _apply_quest_filter(self):
        if not self.account_tab_ids:
            return
        grouped = {}
        for key, state in self.quest_state.items():
            row = (state["account"], state["name"], state["task"], f"{state['progress']:.0f}/{state['target']:.0f}", state["status"])
            if matches_row(row, self.status_filter, self.type_filter):
                grouped.setdefault(state["account"], []).append((key, state))
        for account, tab_data in self.account_tab_ids.items():
            table = self.query_one(f"#{tab_data[1]}", DataTable)
            table.clear()
            for _, state in grouped.get(account, []):
                table.add_row(*self._quest_row(state))
            self.query_one(f"#{tab_data[2]}", Label).update(
                f"Completion: {self._account_percent(account)}%"
            )

    def _request_account_tab_rebuild(self):
        if self._tabs_rebuild_running:
            self._tabs_rebuild_pending = True
            return
        self.run_worker(self._rebuild_account_tabs())

    async def _rebuild_account_tabs(self):
        if self._tabs_rebuild_running:
            self._tabs_rebuild_pending = True
            return
        self._tabs_rebuild_running = True
        try:
            await self._rebuild_account_tabs_impl()
        finally:
            self._tabs_rebuild_running = False
            if self._tabs_rebuild_pending:
                self._tabs_rebuild_pending = False
                self._request_account_tab_rebuild()

    async def _rebuild_account_tabs_impl(self):
        tabs = self.query_one("#account-tabs", TabbedContent)
        for tab_id, _, _ in self.account_tab_ids.values():
            await tabs.remove_pane(tab_id)
        try:
            await tabs.remove_pane("account-empty")
        except Exception:
            pass
        self.account_tab_ids = {}
        accounts = sorted({state["account"] for state in self.quest_state.values()})
        if not accounts:
            await tabs.add_pane(TabPane("No accounts", id="account-empty"))
            return
        for index, account in enumerate(accounts):
            tab_id = f"account-tab-{index}"
            table_id = f"account-table-{index}"
            summary_id = f"account-summary-{index}"
            table = DataTable(id=table_id, cursor_type="row")
            table.add_columns("quest", "progress", "status")
            await tabs.add_pane(
                TabPane(
                    account,
                    Label(f"Completion: {self._account_percent(account)}%", id=summary_id),
                    table,
                    id=tab_id,
                )
            )
            self.account_tab_ids[account] = (tab_id, table_id, summary_id)
        self._apply_quest_filter()

    def _set_quest_data(self, states):
        self.quest_state = states
        self.quest_rows = [
            (state["account"], state["name"], state["task"], f"{state['progress']:.0f}/{state['target']:.0f}", state["status"])
            for state in states.values()
        ]
        self._request_account_tab_rebuild()

    def _event_display_status(self, status):
        if status in {"running", "enrolling", "error", "active"}:
            return "actionable"
        if status == "unavailable":
            return "unsupported"
        return status or "actionable"

    def _handle_runner_event(self, **event):
        key = (event["account"], event["quest_name"])
        previous = self.quest_state.get(key)
        if previous is None:
            return
        status = self._event_display_status(event.get("status"))
        progress = float(event["progress"]) if event.get("progress") is not None else previous["progress"]
        target = float(event.get("target") or previous["target"])
        updated = {**previous, "status": status, "progress": progress, "target": target, "progress_at": event.get("progress_at")}
        self.quest_state[key] = updated
        self.live_state[key] = {
            "progress": progress,
            "display_progress": progress,
            "target": target,
            "status": status,
            "progress_at": event.get("progress_at"),
        }
        self._apply_quest_filter()
        row = (updated["account"], updated["name"], updated["task"], f"{progress:.0f}/{target:.0f}", status)
        if matches_row(row, self.status_filter, self.type_filter):
            if event.get("message"):
                self.log_line(f"[{event['account']}] {event['quest_name']}: {event['message']}")

    def _tick_live_progress(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        changed = False
        for key, state in self.live_state.items():
            if state["status"] not in {"actionable", "running", "active"} or not state.get("progress_at"):
                continue
            try:
                updated = datetime.datetime.fromisoformat(str(state["progress_at"]).replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=datetime.timezone.utc)
                progress = min(state["target"], state["progress"] + max(0, (now - updated).total_seconds()))
            except (TypeError, ValueError):
                continue
            if progress > state["display_progress"]:
                state["display_progress"] = progress
                self.quest_state[key]["progress"] = progress
                changed = True
        if changed:
            self._apply_quest_filter()

    def _thread_event(self, **event):
        self.call_from_thread(self._handle_runner_event, **event)

    def _thread_global_log(self, message):
        if any(value in message for value in ("account failed", "unexpected failure", "cancelled")):
            self.call_from_thread(self.log_line, message)

    def _selected_account(self):
        table = self.query_one("#accounts", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    @on(Button.Pressed, "#add-account")
    def add_account_dialog(self):
        self.push_screen(AccountDialog(), self._account_dialog_result)

    def _account_dialog_result(self, result):
        if result:
            token, label = result
            def validate():
                try:
                    user = Client(token).get_me()
                    username = user.get("username")
                    display_name = user.get("global_name") or username
                    created = add_account(
                        token,
                        label,
                        username,
                        display_name,
                        self.accounts_file,
                    )
                    message = f"account {'added' if created else 'updated'}: {display_name or username or user.get('id')}"
                except ApiError as exc:
                    message = f"token validation failed: {exc}"
                self.call_from_thread(self.log_line, message)
                self.call_from_thread(self.refresh_accounts)
                self.call_from_thread(self.action_load_quests)

            threading.Thread(target=validate, daemon=True).start()

    @on(Button.Pressed, "#quest-settings")
    def quest_settings_dialog(self):
        self.push_screen(FilterDialog(self.status_filter, self.type_filter), self._filter_dialog_result)

    def _filter_dialog_result(self, result):
        if result:
            self.status_filter, self.type_filter = result
            self._apply_quest_filter()

    def action_delete_account(self):
        label = self._selected_account()
        if label and remove_account(label, self.accounts_file):
            self.refresh_accounts()
            self.action_load_quests()

    def action_load_quests(self):
        accounts = self.accounts
        if not accounts:
            self.quest_state = {}
            self.live_state = {}
            self._request_account_tab_rebuild()
            self.log_line("no accounts")
            return

        def worker():
            states = {}
            for index, account in enumerate(accounts):
                label = self._label(account, index)
                try:
                    quests, excluded, _, _ = parse_quests_response(Client(account["token"]).get_quests())
                    for quest in quests:
                        key = (label, quest.name)
                        states[key] = {
                            "account": label,
                            "name": quest.name,
                            "task": quest.task_type() or "-",
                            "progress": quest.local_progress(),
                            "target": quest.target(),
                            "status": "completed" if quest.is_completed() else "expired" if quest.is_expired() else "actionable" if quest.is_actionable() else "unsupported",
                            "progress_at": quest.progress_updated_at(),
                        }
                    self.call_from_thread(self.log_line, f"{label}: {len(quests)} quests loaded ({len(excluded)} excluded)")
                except ApiError as exc:
                    self.call_from_thread(self.log_line, f"{label}: FAILED ({exc})")
            self.call_from_thread(self._set_quest_data, states)

        threading.Thread(target=worker, daemon=True).start()

    def _start_run(self):
        accounts = self.accounts
        if not accounts:
            self.log_line("no accounts")
            return
        self.cancel_event = threading.Event()

        def worker(client, label):
            run_account(
                client,
                label,
                self._thread_global_log,
                self.cancel_event,
                event=self._thread_event,
                enable_rpc=True,
                enable_gateway=True,
            )

        for index, account in enumerate(accounts):
            label = self._label(account, index)
            threading.Thread(target=worker, args=(Client(account["token"]), label), daemon=True).start()

    def action_run(self):
        self._start_run()

    def action_cancel_run(self):
        self.cancel_event.set()

    def action_quit(self):
        self.cancel_event.set()
        self.exit()
