"""Request/response schemas for the patient (account) resource."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

Sex = Literal["male", "female", "other", "unknown"]


class PatientCreate(BaseModel):
    """Profile fields supplied by the client.

    `clerk_user_id` is deliberately absent — identity comes from the verified
    session, never from the request body.
    """

    email: EmailStr | None = None
    full_name: str | None = None
    dob: date | None = None
    sex: Sex | None = None
    description: str | None = None

    @field_validator("dob")
    @classmethod
    def dob_not_in_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("dob cannot be in the future")
        return v


class PatientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr | None
    full_name: str | None
    dob: date | None
    sex: Sex | None
    description: str | None
    created_at: datetime
    updated_at: datetime
