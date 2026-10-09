from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    """文档响应"""

    id: int
    name: str
    original_filename: str
    file_type: str
    document_type: str
    project_id: int
    status: str
    content: str | None
    created_at: datetime

    model_config = {
        "from_attributes": True,
    }