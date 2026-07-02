"""
Google Sheets integration — syncs task data to a Google Spreadsheet dashboard.
Uses a service account for headless authentication (no browser needed).

NOTE: gspread is a synchronous library. All methods that call gspread are
wrapped with asyncio.to_thread() so they run in a thread pool and never
block the Telegram bot's async event loop.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from src.config import settings
from src.models.task import CreatedTask, PlaneWorkItem

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# ── Sheet & column definitions ─────────────────────────────────────────────────

TASKS_SHEET_NAME = "Tasks"
LOG_SHEET_NAME = "Daily Log"

TASKS_HEADERS = [
    "Task ID", "Tiêu đề", "Người phụ trách", "Độ ưu tiên",
    "Trạng thái", "Deadline", "Ước tính (giờ)", "Deliverables",
    "Link Plane", "Ngày tạo", "Cập nhật lần cuối",
]

LOG_HEADERS = [
    "Thời gian", "Loại", "Nội dung", "Tasks mở", "Tasks quá hạn",
]

PRIORITY_LABEL = {
    "urgent": "🔴 Khẩn cấp",
    "high": "🟠 Cao",
    "medium": "🟡 Trung bình",
    "low": "🟢 Thấp",
    "none": "⚪ Không xác định",
}


class SheetsClient:
    """Manages Google Sheets sync for task dashboard.

    All public methods are async. Blocking gspread operations are
    delegated to a thread pool via asyncio.to_thread().
    """

    def __init__(self):
        creds = Credentials.from_service_account_file(
            settings.google_service_account_file,
            scopes=SCOPES,
        )
        self._gc = gspread.authorize(creds)
        self._sheet_id = settings.google_sheet_id
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        logger.info("SheetsClient initialized")

    # ── Internal sync helpers (called inside thread pool) ──────────────────────

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None:
            self._spreadsheet = self._gc.open_by_key(self._sheet_id)
        return self._spreadsheet

    def _get_or_create_worksheet(self, name: str, headers: list[str]) -> gspread.Worksheet:
        """Get or create a worksheet with headers. Sync — run in thread pool."""
        ss = self._get_spreadsheet()
        try:
            ws = ss.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers, value_input_option="USER_ENTERED")
            # Bold header + freeze
            ws.format("A1:K1", {
                "textFormat": {"bold": True, "fontSize": 11},
                "backgroundColor": {"red": 0.2, "green": 0.35, "blue": 0.65},
                "horizontalAlignment": "CENTER",
            })
            try:
                ss.batch_update({"requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": ws.id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                }]})
            except Exception as e:
                logger.warning(f"Could not freeze header row: {e}")
            logger.info(f"Created worksheet: {name}")
        return ws

    def _sync_upsert_task(self, task: CreatedTask) -> None:
        """Sync: insert or update a task row. Run via asyncio.to_thread()."""
        ws = self._get_or_create_worksheet(TASKS_SHEET_NAME, TASKS_HEADERS)
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        deliverables_str = " | ".join(task.deliverables) if task.deliverables else ""
        priority_str = PRIORITY_LABEL.get(task.priority.value, task.priority.value)

        row_data = [
            f"#{task.sequence_id}",
            task.name,
            task.assignee_name,
            priority_str,
            task.status.value,
            task.deadline or "",
            str(task.estimated_hours),
            deliverables_str,
            task.plane_url,
            task.created_at.strftime("%d/%m/%Y %H:%M"),
            now_str,
        ]

        task_id_col = ws.col_values(1)
        search_id = f"#{task.sequence_id}"

        if search_id in task_id_col:
            row_idx = task_id_col.index(search_id) + 1
            ws.update(f"A{row_idx}:K{row_idx}", [row_data], value_input_option="USER_ENTERED")
            logger.info(f"Updated sheet row for task #{task.sequence_id}")
        else:
            ws.append_row(row_data, value_input_option="USER_ENTERED")
            logger.info(f"Appended new row for task #{task.sequence_id}")

    def _sync_sync_tasks_from_plane(self, work_items: list[PlaneWorkItem]) -> None:
        """Sync: full upsert of all Plane work items. Run via asyncio.to_thread()."""
        ws = self._get_or_create_worksheet(TASKS_SHEET_NAME, TASKS_HEADERS)
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

        existing_data = ws.get_all_values()
        id_to_row: dict[str, int] = {}
        for i, row in enumerate(existing_data[1:], start=2):
            if row and row[0]:
                id_to_row[row[0]] = i

        rows_to_append = []
        for item in work_items:
            search_id = f"#{item.sequence_id}"
            priority_str = PRIORITY_LABEL.get(item.priority.lower(), item.priority)
            plane_url = (
                f"https://app.plane.so/{settings.plane_workspace_slug}"
                f"/projects/{settings.plane_project_id}/issues/{item.id}/"
            )

            if search_id in id_to_row:
                row_idx = id_to_row[search_id]
                ws.update(
                    f"C{row_idx}:F{row_idx}",
                    [[item.assignee_name or "", priority_str, item.state_name, item.target_date or ""]],
                    value_input_option="USER_ENTERED",
                )
                ws.update_acell(f"K{row_idx}", now_str)
            else:
                rows_to_append.append([
                    search_id, item.name, item.assignee_name or "",
                    priority_str, item.state_name, item.target_date or "",
                    "", "", plane_url, now_str, now_str,
                ])

        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")

        logger.info(
            f"Sheet sync: {len(rows_to_append)} new, "
            f"{len(work_items) - len(rows_to_append)} updated"
        )

    def _sync_log_reminder(
        self, reminder_type: str, content: str, open_count: int, overdue_count: int
    ) -> None:
        """Sync: append log entry. Run via asyncio.to_thread()."""
        ws = self._get_or_create_worksheet(LOG_SHEET_NAME, LOG_HEADERS)
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        ws.append_row(
            [now_str, reminder_type, content[:500], str(open_count), str(overdue_count)],
            value_input_option="USER_ENTERED",
        )
        logger.debug(f"Logged reminder: {reminder_type}")

    # ── Public async API ───────────────────────────────────────────────────────

    async def upsert_task(self, task: CreatedTask) -> None:
        """Async: insert or update a task row in the Tasks sheet."""
        await asyncio.to_thread(self._sync_upsert_task, task)

    async def sync_tasks_from_plane(self, work_items: list[PlaneWorkItem]) -> None:
        """Async: full sync of all Plane work items to Tasks sheet."""
        await asyncio.to_thread(self._sync_sync_tasks_from_plane, work_items)

    async def log_reminder(
        self,
        reminder_type: str,
        content: str,
        open_count: int,
        overdue_count: int,
    ) -> None:
        """Async: append a reminder log entry to Daily Log sheet."""
        await asyncio.to_thread(
            self._sync_log_reminder, reminder_type, content, open_count, overdue_count
        )

    def apply_conditional_formatting(self) -> None:
        """Apply colour-coding to Done rows. Sync (call at startup only)."""
        ss = self._get_spreadsheet()
        try:
            ws = ss.worksheet(TASKS_SHEET_NAME)
        except gspread.WorksheetNotFound:
            return
        requests = [{
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": ws.id, "startRowIndex": 1}],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": "Done"}],
                        },
                        "format": {
                            "backgroundColor": {
                                "red": 0.72, "green": 0.88, "blue": 0.72,
                            }
                        },
                    },
                },
                "index": 0,
            }
        }]
        ss.batch_update({"requests": requests})
        logger.info("Applied conditional formatting to Tasks sheet")


# ── Singleton ──────────────────────────────────────────────────────────────────
_sheets_client: SheetsClient | None = None


def get_sheets_client() -> SheetsClient:
    global _sheets_client
    if _sheets_client is None:
        _sheets_client = SheetsClient()
    return _sheets_client
