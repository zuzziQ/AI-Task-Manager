"""
AI Task Parser — uses Google Gemini to parse natural language task orders
into structured sub-tasks with assignees, deadlines, and deliverables.

NOTE: All LLM calls are wrapped with asyncio.to_thread() because the
google-genai SDK's synchronous client blocks the event loop otherwise.
Use `await` when calling any method on this class.
"""
import asyncio
import logging
from datetime import datetime
from functools import partial

from google import genai
from google.genai import types

from src.config import settings
from src.models.task import TaskBreakdown
from src.team_config import get_team_context

logger = logging.getLogger(__name__)


# ── System Prompt ──────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    today = datetime.now().strftime("%A, %d/%m/%Y")
    team_context = get_team_context()

    return f"""Bạn là AI Task Manager thông minh của một startup công nghệ Việt Nam.
Nhiệm vụ của bạn là phân tích yêu cầu task từ người dùng và breakdown thành các sub-tasks cụ thể, có thể thực hiện ngay.

**Ngày hôm nay:** {today}

{team_context}

**Quy tắc phân tích task:**
1. Mỗi sub-task phải đủ cụ thể để một người có thể tự thực hiện mà không cần hỏi thêm.
2. Suggest assignee dựa trên skills phù hợp nhất với công việc.
3. Deadline phải thực tế — đừng quá gấp hoặc quá dài. Xem xét độ phức tạp.
4. Deliverables là output cụ thể có thể kiểm chứng được (file, URL, PR, demo, báo cáo...).
5. Estimate hours phải thực tế, tính cả thời gian test và document.
6. Priority: urgent (hôm nay/ngày mai), high (3-5 ngày), medium (1-2 tuần), low (> 2 tuần).
7. Nếu có nhiều người phù hợp, chọn người ít task hơn (cân bằng workload).
8. Luôn trả lời bằng tiếng Việt.

**Lưu ý quan trọng:**
- Nếu yêu cầu không đủ rõ, hãy vẫn breakdown với các giả định hợp lý và ghi vào trường `notes`.
- Breakdown thành 2-8 sub-tasks. Không quá nhỏ lẻ, không quá lớn.
- Trả về JSON theo đúng schema được yêu cầu.
"""


# ── LLM Client ────────────────────────────────────────────────────────────────

