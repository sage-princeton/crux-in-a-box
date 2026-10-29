from pydantic import BaseModel, ConfigDict, Field


class ConversationConfig(BaseModel):
    """Validate the optional conversation window without importing the agent SDK."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int | None = Field(None, strict=True, ge=1)
