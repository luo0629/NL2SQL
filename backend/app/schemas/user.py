from pydantic import BaseModel


class UserContext(BaseModel):
    user_id: str | None = None
    locale: str = "zh-CN"
