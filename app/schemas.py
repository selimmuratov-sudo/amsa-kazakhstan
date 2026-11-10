import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    username: str = Field(min_length=3, max_length=40)
    email: str | None = Field(default=None, min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=200)
    university_name: str = Field(min_length=2, max_length=200)
    city_name: str | None = Field(default=None, max_length=120)
    study_year: int | None = Field(default=None, ge=1, le=12)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", value):
            raise ValueError("Username must use 3–40 letters, numbers, dots, hyphens or underscores")
        return value

    @field_validator("email")
    @classmethod
    def normalize_optional_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("full_name", "university_name")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class MemberProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    university_name: str | None = Field(default=None, min_length=2, max_length=200)
    city_name: str | None = Field(default=None, max_length=120)
    study_year: int | None = Field(default=None, ge=1, le=12)
    committee_interest: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("full_name", "university_name", "city_name", "committee_interest", "phone")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MembershipStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in {"pending", "active", "suspended", "left"}:
            raise ValueError("Unsupported membership status")
        return value


class CommitteeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    university_name: str = Field(min_length=2, max_length=200)
    city_name: str = Field(min_length=2, max_length=120)
    public_description: str | None = Field(default=None, max_length=2000)
    public_email: str | None = Field(default=None, max_length=254)
    is_public: bool = False

    @field_validator("name", "university_name", "city_name")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class CommitteeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    university_name: str | None = Field(default=None, min_length=2, max_length=200)
    city_name: str | None = Field(default=None, min_length=2, max_length=120)
    public_description: str | None = Field(default=None, max_length=2000)
    public_email: str | None = Field(default=None, max_length=254)
    is_public: bool | None = None

    @field_validator("name", "university_name", "city_name", "public_description", "public_email")
    @classmethod
    def trim_committee_fields(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CommitteeOfficerCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    role_code: str

    @field_validator("username")
    @classmethod
    def normalize_officer_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", value):
            raise ValueError("Username must use 3–40 letters, numbers, dots, hyphens or underscores")
        return value

    @field_validator("role_code")
    @classmethod
    def allowed_committee_role(cls, value: str) -> str:
        if value not in {"lc_director", "local_officer"}:
            raise ValueError("Only local committee roles can be assigned here")
        return value


class CommitteePublicOut(BaseModel):
    id: int
    name: str
    university: str | None
    city: str | None
    description: str | None
    public_email: str | None


class CommitteeAdminOut(CommitteePublicOut):
    is_public: bool
    member_count: int


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="Событие AMSA", max_length=80)
    location: str = Field(default="Онлайн", max_length=240)
    starts_at: datetime
    ends_at: datetime | None = None
    registration_deadline: datetime | None = None
    capacity: int | None = Field(default=None, ge=1, le=100000)
    committee_id: int | None = None
    status: str = "draft"

    @field_validator("status")
    @classmethod
    def valid_event_status(cls, value: str) -> str:
        if value not in {"draft", "published", "cancelled"}:
            raise ValueError("Unsupported event status")
        return value


class EventOut(BaseModel):
    id: int
    committee_id: int | None = None
    title: str
    summary: str
    description: str
    category: str
    location: str
    starts_at: datetime
    ends_at: datetime | None
    registration_deadline: datetime | None
    capacity: int | None
    registered_count: int
    my_status: str | None = None
    status: str | None = None


class EventRegistrationOut(BaseModel):
    event_id: int
    status: str


class AttendanceUpdate(BaseModel):
    attended: bool


class EventRegistrationAdminOut(BaseModel):
    user_id: int
    full_name: str
    username: str
    university_name: str
    study_year: int | None
    status: str
    certificate_id: int | None
    certificate_status: str | None
    verification_url: str | None


class EventCertificateOut(BaseModel):
    id: int
    event_id: int
    event_title: str
    public_code: str
    status: str
    issued_at: datetime
    verification_url: str
    print_url: str | None = None


class CertificateVerifyOut(BaseModel):
    is_valid: bool
    recipient_name: str
    event_title: str
    issued_at: datetime
    certificate_code: str


class CertificateRevokeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def trim_revocation_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Provide a reason of at least 5 characters")
        return value


class NewsWrite(BaseModel):
    title: str = Field(min_length=3, max_length=220)
    slug: str | None = Field(default=None, max_length=220)
    summary: str = Field(default="", max_length=600)
    body: str = Field(min_length=10, max_length=50000)
    category: str = Field(default="Новости", min_length=2, max_length=80)
    cover_image: str | None = Field(default=None, max_length=500)
    status: str = "draft"
    is_pinned: bool = False
    published_at: datetime | None = None
    committee_id: int | None = None

    @field_validator("status")
    @classmethod
    def valid_news_status(cls, value: str) -> str:
        if value not in {"draft", "scheduled", "published", "archived"}:
            raise ValueError("Unsupported publication status")
        return value

    @field_validator("title", "summary", "body", "category")
    @classmethod
    def trim_news_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class NewsOut(BaseModel):
    id: int
    slug: str
    title: str
    summary: str
    body: str
    category: str
    cover_image: str | None
    published_at: datetime | None
    is_pinned: bool
    committee_id: int | None = None


