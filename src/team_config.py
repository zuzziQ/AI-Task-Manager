"""
Team configuration — define your team members here.
Loads dynamically from the backend API if available, falls back to static list.
"""
import logging
import httpx
from src.models.task import TeamMember
from src.config import settings

logger = logging.getLogger(__name__)

# Fallback team list
TEAM_MEMBERS: list[TeamMember] = [
    TeamMember(
        name="Huy",
        role="Backend Developer",
        skills=["Python", "Django", "PostgreSQL", "API", "DevOps"],
        telegram_username="huy_dev",
        telegram_chat_id=None,
        plane_member_id="",
    ),
    TeamMember(
        name="Linh",
        role="Frontend Developer",
        skills=["React", "NextJS", "TypeScript", "UI/UX", "CSS"],
        telegram_username="linh_fe",
        telegram_chat_id=None,
        plane_member_id="",
    ),
    TeamMember(
        name="Nam",
        role="Full Stack Developer",
        skills=["Python", "React", "Docker", "Testing", "CI/CD"],
        telegram_username="nam_dev",
        telegram_chat_id=None,
        plane_member_id="",
    ),
    TeamMember(
        name="An",
        role="Project Manager",
        skills=["Planning", "Communication", "Documentation", "QA"],
        telegram_username="an_pm",
        telegram_chat_id=None,
        plane_member_id="",
    ),
]

TEAM_BY_NAME: dict[str, TeamMember] = {m.name.lower(): m for m in TEAM_MEMBERS}


async def load_team_members() -> None:
    """Fetch team members from backend Node.js API."""
    url = f"{settings.backend_api_url}/api/omnitask/team-members"
    logger.info(f"🔄 Loading team members from backend API: {url}...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                members_list = data.get("data", []) if isinstance(data, dict) else data
                if isinstance(members_list, list):
                    fetched_members = []
                    for item in members_list:
                        try:
                            # Map camelCase from Node.js/Prisma to snake_case in Pydantic
                            mapped_item = {
                                "name": item.get("fullName", ""),
                                "role": item.get("role", "developer"),
                                "skills": item.get("skills", []),
                                "telegram_username": item.get("telegramUsername", "") or "",
                                "telegram_chat_id": int(item.get("telegramChatId")) if item.get("telegramChatId") is not None else None,
                                "plane_member_id": item.get("planeMemberId", "") or "",
                            }
                            fetched_members.append(TeamMember(**mapped_item))
                        except Exception as parse_err:
                            logger.error(f"Error parsing team member item: {item}. Error: {parse_err}")

                    if fetched_members:
                        TEAM_MEMBERS.clear()
                        TEAM_MEMBERS.extend(fetched_members)
                        
                        # Rebuild dict
                        TEAM_BY_NAME.clear()
                        for m in TEAM_MEMBERS:
                            TEAM_BY_NAME[m.name.lower()] = m
                            
                        logger.info(f"✅ Dynamic team load success: Loaded {len(TEAM_MEMBERS)} members.")
                        return
            logger.warn(f"⚠️ Failed to load team from API: {response.status_code}. Using fallback.")
    except Exception as e:
        logger.error(f"❌ Error loading team from API: {e}. Using fallback.")


async def update_member_chat_id(telegram_username: str, chat_id: int) -> bool:
    """Send user telegram chat ID back to Node.js backend for database sync."""
    url = f"{settings.backend_api_url}/api/omnitask/update-chat-id"
    payload = {
        "telegram_username": telegram_username,
        "telegram_chat_id": chat_id
    }
    logger.info(f"🔄 Sending chat ID update for {telegram_username} ({chat_id}) to {url}...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                logger.info(f"✅ Backend chat ID sync success for {telegram_username}.")
                # Update local in-memory config as well
                for m in TEAM_MEMBERS:
                    if m.telegram_username.replace("@", "").lower() == telegram_username.replace("@", "").lower():
                        m.telegram_chat_id = chat_id
                        TEAM_BY_NAME[m.name.lower()] = m
                return True
            logger.warn(f"⚠️ Backend returned error for chat ID update: {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Error updating chat ID to backend API: {e}")
    return False


def get_member_by_name(name: str) -> TeamMember | None:
    """Case-insensitive member lookup by name."""
    return TEAM_BY_NAME.get(name.lower())


def get_team_context() -> str:
    """Returns team info formatted for LLM system prompt."""
    lines = ["**Danh sách nhân sự trong team:**\n"]
    for m in TEAM_MEMBERS:
        skills_str = ", ".join(m.skills) if m.skills else "Không rõ"
        lines.append(f"- **{m.name}** ({m.role}): Skills → {skills_str}")
    lines.append(
        "\nKhi assign task, hãy dựa vào skills để suggest người phù hợp nhất. "
        "Cân bằng workload giữa các thành viên."
    )
    return "\n".join(lines)
