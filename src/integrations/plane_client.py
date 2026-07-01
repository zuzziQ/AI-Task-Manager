"""
Plane.so REST API client — wraps all API interactions.
Supports work items (tasks), members, states, and labels.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from src.config import settings
from src.models.task import PlaneWorkItem, TaskStatus, Priority, SubTask, CreatedTask
from src.team_config import TEAM_MEMBERS, TeamMember

logger = logging.getLogger(__name__)


class PlaneClient:
    """Async HTTP client for Plane.so REST API."""

    def __init__(self):
        self._base_url = settings.plane_base_url.rstrip("/")
        self._workspace = settings.plane_workspace_slug
        self._project = settings.plane_project_id
        self._headers = {
            "X-API-Key": settings.plane_api_key,
            "Content-Type": "application/json",
        }
        # Cache: member name → plane UUID
        self._member_cache: dict[str, str] = {}
        # Cache: state name → state UUID
        self._state_cache: dict[str, str] = {}
        logger.info(
            f"PlaneClient init — workspace: {self._workspace}, project: {self._project}"
        )

    @property
    def _project_url(self) -> str:
        return f"{self._base_url}/workspaces/{self._workspace}/projects/{self._project}"

    # ── Member Management ──────────────────────────────────────────────────────

    async def fetch_members(self) -> list[dict]:
        """Fetch all workspace members and populate member_cache."""
        url = f"{self._base_url}/workspaces/{self._workspace}/members/"
        async with httpx.AsyncClient(headers=self._headers, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            members = data if isinstance(data, list) else data.get("results", [])

            # Populate cache: display_name → member id
            for m in members:
                member_info = m.get("member", m)
                name = (
                    member_info.get("display_name")
                    or f"{member_info.get('first_name','')} {member_info.get('last_name','')}".strip()
                )
                mid = m.get("id") or member_info.get("id", "")
                if name and mid:
                    self._member_cache[name.lower()] = mid

            logger.info(f"Fetched {len(self._member_cache)} members from Plane")
            return members

    async def resolve_member_id(self, name: str) -> Optional[str]:
        """Resolve a member name to their Plane UUID."""
        if not self._member_cache:
            await self.fetch_members()

        # Try exact match first
        mid = self._member_cache.get(name.lower())
        if mid:
            return mid

        # Try partial match
        for cached_name, cached_id in self._member_cache.items():
            if name.lower() in cached_name or cached_name in name.lower():
                return cached_id

        # Try matching against team config
        for member in TEAM_MEMBERS:
            if member.name.lower() == name.lower() and member.plane_member_id:
                return member.plane_member_id

        logger.warning(f"Could not resolve Plane member ID for: {name}")
        return None

    # ── State Management ───────────────────────────────────────────────────────

    async def fetch_states(self) -> dict[str, str]:
        """Fetch project states and return name→id map."""
        if self._state_cache:
            return self._state_cache

        url = f"{self._project_url}/states/"
        async with httpx.AsyncClient(headers=self._headers, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            states = data if isinstance(data, list) else data.get("results", [])
            for s in states:
                self._state_cache[s["name"].lower()] = s["id"]
            logger.info(f"Fetched {len(self._state_cache)} states")
            return self._state_cache

    async def get_state_id(self, state_name: str) -> Optional[str]:
        """Get state UUID by name."""
        states = await self.fetch_states()
        return states.get(state_name.lower())

    # ── Work Item (Task) Operations ────────────────────────────────────────────

    async def create_work_item(
        self,
        subtask: SubTask,
        assignee_id: Optional[str] = None,
    ) -> CreatedTask:
        """
        Create a single work item on Plane.

        Args:
            subtask: The SubTask model from LLM output.
            assignee_id: Resolved Plane member UUID.

        Returns:
            CreatedTask with all created item details.
        """
        # Calculate deadline date
        deadline_date = (datetime.now() + timedelta(days=subtask.deadline_days)).strftime(
            "%Y-%m-%d"
        )

        # Build description with deliverables
        deliverables_md = "\n".join(f"- [ ] {d}" for d in subtask.deliverables)
        description = (
            f"{subtask.description}\n\n"
            f"## ✅ Deliverables\n{deliverables_md}"
        ) if subtask.deliverables else subtask.description

        payload: dict = {
            "name": subtask.name,
            "description_html": f"<p>{description.replace(chr(10), '<br>')}</p>",
            "priority": subtask.priority.value,
            "target_date": deadline_date,
        }

        if assignee_id:
            payload["assignees"] = [assignee_id]

        url = f"{self._project_url}/work-items/"
        async with httpx.AsyncClient(headers=self._headers, timeout=30) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            item = resp.json()

        plane_item_id = item["id"]
        sequence_id = item.get("sequence_id", 0)
        plane_url = (
            f"https://app.plane.so/{self._workspace}/projects/{self._project}/issues/{plane_item_id}/"
        )

        logger.info(f"Created Plane work item: #{sequence_id} — {subtask.name}")

        # Determine assignee name
        assignee_name = subtask.suggested_assignee_name
        state_name = item.get("state_detail", {}).get("name", "Backlog")

        return CreatedTask(
            plane_id=plane_item_id,
            sequence_id=sequence_id,
            name=subtask.name,
            description=description,
            priority=subtask.priority,
            status=TaskStatus(state_name) if state_name in TaskStatus._value2member_map_ else TaskStatus.BACKLOG,
            assignee_name=assignee_name,
            assignee_plane_id=assignee_id or "",
            deadline=deadline_date,
            estimated_hours=subtask.estimated_hours,
            deliverables=subtask.deliverables,
            plane_url=plane_url,
        )

    async def list_work_items(
        self,
        state_filter: Optional[str] = None,
        assignee_id: Optional[str] = None,
    ) -> list[PlaneWorkItem]:
        """
        List work items from the project with optional filters.

        Args:
            state_filter: Filter by state name (e.g. "In Progress").
            assignee_id: Filter by assignee Plane member UUID.

        Returns:
            List of PlaneWorkItem objects.
        """
        url = f"{self._project_url}/work-items/"
        params: dict = {}
        if state_filter:
            params["state__name"] = state_filter
        if assignee_id:
            params["assignees__in"] = assignee_id

        items = []
        while url:
            async with httpx.AsyncClient(headers=self._headers, timeout=30) as client:
                resp = await client.get(url, params=params if params else None)
                resp.raise_for_status()
                data = resp.json()

            results = data if isinstance(data, list) else data.get("results", [])
            items.extend(results)

            # Handle pagination
            if isinstance(data, dict) and data.get("next"):
                url = data["next"]
                params = {}  # URL already contains params
            else:
                break

        now = datetime.now().date()
        work_items = []
        for item in items:
            state_name = (
                item.get("state_detail", {}).get("name")
                or item.get("state__name", "Backlog")
            )
            assignee_name = ""
            if item.get("assignee_details"):
                ad = item["assignee_details"][0] if isinstance(item["assignee_details"], list) else item["assignee_details"]
                assignee_name = ad.get("display_name") or f"{ad.get('first_name','')} {ad.get('last_name','')}".strip()

            target_date_str = item.get("target_date")
            is_overdue = False
            if target_date_str:
                try:
                    td = datetime.fromisoformat(target_date_str.replace("Z", "+00:00")).date()
                    is_overdue = td < now and state_name not in ("Done", "Cancelled")
                except ValueError:
                    pass

            work_items.append(
                PlaneWorkItem(
                    id=item["id"],
                    sequence_id=item.get("sequence_id", 0),
                    name=item.get("name", "Untitled"),
                    priority=item.get("priority", "none"),
                    state_name=state_name,
                    assignee_id=item.get("assignees", [None])[0] if item.get("assignees") else None,
                    assignee_name=assignee_name or None,
                    target_date=target_date_str,
                    completed_at=item.get("completed_at"),
                    is_overdue=is_overdue,
                )
            )

        logger.info(f"Fetched {len(work_items)} work items from Plane")
        return work_items

    async def get_open_tasks(self) -> list[PlaneWorkItem]:
        """Get all non-completed tasks."""
        all_items = await self.list_work_items()
        return [i for i in all_items if i.state_name not in ("Done", "Cancelled")]

    async def get_overdue_tasks(self) -> list[PlaneWorkItem]:
        """Get all overdue tasks."""
        all_items = await self.list_work_items()
        return [i for i in all_items if i.is_overdue]

    async def update_work_item_status(self, item_id: str, new_state_name: str) -> bool:
        """Update a work item's state."""
        state_id = await self.get_state_id(new_state_name)
        if not state_id:
            logger.error(f"State not found: {new_state_name}")
            return False

        url = f"{self._project_url}/work-items/{item_id}/"
        async with httpx.AsyncClient(headers=self._headers, timeout=30) as client:
            resp = await client.patch(url, json={"state": state_id})
            resp.raise_for_status()

        logger.info(f"Updated work item {item_id} → {new_state_name}")
        return True


# ── Singleton ──────────────────────────────────────────────────────────────────
_plane_client: PlaneClient | None = None


def get_plane_client() -> PlaneClient:
    global _plane_client
    if _plane_client is None:
        _plane_client = PlaneClient()
    return _plane_client
