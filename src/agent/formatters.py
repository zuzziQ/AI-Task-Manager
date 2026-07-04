"""
Message formatter — converts task data into Telegram-friendly Markdown v2 messages.
All messages in Vietnamese.
"""
from datetime import datetime
from typing import List

from src.models.task import TaskBreakdown, CreatedTask, PlaneWorkItem, SubTask
from src.team_config import TEAM_MEMBERS


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    specials = r"\_*[]()~`>#+-=|{}.!"
    for ch in specials:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_task_breakdown(breakdown: TaskBreakdown) -> str:
    """Format LLM task breakdown for user confirmation."""
    lines = [
        f"🤖 *Phân tích task xong\\!*\n",
        f"📌 *{escape_md(breakdown.project_title)}*",
        f"_{escape_md(breakdown.summary)}_\n",
        f"⏱ Tổng ước tính: *{breakdown.total_estimated_hours} giờ*",
        f"📦 Số sub\\-tasks: *{len(breakdown.subtasks)}*\n",
        "─────────────────────\n",
    ]

    for i, st in enumerate(breakdown.subtasks, 1):
        deadline_label = _deadline_label(st.deadline_days)
        deliverables_str = "\n".join(f"    ▪️ {escape_md(d)}" for d in st.deliverables)

        lines.extend([
            f"*{i}\\. {escape_md(st.name)}*",
            f"   👤 Assign: *{escape_md(st.suggested_assignee_name)}*",
            f"   ⚡ Độ ưu tiên: {_priority_label(st.priority.value)}",
            f"   ⏰ Ước tính: *{st.estimated_hours}h* \\| Deadline: {deadline_label}",
            f"   📋 Deliverables:",
            deliverables_str if deliverables_str else "    ▪️ \\(Chưa xác định\\)",
            "",
        ])

    if breakdown.notes:
        lines.extend([
            "─────────────────────",
            f"📝 *Ghi chú:* _{escape_md(breakdown.notes)}_\n",
        ])

    lines.extend([
        "─────────────────────",
        "Bạn muốn tạo các tasks này không?",
    ])

    return "\n".join(lines)


def format_created_tasks_summary(tasks: List[CreatedTask], order_text: str) -> str:
    """Summary message after tasks are successfully created."""
    lines = [
        "✅ *Đã tạo tasks thành công\\!*\n",
        f"📝 Yêu cầu: _{escape_md(order_text[:80])}\\.\\.\\._\n" if len(order_text) > 80
        else f"📝 Yêu cầu: _{escape_md(order_text)}_\n",
    ]

    for task in tasks:
        lines.extend([
            f"• \\#{task.sequence_id} *{escape_md(task.name)}*",
            f"  👤 {escape_md(task.assignee_name)} \\| 📅 {escape_md(task.deadline or 'Chưa có')}",
            f"  🔗 [Xem trên Plane]({task.plane_url})",
            "",
        ])

    lines.append(f"🗂 Đã sync lên Google Sheets\\.")
    return "\n".join(lines)


def format_task_list(items: List[PlaneWorkItem], title: str = "Danh sách tasks") -> str:
    """Format list of Plane work items for display."""
    if not items:
        return f"📋 *{escape_md(title)}*\n\n_Không có task nào\\._"

    lines = [f"📋 *{escape_md(title)}* \\({len(items)} tasks\\)\n"]

    # Group by assignee
    by_assignee: dict[str, list[PlaneWorkItem]] = {}
    for item in items:
        key = item.assignee_name or "Chưa assign"
        by_assignee.setdefault(key, []).append(item)

    for assignee, assignee_items in sorted(by_assignee.items()):
        lines.append(f"👤 *{escape_md(assignee)}*")
        for item in assignee_items:
            overdue_tag = " ⚠️" if item.is_overdue else ""
            deadline_str = f" \\| 📅 {escape_md(item.target_date or 'Chưa có')}"
            lines.append(
                f"  {item.status_emoji} {item.priority_emoji} \\#{item.sequence_id} "
                f"{escape_md(item.name)}{overdue_tag}{deadline_str}"
            )
        lines.append("")

    return "\n".join(lines)


def format_daily_reminder(
    open_tasks: List[PlaneWorkItem],
    overdue_tasks: List[PlaneWorkItem],
    ai_summary: str,
    reminder_type: str = "morning",
) -> str:
    """Format daily reminder message."""
    today = datetime.now().strftime("%d/%m/%Y")
    emoji = "☀️" if reminder_type == "morning" else "🌆"
    time_label = "Báo cáo buổi sáng" if reminder_type == "morning" else "Nhắc nhở cuối ngày"

    lines = [
        f"{emoji} *{time_label} — {escape_md(today)}*\n",
        escape_md(ai_summary),
        "",
        "─────────────────────",
        f"📊 Tasks đang mở: *{len(open_tasks)}* \\| Quá hạn: *{len(overdue_tasks)}*",
    ]

    if overdue_tasks:
        lines.extend([
            "",
            "🔴 *Tasks quá hạn cần xử lý gấp:*",
        ])
        for item in overdue_tasks[:5]:  # Show max 5 overdue
            lines.append(
                f"  • \\#{item.sequence_id} {escape_md(item.name)} "
                f"\\— {escape_md(item.assignee_name or 'Chưa assign')} "
                f"\\(Hạn: {escape_md(item.target_date or '?')}\\)"
            )
        if len(overdue_tasks) > 5:
            lines.append(f"  _\\.\\.\\. và {len(overdue_tasks) - 5} task khác_")

    return "\n".join(lines)


def format_team_overview(items: List[PlaneWorkItem]) -> str:
    """Format team workload overview."""
    by_member: dict[str, list[PlaneWorkItem]] = {}
    for item in items:
        key = item.assignee_name or "Chưa assign"
        by_member.setdefault(key, []).append(item)

    lines = ["👥 *Tổng quan nhân sự*\n"]
    for member, tasks in sorted(by_member.items(), key=lambda x: -len(x[1])):
        overdue = sum(1 for t in tasks if t.is_overdue)
        done = sum(1 for t in tasks if t.state_name == "Done")
        open_count = len(tasks) - done
        overdue_tag = f" ⚠️{overdue} quá hạn" if overdue else ""
        lines.append(
            f"👤 *{escape_md(member)}*: {open_count} đang mở, {done} hoàn thành{escape_md(overdue_tag)}"
        )
    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _priority_label(priority: str) -> str:
    return {
        "urgent": "🔴 Khẩn cấp",
        "high": "🟠 Cao",
        "medium": "🟡 Trung bình",
        "low": "🟢 Thấp",
    }.get(priority.lower(), "⚪ Không rõ")


def _deadline_label(days: int) -> str:
    if days == 1:
        return escape_md("ngày mai")
    elif days <= 3:
        return f"*{days} ngày*"
    elif days <= 7:
        return f"{days} ngày \\({_weekday(days)}\\)"
    else:
        target = datetime.now()
        from datetime import timedelta
        target += timedelta(days=days)
        return escape_md(target.strftime("%d/%m/%Y"))


def _weekday(days: int) -> str:
    from datetime import timedelta
    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    target = datetime.now().date() + timedelta(days=days)
    return weekday_names[target.weekday()]
