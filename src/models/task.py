"""
Data models for the Task Manager system.
All models use Pydantic v2 for validation.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────

class Priority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(str, Enum):
    BACKLOG = "Backlog"
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    IN_REVIEW = "In Review"
    DONE = "Done"
    CANCELLED = "Cancelled"


# ── Team Member ────────────────────────────────────────────────────────────────

class TeamMember(BaseModel):
    name: str = Field(description="Full name của nhân sự")
    role: str = Field(description="Vai trò/chức danh trong team")
    skills: List[str] = Field(default_factory=list, description="Danh sách kỹ năng")
    telegram_username: str = Field(default="", description="Telegram username (không có @)")
    telegram_chat_id: int | None = Field(default=None, description="Chat ID riêng (nếu đã chat với bot)")
    plane_member_id: str = Field(default="", description="Plane member UUID")


# ── AI-parsed sub-task ─────────────────────────────────────────────────────────

class SubTask(BaseModel):
    name: str = Field(description="Tên ngắn gọn của sub-task")
    description: str = Field(description="Mô tả chi tiết công việc cần làm")
    priority: Priority = Field(description="Mức độ ưu tiên")
    estimated_hours: int = Field(ge=1, description="Số giờ ước tính để hoàn thành")
    deadline_days: int = Field(ge=1, description="Số ngày từ hôm nay để hoàn thành")
    suggested_assignee_name: str = Field(description="Tên nhân sự được suggest assign")
    deliverables: List[str] = Field(description="Danh sách output cụ thể cần bàn giao")
    labels: List[str] = Field(default_factory=list, description="Labels/tags cho task")


class TaskBreakdown(BaseModel):
    """Kết quả LLM phân tích và breakdown một task order."""
    project_title: str = Field(description="Tên dự án / task tổng")
    summary: str = Field(description="Tóm tắt ngắn gọn yêu cầu")
    context: str = Field(default="", description="Bối cảnh hoặc thông tin bổ sung")
    subtasks: List[SubTask] = Field(description="Danh sách sub-tasks sau khi phân rã")
    total_estimated_hours: int = Field(description="Tổng số giờ ước tính toàn bộ dự án")
    notes: str = Field(default="", description="Ghi chú thêm về rủi ro, phụ thuộc, v.v.")


# ── Created Task (after Plane sync) ───────────────────────────────────────────

class CreatedTask(BaseModel):
    """Task đã được tạo thành công trên Plane."""
    plane_id: str
    sequence_id: int
    name: str
    description: str
    priority: Priority
    status: TaskStatus
    assignee_name: str
    assignee_plane_id: str
    deadline: Optional[str] = None   # ISO date string YYYY-MM-DD
    estimated_hours: int
    deliverables: List[str]
    plane_url: str
    created_at: datetime = Field(default_factory=datetime.now)


class TaskBatch(BaseModel):
    """Một batch các tasks được tạo từ một order."""
    order_text: str
    breakdown: TaskBreakdown
    created_tasks: List[CreatedTask]
    created_at: datetime = Field(default_factory=datetime.now)
    requested_by: str = ""   # Telegram username của người tạo


# ── Plane API response shapes ──────────────────────────────────────────────────

class PlaneWorkItem(BaseModel):
    """Simplified Plane work item for display."""
    id: str
    sequence_id: int
    name: str
    priority: str
    state_name: str
    assignee_id: Optional[str] = None
    assignee_name: Optional[str] = None
    target_date: Optional[str] = None
    completed_at: Optional[str] = None
    is_overdue: bool = False

    @property
    def status_emoji(self) -> str:
        mapping = {
            "Backlog": "📋",
            "Todo": "📌",
            "In Progress": "🔄",
            "In Review": "👀",
            "Done": "✅",
            "Cancelled": "❌",
        }
        return mapping.get(self.state_name, "❓")

    @property
    def priority_emoji(self) -> str:
        mapping = {
            "urgent": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🟢",
            "none": "⚪",
        }
        return mapping.get(self.priority.lower(), "⚪")