class TaskParser:
    """Parses natural language task orders using Gemini/Gemma LLM.

    All public methods are async and run blocking LLM calls in a
    thread pool executor via asyncio.to_thread() to keep the event
    loop free for Telegram message handling.
    """

    def __init__(self):
        # Synchronous client — calls will be offloaded to thread pool
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        logger.info(f"TaskParser initialized with model: {self._model}")

    # ── Public async API ───────────────────────────────────────────────────────

    async def parse_task(
        self, order_text: str, current_tasks: list[dict] | None = None
    ) -> TaskBreakdown:
        """
        Parse a natural language task order into structured sub-tasks.

        Runs the synchronous Gemini API call in a thread pool so the
        Telegram event loop is never blocked.

        Args:
            order_text: The raw task order from the user.
            current_tasks: Open tasks for workload balancing context.

        Returns:
            TaskBreakdown pydantic model with all sub-tasks.
        """
        system_prompt = _build_system_prompt()

        user_parts = [order_text]
        if current_tasks:
            task_summary = "\n".join(
                f"- {t.get('name', '?')} → {t.get('assignee_name', 'Chưa assign')} [{t.get('status', '?')}]"
                for t in current_tasks[:20]
            )
            user_parts.append(
                f"\n\n**Tasks đang mở hiện tại (để cân bằng workload):**\n{task_summary}"
            )

        full_user_message = "\n".join(user_parts)
        logger.info(f"Parsing task order: {order_text[:100]}...")

        # Run blocking SDK call in thread pool
        result: TaskBreakdown = await asyncio.to_thread(
            self._sync_parse, system_prompt, full_user_message
        )
        logger.info(
            f"Parsed '{result.project_title}' → {len(result.subtasks)} sub-tasks, "
            f"~{result.total_estimated_hours}h total"
        )
        return result

    async def summarize_tasks_for_reminder(
        self,
        open_tasks: list[dict],
        overdue_tasks: list[dict],
        reminder_type: str = "morning",
    ) -> str:
        """
        Generate a natural-language summary for daily reminders.

        Args:
            open_tasks: Currently open tasks.
            overdue_tasks: Overdue tasks.
            reminder_type: "morning" or "afternoon".

        Returns:
            Vietnamese summary string with emojis.
        """
        today = datetime.now().strftime("%d/%m/%Y")

        if reminder_type == "morning":
            prompt = (
                f"Hôm nay {today}, hãy tạo báo cáo tiến độ buổi sáng ngắn gọn, "
                f"súc tích và motivating cho team.\n\n"
                f"**Tasks đang mở ({len(open_tasks)} tasks):**\n"
                f"{self._format_task_list(open_tasks)}\n"
                f"**Tasks quá hạn ({len(overdue_tasks)} tasks):**\n"
                f"{self._format_task_list(overdue_tasks)}\n\n"
                "Viết báo cáo bằng tiếng Việt, dùng emoji, không quá 300 từ. "
                "Highlight tasks quá hạn cần xử lý gấp."
            )
        else:
            prompt = (
                f"Buổi chiều {today}, tạo nhắc nhở nhẹ về tasks sắp đến deadline hoặc đang trễ.\n\n"
                f"**Tasks sắp đến hạn / quá hạn ({len(overdue_tasks)} tasks):**\n"
                f"{self._format_task_list(overdue_tasks)}\n\n"
                "Viết thông báo ngắn gọn, thân thiện bằng tiếng Việt, dùng emoji."
            )

        try:
            text: str = await asyncio.to_thread(self._sync_text_generate, prompt)
            return text or "📊 Không thể tạo báo cáo lúc này."
        except Exception as e:
            logger.error(f"Failed to generate reminder summary: {e}")
            return self._fallback_summary(open_tasks, overdue_tasks, reminder_type)

    # ── Sync helpers (run inside thread pool) ─────────────────────────────────

    def _sync_parse(self, system_prompt: str, user_message: str) -> TaskBreakdown:
        """Blocking Gemini call for structured task parsing."""
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=TaskBreakdown,
                    temperature=0.3,
                ),
            )
            return response.parsed
        except Exception as e:
            logger.error(f"LLM parsing failed: {e}", exc_info=True)
            raise RuntimeError(f"Không thể phân tích task: {e}") from e

    def _sync_text_generate(self, prompt: str) -> str:
        """Blocking Gemini call for free-form text generation."""
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=600,
            ),
        )
        return response.text or ""

    # ── Formatting helpers ─────────────────────────────────────────────────────

    def _format_task_list(self, tasks: list[dict]) -> str:
        if not tasks:
            return "  (Không có)\n"
        lines = []
        for t in tasks:
            name = t.get("name", "?")
            assignee = t.get("assignee_name", "Chưa assign")
            status = t.get("status", "?")
            deadline = t.get("target_date", "Chưa có")
            lines.append(f"  • [{status}] {name} → {assignee} | Deadline: {deadline}")
        return "\n".join(lines) + "\n"

    def _fallback_summary(
        self, open_tasks: list, overdue_tasks: list, reminder_type: str
    ) -> str:
        """Fallback if LLM call fails."""
        today = datetime.now().strftime("%d/%m/%Y")
        if reminder_type == "morning":
            return (
                f"☀️ **Báo cáo tiến độ sáng {today}**\n\n"
                f"📋 Tasks đang mở: **{len(open_tasks)}**\n"
                f"⚠️ Tasks quá hạn: **{len(overdue_tasks)}**\n\n"
                + (
                    "🔴 Cần xử lý gấp các tasks quá hạn!\n"
                    if overdue_tasks
                    else "✅ Không có task quá hạn.\n"
                )
            )
        return (
            f"⏰ **Nhắc nhở cuối ngày {today}**\n\n"
            f"⚠️ Còn **{len(overdue_tasks)}** tasks quá hạn/sắp đến hạn.\n"
            "Hãy cập nhật tiến độ nhé!"
        )


    async def detect_intent(self, text: str) -> UserIntent:
        """
        Detect user intent (leave, late, extend_deadline, normal_task, unknown)
        and extract parameters.
        """
        today = datetime.now().strftime("%Y-%m-%d (%A)")
        system_prompt = f"""Bạn là trợ lý ảo HR thông minh. Nhiệm vụ của bạn là phân tích tin nhắn người dùng và phân loại intent chính xác.
Ngày hôm nay là: {today}

Hãy trích xuất các thông tin cần thiết dựa theo cấu trúc Pydantic được cung cấp.
Lưu ý về ngày tháng:
- Nếu người dùng nhắn "hôm nay", hãy điền start_date và end_date là ngày hôm nay ({datetime.now().strftime("%Y-%m-%d")}).
- Nếu người dùng nhắn "ngày mai", hãy tính toán đúng ngày tiếp theo.
- Luôn trả về đúng định dạng YYYY-MM-DD."""

        result: UserIntent = await asyncio.to_thread(
            self._sync_detect_intent, system_prompt, text
        )
        return result

    def _sync_detect_intent(self, system_prompt: str, user_message: str) -> UserIntent:
        """Blocking Gemini call for intent detection."""
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=UserIntent,
                    temperature=0.1,
                ),
            )
            return response.parsed
        except Exception as e:
            logger.error(f"LLM intent detection failed: {e}", exc_info=True)
            # Safe fallback
            return UserIntent(intent="unknown")


# ── UserIntent Model ──────────────────────────────────────────────────────────
from pydantic import BaseModel, Field

class UserIntent(BaseModel):
    intent: str = Field(description="Intent: 'leave' (xin nghỉ), 'late' (xin đi muộn/trễ), 'extend_deadline' (lùi deadline), 'normal_task' (tạo công việc mới), 'unknown'")
    leave_type: str | None = Field(default=None, description="Loại nghỉ nếu intent='leave': 'sick' (nghỉ ốm), 'annual' (nghỉ phép năm), 'personal' (việc riêng)")
    start_date: str | None = Field(default=None, description="Ngày bắt đầu nghỉ YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="Ngày kết thúc nghỉ YYYY-MM-DD")
    reason: str | None = Field(default=None, description="Lý do xin nghỉ hoặc đi muộn")
    task_key: str | None = Field(default=None, description="Mã task cần lùi deadline (ví dụ: T-001 hoặc t1)")
    days_to_extend: int | None = Field(default=None, description="Số ngày muốn lùi deadline")


# ── Singleton ──────────────────────────────────────────────────────────────────
_parser_instance: TaskParser | None = None


def get_parser() -> TaskParser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = TaskParser()
    return _parser_instance
