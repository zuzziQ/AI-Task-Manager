"""
Main Telegram bot entry point.
Run: python -m src.agent.bot
"""
import asyncio
import logging
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.config import settings
from src.agent.handlers import (
    start_handler,
    help_handler,
    newtask_handler,
    projects_handler,
    tasks_handler,
    mytasks_handler,
    done_handler,
    team_handler,
    remind_handler,
    sync_handler,
    team_status_handler,
    subtask_handler,
    message_handler,
    callback_handler,
    error_handler,
)
from src.scheduler.reminder import register_jobs

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
# Reduce noise from httpx/telegram internals
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Called after bot initialization — register scheduled jobs."""
    from src.team_config import load_team_members
    await load_team_members()
    register_jobs(app)
    logger.info("✅ Bot initialized — jobs registered")

    # Set bot commands menu in Telegram
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start", "Khởi động bot"),
        BotCommand("help", "Xem danh sách lệnh"),
        BotCommand("newtask", "Tạo task mới"),
        BotCommand("projects", "Xem danh sách dự án"),
        BotCommand("tasks", "Xem tất cả tasks đang mở"),
        BotCommand("mytasks", "Tasks được assign cho tôi"),
        BotCommand("done", "Báo hoàn thành task"),
        BotCommand("team", "Tổng quan workload team"),
        BotCommand("team_status", "Trạng thái checkin/checkout"),
        BotCommand("subtask", "Phân rã task thành subtasks"),
        BotCommand("remind", "Báo cáo tiến độ ngay"),
        BotCommand("sync", "Sync dữ liệu lên Google Sheets"),
    ])


def build_application() -> Application:
    """Build and configure the Telegram bot application."""
    if not settings.telegram_bot_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN chưa được cấu hình!\n"
            "Hãy copy .env.example → .env và điền thông tin."
        )

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    # ── Register handlers ──────────────────────────────────────────────────────
    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("newtask", newtask_handler))
    app.add_handler(CommandHandler("projects", projects_handler))
    app.add_handler(CommandHandler("tasks", tasks_handler))
    app.add_handler(CommandHandler("mytasks", mytasks_handler))
    app.add_handler(CommandHandler("done", done_handler))
    app.add_handler(CommandHandler("team", team_handler))
    app.add_handler(CommandHandler("remind", remind_handler))
    app.add_handler(CommandHandler("sync", sync_handler))
    app.add_handler(CommandHandler("team_status", team_status_handler))
    app.add_handler(CommandHandler("subtask", subtask_handler))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Plain text messages (natural language task orders)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    # Global error handler
    app.add_error_handler(error_handler)

    return app


def main() -> None:
    """Run the bot in polling mode."""
    logger.info("🚀 Starting Task Manager Bot...")
    logger.info(f"   Plane workspace: {settings.plane_workspace_slug}")
    logger.info(f"   LLM model: {settings.gemini_model}")
    logger.info(f"   Timezone: {settings.timezone}")

    app = build_application()
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
