"""
Daily reminder scheduler — sends automatic progress reports via Telegram.
Uses python-telegram-bot's built-in JobQueue (APScheduler wrapper).

Hybrid reminder logic:
- Group chat gets a high-level summary.
- Individual members get DMs with their specific tasks.
"""
import logging
from collections import defaultdict
from datetime import time

import pytz
from telegram import Bot
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from src.agent.formatters import format_daily_reminder, escape_md
from src.config import settings
from src.integrations.plane_client import get_plane_client
from src.integrations.sheets_client import get_sheets_client
from src.ai.task_parser import get_parser
from src.team_config import TEAM_MEMBERS

logger = logging.getLogger(__name__)

tz = pytz.timezone(settings.timezone)


# ── Internal Helpers ───────────────────────────────────────────────────────────

def _group_tasks_by_assignee(tasks: list) -> dict:
    """Group tasks by lowercase assignee name."""
    grouped = defaultdict(list)
    for t in tasks:
        name = (t.assignee_name or "").lower()
        if name:
            grouped[name].append(t)
    return grouped


async def _send_dm(bot: Bot, chat_id: int, text: str) -> None:
    """Safely send a direct message."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=False,
        )
    except (Forbidden, BadRequest) as e:
        logger.warning(f"Could not send DM to {chat_id}: {e}")
    except Exception as e:
        logger.error(f"Error sending DM to {chat_id}: {e}")


# ── Core reminder logic ────────────────────────────────────────────────────────

async def send_morning_reminder(bot: Bot, chat_id: int) -> None:
    """Morning logic: DM individuals their tasks, send summary to group."""
    logger.info("Starting morning reminder cycle")

    try:
        import httpx
        from src.models.task import PlaneWorkItem
        
        # 1. Fetch pending tasks from database via backend API
        url = f"{settings.backend_api_url}/api/omnitask/tasks/pending"
        logger.info(f"🔄 Fetching pending tasks for reminder from: {url}")
        
        open_items = []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    for item in data:
                        assignee = item.get("assignee", "")
                        if assignee.startswith("@"):
                            assignee = assignee[1:]
                        for suffix in ["-dev", "-fe", "-pm"]:
                            if assignee.lower().endswith(suffix):
                                assignee = assignee[:-len(suffix)]

                        open_items.append(PlaneWorkItem(
                            id=item.get("id", ""),
                            sequence_id=0,
                            name=item.get("taskName", ""),
                            priority="medium",
                            state_name=item.get("status", "Todo"),
                            assignee_id=None,
                            assignee_name=assignee.capitalize(),
                            target_date=item.get("eta"),
                            completed_at=None,
                            is_overdue=False
                        ))
                else:
                    logger.error(f"❌ Failed to fetch pending tasks from API: {res.status_code}")
        except Exception as api_err:
            logger.error(f"❌ Error fetching pending tasks from backend API: {api_err}")
            # Fallback: try to fetch from Plane if API fails
            try:
                plane = get_plane_client()
                open_items = await plane.get_open_tasks()
            except Exception as plane_err:
                logger.error(f"❌ Plane fallback also failed: {plane_err}")

        # Assume overdue tasks are those with status "pending" and have a deadline (e.g. eta is set)
        overdue_items = [t for t in open_items if t.target_date]

        # 1. Send DMs to individual members
        open_grouped = _group_tasks_by_assignee(open_items)
        overdue_grouped = _group_tasks_by_assignee(overdue_items)

        for member in TEAM_MEMBERS:
            if not member.telegram_chat_id:
                continue

            member_open = open_grouped.get(member.name.lower(), [])
            member_overdue = overdue_grouped.get(member.name.lower(), [])

            if not member_open and not member_overdue:
                continue

            dm_lines = [f"☀️ *Chào buổi sáng {escape_md(member.name)}\\!*"]
            
            if member_overdue:
                dm_lines.append(f"\n⚠️ *Tasks quá hạn cần chú ý ({len(member_overdue)}):*")
                for t in member_overdue:
                    dm_lines.append(f"• [{escape_md(t.state_name)}] {escape_md(t.name)}")
            
            if member_open:
                dm_lines.append(f"\n📋 *Tasks đang xử lý ({len(member_open)}):*")
                for t in member_open:
                    if t not in member_overdue:
                        dm_lines.append(f"• [{escape_md(t.state_name)}] {escape_md(t.name)}")
            
            dm_lines.append(f"\n🔗 [Mở Dashboard Sheets](https://docs.google.com/spreadsheets/d/{settings.google_sheet_id})")
            
            await _send_dm(bot, member.telegram_chat_id, "\n".join(dm_lines))

        # 2. Send short summary to Group (if configured)
        if chat_id:
            # Generate AI summary for group
            open_dicts = [{"name": i.name, "assignee_name": i.assignee_name or "", "status": i.state_name} for i in open_items]
            overdue_dicts = [{"name": i.name, "assignee_name": i.assignee_name or "", "target_date": i.target_date or "?"} for i in overdue_items]
            
            parser = get_parser()
            ai_summary = await parser.summarize_tasks_for_reminder(
                open_dicts, overdue_dicts, reminder_type="morning"
            )

            group_msg = (
                "🌅 *Báo Cáo Sáng Nay*\n\n"
                f"{ai_summary}\n\n"
                f"📊 [Xem chi tiết trên Google Sheets](https://docs.google.com/spreadsheets/d/{settings.google_sheet_id})"
            )

            await bot.send_message(
                chat_id=chat_id,
                text=group_msg,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_notification=True,  # Don't buzz phones for group summary
            )

        # 3. Sync to sheets
        try:
            sheets = get_sheets_client()
            await sheets.sync_tasks_from_plane(open_items)
            await sheets.log_reminder("morning", "Sent hybrid reminder", len(open_items), len(overdue_items))
        except Exception as e:
            logger.warning(f"Sheet sync failed: {e}")

    except Exception as e:
        logger.error(f"Morning reminder failed: {e}", exc_info=True)


async def send_afternoon_reminder(bot: Bot, chat_id: int) -> None:
    """Afternoon logic: ONLY DM people who have overdue/urgent tasks. No group spam."""
    logger.info("Starting afternoon reminder cycle")

    try:
        import httpx
        from src.models.task import PlaneWorkItem
        
        # 1. Fetch pending tasks from database via backend API
        url = f"{settings.backend_api_url}/api/omnitask/tasks/pending"
        logger.info(f"🔄 Fetching pending tasks for afternoon reminder from: {url}")
        
        open_items = []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    for item in data:
                        assignee = item.get("assignee", "")
                        if assignee.startswith("@"):
                            assignee = assignee[1:]
                        for suffix in ["-dev", "-fe", "-pm"]:
                            if assignee.lower().endswith(suffix):
                                assignee = assignee[:-len(suffix)]

                        open_items.append(PlaneWorkItem(
                            id=item.get("id", ""),
                            sequence_id=0,
                            name=item.get("taskName", ""),
                            priority="medium",
                            state_name=item.get("status", "Todo"),
                            assignee_id=None,
                            assignee_name=assignee.capitalize(),
                            target_date=item.get("eta"),
                            completed_at=None,
                            is_overdue=False
                        ))
                else:
                    logger.error(f"❌ Failed to fetch pending tasks from API: {res.status_code}")
        except Exception as api_err:
            logger.error(f"❌ Error fetching pending tasks from backend API: {api_err}")
            # Fallback
            try:
                plane = get_plane_client()
                overdue_items = await plane.get_overdue_tasks()
                open_items = overdue_items
            except Exception as plane_err:
                logger.error(f"❌ Plane fallback failed: {plane_err}")

        # Assume overdue tasks are those with status "pending" and have a deadline (e.g. eta is set)
        overdue_items = [t for t in open_items if t.target_date]

        if not overdue_items:
            logger.info("No overdue tasks — skipping afternoon reminder")
            return

        overdue_grouped = _group_tasks_by_assignee(overdue_items)
        dm_count = 0

        for member in TEAM_MEMBERS:
            if not member.telegram_chat_id:
                continue

            member_overdue = overdue_grouped.get(member.name.lower(), [])
            if not member_overdue:
                continue

            dm_lines = [
                f"⏰ *Nhắc nhở nhẹ cuối ngày, {escape_md(member.name)} ơi\\!*",
                f"Bạn đang có {len(member_overdue)} task quá hạn/sắp đến hạn:\n"
            ]
            for t in member_overdue:
                dm_lines.append(f"• {escape_md(t.name)}")
            
            dm_lines.append("\nHãy update status trên Plane hoặc báo cho team nhé\\! 💪")
            
            await _send_dm(bot, member.telegram_chat_id, "\n".join(dm_lines))
            dm_count += 1

        # Optionally drop a single line in group if DMs were sent
        if chat_id and dm_count > 0:
            group_msg = (
                f"⏰ Đã nhắc riêng {dm_count} thành viên về các tasks sắp đến hạn\\.\n"
                "Mọi người nhớ update tiến độ trước khi nghỉ nhé\\! 🌅"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=group_msg,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_notification=True,
            )

        # Log
        try:
            sheets = get_sheets_client()
            await sheets.log_reminder("afternoon", f"Sent {dm_count} DMs for overdue tasks", 0, len(overdue_items))
        except Exception as e:
            logger.warning(f"Sheet log failed: {e}")

    except Exception as e:
        logger.error(f"Afternoon reminder failed: {e}", exc_info=True)


async def send_weekly_summary(bot: Bot, chat_id: int) -> None:
    """Weekly logic: Group only. High level stats."""
    if not chat_id:
        return
        
    logger.info("Sending weekly summary to group")
    try:
        plane = get_plane_client()
        all_items = await plane.list_work_items()

        done = [i for i in all_items if i.state_name == "Done"]
        open_items = [i for i in all_items if i.state_name not in ("Done", "Cancelled")]

        msg_lines = [
            "📅 *Tổng Kết Tuần*\n",
            f"✅ Hoàn thành: *{len(done)} tasks*",
            f"🔄 Đang thực hiện: *{len(open_items)} tasks*\n",
            "Chúc team một tuần mới đầy năng lượng\\! 💪",
            f"🔗 [Mở Dashboard Sheets](https://docs.google.com/spreadsheets/d/{settings.google_sheet_id})"
        ]
        await bot.send_message(
            chat_id=chat_id,
            text="\n".join(msg_lines),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=False,  # Weekly summary gets a ping
        )
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}", exc_info=True)


# ── Job wrappers for JobQueue ──────────────────────────────────────────────────

async def morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data.get("chat_id")
    await send_morning_reminder(context.bot, chat_id)

async def afternoon_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data.get("chat_id")
    await send_afternoon_reminder(context.bot, chat_id)

async def weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data.get("chat_id")
    await send_weekly_summary(context.bot, chat_id)


# ── Schedule registration ──────────────────────────────────────────────────────

def register_jobs(app) -> None:
    admin_id = settings.admin_chat_id
    if not admin_id:
        logger.warning("TELEGRAM_ADMIN_CHAT_ID not set. Group summaries will be skipped.")

    jq = app.job_queue
    
    # Morning reminder
    morning_time = time(hour=settings.morning_reminder_hour, minute=settings.morning_reminder_minute, tzinfo=tz)
    jq.run_daily(morning_job, time=morning_time, data={"chat_id": admin_id}, name="morning_reminder")

    # Afternoon reminder
    afternoon_time = time(hour=settings.afternoon_reminder_hour, minute=settings.afternoon_reminder_minute, tzinfo=tz)
    jq.run_daily(afternoon_job, time=afternoon_time, data={"chat_id": admin_id}, name="afternoon_reminder")

    # Weekly summary
    weekly_time = time(hour=9, minute=0, tzinfo=tz)
    jq.run_daily(weekly_job, time=weekly_time, days=(0,), data={"chat_id": admin_id}, name="weekly_summary")

    logger.info("Hybrid scheduler jobs registered successfully.")
