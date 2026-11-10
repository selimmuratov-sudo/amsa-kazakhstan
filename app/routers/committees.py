from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import AuditEvent, City, LocalCommittee, MemberProfile, Role, University, User, UserRoleAssignment
from app.rbac import MEMBER_ADMIN_ROLES, committee_scope, require_member_admin, role_assignments
from app.schemas import CommitteeAdminOut, CommitteeCreate, CommitteeOfficerCreate, CommitteePublicOut, CommitteeUpdate
from app.security import current_user, require_csrf

router = APIRouter(tags=["local committees"])


def committee_out(db: Session, committee: LocalCommittee, admin: bool = False):
    base = dict(id=committee.id, name=committee.name,
                university=committee.university.name if committee.university else None,
                city=committee.city.name if committee.city else None,
                description=committee.public_description, public_email=committee.public_email)
    if not admin:
        return CommitteePublicOut(**base)
    count = db.scalar(select(func.count(MemberProfile.id)).where(MemberProfile.committee_id == committee.id)) or 0
    return CommitteeAdminOut(**base, is_public=committee.is_public, member_count=count)


def allowed_committee(assignments: list[UserRoleAssignment], committee_id: int, *, director_required: bool = False) -> None:
    scope = committee_scope(assignments)
    if scope is None:
        return
    role_codes = {a.role.code for a in assignments}
    if committee_id not in scope or (director_required and "lc_director" not in role_codes):
        raise HTTPException(status_code=403, detail="This committee is outside your permissions")


def get_or_create_city(db: Session, name: str) -> City:
    city = db.scalar(select(City).where(func.lower(City.name) == name.lower()))
    if city:
        return city
    city = City(name=name)
    db.add(city)
    db.flush()
    return city


def get_or_create_university(db: Session, name: str, city: City) -> University:
    university = db.scalar(select(University).where(func.lower(University.name) == name.lower()))
    if university:
        if university.city_id is None:
            university.city_id = city.id
        return university
    university = University(name=name, city_id=city.id)
    db.add(university)
    db.flush()
    return university


@router.get("/api/v1/committees", response_model=list[CommitteePublicOut])
def list_public_committees(db: Session = Depends(get_db)) -> list[CommitteePublicOut]:
    rows = db.scalars(select(LocalCommittee).options(joinedload(LocalCommittee.city), joinedload(LocalCommittee.university))
                      .where(LocalCommittee.is_public.is_(True)).order_by(LocalCommittee.name.asc())).unique()
    return [committee_out(db, row) for row in rows]


@router.get("/api/v1/admin/committees", response_model=list[CommitteeAdminOut])
def list_managed_committees(request: Request, db: Session = Depends(get_db)) -> list[CommitteeAdminOut]:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    stmt = select(LocalCommittee).options(joinedload(LocalCommittee.city), joinedload(LocalCommittee.university)).order_by(LocalCommittee.name.asc())
    if scope is not None:
        if not scope:
            return []
        stmt = stmt.where(LocalCommittee.id.in_(scope))
    return [committee_out(db, row, admin=True) for row in db.scalars(stmt).unique()]


@router.post("/api/v1/admin/committees", response_model=CommitteeAdminOut, status_code=status.HTTP_201_CREATED)
def create_committee(payload: CommitteeCreate, request: Request, db: Session = Depends(get_db)) -> CommitteeAdminOut:
    actor = current_user(request, db)
    require_csrf(request, db)
    assignments = role_assignments(db, actor)
    if not any(a.role.code in {"super_admin", "national_board"} for a in assignments):
        raise HTTPException(status_code=403, detail="Only national administrators can create committees")
    city = get_or_create_city(db, payload.city_name)
    university = get_or_create_university(db, payload.university_name, city)
    committee = LocalCommittee(name=payload.name, city_id=city.id, university_id=university.id,
                               public_description=payload.public_description, public_email=payload.public_email,
                               is_public=payload.is_public)
    db.add(committee)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A committee with this name already exists") from exc
    db.add(AuditEvent(actor_user_id=actor.id, action="committee.created", entity_type="local_committee", entity_id=str(committee.id), details={"is_public": committee.is_public}))
    db.commit()
    db.refresh(committee)
    return committee_out(db, committee, admin=True)


@router.patch("/api/v1/admin/committees/{committee_id}", response_model=CommitteeAdminOut)
def update_committee(committee_id: int, payload: CommitteeUpdate, request: Request, db: Session = Depends(get_db)) -> CommitteeAdminOut:
    actor = current_user(request, db)
    require_csrf(request, db)
    assignments = require_member_admin(db, actor)
    committee = db.scalar(select(LocalCommittee).options(joinedload(LocalCommittee.city), joinedload(LocalCommittee.university)).where(LocalCommittee.id == committee_id))
    if not committee:
        raise HTTPException(status_code=404, detail="Committee not found")
    allowed_committee(assignments, committee_id, director_required=True)
    changes = payload.model_dump(exclude_unset=True)
    if "is_public" in changes and not any(a.role.code in {"super_admin", "national_board"} for a in assignments):
        raise HTTPException(status_code=403, detail="Only national administrators can publish a committee")
    city_name = changes.pop("city_name", None)
    university_name = changes.pop("university_name", None)
    if city_name:
        city = get_or_create_city(db, city_name)
        committee.city_id = city.id
    if university_name:
        university = db.scalar(select(University).where(func.lower(University.name) == university_name.lower()))
        if university and not city_name:
            committee.university_id = university.id
            if university.city_id:
                committee.city_id = university.city_id
        else:
            city = db.get(City, committee.city_id) if not city_name else db.scalar(select(City).where(func.lower(City.name) == city_name.lower()))
            if not city:
                raise HTTPException(status_code=422, detail="Provide a city when linking a new university")
            university = university or get_or_create_university(db, university_name, city)
            if university.city_id is None:
                university.city_id = city.id
            committee.university_id = university.id
            committee.city_id = city.id
    for field, value in changes.items():
        setattr(committee, field, value)
    db.add(AuditEvent(actor_user_id=actor.id, action="committee.updated", entity_type="local_committee", entity_id=str(committee.id), details={"fields": sorted(payload.model_fields_set)}))
    db.commit()
    db.refresh(committee)
    return committee_out(db, committee, admin=True)


