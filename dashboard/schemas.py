"""Request bodies for dashboard writes. Unknown fields are rejected."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterventionCreate(_Strict):
    student_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=140)
    plan: str = Field(min_length=1, max_length=4000)
    status: Literal["planned", "in_progress", "completed", "cancelled"] = "planned"
    assignee_user_id: Optional[str] = None


class InterventionPatch(_Strict):
    title: Optional[str] = Field(default=None, min_length=3, max_length=140)
    plan: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    status: Optional[Literal["planned", "in_progress", "completed", "cancelled"]] = None
    assignee_user_id: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("No changes were provided")
        return self


class ParentContactRequest(_Strict):
    message: str = Field(min_length=1, max_length=2000)
    reason: Optional[str] = Field(default=None, max_length=200)


class NotifyCounselorRequest(_Strict):
    message: str = Field(min_length=1, max_length=2000)


class TeacherMessageRequest(_Strict):
    message: str = Field(min_length=1, max_length=2000)


class TeacherReviewRequest(_Strict):
    notes: str = Field(min_length=1, max_length=2000)
    focus_areas: list[str] = Field(default_factory=list, max_length=8)


class SupportPlanRequest(_Strict):
    summary: str = Field(min_length=1, max_length=4000)
    focus_areas: list[str] = Field(default_factory=list, max_length=8)


class ReportCreate(_Strict):
    type: Literal["parent", "board", "wellbeing"]
    scope: Literal["school", "grade", "class", "student"] = "school"
    grade_id: Optional[str] = None
    class_id: Optional[str] = None
    student_id: Optional[str] = None
    academic_year: Optional[str] = None
    include_ai_insights: bool = False
    include_charts: bool = False


class AssistantContext(_Strict):
    grade_id: Optional[str] = None
    class_id: Optional[str] = None
    subject_id: Optional[str] = None
    academic_year: Optional[str] = None


class AssistantRequest(_Strict):
    message: str = Field(min_length=1, max_length=2000)
    context: AssistantContext = Field(default_factory=AssistantContext)


class SettingsPatch(_Strict):
    at_risk_alerts: Optional[bool] = None
    weekly_digest: Optional[bool] = None
    report_generation_alerts: Optional[bool] = None
    ai_insights: Optional[bool] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if not any(field in self.model_fields_set for field in (
            "at_risk_alerts",
            "weekly_digest",
            "report_generation_alerts",
            "ai_insights",
        )):
            raise ValueError("No changes were provided")
        return self
