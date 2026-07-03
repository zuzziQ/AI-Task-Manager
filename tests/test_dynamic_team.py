import asyncio
import httpx
from pydantic import BaseModel, Field
from typing import List, Optional

class TeamMember(BaseModel):
    name: str = Field(description="Full name của nhân sự")
    role: str = Field(description="Vai trò/chức danh trong team")
    skills: List[str] = Field(default_factory=list, description="Danh sách kỹ năng")
    telegram_username: str = Field(default="", description="Telegram username (không có @)")
    telegram_chat_id: int | None = Field(default=None, description="Chat ID riêng (nếu đã chat với bot)")
    plane_member_id: str = Field(default="", description="Plane member UUID")

async def test_load():
    url = "http://localhost:4500/api/omnitask/team-members"
    print(f"Fetching from {url}...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print("Raw Response structure type:", type(data))
                print("Raw keys:", data.keys() if isinstance(data, dict) else "not a dict")
                
                # Check original logic
                print("\nOriginal logic check:")
                if isinstance(data, list):
                    print("Original logic: data is a list")
                else:
                    print("Original logic: data is NOT a list (FAILED original check)")
                
                # Let's try parsing if we extract 'data'
                members_list = data.get("data", []) if isinstance(data, dict) else data
                print(f"Found {len(members_list)} items in data list.")
                for item in members_list[:2]:
                    print("Item:", item)
                    try:
                        member = TeamMember(**item)
                        print("Parsed successfully:", member)
                    except Exception as e:
                        print("Parse error (FAILED):", e)
    except Exception as e:
        print("Error connecting to API:", e)

if __name__ == "__main__":
    asyncio.run(test_load())