class NewsAdminOut(NewsOut):
    status: str
    created_at: datetime
    updated_at: datetime


class ExchangeCallWrite(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    program_type: str = Field(default="AMSEP", min_length=2, max_length=80)
    destination: str = Field(min_length=2, max_length=200)
    summary: str = Field(default="", max_length=600)
    description: str = Field(default="", max_length=10000)
    requirements: str = Field(default="", max_length=10000)
    opens_at: datetime | None = None
    closes_at: datetime
    seats: int | None = Field(default=None, ge=1, le=10000)
    status: str = "draft"

    @field_validator("status")
    @classmethod
    def valid_exchange_status(cls, value: str) -> str:
        if value not in {"draft", "open", "closed", "archived"}:
            raise ValueError("Unsupported exchange call status")
        return value

    @field_validator("title", "program_type", "destination", "summary", "description", "requirements")
    @classmethod
    def trim_exchange_text(cls, value: str) -> str:
        return value.strip()


class ExchangeCallOut(BaseModel):
    id: int
    title: str
    program_type: str
    destination: str
    summary: str
    description: str
    requirements: str
    opens_at: datetime | None
    closes_at: datetime
    seats: int | None
    status: str | None = None
    applications_count: int = 0


class ExchangeApplicationWrite(BaseModel):
    motivation: str = Field(min_length=50, max_length=5000)
    relevant_experience: str = Field(default="", max_length=5000)
    preferred_destination: str | None = Field(default=None, max_length=200)

    @field_validator("motivation", "relevant_experience", "preferred_destination")
    @classmethod
    def trim_application_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class ExchangeReviewerCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)

    @field_validator("username")
    @classmethod
    def normalize_reviewer_username(cls, value: str) -> str:
        return value.strip().lower()


class ExchangeApplicationReview(BaseModel):
    status: str
    reviewer_note: str | None = Field(default=None, max_length=5000)

    @field_validator("status")
    @classmethod
    def valid_review_status(cls, value: str) -> str:
        if value not in {"under_review", "shortlisted", "accepted", "rejected"}:
            raise ValueError("Unsupported application status")
        return value


class ExchangeApplicationOut(BaseModel):
    id: int
    call_id: int
    call_title: str
    destination: str
    status: str
    motivation: str | None = None
    relevant_experience: str | None = None
    preferred_destination: str | None = None
    reviewer_note: str | None = None
    candidate_name: str | None = None
    candidate_username: str | None = None
    university_name: str | None = None
    submitted_at: datetime
    updated_at: datetime


class ExchangeHistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    note: str | None
    created_at: datetime


class CommitteeReportWrite(BaseModel):
    committee_id: int
    period: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    executive_summary: str = Field(min_length=20, max_length=5000)
    activities: str = Field(min_length=10, max_length=10000)
    impact: str = Field(default="", max_length=10000)
    challenges: str = Field(default="", max_length=5000)
    next_steps: str = Field(default="", max_length=5000)
    members_count: int | None = Field(default=None, ge=0, le=100000)
    events_count: int | None = Field(default=None, ge=0, le=100000)

    @field_validator("executive_summary", "activities", "impact", "challenges", "next_steps")
    @classmethod
    def trim_report_text(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if info.field_name in {"executive_summary", "activities"} and not value:
            raise ValueError("This field cannot be blank")
        return value


class CommitteeReportOut(BaseModel):
    id: int
    committee_id: int
    committee_name: str
    period: str
    executive_summary: str
    activities: str
    impact: str
    challenges: str
    next_steps: str
    members_count: int | None
    events_count: int | None
    status: str
    submitted_at: datetime | None
    updated_at: datetime


class CommitteeReportReview(BaseModel):
    status: str
    comment: str = Field(min_length=3, max_length=5000)

    @field_validator("status")
    @classmethod
    def valid_review_status(cls, value: str) -> str:
        if value not in {"under_review", "changes_requested", "approved"}:
            raise ValueError("Unsupported report review status")
        return value


class CommitteeReportCommentIn(BaseModel):
    body: str = Field(min_length=3, max_length=5000)

    @field_validator("body")
    @classmethod
    def trim_comment(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment cannot be blank")
        return value


class CommitteeReportCommentOut(BaseModel):
    id: int
    author_name: str
    body: str
    created_at: datetime


class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int
    has_more: bool
    next_before_id: int | None


class MemberMeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    account_status: str
    roles: list[str]
    full_name: str
    university_name: str
    city_name: str | None
    study_year: int | None
    committee_interest: str | None
    phone: str | None
    position_title: str | None
    activity_area: str | None
    membership_status: str
    joined_on: date | None
    created_at: datetime


class AdminMemberOut(BaseModel):
    id: int
    full_name: str
    username: str
    email: str | None
    university_name: str
    city_name: str | None
    study_year: int | None
    committee_id: int | None
    committee_name: str | None
    phone: str | None
    position_title: str | None
    activity_area: str | None
    membership_status: str
    joined_on: date | None
    password_reset_required: bool = False
    registered_at: datetime
