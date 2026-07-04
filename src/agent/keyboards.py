"""
Telegram bot keyboard/button helpers.
All text in Vietnamese.

Hierarchy: Dự Án (OmniProject) > Task (OmniTask) > Sub-Task (SubTask)
Review flow: in_progress → in_review → done | in_progress (rejected)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


# ── Confirm/Edit/Cancel after LLM parses ─────────────────────────────────────

def confirm_task_keyboard(batch_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown after LLM parses a task — confirm, edit, or cancel."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác nhận & Tạo task", callback_data=f"confirm:{batch_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Chỉnh sửa", callback_data=f"edit:{batch_id}"),
            InlineKeyboardButton("❌ Hủy", callback_data=f"cancel:{batch_id}"),
        ],
    ])


# ── Project selection ─────────────────────────────────────────────────────────

def project_select_keyboard(projects: list, batch_id: str) -> InlineKeyboardMarkup:
    """Show list of projects to assign a new task to."""
    rows = []
    for p in projects[:8]:  # max 8 nút để gọn
        rows.append([
            InlineKeyboardButton(
                f"📁 {p['name']}",
                callback_data=f"proj_select:{batch_id}:{p['id']}"
            )
        ])
    rows.append([
        InlineKeyboardButton("➕ Tạo dự án mới", callback_data=f"proj_new:{batch_id}"),
        InlineKeyboardButton("⏭ Bỏ qua", callback_data=f"proj_skip:{batch_id}"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Task status update ────────────────────────────────────────────────────────

def task_status_keyboard(task_plane_id: str) -> InlineKeyboardMarkup:
    """Keyboard to quickly update a task's status."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 In Progress", callback_data=f"status:{task_plane_id}:In Progress"),
            InlineKeyboardButton("👀 In Review", callback_data=f"status:{task_plane_id}:In Review"),
        ],
        [
            InlineKeyboardButton("✅ Done", callback_data=f"status:{task_plane_id}:Done"),
            InlineKeyboardButton("❌ Cancelled", callback_data=f"status:{task_plane_id}:Cancelled"),
        ],
    ])


# ── Done → collect output ─────────────────────────────────────────────────────

def done_collect_output_keyboard(subtask_id: str) -> InlineKeyboardMarkup:
    """After user says 'done', ask if they have output to share."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📎 Gửi link/output ngay", callback_data=f"done_has_output:{subtask_id}"),
        ],
        [
            InlineKeyboardButton("✅ Không có output, gửi review luôn", callback_data=f"done_no_output:{subtask_id}"),
        ],
        [
            InlineKeyboardButton("❌ Hủy", callback_data=f"done_cancel:{subtask_id}"),
        ],
    ])


# ── Admin review decision ─────────────────────────────────────────────────────

def review_decision_keyboard(subtask_id: str) -> InlineKeyboardMarkup:
    """Admin keyboard to approve or reject a task in review."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Duyệt — Task Done!", callback_data=f"review_approve:{subtask_id}"),
        ],
        [
            InlineKeyboardButton("↩️ Trả lại — Cần làm lại", callback_data=f"review_reject:{subtask_id}"),
        ],
    ])


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard with quick-access buttons."""
    return ReplyKeyboardMarkup(
        [
            ["📋 Xem tasks", "👤 Task của tôi"],
            ["➕ Tạo Task mới", "📁 Dự án"],
            ["📊 Báo cáo ngay", "👥 Team"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
