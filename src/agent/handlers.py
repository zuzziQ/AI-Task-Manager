"""
Telegram bot command and message handlers.
All user-facing text in Vietnamese.

Hierarchy: Dự Án (OmniProject) > Task (OmniTask) > Sub-Task (SubTask)
Review flow: in_progress → in_review → done | in_progress (rejected)
"""
import asyncio
import logging
import uuid
from datetime import datetime

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ContextTypes

from src.agent.formatters import (
    format_task_breakdown,
    format_created_tasks_summary,
    format_task_list,
    format_team_overview,
    escape_md,
)
from src.agent.keyboards import (
    confirm_task_keyboard,
    task_status_keyboard,
    main_menu_keyboard,
    project_select_keyboard,
    done_collect_output_keyboard,
    review_decision_keyboard,
)
from src.ai.task_parser import get_parser
from src.config import settings
from src.integrations.plane_client import get_plane_client
from src.integrations.sheets_client import get_sheets_client
from src.models.task import TaskBreakdown, CreatedTask
from src.team_config import TEAM_MEMBERS

logger = logging.getLogger(__name__)

# ── In-memory stores ──────────────────────────────────────────────────────────
# batch_id → (order_text, TaskBreakdown)
_pending_batches: dict[str, tuple[str, TaskBreakdown]] = {}

# batch_id → project_id (sau khi user chọn project)
_pending_project: dict[str, str | None] = {}

# subtask_id → { assignee_chat_id, title, description, checkin_time, ... }
_pending_reviews: dict[str, dict] = {}

# subtask_id chờ user gửi output text
_waiting_output: dict[int, str] = {}  # chat_id → subtask_id


def _is_allowed(chat_id: int) -> bool:
    """Check if the chat/group is in the allowed list."""
    allowed = settings.allowed_chat_ids
    if not allowed:
        return True
    return chat_id in allowed


# ── /start ─────────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return

    username = update.effective_user.username or ""
    if username:
        from src.team_config import update_member_chat_id
        await update_member_chat_id(username, update.effective_chat.id)
        await _checkin_user(update, username)

    msg = (
        "👋 *Chào mừng đến với Task Manager Bot\\!*\n\n"
        "Tôi có thể giúp bạn:\n"
        "• 📁 Quản lý *Dự Án → Task → Sub\\-Task*\n"
        "• 📝 Nhận yêu cầu task bằng ngôn ngữ tự nhiên\n"
        "• 🤖 Tự động phân rã và assign cho team\n"
        "• ✅ Luồng duyệt: Done → Review → Approve/Reject\n"
        "• 📊 Sync lên Plane \\& Google Sheets\n\n"
        "*Cách sử dụng:*\n"
        "Chỉ cần gửi mô tả task, hoặc dùng /newtask\n\n"
        "Ví dụ: _\\\"Làm trang login cho web, cần UI đẹp và API, deadline cuối tuần\\\"_\n\n"
        "Gõ /help để xem tất cả lệnh\\."
    )
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=main_menu_keyboard(),
    )


# ── /help ──────────────────────────────────────────────────────────────────────

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return

    msg = (
        "📚 *Danh sách lệnh*\n\n"
        "/newtask `<mô tả>` — Tạo task mới từ mô tả\n"
        "/projects — Xem danh sách dự án\n"
        "/tasks — Xem tất cả tasks đang mở\n"
        "/mytasks — Tasks được assign cho bạn\n"
        "/done `<tên task>` — Báo hoàn thành task\n"
        "/team — Tổng quan workload team\n"
        "/remind — Báo cáo tiến độ ngay\n"
        "/sync — Sync dữ liệu lên Google Sheets\n"
        "/help — Hiển thị menu này\n\n"
        "💡 _Hoặc chỉ cần nhắn tin mô tả task, bot sẽ tự hiểu\\!_"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)


# ── /projects ─────────────────────────────────────────────────────────────────

async def projects_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all OmniProjects from backend."""
    if not _is_allowed(update.effective_chat.id):
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    projects = await _fetch_projects()

    if not projects:
        await update.message.reply_text(
            "📁 *Danh sách Dự án*\n\n_Chưa có dự án nào\\. Hãy tạo task mới và chọn tạo dự án mới\\!_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    lines = ["📁 *Danh sách Dự án*\n"]
    for p in projects:
        lines.append(f"• *{escape_md(p['name'])}* — _{escape_md(p.get('description') or 'Không có mô tả')}_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


# ── /newtask & natural language input ─────────────────────────────────────────

async def newtask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /newtask <text> command."""
    if not _is_allowed(update.effective_chat.id):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Vui lòng thêm mô tả task\\.\n\n"
            "Ví dụ: `/newtask Làm trang đăng nhập, deadline thứ 6`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    order_text = " ".join(context.args)
    await _process_task_order(update, context, order_text)


# ── /done ─────────────────────────────────────────────────────────────────────

