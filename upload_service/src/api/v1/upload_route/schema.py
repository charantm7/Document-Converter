from typing import List
from pydantic import BaseModel, Field, Field, field_validator, ValidationError


class PreSignedSchema(BaseModel):
    filename: str
    content_type: str


class MergeRequest(BaseModel):
    job_id: str
    path: List[str] = Field(..., min_items=2,
                            description="List of file paths to be merged")
    target_format: str = Field(
        "merge", description="Target format for merging, should be 'merge'")

    @field_validator("target_format")
    def validate_target_format(cls, value):
        if value != "merge":
            raise ValueError("target_format must be 'merge'")
        return value

    @field_validator("path")
    def validate_paths(cls, value):
        for path in value:
            if not path.lower().endswith(".pdf"):
                raise ValueError("All files must be in PDF format for merging")
        return value


class ConvertRequest(BaseModel):
    job_id: str
    path: str
    target_format: str
    source_format: str