@router.post("/api/v1/admin/committees/{committee_id}/officers", status_code=status.HTTP_201_CREATED)
def assign_committee_officer(committee_id: int, payload: CommitteeOfficerCreate, request: Request,
                             db: Session = Depends(get_db)) -> dict:
    actor = current_user(request, db)
    require_csrf(request, db)
    assignments = require_member_admin(db, actor)
    committee = db.get(LocalCommittee, committee_id)
    if not committee:
        raise HTTPException(status_code=404, detail="Committee not found")
    allowed_committee(assignments, committee_id, director_required=True)
    is_national = any(a.role.code in {"super_admin", "national_board"} for a in assignments)
    if payload.role_code == "lc_director" and not is_national:
        raise HTTPException(status_code=403, detail="Only national administrators can appoint committee directors")
    target = db.scalar(select(User).where(User.username == payload.username))
    if not target:
        raise HTTPException(status_code=404, detail="No account exists for that username")
    role = db.scalar(select(Role).where(Role.code == payload.role_code))
    existing = db.scalar(select(UserRoleAssignment).where(
        UserRoleAssignment.user_id == target.id, UserRoleAssignment.role_id == role.id,
        UserRoleAssignment.scope_type == "local_committee", UserRoleAssignment.scope_id == committee_id))
    if existing:
        raise HTTPException(status_code=409, detail="This role is already assigned")
    if payload.role_code == "lc_director":
        occupied = db.scalar(select(UserRoleAssignment.id).join(Role).where(
            Role.code == "lc_director", UserRoleAssignment.scope_type == "local_committee",
            UserRoleAssignment.scope_id == committee_id))
        if occupied:
            raise HTTPException(status_code=409, detail="This committee already has a director; reassign the current director first")
    assignment = UserRoleAssignment(user_id=target.id, role_id=role.id, scope_type="local_committee", scope_id=committee_id)
    db.add(assignment)
    db.add(AuditEvent(actor_user_id=actor.id, action="committee.officer_assigned", entity_type="user_role_assignment", entity_id=None,
                      details={"committee_id": committee_id, "target_user_id": target.id, "role": payload.role_code}))
    db.commit()
    return {"committee_id": committee_id, "user_id": target.id, "role": payload.role_code, "status": "assigned"}


@router.get("/api/v1/admin/committees/{committee_id}/officers")
def list_committee_officers(committee_id: int, request: Request, db: Session = Depends(get_db)) -> list[dict]:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    if not db.get(LocalCommittee, committee_id):
        raise HTTPException(status_code=404, detail="Committee not found")
    allowed_committee(assignments, committee_id)
    rows = db.execute(select(UserRoleAssignment, User, Role).join(User, User.id == UserRoleAssignment.user_id)
                      .join(Role, Role.id == UserRoleAssignment.role_id)
                      .where(UserRoleAssignment.scope_type == "local_committee", UserRoleAssignment.scope_id == committee_id,
                             Role.code.in_(["lc_director", "local_officer"]))
                      .order_by(Role.code, User.username)).all()
    return [{"user_id": user.id, "username": user.username, "email": user.email, "full_name": user.profile.full_name if user.profile else "", "role": role.code}
            for _, user, role in rows]


@router.delete("/api/v1/admin/committees/{committee_id}/officers/{user_id}/{role_code}", status_code=status.HTTP_204_NO_CONTENT)
def remove_committee_officer(committee_id: int, user_id: int, role_code: str, request: Request,
                             db: Session = Depends(get_db)) -> None:
    actor = current_user(request, db)
    require_csrf(request, db)
    assignments = require_member_admin(db, actor)
    allowed_committee(assignments, committee_id, director_required=True)
    is_national = any(a.role.code in {"super_admin", "national_board"} for a in assignments)
    if role_code not in {"lc_director", "local_officer"} or (role_code == "lc_director" and not is_national):
        raise HTTPException(status_code=403, detail="Cannot remove this role")
    role = db.scalar(select(Role).where(Role.code == role_code))
    assignment = db.scalar(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id,
        UserRoleAssignment.role_id == role.id, UserRoleAssignment.scope_type == "local_committee",
        UserRoleAssignment.scope_id == committee_id))
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    db.add(AuditEvent(actor_user_id=actor.id, action="committee.officer_removed", entity_type="user_role_assignment",
                      entity_id=str(assignment.id), details={"committee_id": committee_id, "target_user_id": user_id, "role": role_code}))
    db.delete(assignment)
    db.commit()