async def done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /done <task name or ID> — user reports task completion."""
    if not _is_allowed(update.effective_chat.id):
        return

    query_text = " ".join(context.args) if context.args else ""
    await _start_done_flow(update, context, query_text)


# ── Message handler ────────────────────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages as potential task orders or done reports."""
    if not _is_allowed(update.effective_chat.id):
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    chat_id = update.effective_chat.id
    username = update.effective_user.username or ""
    if username:
        await _checkin_user(update, username)

    # ── Nếu đang chờ user nhập output sau khi báo Done ──────────────────────
    if chat_id in _waiting_output:
        subtask_id = _waiting_output.pop(chat_id)
        await _submit_review_to_admin(update, context, subtask_id, output_text=text)
        return

    # ── Quick-access buttons từ menu ─────────────────────────────────────────
    if text == "📋 Xem tasks":
        await tasks_handler(update, context)
        return
    elif text == "👤 Task của tôi":
        await mytasks_handler(update, context)
        return
    elif text == "➕ Tạo Task mới":
        await update.message.reply_text(
            "📝 Hãy mô tả task bạn muốn tạo:\n\n"
            "_Ví dụ: \\\"Làm tính năng checkout, cần tích hợp payment, deadline tuần sau\\\"_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    elif text == "📁 Dự án":
        await projects_handler(update, context)
        return
    elif text == "📊 Báo cáo ngay":
        await remind_handler(update, context)
        return
    elif text == "👥 Team":
        await team_handler(update, context)
        return

    # ── Intent detection với Gemini ──────────────────────────────────────────
    parser = get_parser()
    try:
        intent_info = await parser.detect_intent(text)
        logger.info(f"Detected intent: {intent_info.intent} for message: '{text[:50]}'")

        if intent_info.intent == "leave":
            await _handle_leave_intent(update, context, intent_info)
            return
        elif intent_info.intent == "late":
            await _handle_late_intent(update, context, intent_info)
            return
        elif intent_info.intent == "extend_deadline":
            await _handle_extend_deadline_intent(update, context, intent_info)
            return
        elif intent_info.intent == "done_task":
            task_hint = getattr(intent_info, "task_title", "") or ""
            await _start_done_flow(update, context, task_hint)
            return
    except Exception as e:
        logger.error(f"Failed to detect intent: {e}")

    # ── Default: tạo task mới ────────────────────────────────────────────────
    if len(text) > 10:
        await _process_task_order(update, context, text)
    else:
        await update.message.reply_text(
            "❓ Bạn muốn tạo task mới? Hãy mô tả rõ hơn nhé\\!\\n\\n"
            "_Ví dụ: \\\"Làm tính năng checkout, cần tích hợp payment, deadline tuần sau\\\"_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ── Core: process task order ──────────────────────────────────────────────────

async def _process_task_order(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_text: str
) -> None:
    """Core logic: parse order → show breakdown → select project → await confirmation."""
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    thinking_msg = await update.message.reply_text(
        "🤖 _Đang phân tích yêu cầu\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    try:
        plane = get_plane_client()
        open_tasks_raw = []
        try:
            open_items = await plane.get_open_tasks()
            open_tasks_raw = [
                {"name": i.name, "assignee_name": i.assignee_name, "status": i.state_name}
                for i in open_items
            ]
        except Exception as e:
            logger.warning(f"Could not fetch open tasks for context: {e}")

        parser = get_parser()
        breakdown = await parser.parse_task(order_text, current_tasks=open_tasks_raw)

        batch_id = str(uuid.uuid4())[:8]
        _pending_batches[batch_id] = (order_text, breakdown)

        await thinking_msg.delete()

        # Lấy danh sách projects, nếu có thì hỏi chọn
        projects = await _fetch_projects()
        if projects:
            breakdown_text = format_task_breakdown(breakdown)
            await update.message.reply_text(
                breakdown_text + "\n\n📁 *Chọn Dự án cho task này:*",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=project_select_keyboard(projects, batch_id),
            )
        else:
            # Không có project nào → hỏi tạo mới hay bỏ qua
            breakdown_text = format_task_breakdown(breakdown)
            await update.message.reply_text(
                breakdown_text + "\n\n📁 _Chưa có dự án nào trong hệ thống_",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=project_select_keyboard([], batch_id),
            )

    except Exception as e:
        logger.error(f"Task processing failed: {e}", exc_info=True)
        await thinking_msg.edit_text(
            f"❌ Có lỗi xảy ra khi phân tích task\\: {escape_md(str(e)[:100])}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ── Core: Done flow ───────────────────────────────────────────────────────────

async def _start_done_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, task_hint: str
) -> None:
    """Step 1: User says done → find their subtask → ask for output."""
    username = update.effective_user.username or ""
    member = await _get_member_by_username(username)

    if not member:
        await update.message.reply_text(
            "⚠️ Không tìm thấy bạn trong hệ thống\\. Liên hệ Admin để được thêm vào team\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Lấy subtasks đang in_progress của member
    subtasks = await _fetch_my_inprogress_subtasks(member["id"])

    if not subtasks:
        await update.message.reply_text(
            "🤔 Bạn không có task nào đang *In Progress*\\.\n"
            "_Hãy chắc chắn task đã được assign và đang thực hiện trước khi báo Done\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Nếu chỉ có 1 task, dùng luôn
    if len(subtasks) == 1:
        subtask = subtasks[0]
        await _ask_for_output(update, context, subtask)
        return

    # Nhiều task → cho chọn
    lines = ["📋 *Bạn đang có nhiều task In Progress\\. Chọn task muốn báo Done:*\n"]
    rows = []
    for st in subtasks[:8]:
        title = escape_md(st["title"][:40])
        rows.append([InlineKeyboardButton(
            f"✅ {st['title'][:35]}",
            callback_data=f"done_select:{st['id']}"
        )])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _ask_for_output(update_or_query, context: ContextTypes.DEFAULT_TYPE, subtask: dict) -> None:
    """Step 2: Ask if user has output (link, description) to share."""
    subtask_id = subtask["id"]
    title = escape_md(subtask["title"])

    msg = (
        f"🎉 Tốt lắm\\! Bạn muốn báo *Done* cho task:\n"
        f"📌 *{title}*\n\n"
        f"Bạn có *output* \\(link, ảnh, mô tả kết quả\\) muốn gửi kèm không?"
    )

    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(
            msg,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=done_collect_output_keyboard(subtask_id),
        )
    else:
        await update_or_query.edit_message_text(
            msg,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=done_collect_output_keyboard(subtask_id),
        )


async def _submit_review_to_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    subtask_id: str,
    output_text: str = "",
) -> None:
    """Step 3: Set status in_review + send review request to Admin via DM."""
    username = update.effective_user.username or ""
    member = await _get_member_by_username(username)

    # 1. Cập nhật status → in_review
    subtask = await _patch_subtask_status(subtask_id, "in_review")
    if not subtask:
        await update.message.reply_text("❌ Không thể cập nhật trạng thái task\\. Thử lại sau\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # 2. Thông báo cho user
    title = escape_md(subtask.get("title", subtask_id))
    await update.message.reply_text(
        f"✅ Task *{title}* đã được gửi lên Review\\!\n"
        f"⏳ Đang chờ Admin duyệt\\. Em sẽ nhận thông báo khi có kết quả\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    # 3. Gửi tin nhắn cho Admin (DM)
    admin_chat_id = settings.admin_chat_id
    if not admin_chat_id:
        logger.warning("admin_chat_id not configured — review notification not sent")
        return

    # Lấy lịch sử chấm công hôm nay
    attendance_str = await _get_today_attendance_str(member["id"] if member else None)

    # Tên assignee
    assignee_name = member.get("fullName", username) if member else username
    assignee_tg = f"@{username}" if username else "Không rõ"

    output_block = ""
    if output_text.strip():
        output_block = f"\n• 📎 *Output:*\n  `{escape_md(output_text[:500])}`"

    admin_msg = (
        f"🔔 *\\[YÊU CẦU DUYỆT TASK\\]*\n\n"
        f"👤 *Nhân sự:* {escape_md(assignee_name)} \\({escape_md(assignee_tg)}\\)\n"
        f"📌 *Task:* {title}\n"
        f"🆔 *ID:* `{escape_md(subtask_id)}`\n"
        f"📝 *Mô tả:* _{escape_md(subtask.get('description') or 'Không có')}_\n"
        f"⏰ *Ước tính:* {subtask.get('estimatedHours', '?')}h"
        f"{output_block}\n"
        f"🕐 *Chấm công hôm nay:* {escape_md(attendance_str)}\n\n"
        f"─────────────────────\n"
        f"*Sếp duyệt task này không?*"
    )

    try:
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=admin_msg,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=review_decision_keyboard(subtask_id),
        )
        logger.info(f"Review request sent to admin for subtask {subtask_id}")
    except Exception as e:
        logger.error(f"Failed to send review to admin: {e}")


# ── Callback query handler ─────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all inline keyboard button callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":", 2)
    action = parts[0]

    if action == "confirm":
        await _handle_confirm(query, context, parts[1])
    elif action == "cancel":
        await _handle_cancel(query, parts[1])
    elif action == "edit":
        await _handle_edit(query, parts[1])
    elif action == "status":
        await _handle_status_update(query, context, parts[1], parts[2] if len(parts) > 2 else "")
    elif action == "hr":
        await _handle_hr_callback(query, context, parts[1], parts[2] if len(parts) > 2 else "")

    # ── Project selection ────────────────────────────────────────────────────
    elif action == "proj_select":
        batch_id = parts[1]
        project_id = parts[2] if len(parts) > 2 else None
        _pending_project[batch_id] = project_id
        await _handle_confirm_with_project(query, context, batch_id, project_id)
    elif action == "proj_new":
        batch_id = parts[1]
        await _handle_create_new_project(query, context, batch_id)
    elif action == "proj_skip":
        batch_id = parts[1]
        _pending_project[batch_id] = None
        await _handle_confirm_with_project(query, context, batch_id, None)

    # ── Done flow ────────────────────────────────────────────────────────────
    elif action == "done_select":
        subtask_id = parts[1]
        subtask = await _fetch_subtask(subtask_id)
        if subtask:
            await _ask_for_output(query, context, subtask)
    elif action == "done_has_output":
        subtask_id = parts[1]
        chat_id = query.message.chat_id
        _waiting_output[chat_id] = subtask_id
        await query.edit_message_text(
            "📎 *Hãy gửi link hoặc mô tả output của bạn trong tin nhắn tiếp theo:*\n\n"
            "_Ví dụ: link figma, link GitHub, ảnh chụp màn hình, hoặc mô tả ngắn kết quả đã làm_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif action == "done_no_output":
        subtask_id = parts[1]
        # Tạo fake update object để dùng lại hàm
        class FakeUpdate:
            message = query.message
            effective_user = query.from_user
            effective_chat = query.message.chat
        await _submit_review_to_admin(FakeUpdate(), context, subtask_id, output_text="")
    elif action == "done_cancel":
        await query.edit_message_text("❌ Đã hủy báo cáo Done\\.", parse_mode=ParseMode.MARKDOWN_V2)

    # ── Review decision by Admin ─────────────────────────────────────────────
    elif action == "review_approve":
        subtask_id = parts[1]
        await _handle_review_approve(query, context, subtask_id)
    elif action == "review_reject":
        subtask_id = parts[1]
        await _handle_review_reject(query, context, subtask_id)


# ── Project creation ──────────────────────────────────────────────────────────

async def _handle_create_new_project(query, context: ContextTypes.DEFAULT_TYPE, batch_id: str) -> None:
    """Create new project via API, then confirm task batch."""
    # Lấy tên project từ breakdown title
    if batch_id not in _pending_batches:
        await query.edit_message_text("⚠️ Phiên làm việc đã hết hạn\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    _, breakdown = _pending_batches[batch_id]
    project_name = breakdown.project_title or "Dự án mới"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"{settings.backend_api_url}/api/hr/projects",
                json={"name": project_name, "description": breakdown.summary or ""},
            )
            if res.status_code in [200, 201]:
                project = res.json().get("data", {})
                project_id = project.get("id")
                _pending_project[batch_id] = project_id
                await query.edit_message_text(
                    f"✅ Đã tạo dự án *{escape_md(project_name)}*\\!\n⏳ _Đang tạo tasks\\.\\.\\._",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                await _handle_confirm_with_project(query, context, batch_id, project_id, already_edited=True)
            else:
                logger.error(f"Create project failed: {res.status_code} {res.text}")
                await query.edit_message_text("❌ Không thể tạo dự án\\. Thử lại sau\\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        await query.edit_message_text(f"❌ Lỗi kết nối: {escape_md(str(e)[:80])}", parse_mode=ParseMode.MARKDOWN_V2)


# ── Confirm task batch ────────────────────────────────────────────────────────

async def _handle_confirm(query, context: ContextTypes.DEFAULT_TYPE, batch_id: str) -> None:
    """Legacy confirm — used when no project flow (direct confirm)."""
    project_id = _pending_project.get(batch_id)
    await _handle_confirm_with_project(query, context, batch_id, project_id)


async def _handle_confirm_with_project(
    query, context: ContextTypes.DEFAULT_TYPE, batch_id: str, project_id: str | None, already_edited: bool = False
) -> None:
    """Confirm task batch with resolved project_id — create in DB + Plane + Sheets."""
    if batch_id not in _pending_batches:
        if not already_edited:
            await query.edit_message_text(
                "⚠️ Batch đã hết hạn\\. Vui lòng gửi lại yêu cầu\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        return

    order_text, breakdown = _pending_batches.pop(batch_id)
    _pending_project.pop(batch_id, None)

    if not already_edited:
        await query.edit_message_text(
            "⏳ _Đang lưu tasks vào PostgreSQL DB \\& khởi tạo trên Plane\\.\\.\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    # 1. Save to PostgreSQL via backend API
    subtasks_payload = []
    for subtask in breakdown.subtasks:
        subtasks_payload.append({
            "title": subtask.name,
            "description": subtask.description or "",
            "suggestedAssigneeName": subtask.suggested_assignee_name,
            "deadlineDays": subtask.deadline_days,
            "estimatedHours": subtask.estimated_hours,
            "deliverables": subtask.deliverables or [],
            "priority": subtask.priority.value if hasattr(subtask.priority, "value") else str(subtask.priority)
        })

    try:
        payload = {
            "title": breakdown.project_title or "Telegram Task",
            "description": breakdown.summary or "",
            "source": "telegram",
            "rawInput": order_text,
            "subtasks": subtasks_payload,
        }
        if project_id:
            payload["projectId"] = project_id

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{settings.backend_api_url}/api/omnitask/tasks",
                json=payload,
            )
            if res.status_code in [200, 201]:
                logger.info(f"✅ Saved {len(subtasks_payload)} subtasks to DB (projectId={project_id})")
            else:
                logger.error(f"❌ Failed to save tasks: {res.status_code} - {res.text}")
    except Exception as e:
        logger.error(f"❌ Error calling backend API: {e}")

    # 2. Create in Plane + Sheets
    plane = get_plane_client()
    sheets = get_sheets_client()
    created_tasks: list[CreatedTask] = []
    errors: list[str] = []

    for subtask in breakdown.subtasks:
        try:
            assignee_id = await plane.resolve_member_id(subtask.suggested_assignee_name)
            task = await plane.create_work_item(subtask, assignee_id=assignee_id)
            created_tasks.append(task)
            await sheets.upsert_task(task)
        except Exception as e:
            logger.error(f"Failed to create subtask '{subtask.name}': {e}", exc_info=True)
            errors.append(subtask.name)

    if not created_tasks:
        await query.edit_message_text(
            "❌ Không thể tạo task nào\\. Kiểm tra lại cấu hình Plane API\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # 3. Show summary
    proj_note = f"\n📁 _Dự án: {escape_md(project_id or 'Chưa gán')}_" if project_id else ""
    summary = format_created_tasks_summary(created_tasks, order_text)
    if errors:
        summary += f"\n\n⚠️ _Không tạo được {len(errors)} tasks: {escape_md(', '.join(errors))}_"
    summary += proj_note

    await query.edit_message_text(summary, parse_mode=ParseMode.MARKDOWN_V2)

    # 4. Notify each assignee via DM
    for task in created_tasks:
        member_info = next(
            (m for m in TEAM_MEMBERS if m.name.lower() == task.assignee_name.lower()), None
        )
        if member_info and getattr(member_info, "telegram_chat_id", None):
            notification = (
                f"📌 *Task mới được assign cho bạn\\!*\n"
                f"*{escape_md(task.name)}*\n"
                f"⏰ Deadline: {escape_md(task.deadline or 'Chưa có')}\n"
                f"🔗 [Xem trên Plane]({task.plane_url})\n\n"
                f"_Gõ 'xong' khi hoàn thành để gửi review lên Sếp\\!_"
            )
            try:
                await context.bot.send_message(
                    chat_id=member_info.telegram_chat_id,
                    text=notification,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception:
                pass


# ── Review: Admin approve ─────────────────────────────────────────────────────

async def _handle_review_approve(query, context: ContextTypes.DEFAULT_TYPE, subtask_id: str) -> None:
    """Admin approves task → status = done, notify assignee."""
    subtask = await _patch_subtask_status(subtask_id, "done")
    if not subtask:
        await query.answer("❌ Lỗi cập nhật DB. Thử lại.", show_alert=True)
        return

    title = escape_md(subtask.get("title", subtask_id))
    admin_name = query.from_user.first_name or "Admin"

    # Cập nhật tin nhắn admin
    await query.edit_message_text(
        f"✅ *Đã duyệt task thành công\\!*\n"
        f"📌 Task: *{title}*\n"
        f"👤 Duyệt bởi: {escape_md(admin_name)}",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    # Notify assignee
    assignee = subtask.get("Assignee") or {}
    assignee_chat_id = assignee.get("telegramChatId")
    assignee_name = escape_md(assignee.get("fullName", "bạn"))

    if assignee_chat_id:
        try:
            await context.bot.send_message(
                chat_id=int(assignee_chat_id),
                text=(
                    f"🎉 *Chúc mừng\\!* Task của bạn đã được *Sếp duyệt Done\\!*\n\n"
                    f"📌 *{title}*\n\n"
                    f"Cảm ơn {assignee_name} đã hoàn thành xuất sắc\\! 💪"
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Failed to notify assignee on approve: {e}")

    # Sync Plane
    try:
        plane = get_plane_client()
        plane_id = subtask.get("planeTaskId")
        if plane_id:
            await plane.update_work_item_status(plane_id, "Done")
    except Exception as e:
        logger.warning(f"Could not sync Plane status to Done: {e}")


# ── Review: Admin reject ─────────────────────────────────────────────────────

async def _handle_review_reject(query, context: ContextTypes.DEFAULT_TYPE, subtask_id: str) -> None:
    """Admin rejects task → status = in_progress, notify assignee."""
    subtask = await _patch_subtask_status(subtask_id, "in_progress")
    if not subtask:
        await query.answer("❌ Lỗi cập nhật DB. Thử lại.", show_alert=True)
        return

    title = escape_md(subtask.get("title", subtask_id))
    admin_name = query.from_user.first_name or "Admin"

    await query.edit_message_text(
        f"↩️ *Đã trả lại task để làm lại*\n"
        f"📌 Task: *{title}*\n"
        f"👤 Bởi: {escape_md(admin_name)}\n\n"
        f"_Nhân sự sẽ nhận thông báo để xử lý lại\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    # Notify assignee
    assignee = subtask.get("Assignee") or {}
    assignee_chat_id = assignee.get("telegramChatId")
    assignee_name = escape_md(assignee.get("fullName", "bạn"))

    if assignee_chat_id:
        try:
            await context.bot.send_message(
                chat_id=int(assignee_chat_id),
                text=(
                    f"↩️ *Task cần làm lại* — Sếp chưa duyệt nhé {assignee_name}\\!\n\n"
                    f"📌 *{title}*\n\n"
                    f"Hãy xem lại yêu cầu và cập nhật kết quả, rồi báo Done lại khi xong nhé\\! 💪"
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Failed to notify assignee on reject: {e}")

    # Sync Plane
    try:
        plane = get_plane_client()
        plane_id = subtask.get("planeTaskId")
        if plane_id:
            await plane.update_work_item_status(plane_id, "In Progress")
    except Exception as e:
        logger.warning(f"Could not sync Plane status to In Progress: {e}")


# ── Misc callbacks ─────────────────────────────────────────────────────────────

async def _handle_cancel(query, batch_id: str) -> None:
    _pending_batches.pop(batch_id, None)
    _pending_project.pop(batch_id, None)
    await query.edit_message_text("❌ Đã hủy tạo task\\.", parse_mode=ParseMode.MARKDOWN_V2)


async def _handle_edit(query, batch_id: str) -> None:
    _pending_batches.pop(batch_id, None)
    _pending_project.pop(batch_id, None)
    await query.edit_message_text(
        "✏️ Vui lòng mô tả lại yêu cầu task của bạn\\.\\nTôi sẽ phân tích lại từ đầu\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def _handle_status_update(query, context: ContextTypes.DEFAULT_TYPE, item_id: str, new_state: str) -> None:
    plane = get_plane_client()
    success = await plane.update_work_item_status(item_id, new_state)
    if success:
        await query.edit_message_text(
            f"✅ Đã cập nhật trạng thái sang *{escape_md(new_state)}*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await query.answer("❌ Cập nhật thất bại. Thử lại sau.", show_alert=True)


# ── /tasks ─────────────────────────────────────────────────────────────────────

async def tasks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        plane = get_plane_client()
        open_items = await plane.get_open_tasks()
        msg = format_task_list(open_items, "Tất cả tasks đang mở")
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Không thể lấy danh sách tasks\\: {escape_md(str(e)[:100])}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ── /mytasks ───────────────────────────────────────────────────────────────────

async def mytasks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    username = update.effective_user.username or ""
    member = next(
        (m for m in TEAM_MEMBERS if m.telegram_username.lstrip("@") == username), None
    )
    if not member:
        await update.message.reply_text(
            "⚠️ Không tìm thấy bạn trong danh sách team\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    try:
        plane = get_plane_client()
        all_items = await plane.get_open_tasks()
        my_items = [i for i in all_items if (i.assignee_name or "").lower() == member.name.lower()]
        msg = format_task_list(my_items, f"Tasks của {member.name}")
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {escape_md(str(e)[:100])}", parse_mode=ParseMode.MARKDOWN_V2)


# ── /team ──────────────────────────────────────────────────────────────────────

async def team_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        plane = get_plane_client()
        all_items = await plane.get_open_tasks()
        msg = format_team_overview(all_items)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {escape_md(str(e)[:100])}", parse_mode=ParseMode.MARKDOWN_V2)


# ── /remind ────────────────────────────────────────────────────────────────────

async def remind_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        from src.scheduler.reminder import send_morning_reminder
        await send_morning_reminder(context.bot, update.effective_chat.id)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Không thể tạo báo cáo\\: {escape_md(str(e)[:100])}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ── /sync ──────────────────────────────────────────────────────────────────────

async def sync_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_chat.id):
        return
    msg = await update.message.reply_text(
        "🔄 _Đang sync dữ liệu lên Google Sheets\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    try:
        plane = get_plane_client()
        sheets = get_sheets_client()
        all_items = await plane.list_work_items()
        await sheets.sync_tasks_from_plane(all_items)
        await msg.edit_text(
            f"✅ Sync hoàn tất\\! *{len(all_items)} tasks* đã được cập nhật lên Google Sheets\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ Sync thất bại\\: {escape_md(str(e)[:100])}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ── Error handler ──────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Telegram error:", exc_info=context.error)


# ── HR & Coordinator Agent Helpers ────────────────────────────────────────────

async def _checkin_user(update: Update, username: str) -> None:
    if not username:
        return
    url = f"{settings.backend_api_url}/api/omnitask/hr/checkin"
    payload = {"telegramUsername": username}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                data = res.json()
                checkin_data = data.get("data", {})
                if checkin_data.get("isNew") is True:
                    status = checkin_data.get("status", "")
                    status_str = "có mặt (đúng giờ) 🟢" if status == "present" else "đi muộn 🟡"
                    time_str = datetime.now().strftime("%H:%M")
                    msg = (
                        f"⏰ *[Chấm Công Tự Động]*\n"
                        f"Chào *{update.effective_user.first_name}*, hệ thống đã ghi nhận bạn *{status_str}* hôm nay lúc *{time_str}*! 🚀"
                    )
                    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Auto check-in failed: {e}")


async def _handle_leave_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, intent_info) -> None:
    username = update.effective_user.username or ""
    if not username:
        await update.message.reply_text("⚠️ Không thể xác định Telegram username của bạn để xin nghỉ phép.")
        return

    url = f"{settings.backend_api_url}/api/omnitask/hr/leave-request"
    start_date = intent_info.start_date or datetime.now().strftime("%Y-%m-%d")
    end_date = intent_info.end_date or start_date
    leave_type = intent_info.leave_type or "sick"
    reason = intent_info.reason or "Có việc bận"

    payload = {
        "telegramUsername": username,
        "leaveType": leave_type,
        "startDate": start_date,
        "endDate": end_date,
        "reason": reason
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                result_data = res.json().get("data", {})
                leave_req = result_data.get("leaveRequest", {})
                affected_tasks = result_data.get("affectedTasks", [])
                suggested_handover = result_data.get("suggestedHandover")

                leave_type_vi = {
                    "sick": "nghỉ ốm 🤒",
                    "annual": "nghỉ phép năm 🏖️",
                    "personal": "việc riêng 💼"
                }.get(leave_type, "nghỉ phép")

                await update.message.reply_text(
                    f"📝 Đã nhận yêu cầu xin {leave_type_vi} từ "
                    f"*{start_date}* đến *{end_date}* của em.\n"
                    f"Hệ thống đã gửi đề xuất phê duyệt đến Sếp. Chúc em sớm khỏe! 🍀",
                    parse_mode=ParseMode.MARKDOWN
                )

                tasks_str = "\n".join(
                    f"- *{t.get('title', '')}* (Ước tính: {t.get('estimatedHours', 0)}h)"
                    for t in affected_tasks
                ) if affected_tasks else "- Không có task nào bị ảnh hưởng."

                handover_str = "Không tìm thấy ai phù hợp."
                if suggested_handover:
                    handover_str = f"*{suggested_handover.get('fullName')}* (@{suggested_handover.get('telegramUsername')})"

                boss_msg = (
                    f"📝 *[YÊU CẦU NGHỈ PHÉP MỚI]*\n\n"
                    f"• *Nhân sự:* {leave_req.get('member', {}).get('fullName', username)} (@{username})\n"
                    f"• *Loại nghỉ:* {leave_type_vi}\n"
                    f"• *Thời gian:* {start_date} → {end_date}\n"
                    f"• *Lý do:* {reason}\n"
                    f"• *Task bị ảnh hưởng:*\n{tasks_str}\n"
                    f"• 👉 *Đề xuất chuyển giao:* {handover_str}\n"
                )

                keyboard = [[
                    InlineKeyboardButton("Duyệt nghỉ & Chuyển giao", callback_data=f"hr:approve:{leave_req.get('id')}"),
                    InlineKeyboardButton("Từ chối", callback_data=f"hr:reject:{leave_req.get('id')}")
                ]]

                admin_chat_id = settings.admin_chat_id
                if admin_chat_id:
                    await context.bot.send_message(
                        chat_id=admin_chat_id,
                        text=boss_msg,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await update.message.reply_text(boss_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ Lỗi xử lý yêu cầu nghỉ phép. Vui lòng liên hệ Admin.")
    except Exception as e:
        logger.error(f"Error calling leave-request API: {e}")
        await update.message.reply_text("❌ Lỗi kết nối đến backend.")


async def _handle_late_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, intent_info) -> None:
    username = update.effective_user.username or ""
    reason_str = intent_info.reason or "Có việc bận đột xuất"
    if username:
        await _checkin_user(update, username)
    await update.message.reply_text(
        f"🛵 Chào *{update.effective_user.first_name}*, anh đã ghi nhận báo cáo đi muộn hôm nay của em.\n"
        f"Lý do: _\"{reason_str}\"_\n"
        f"Hãy đi đường cẩn thận và báo cáo khi đến văn phòng nhé! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )


async def _handle_extend_deadline_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, intent_info) -> None:
    task_key = intent_info.task_key or "task"
    days = intent_info.days_to_extend or 1
    reason = intent_info.reason or "Cần thêm thời gian hoàn thiện"
    await update.message.reply_text(
        f"📅 Đã ghi nhận yêu cầu lùi deadline cho task *{task_key}* thêm *{days} ngày*.\n"
        f"Lý do: _\"{reason}\"_\n"
        f"Anh đã cập nhật thông tin, em nhớ báo lại cho Sếp nhé! 💪",
        parse_mode=ParseMode.MARKDOWN
    )


async def _handle_hr_callback(query, context: ContextTypes.DEFAULT_TYPE, sub_action: str, request_id: str) -> None:
    if sub_action == "approve":
        url = f"{settings.backend_api_url}/api/omnitask/hr/approve-leave"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, json={"requestId": request_id})
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    leave_req = data.get("leaveRequest", {})
                    transferred_count = data.get("transferredTasksCount", 0)
                    handover_member = data.get("handoverMember")

                    handover_info = (
                        f" cho *{handover_member.get('fullName')}* (@{handover_member.get('telegramUsername')})"
                        if handover_member else ""
                    )
                    await query.edit_message_text(
                        f"✅ *Đã duyệt yêu cầu nghỉ phép thành công!*\n"
                        f"• Đã chuyển giao *{transferred_count} tasks*{handover_info}.",
                        parse_mode=ParseMode.MARKDOWN
                    )

                    requester_chat_id = leave_req.get("member", {}).get("telegramChatId")
                    if requester_chat_id:
                        try:
                            await context.bot.send_message(
                                chat_id=int(requester_chat_id),
                                text="✅ *Yêu cầu nghỉ phép đã được Sếp duyệt!*\nChúc em có một ngày nghỉ tốt lành.",
                                parse_mode=ParseMode.MARKDOWN
                            )
                        except Exception as dm_err:
                            logger.error(f"Failed to DM requester: {dm_err}")

                    if handover_member and handover_member.get("telegramChatId"):
                        try:
                            await context.bot.send_message(
                                chat_id=int(handover_member.get("telegramChatId")),
                                text=(
                                    f"📌 *[Nhận bàn giao công việc]*\n"
                                    f"Hôm nay bạn nhận bàn giao tạm thời *{transferred_count} tasks* từ *{leave_req.get('member', {}).get('fullName')}*."
                                ),
                                parse_mode=ParseMode.MARKDOWN
                            )
                        except Exception as dm_err:
                            logger.error(f"Failed to DM handover member: {dm_err}")
                else:
                    await query.answer("❌ Lỗi duyệt phép từ backend API.", show_alert=True)
        except Exception as e:
            logger.error(f"Approve callback failed: {e}")
            await query.answer("❌ Lỗi kết nối đến backend.", show_alert=True)

    elif sub_action == "reject":
        url = f"{settings.backend_api_url}/api/omnitask/hr/reject-leave"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, json={"requestId": request_id})
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    await query.edit_message_text("❌ *Đã từ chối yêu cầu nghỉ phép.*", parse_mode=ParseMode.MARKDOWN)
                    member_chat_id = data.get("member", {}).get("telegramChatId")
                    if member_chat_id:
                        try:
                            await context.bot.send_message(
                                chat_id=int(member_chat_id),
                                text="❌ Sếp đã từ chối yêu cầu nghỉ phép. Vui lòng liên hệ trực tiếp Sếp để trao đổi.",
                                parse_mode=ParseMode.MARKDOWN
                            )
                        except Exception as dm_err:
                            logger.error(f"Failed to DM requester: {dm_err}")
                else:
                    await query.answer("❌ Từ chối thất bại.", show_alert=True)
        except Exception as e:
            logger.error(f"Reject callback failed: {e}")
            await query.answer("❌ Lỗi kết nối.", show_alert=True)


# ── Utility helpers ───────────────────────────────────────────────────────────

async def _fetch_projects() -> list[dict]:
    """Fetch OmniProjects from backend API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{settings.backend_api_url}/api/hr/projects")
            if res.status_code == 200:
                return res.json().get("data", [])
    except Exception as e:
        logger.error(f"Failed to fetch projects: {e}")
    return []


async def _fetch_my_inprogress_subtasks(member_id: str) -> list[dict]:
    """Fetch subtasks in_progress for a specific member."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{settings.backend_api_url}/api/hr/subtasks")
            if res.status_code == 200:
                all_subtasks = res.json().get("data", [])
                return [
                    st for st in all_subtasks
                    if st.get("assigneeId") == member_id and st.get("status") == "in_progress"
                ]
    except Exception as e:
        logger.error(f"Failed to fetch subtasks: {e}")
    return []


async def _fetch_subtask(subtask_id: str) -> dict | None:
    """Fetch a single subtask by ID."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{settings.backend_api_url}/api/hr/subtasks")
            if res.status_code == 200:
                all_subtasks = res.json().get("data", [])
                for st in all_subtasks:
                    if st.get("id") == subtask_id:
                        return st
    except Exception as e:
        logger.error(f"Failed to fetch subtask {subtask_id}: {e}")
    return None


async def _patch_subtask_status(subtask_id: str, status: str) -> dict | None:
    """PATCH subtask status in backend DB."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.patch(
                f"{settings.backend_api_url}/api/hr/subtasks/{subtask_id}",
                json={"status": status},
            )
            if res.status_code == 200:
                return res.json().get("data", {})
            else:
                logger.error(f"PATCH subtask {subtask_id} failed: {res.status_code} {res.text}")
    except Exception as e:
        logger.error(f"Error patching subtask {subtask_id}: {e}")
    return None


async def _get_member_by_username(username: str) -> dict | None:
    """Get team member info from backend by Telegram username."""
    if not username:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{settings.backend_api_url}/api/hr/team-members")
            if res.status_code == 200:
                members = res.json().get("data", [])
                for m in members:
                    if (m.get("telegramUsername") or "").lstrip("@") == username.lstrip("@"):
                        return m
    except Exception as e:
        logger.error(f"Failed to fetch member by username {username}: {e}")
    return None


async def _get_today_attendance_str(member_id: str | None) -> str:
    """Get today's attendance summary for a member."""
    if not member_id:
        return "Không rõ"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(
                f"{settings.backend_api_url}/api/hr/attendance",
                params={"memberId": member_id}
            )
            if res.status_code == 200:
                records = res.json().get("data", [])
                today = datetime.now().strftime("%Y-%m-%d")
                for r in records:
                    if r.get("date", "").startswith(today):
                        check_in = r.get("checkIn", "")
                        check_out = r.get("checkOut", "")
                        ci = check_in[11:16] if check_in else "?"
                        co = check_out[11:16] if check_out else "Chưa out"
                        hours = r.get("totalHours") or "?"
                        work_type = "Remote" if r.get("workType") == "remote" else "Văn phòng"
                        return f"Vào {ci} — Ra {co} — {hours}h — {work_type}"
                return "Chưa có chấm công hôm nay"
    except Exception as e:
        logger.error(f"Failed to get attendance: {e}")
    return "Không rõ"

# ── /team_status ──────────────────────────────────────────────────────────────

async def team_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show today's checkin/checkout status for the whole team."""
    if not _is_allowed(update.effective_chat.id):
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{settings.backend_api_url}/api/hr/attendance")
            if res.status_code != 200:
                await update.message.reply_text("❌ Không thể lấy dữ liệu chấm công.")
                return
            
            all_records = res.json().get("data", [])
            today = datetime.now().strftime("%Y-%m-%d")
            today_records = [r for r in all_records if r.get("date", "").startswith(today)]
            
            if not today_records:
                await update.message.reply_text(f"📊 *Báo cáo Check\\-in hôm nay \\({escape_md(today)}\\)*\n\nChưa có ai check\\-in hôm nay\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return

            lines = [f"📊 *Báo cáo Check\\-in hôm nay \\({escape_md(today)}\\)*\n"]
            
            checked_in = 0
            late = 0
            
            for r in today_records:
                member_name = r.get("member", {}).get("fullName", "Unknown")
                work_type = "Remote" if r.get("workType") == "remote" else "Office"
                check_in = r.get("checkIn", "")
                check_out = r.get("checkOut", "")
                status = r.get("status", "")
                
                ci_time = check_in[11:16] if check_in else "?"
                co_time = check_out[11:16] if check_out else "Chưa out"
                
                status_icon = "✅"
                if status == "late":
                    status_icon = "⚠️"
                    late += 1
                elif status == "leave":
                    status_icon = "🏖️"
                
                if check_in:
                    checked_in += 1
                    
                lines.append(f"• {status_icon} *{escape_md(member_name)}* \\({work_type}\\): {ci_time} \\- {co_time}")
            
            lines.insert(1, f"👥 Đã check\\-in: *{checked_in}* \\| Đi muộn: *{late}*\n")
            
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Failed to fetch team status: {e}")
        await update.message.reply_text("❌ Lỗi kết nối đến backend.")


# ── /subtask ──────────────────────────────────────────────────────────────────

async def subtask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Breakdown a task into subtasks via Gemini."""
    if not _is_allowed(update.effective_chat.id):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Vui lòng cung cấp mã task hoặc tên task\\.\n\n"
            "Ví dụ: `/subtask T-104`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    query_text = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        f"🤖 _Đang phân rã task {escape_md(query_text)} bằng AI\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    
    try:
        plane = get_plane_client()
        open_items = await plane.get_open_tasks()
        
        target_task = None
        for t in open_items:
            # sequence_id is like "T-104"
            t_seq = f"T-{t.sequence_id}" if t.sequence_id else ""
            if query_text.lower() in t.name.lower() or query_text.lower() in t_seq.lower():
                target_task = t
                break
                
        if not target_task:
            await thinking_msg.edit_text(f"❌ Không tìm thấy task nào khớp với `{escape_md(query_text)}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        parser = get_parser()
        prompt = f"Tạo subtask cho task: {target_task.name}\nMô tả: {target_task.description or 'Không có'}"
        breakdown = await parser.parse_task(prompt, current_tasks=[])
        
        if not breakdown.subtasks:
            await thinking_msg.edit_text("❌ AI không tạo được subtask nào.")
            return
            
        created_count = 0
        async with httpx.AsyncClient(timeout=15.0) as client:
            for st in breakdown.subtasks:
                res = await client.post(
                    f"{settings.backend_api_url}/api/hr/subtasks",
                    json={
                        "taskId": target_task.id,
                        "title": st.title,
                        "description": st.deliverables or "",
                        "status": "todo"
                    }
                )
                if res.status_code in (200, 201):
                    created_count += 1

        await thinking_msg.edit_text(
            f"✅ Đã phân rã và tạo thành công *{created_count}* subtasks cho *{escape_md(target_task.name)}*\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"Task breakdown failed: {e}", exc_info=True)
        await thinking_msg.edit_text("🤖 Lỗi kết nối AI. Vui lòng thử lại sau.")
