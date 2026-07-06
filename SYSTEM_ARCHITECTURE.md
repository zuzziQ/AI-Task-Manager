---
type: docs
title: SYSTEM ARCHITECTURE
description: Standardized system architecture document for AI Task Manager.
tags: [architecture, telegram-bot, gemini, ai-task-manager]
---

# Tài liệu kỹ thuật — AI Task Manager System

> **Version:** 1.0  
> **Stack:** Python 3.12 · Telegram Bot · Gemma 4 31B (Gemini API) · Plane.so · Google Sheets  
> **Deploy:** Docker / VPS

---

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Kiến trúc tổng thể](#2-kiến-trúc-tổng-thể)
3. [Cấu trúc thư mục](#3-cấu-trúc-thư-mục)
4. [Luồng dữ liệu chính](#4-luồng-dữ-liệu-chính)
5. [Chi tiết từng module](#5-chi-tiết-từng-module)
   - [5.1 Config & Settings](#51-config--settings)
   - [5.2 Data Models](#52-data-models)
   - [5.3 Team Config](#53-team-config)
   - [5.4 AI Task Parser](#54-ai-task-parser)
   - [5.5 Plane.so Client](#55-planeso-client)
   - [5.6 Google Sheets Client](#56-google-sheets-client)
   - [5.7 Telegram Bot](#57-telegram-bot)
   - [5.8 Scheduler / Reminder](#58-scheduler--reminder)
6. [Tính năng chi tiết](#6-tính-năng-chi-tiết)
7. [Cấu hình & ENV Variables](#7-cấu-hình--env-variables)
8. [Deploy lên VPS (Docker)](#8-deploy-lên-vps-docker)
9. [Hướng dẫn mở rộng](#9-hướng-dẫn-mở-rộng)

---

## 1. Tổng quan hệ thống

AI Task Manager là một hệ thống tự động hóa quản lý task cho team startup. Người dùng chỉ cần gửi mô tả công việc bằng ngôn ngữ tự nhiên qua Telegram — hệ thống tự động làm hết phần còn lại:

```
Mô tả task (tiếng Việt)
        ↓
AI phân tích & breakdown
        ↓
Assign nhân sự phù hợp theo skill
        ↓
Đề xuất deadline & deliverables cụ thể
        ↓
Tạo trên Plane.so (quản lý chi tiết)
        ↓
Sync lên Google Sheets (dashboard)
        ↓
Nhắc nhở tự động hàng ngày
```

### Vì sao chọn stack này?

| Thành phần | Lý do |
|-----------|-------|
| **Telegram Bot** | Ubiquitous trong team Việt Nam, mobile-first, dễ dùng |
| **Gemma 4 31B** | Context window lớn, tiếng Việt xuất sắc, structured output chính xác |
| **Plane.so** | Miễn phí, 180+ API endpoints, UI đẹp, alternative tốt cho Jira |
| **Google Sheets** | Mọi người biết dùng, dễ share, pivot table, không cần học tool mới |
| **Python + PTB** | Async-native, mature ecosystem, dễ maintain |

---

## 2. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────┐
│                      TELEGRAM                               │
│  User/Group Chat                                            │
│  • Text messages    • /commands    • Button callbacks       │
└──────────────────────────┬──────────────────────────────────┘
                           │ python-telegram-bot v21
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   BOT APPLICATION LAYER                     │
│  src/agent/                                                 │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │  bot.py      │  │  handlers.py    │  │ keyboards.py  │  │
│  │  (Entry +    │  │  (Commands +    │  │ (Buttons)     │  │
│  │   Scheduler  │  │   Callbacks +   │  │               │  │
│  │   Setup)     │  │   State Mgmt)   │  │               │  │
│  └──────────────┘  └────────┬────────┘  └───────────────┘  │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │  formatters.py  │                      │
│                    │  (MarkdownV2)   │                      │
│                    └─────────────────┘                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  AI ENGINE   │  │  PLANE.SO    │  │   GOOGLE     │
│  src/ai/     │  │  INTEGRATION │  │   SHEETS     │
│              │  │  src/integr/ │  │  src/integr/ │
│ task_parser  │  │ plane_client │  │sheets_client │
│              │  │              │  │              │
│ Gemma 4 31B  │  │  REST API    │  │  gspread     │
│ via Gemini   │  │  (httpx)     │  │  Service Acc │
│   API        │  │              │  │              │
└──────────────┘  └──────────────┘  └──────────────┘
          ▲
          │
┌──────────────────────────────────────────────────┐
│              SCHEDULER                           │
│  src/scheduler/reminder.py                       │
│                                                  │
│  PTB JobQueue (APScheduler)                      │
│  • 08:30 daily  • 17:00 daily  • Mon 09:00       │
└──────────────────────────────────────────────────┘
```

---

## 3. Cấu trúc thư mục

```
Task-Manager/
│
├── src/                          # Source code chính
│   ├── __init__.py
│   │
│   ├── config.py                 # Cấu hình từ .env (pydantic-settings)
│   ├── team_config.py            # Danh sách nhân sự + skills
│   │
│   ├── agent/                    # Telegram bot layer
│   │   ├── __init__.py
│   │   ├── bot.py                # Entry point — khởi động application
│   │   ├── handlers.py           # Xử lý tất cả commands & messages
│   │   ├── keyboards.py          # Inline keyboards & reply keyboards
│   │   └── formatters.py         # Format messages → Telegram MarkdownV2
│   │
│   ├── ai/                       # AI engine
│   │   ├── __init__.py
│   │   └── task_parser.py        # Gemma 4 31B integration, structured output
│   │
│   ├── integrations/             # External service clients
│   │   ├── __init__.py
│   │   ├── plane_client.py       # Plane.so REST API wrapper (async httpx)
│   │   └── sheets_client.py      # Google Sheets client (gspread)
│   │
│   ├── models/                   # Pydantic data models
│   │   ├── __init__.py
│   │   └── task.py               # All domain models
│   │
│   └── scheduler/                # Scheduled jobs
│       ├── __init__.py
│       └── reminder.py           # Daily/weekly reminder logic
│
├── Dockerfile                    # Python 3.12-slim image
├── docker-compose.yml            # VPS deployment config
├── requirements.txt              # Python dependencies
├── .env.example                  # Template biến môi trường
├── .env                          # (gitignored) Biến môi trường thật
├── service_account.json          # (gitignored) Google Cloud credentials
├── .gitignore
└── README.md
```

---

## 4. Luồng dữ liệu chính

### Flow 1: Tạo task mới

```
User nhắn tin
    │
    ▼
message_handler() / newtask_handler()
    │ text > 10 chars
    ▼
_process_task_order()
    │
    ├─→ [Plane API] get_open_tasks()        ← lấy context workload hiện tại
    │
    ├─→ [LLM] TaskParser.parse_task()       ← Gemma 4 31B phân tích
    │         ↓ returns TaskBreakdown
    │
    ├─→ Lưu vào _pending_batches{batch_id}  ← in-memory, 1 step confirmation
    │
    └─→ Telegram: format_task_breakdown()   ← hiển thị preview + buttons

User bấm ✅ Xác nhận
    │
    ▼
callback_handler() → _handle_confirm()
    │
    ├─→ FOR EACH SubTask:
    │       ├─→ plane.resolve_member_id()   ← map tên → Plane UUID
    │       ├─→ plane.create_work_item()    ← POST /work-items/
    │       └─→ sheets.upsert_task()        ← append/update row
    │
    └─→ Telegram: format_created_tasks_summary()  ← success message
```

### Flow 2: Daily reminder (tự động)

```
JobQueue fires (08:30 / 17:00 / Mon 09:00)
    │
    ▼
morning_job() / afternoon_job() / weekly_job()
    │
    ├─→ [Plane API] get_open_tasks()
    ├─→ [Plane API] get_overdue_tasks()
    │
    ├─→ [LLM] summarize_tasks_for_reminder()   ← AI tạo tóm tắt dạng prose
    │
    ├─→ Telegram: format_daily_reminder()       ← gửi vào admin chat
    │
    └─→ [Sheets] sync_tasks_from_plane()        ← cập nhật toàn bộ sheet
        [Sheets] log_reminder()                 ← append vào Daily Log
```

### Flow 3: Sync thủ công

```
/sync command
    │
    ▼
sync_handler()
    │
    ├─→ [Plane] list_work_items()          ← tất cả tasks
    └─→ [Sheets] sync_tasks_from_plane()   ← upsert toàn bộ
```

---

## 5. Chi tiết từng module

### 5.1 Config & Settings

**File:** [`src/config.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/config.py)

Sử dụng `pydantic-settings` để load biến môi trường với type safety:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    telegram_bot_token: str = ""
    plane_api_key: str = ""
    gemini_model: str = "gemma-4-31b"
    timezone: str = "Asia/Ho_Chi_Minh"
    ...

    @property
    def allowed_chat_ids(self) -> List[int]:
        # Parse "id1,id2,id3" → [id1, id2, id3]
```

**Đặc điểm:**
- Tất cả settings đều có default value → không crash khi thiếu ENV
- `allowed_chat_ids` property tự parse chuỗi CSV sang list int
- Singleton: `settings = Settings()` ở module level

---

### 5.2 Data Models

**File:** [`src/models/task.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/models/task.py)

#### Enums

```python
class Priority(str, Enum):
    URGENT = "urgent"    # 🔴 Hôm nay/ngày mai
    HIGH   = "high"      # 🟠 3-5 ngày
    MEDIUM = "medium"    # 🟡 1-2 tuần
    LOW    = "low"       # 🟢 > 2 tuần

class TaskStatus(str, Enum):
    BACKLOG     = "Backlog"
    TODO        = "Todo"
    IN_PROGRESS = "In Progress"
    IN_REVIEW   = "In Review"
    DONE        = "Done"
    CANCELLED   = "Cancelled"
```

#### Model hierarchy

```
TeamMember              ← Cấu hình nhân sự (team_config.py)
    • name, role, skills
    • telegram_username, plane_member_id

SubTask                 ← Output của LLM cho 1 sub-task
    • name, description
    • priority (Priority enum)
    • estimated_hours, deadline_days
    • suggested_assignee_name
    • deliverables: List[str]

TaskBreakdown           ← Toàn bộ kết quả phân tích LLM
    • project_title, summary
    • subtasks: List[SubTask]
    • total_estimated_hours, notes

CreatedTask             ← Task đã tạo thành công trên Plane
    • plane_id (UUID), sequence_id (#123)
    • plane_url
    • assignee_name, assignee_plane_id
    • deadline (YYYY-MM-DD)

PlaneWorkItem           ← Task từ Plane API (cho display)
    • status_emoji property → ✅/🔄/📌...
    • priority_emoji property → 🔴/🟠/🟡/🟢
    • is_overdue (computed từ target_date vs today)
```

---

### 5.3 Team Config

**File:** [`src/team_config.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/team_config.py)

File duy nhất cần chỉnh sửa để configure team. Mỗi thành viên:

```python
TeamMember(
    name="Huy",
    role="Backend Developer",
    skills=["Python", "Django", "PostgreSQL", "API"],
    telegram_username="huy_dev",   # để mention khi assign
    plane_member_id="",            # tự resolve từ API
)
```

**`get_team_context()`** — tạo context string cho LLM prompt:
```
Danh sách nhân sự trong team:
- Huy (Backend Developer): Skills → Python, Django, PostgreSQL, API
- Linh (Frontend Developer): Skills → React, NextJS, TypeScript, UI/UX
...
Khi assign task, hãy dựa vào skills để suggest người phù hợp nhất.
Cân bằng workload giữa các thành viên.
```

LLM dùng thông tin này để assign đúng người — ví dụ task "làm API endpoint" → assign Huy, task "làm UI component" → assign Linh.

---

### 5.4 AI Task Parser

**File:** [`src/ai/task_parser.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/ai/task_parser.py)

#### Cách hoạt động

```python
class TaskParser:
    async def parse_task(self, order_text: str, current_tasks: list) -> TaskBreakdown:
```

**Input:** chuỗi text tự nhiên + danh sách tasks đang mở (context workload)

**Process:**
1. Build system prompt chứa:
   - Ngày hôm nay
   - Danh sách nhân sự + skills
   - Quy tắc phân tích (deadline realistic, deliverables kiểm chứng được, ...)
2. Gọi Gemini API với `response_schema=TaskBreakdown` → **guaranteed JSON output**
3. SDK tự parse JSON → trả về Pydantic model

**Structured Output Schema:**
```python
# Gemini API được constrain bởi schema này
# → KHÔNG thể trả về format khác
config=GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=TaskBreakdown,    # Pydantic model
    temperature=0.3,                  # Low temp = deterministic
)
```

**Workload balancing:** Nếu Huy đang có 5 tasks mở còn Nam chỉ có 1 task, LLM sẽ prefer assign cho Nam khi skill tương đương.

#### AI Reminder Summary

```python
async def summarize_tasks_for_reminder(
    self, open_tasks, overdue_tasks, reminder_type
) -> str:
```

Tạo văn bản tóm tắt tự nhiên (không phải bullet list khô khan) cho daily reminder. Ví dụ output:

> *"Chào buổi sáng team! Hôm nay có 8 tasks đang chạy, trong đó Huy đang xử lý tính năng auth (deadline ngày mai - cần theo dõi sát!), Linh đang làm dashboard UI (đúng tiến độ). Đặc biệt có 2 tasks quá hạn cần xử lý gấp..."*

---

### 5.5 Plane.so Client

**File:** [`src/integrations/plane_client.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/integrations/plane_client.py)

HTTP client async dùng `httpx`. Tất cả requests đều có `X-API-Key` header.

#### Endpoints sử dụng

| Method | Endpoint | Mục đích |
|--------|----------|---------|
| `GET` | `/workspaces/{slug}/members/` | Lấy danh sách thành viên, build cache |
| `GET` | `/workspaces/{slug}/projects/{id}/states/` | Lấy các trạng thái task |
| `POST` | `/workspaces/{slug}/projects/{id}/work-items/` | Tạo task mới |
| `GET` | `/workspaces/{slug}/projects/{id}/work-items/` | Lấy danh sách tasks |
| `PATCH` | `/workspaces/{slug}/projects/{id}/work-items/{item_id}/` | Update trạng thái |

#### Member resolution

```python
async def resolve_member_id(self, name: str) -> str | None:
    # 1. Lấy tất cả members từ API → cache dict: name.lower() → id
    # 2. Exact match
    # 3. Partial match (chứa trong nhau)
    # 4. Fallback sang team_config.py
```

#### Tạo work item

Description được tạo dạng Markdown với checklist deliverables:
```
Mô tả task...

## ✅ Deliverables
- [ ] API endpoint /auth/login
- [ ] Unit tests coverage > 80%
- [ ] API documentation
```

#### Overdue detection

```python
is_overdue = (
    target_date < today
    and state_name not in ("Done", "Cancelled")
)
```

#### Pagination

List endpoint hỗ trợ pagination — client tự loop theo `next` URL cho đến khi hết.

---

### 5.6 Google Sheets Client

**File:** [`src/integrations/sheets_client.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/integrations/sheets_client.py)

Dùng `gspread` + Google Service Account credentials (không cần browser/OAuth).

#### Cấu trúc 2 sheets

**Sheet "Tasks"** — Dashboard chính:

| Cột | Nội dung | Ví dụ |
|-----|---------|-------|
| A | Task ID | `#42` |
| B | Tiêu đề | `Làm API đăng nhập` |
| C | Người phụ trách | `Huy` |
| D | Độ ưu tiên | `🔴 Khẩn cấp` |
| E | Trạng thái | `In Progress` |
| F | Deadline | `2024-01-15` |
| G | Ước tính (giờ) | `8` |
| H | Deliverables | `API endpoint \| Unit tests \| Docs` |
| I | Link Plane | `https://app.plane.so/...` |
| J | Ngày tạo | `27/06/2024 14:30` |
| K | Cập nhật lần cuối | `27/06/2024 18:00` |

**Sheet "Daily Log"** — Lịch sử nhắc nhở:

| Thời gian | Loại | Nội dung | Tasks mở | Tasks quá hạn |
|-----------|------|---------|---------|--------------|
| 27/06/2024 08:30 | morning | Chào buổi sáng... | 8 | 2 |

#### Upsert logic

```python
async def upsert_task(self, task: CreatedTask):
    # Tìm row có Task ID khớp (#sequence_id)
    task_id_col = ws.col_values(1)    # đọc cột A
    if "#42" in task_id_col:
        row_idx = task_id_col.index("#42") + 1
        ws.update(f"A{row_idx}:K{row_idx}", [row_data])   # UPDATE
    else:
        ws.append_row(row_data)   # INSERT
```

Không tạo duplicate — luôn upsert theo `sequence_id`.

#### Auto-create worksheets

Nếu sheet "Tasks" hoặc "Daily Log" chưa tồn tại, client tự tạo với headers + formatting (bold, màu nền, freeze row đầu).

---

### 5.7 Telegram Bot

**File chính:** [`src/agent/bot.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/agent/bot.py), [`src/agent/handlers.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/agent/handlers.py)

#### Handler map

```python
# Commands
/start      → start_handler()      # Welcome message + main menu
/help       → help_handler()       # Danh sách lệnh
/newtask    → newtask_handler()    # Tạo task từ args
/tasks      → tasks_handler()      # Xem tất cả open tasks
/mytasks    → mytasks_handler()    # Tasks của tôi (match Telegram username)
/team       → team_handler()       # Workload overview
/remind     → remind_handler()     # Manual trigger reminder
/sync       → sync_handler()       # Force sync lên Sheets

# Plain text → message_handler()
"📋 Xem tasks"    → tasks_handler()
"👤 Task của tôi" → mytasks_handler()
"📊 Báo cáo ngay" → remind_handler()
"👥 Team"         → team_handler()
len(text) > 10   → _process_task_order()   # treat as task order

# Inline buttons → callback_handler()
"confirm:{batch_id}"        → _handle_confirm()
"cancel:{batch_id}"         → _handle_cancel()
"edit:{batch_id}"           → _handle_edit()
"status:{item_id}:{state}"  → _handle_status_update()
```

#### State management — Pending batches

In-memory dict lưu pending confirmations:
```python
_pending_batches: dict[str, tuple[str, TaskBreakdown]] = {}
# key: batch_id (8-char UUID)
# value: (order_text, breakdown)
```

Khi user confirm → pop từ dict → tạo tasks → clear.  
Khi user cancel/edit → pop từ dict → discard.

#### Access control

```python
def _is_allowed(chat_id: int) -> bool:
    allowed = settings.allowed_chat_ids
    if not allowed:
        return True   # Cho phép tất cả nếu chưa configure
    return chat_id in allowed
```

#### UX patterns

- **Typing indicator** trước mọi thao tác tốn thời gian (`ChatAction.TYPING`)
- **"Đang phân tích..."** message được delete sau khi có kết quả
- **Inline keyboard** 3 nút: ✅ Xác nhận / ✏️ Chỉnh sửa / ❌ Hủy
- **Main menu** persistent reply keyboard ở bottom
- **Error messages** thân thiện, không expose stack trace

#### formatters.py

Toàn bộ text đều escaped theo Telegram MarkdownV2:
```python
def escape_md(text: str) -> str:
    specials = r"\_*[]()~`>#+-=|{}.!"
    for ch in specials: text = text.replace(ch, f"\\{ch}")
    return text
```

---

### 5.8 Scheduler / Reminder

**File:** [`src/scheduler/reminder.py`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/src/scheduler/reminder.py)

Dùng **python-telegram-bot JobQueue** (wrapper của APScheduler) — async-safe, không block bot.

#### Lịch chạy

```python
def register_jobs(app) -> None:
    # Morning: daily tại giờ config (default 08:30 ICT)
    jq.run_daily(morning_job, time=time(8, 30, tzinfo=tz), ...)

    # Afternoon: daily tại 17:00 ICT
    jq.run_daily(afternoon_job, time=time(17, 0, tzinfo=tz), ...)

    # Weekly: Thứ Hai 09:00 ICT
    jq.run_daily(weekly_job, time=time(9, 0, tzinfo=tz), days=(0,), ...)
```

#### Hệ thống Hybrid Reminder

Sử dụng kết hợp tin nhắn riêng (DM) và nhóm chung để tối ưu trải nghiệm.

**Morning (08:30):**
- Lấy open tasks + overdue tasks từ Plane.
- **Tin nhắn riêng (DM):** Nhắc từng người cụ thể các tasks quá hạn và đang xử lý của họ.
- **Nhóm chung:** Gửi tóm tắt ngắn gọn do AI tạo ra (dùng `disable_notification=True` để không làm ồn).
- Đồng bộ toàn bộ dữ liệu lên Sheets và lưu vào nhật ký (Daily Log).

**Afternoon (17:00):**
- Lấy các tasks sắp đến hạn hoặc đã quá hạn.
- **Tin nhắn riêng (DM):** CHỈ gửi cho những ai có task quá hạn/sắp hạn (không spam toàn đội nếu mọi người đều ổn).
- **Nhóm chung:** Gửi một thông báo nhỏ gọn xác nhận đã nhắc riêng bao nhiêu thành viên (chế độ im lặng).
- Ghi nhật ký vào Sheets.

**Monday Weekly (09:00):**
- Đếm số lượng tasks `Done` trong tuần và số tasks đang mở.
- Gửi thống kê kèm lời chúc động viên vào Nhóm chung (có thông báo).

---

## 6. Tính năng chi tiết

### 6.1 Nhận task từ nhiều cách

```
✅ Nhắn text tự nhiên (> 10 ký tự)
✅ /newtask <mô tả>
✅ Bấm nút "📋 Xem tasks" từ main menu
✅ Hoạt động trong DM hoặc Group chat
```

### 6.2 AI phân tích task

LLM nhận input và trả về:

- **project_title** — tên tổng dự án
- **summary** — 1 câu tóm tắt
- **subtasks[]** mỗi task có:
  - Tên rõ ràng, có thể action ngay
  - Mô tả chi tiết
  - Priority (urgent/high/medium/low)
  - Estimated hours (realistic, tính cả test & doc)
  - Deadline (ngày cụ thể)
  - Suggested assignee (dựa trên skill matrix)
  - Deliverables (list output kiểm chứng được)
- **notes** — rủi ro, phụ thuộc, giả định

### 6.3 Assign thông minh

Dựa trên 2 yếu tố:
1. **Skill match** — task backend → assign backend dev
2. **Workload balance** — người ít task hơn được ưu tiên

Context workload được fetch từ Plane trước khi call LLM.

### 6.4 Confirmation flow

```
[AI Breakdown Preview]
┌────────────────────────────────────┐
│ 🤖 Phân tích task xong!            │
│                                    │
│ 📌 Tính năng đăng nhập web         │
│ "Xây dựng hệ thống auth..."        │
│ ⏱ Tổng: 24 giờ | 📦 3 sub-tasks   │
│ ─────────────────────────────────  │
│ 1. Backend API Login               │
│    👤 Huy | 🔴 Khẩn cấp           │
│    ⏰ 8h | Deadline: ngày mai       │
│    ▪ POST /auth/login endpoint     │
│    ▪ JWT token generation          │
│    ▪ Unit tests                    │
│                                    │
│ 2. Frontend Login UI               │
│    👤 Linh | 🟠 Cao                │
│    ⏰ 6h | Deadline: 3 ngày        │
│    ▪ Login form component          │
│    ▪ Responsive design             │
│ ...                                │
│                                    │
│ [✅ Xác nhận & Tạo task]           │
│ [✏️ Chỉnh sửa]  [❌ Hủy]          │
└────────────────────────────────────┘
```

### 6.5 Xem tasks

**/tasks** — Nhóm theo assignee:
```
📋 Tất cả tasks đang mở (8 tasks)

👤 Huy
  🔄 🔴 #12 API Login endpoint | 📅 2024-01-15
  📌 🟠 #15 Database migration | 📅 2024-01-20

👤 Linh
  🔄 🟡 #13 Login UI component | 📅 2024-01-17 ⚠️
```

**/mytasks** — Match Telegram username với team_config → filter tasks của đúng người.

**/team** — Workload summary:
```
👥 Tổng quan nhân sự
👤 Huy: 5 đang mở, 2 hoàn thành ⚠️1 quá hạn
👤 Linh: 2 đang mở, 4 hoàn thành
```

### 6.6 Update status nhanh

Từ danh sách task, mỗi task có thể update status bằng inline keyboard:
- 🔄 In Progress
- 👀 In Review
- ✅ Done
- ❌ Cancelled

---

## 7. Cấu hình & ENV Variables

**File:** [`.env.example`](file:///Users/lhda/Documents/Code/StoryMee/Task-Manager/.env.example)

```bash
# ─── TELEGRAM ────────────────────────────────
TELEGRAM_BOT_TOKEN=        # @BotFather → tạo bot → lấy token
TELEGRAM_ALLOWED_CHAT_IDS= # -100xxx,yyy (để trống = cho phép tất cả)
TELEGRAM_ADMIN_CHAT_ID=    # Group/chat nhận daily reminder

# ─── PLANE.SO ────────────────────────────────
PLANE_API_KEY=             # Profile > API Tokens
PLANE_WORKSPACE_SLUG=      # URL: app.plane.so/{SLUG}/...
PLANE_PROJECT_ID=          # UUID của project
PLANE_BASE_URL=https://api.plane.so/api/v1  # Cloud hoặc self-hosted

# ─── GOOGLE SHEETS ───────────────────────────
GOOGLE_SHEET_ID=           # URL: /spreadsheets/d/{SHEET_ID}/
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json

# ─── AI ENGINE ───────────────────────────────
GEMINI_API_KEY=            # aistudio.google.com
GEMINI_MODEL=gemma-4-31b   # Model ID

# ─── SCHEDULER ───────────────────────────────
TIMEZONE=Asia/Ho_Chi_Minh
MORNING_REMINDER_HOUR=8
MORNING_REMINDER_MINUTE=30
AFTERNOON_REMINDER_HOUR=17
AFTERNOON_REMINDER_MINUTE=0
```

---

## 8. Deploy lên VPS (Docker)

### Yêu cầu VPS

- RAM: ≥ 512 MB (bot dùng ~128-256 MB)
- Storage: ≥ 1 GB
- OS: Ubuntu 22.04+ hoặc Debian 12+
- Cần: Docker + Docker Compose v2

### Bước deploy

```bash
# 1. SSH vào VPS
ssh user@your-vps-ip

# 2. Clone repo
git clone <repo-url> && cd Task-Manager

# 3. Cấu hình environment
cp .env.example .env
nano .env           # điền tất cả giá trị

# 4. Upload service_account.json
# (từ máy local)
scp service_account.json user@your-vps-ip:~/Task-Manager/

# 5. Build và chạy
docker compose up -d --build

# 6. Kiểm tra logs
docker compose logs -f --tail=50
```

### Docker Compose config

```yaml
services:
  task-manager-bot:
    restart: unless-stopped    # Tự restart nếu crash
    env_file: .env             # Load tất cả từ .env
    volumes:
      - ./service_account.json:/app/service_account.json:ro
      - ./logs:/app/logs
    deploy:
      resources:
        limits:
          cpus: "0.50"
          memory: "256M"       # Phù hợp VPS nhỏ 512MB RAM
    healthcheck:
      interval: 60s            # Kiểm tra mỗi phút
```

### Quản lý

```bash
# Xem logs
docker compose logs -f

# Restart
docker compose restart

# Update code
git pull && docker compose up -d --build

# Stop
docker compose down

# Xem resource usage
docker stats task-manager-bot
```

---

## 9. Hướng dẫn mở rộng

### Thêm thành viên mới

```python
# src/team_config.py
TEAM_MEMBERS.append(TeamMember(
    name="Tú",
    role="QA Engineer",
    skills=["Testing", "Selenium", "Postman", "Documentation"],
    telegram_username="tu_qa",
    plane_member_id="",
))
```

Bot sẽ tự động biết Tú khi phân tích task có liên quan đến testing/QA.

### Thêm command mới

```python
# src/agent/handlers.py
async def stats_handler(update, context):
    # Logic xem thống kê
    pass

# src/agent/bot.py
app.add_handler(CommandHandler("stats", stats_handler))
```

### Thêm loại reminder

```python
# src/scheduler/reminder.py
async def friday_reminder(bot, chat_id):
    # Nhắc cập nhật tiến độ cuối tuần
    pass

# Trong register_jobs():
jq.run_daily(friday_wrap, time=time(16, 0, tzinfo=tz), days=(4,))  # Friday=4
```

### Tích hợp thêm tool khác

Cấu trúc `src/integrations/` cho phép dễ dàng thêm:
- **Jira** thay thế hoặc song song Plane
- **Notion** thay thế Google Sheets
- **Slack** song song Telegram

---

## Checklist triển khai

```
□ Tạo Telegram Bot qua @BotFather → lấy token
□ Lấy Plane API Key từ Profile Settings
□ Lấy Plane Workspace Slug và Project ID từ URL
□ Tạo Google Cloud Project → Enable Sheets API + Drive API
□ Tạo Service Account → Download JSON → Đặt tên service_account.json
□ Tạo Google Sheet → Share cho service account email
□ Lấy Gemini API Key từ aistudio.google.com
□ Điền .env với tất cả giá trị trên
□ Cập nhật TEAM_MEMBERS trong src/team_config.py
□ Lấy Chat ID của group admin → điền TELEGRAM_ADMIN_CHAT_ID
□ Lấy Chat ID được phép dùng bot → điền TELEGRAM_ALLOWED_CHAT_IDS
□ docker compose up -d --build
□ Test: gửi /start cho bot
□ Test: nhắn mô tả task tự nhiên → confirm → kiểm tra Plane + Sheets
```
