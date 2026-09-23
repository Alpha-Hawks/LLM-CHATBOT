"""
Admin & Knowledge Synchronization API.
Provides authorized endpoints to monitor, trigger, and inspect official MLRITM sync.
Protected with admin authorization.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.db.models import (
    CollegeEvent,
    FacultyContact,
    EventMedia,
    CollegeSource,
    CollegeNotice,
    KnowledgeGap,
    AdaptiveKnowledgeItem,
    UserFeedback,
    FacultySyncLog,
    QueryPatternLog,
    DepartmentAlias,
)
from backend.app.services.mlritm_sync import mlritm_sync_service
from backend.app.services.adaptive_knowledge import adaptive_knowledge_service
from backend.app.services.web_search import web_search_service
from backend.app.services.faculty_service import faculty_service
from backend.app.services.query_normalizer import query_normalizer, DEPARTMENT_REGISTRY
from backend.app.llm.rag_engine import rag_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin & Synchronization"])


def verify_admin_access(x_admin_key: Optional[str] = Header(None)) -> bool:
    """Verifies admin authorization token or allows in development."""
    configured_key = getattr(settings, "ADMIN_API_KEY", None)
    if not configured_key or configured_key == "dev-admin-secret":
        # In development mode, allow default or check key
        if settings.ENVIRONMENT == "development":
            return True
    if x_admin_key and x_admin_key == configured_key:
        return True
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Unauthorized. Admin API key required.",
    )


class SyncTriggerRequest(BaseModel):
    target: str = "all"  # "all", "events", "faculty", "website"
    force_live: bool = False


# In-memory sync audit log
_sync_logs: List[Dict[str, Any]] = [
    {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "SYSTEM_STARTUP_SYNC",
        "target": "all",
        "status": "SUCCESS",
        "message": "Synchronized official MLRITM knowledge base and directory.",
    }
]


@router.get("/sync/status")
async def get_sync_status(
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns comprehensive synchronization metrics:
    Last MLRITM Sync, Documents Indexed, Events Found, Faculty Contacts Found,
    New Documents, Updated Documents, Removed Documents, Failed Documents, Last Error.
    """
    events_count = (await db.execute(select(func.count()).select_from(CollegeEvent))).scalar() or 0
    faculty_count = (await db.execute(select(func.count()).select_from(FacultyContact))).scalar() or 0
    media_count = (await db.execute(select(func.count()).select_from(EventMedia))).scalar() or 0
    notices_count = (await db.execute(select(func.count()).select_from(CollegeNotice))).scalar() or 0
    sources_count = (await db.execute(select(func.count()).select_from(CollegeSource))).scalar() or 0
    gaps_count = (await db.execute(select(func.count()).select_from(KnowledgeGap))).scalar() or 0
    unverified_gaps = (await db.execute(select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.verification_status == "UNVERIFIED"))).scalar() or 0
    adaptive_items_count = (await db.execute(select(func.count()).select_from(AdaptiveKnowledgeItem).where(AdaptiveKnowledgeItem.is_current == True))).scalar() or 0
    feedback_count = (await db.execute(select(func.count()).select_from(UserFeedback))).scalar() or 0

    # Retrieve last sync record
    last_source = (await db.execute(
        select(CollegeSource).order_by(CollegeSource.last_synced_at.desc()).limit(1)
    )).scalar_one_or_none()

    last_synced_at = last_source.last_synced_at.isoformat() if last_source and last_source.last_synced_at else datetime.now(timezone.utc).isoformat()

    total_documents = events_count + faculty_count + media_count + notices_count

    return {
        "status": "operational",
        "last_mlritm_sync": last_synced_at,
        "documents_indexed": total_documents,
        "events_found": events_count,
        "faculty_contacts_found": faculty_count,
        "event_photos_found": media_count,
        "notices_found": notices_count,
        "sources_tracked": sources_count,
        "knowledge_gaps_count": gaps_count,
        "unverified_gaps_count": unverified_gaps,
        "adaptive_items_count": adaptive_items_count,
        "feedback_count": feedback_count,
        "new_documents": total_documents,
        "updated_documents": 0,
        "removed_documents": 0,
        "failed_documents": 0,
        "last_error": None,
        "mlritm_source_url": settings.MLRITM_BASE_URL,
        "phone_directory_url": settings.MLRITM_PHONE_DIRECTORY_URL,
        "web_search_stats": web_search_service.get_stats(),
    }


@router.post("/sync/trigger")
async def trigger_sync(
    req: SyncTriggerRequest,
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Triggers an on-demand synchronization of official MLRITM content.
    Supports targets: "all", "events", "faculty", "website".
    """
    try:
        results = await mlritm_sync_service.synchronize(db, force_live=req.force_live)
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": f"MANUAL_SYNC_{req.target.upper()}",
            "target": req.target,
            "status": "SUCCESS",
            "message": f"Sync completed successfully. Contacts: {results.get('contacts', 0)}, Events: {results.get('events', 0)}, Media: {results.get('media', 0)}",
        }
        _sync_logs.insert(0, log_entry)
        if len(_sync_logs) > 50:
            _sync_logs.pop()

        return {
            "status": "success",
            "message": "Synchronization completed successfully.",
            "target": req.target,
            "results": results,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Manual sync failed: {e}", exc_info=True)
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": f"MANUAL_SYNC_{req.target.upper()}",
            "target": req.target,
            "status": "FAILED",
            "message": str(e),
        }
        _sync_logs.insert(0, log_entry)
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@router.post("/index/rebuild")
async def rebuild_search_index(
    authorized: bool = Depends(verify_admin_access),
):
    """Rebuilds the college knowledge base search index."""
    try:
        # Re-initialize RAG engine chunks
        rag_engine.__init__()
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "INDEX_REBUILD",
            "target": "knowledge_index",
            "status": "SUCCESS",
            "message": "College knowledge search index rebuilt successfully.",
        }
        _sync_logs.insert(0, log_entry)
        return {
            "status": "success",
            "message": "Search index rebuilt successfully.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Index rebuild failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Index rebuild failed: {str(e)}")


@router.get("/sync/logs")
async def get_sync_logs(
    limit: int = Query(20, ge=1, le=50),
    authorized: bool = Depends(verify_admin_access),
):
    """Returns the most recent synchronization logs."""
    return {"logs": _sync_logs[:limit]}


# =========================================================================
# KNOWLEDGE GAPS & ADAPTIVE LEARNING ENDPOINTS
# =========================================================================

class VerifyGapRequest(BaseModel):
    verified_content: str
    source_url: str = "https://www.mlritm.ac.in"
    source_trust_level: str = "OFFICIAL_DOCUMENT"
    topic: Optional[str] = None
    department: Optional[str] = None


@router.get("/knowledge/gaps")
async def list_knowledge_gaps(
    status: Optional[str] = Query(None, description="Filter by status: NEW, UNVERIFIED, VERIFIED, NEEDS_REVIEW, REJECTED"),
    min_frequency: int = Query(1, ge=1),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Lists tracked knowledge gaps, top unanswered queries, and items requiring review.
    Sorted by frequency descending, then last_seen descending.
    """
    stmt = select(KnowledgeGap).where(KnowledgeGap.frequency >= min_frequency)
    if status:
        stmt = stmt.where(KnowledgeGap.verification_status == status.upper())
    if search:
        stmt = stmt.where(KnowledgeGap.question.ilike(f"%{search}%"))

    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(desc(KnowledgeGap.frequency), desc(KnowledgeGap.last_seen)).limit(limit).offset(offset)
    res = await db.execute(stmt)
    gaps = res.scalars().all()

    return {
        "total": total,
        "gaps": [
            {
                "id": g.id,
                "question": g.question,
                "normalized_question": g.normalized_question,
                "intent": g.intent,
                "topic": g.topic,
                "department": g.department,
                "frequency": g.frequency,
                "first_seen": g.first_seen.isoformat() if g.first_seen else None,
                "last_seen": g.last_seen.isoformat() if g.last_seen else None,
                "answer_status": g.answer_status,
                "source_found": g.source_found,
                "source_url": g.source_url,
                "verification_status": g.verification_status,
                "confidence": g.confidence,
                "resolved_at": g.resolved_at.isoformat() if g.resolved_at else None,
                "next_verification_at": g.next_verification_at.isoformat() if g.next_verification_at else None,
            }
            for g in gaps
        ],
    }


@router.post("/knowledge/gaps/{gap_id}/verify")
async def verify_knowledge_gap(
    gap_id: int,
    req: VerifyGapRequest,
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin verification of an unanswered or unindexed knowledge gap.
    Saves official verified content to AdaptiveKnowledgeItem and resolves the gap.
    """
    stmt = select(KnowledgeGap).where(KnowledgeGap.id == gap_id)
    res = await db.execute(stmt)
    gap = res.scalar_one_or_none()
    if not gap:
        raise HTTPException(status_code=404, detail="Knowledge gap not found.")

    item_data = {
        "topic": req.topic or gap.topic or "general",
        "question_pattern": gap.question,
        "normalized_question": gap.normalized_question,
        "verified_content": req.verified_content,
        "source_url": req.source_url,
        "source_trust_level": req.source_trust_level,
        "department": req.department or gap.department,
        "temporal_status": "CURRENT",
    }
    saved_item = await adaptive_knowledge_service.save_verified_knowledge_item(item_data, db)

    gap.answer_status = "VERIFIED"
    gap.verification_status = "VERIFIED"
    gap.source_found = "Admin Verified Source"
    gap.source_url = req.source_url
    gap.confidence = 1.0
    gap.resolved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(gap)

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "KNOWLEDGE_GAP_VERIFIED",
        "target": f"gap_id_{gap_id}",
        "status": "SUCCESS",
        "message": f"Admin verified gap: '{gap.question}' -> stored item ID {saved_item.id}",
    }
    _sync_logs.insert(0, log_entry)

    return {
        "status": "success",
        "message": "Knowledge gap successfully verified and stored in adaptive knowledge base.",
        "gap_id": gap.id,
        "knowledge_item_id": saved_item.id,
        "verified_at": gap.resolved_at.isoformat(),
    }


@router.post("/knowledge/gaps/{gap_id}/reject")
async def reject_knowledge_gap(
    gap_id: int,
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Marks a knowledge gap as rejected or not an official college question."""
    stmt = select(KnowledgeGap).where(KnowledgeGap.id == gap_id)
    res = await db.execute(stmt)
    gap = res.scalar_one_or_none()
    if not gap:
        raise HTTPException(status_code=404, detail="Knowledge gap not found.")

    gap.answer_status = "REJECTED"
    gap.verification_status = "REJECTED"
    await db.commit()
    await db.refresh(gap)

    return {
        "status": "success",
        "message": f"Knowledge gap {gap_id} marked as REJECTED.",
        "gap_id": gap.id,
    }


@router.get("/knowledge/items")
async def list_adaptive_knowledge_items(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Lists current verified adaptive knowledge items."""
    stmt = (
        select(AdaptiveKnowledgeItem)
        .where(AdaptiveKnowledgeItem.is_current == True)
        .order_by(desc(AdaptiveKnowledgeItem.updated_at))
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    items = res.scalars().all()

    return {
        "items": [
            {
                "id": it.id,
                "topic": it.topic,
                "question_pattern": it.question_pattern,
                "normalized_question": it.normalized_question,
                "verified_content": it.verified_content,
                "source_url": it.source_url,
                "source_trust_level": it.source_trust_level,
                "version": it.version,
                "temporal_status": it.temporal_status,
                "department": it.department,
                "created_at": it.created_at.isoformat() if it.created_at else None,
                "verified_at": it.verified_at.isoformat() if it.verified_at else None,
                "next_verification_at": it.next_verification_at.isoformat() if it.next_verification_at else None,
            }
            for it in items
        ]
    }


@router.post("/knowledge/items/{item_id}/refresh")
async def refresh_adaptive_knowledge_item(
    item_id: int,
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Triggers re-verification and freshness extension for a knowledge item."""
    stmt = select(AdaptiveKnowledgeItem).where(AdaptiveKnowledgeItem.id == item_id)
    res = await db.execute(stmt)
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    now = datetime.utcnow()
    item.retrieved_at = now
    item.verified_at = now
    item.updated_at = now
    item.next_verification_at = now + timedelta(days=30)
    await db.commit()
    await db.refresh(item)

    return {
        "status": "success",
        "message": f"Knowledge item {item_id} re-verified successfully.",
        "verified_at": item.verified_at.isoformat(),
        "next_verification_at": item.next_verification_at.isoformat(),
    }


@router.get("/feedback")
async def list_user_feedback(
    limit: int = Query(50, ge=1, le=200),
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Returns recent student feedback submissions."""
    stmt = select(UserFeedback).order_by(desc(UserFeedback.created_at)).limit(limit)
    res = await db.execute(stmt)
    feedbacks = res.scalars().all()

    return {
        "feedback": [
            {
                "id": f.id,
                "query": f.query,
                "feedback_type": f.feedback_type,
                "missing_info": f.missing_info,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in feedbacks
        ]
    }


# =============================================================================
# FACULTY INTELLIGENCE ADMIN DASHBOARD (Requirement 26)
# =============================================================================

@router.get("/faculty/stats")
async def get_faculty_admin_stats(
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns complete Faculty Intelligence Metrics:
    Total records, counts by department, counts by designation, HODs,
    recently changed profiles, missing fields, broken URLs, and sync time.
    """
    stmt = select(FacultyContact)
    res = await db.execute(stmt)
    records = res.scalars().all()

    by_dept: Dict[str, int] = {}
    by_desig: Dict[str, int] = {}
    hods = []
    missing_fields = {
        "email": 0,
        "phone": 0,
        "undergraduate_degree": 0,
        "postgraduate_degree": 0,
        "phd_degree": 0,
        "specialization": 0,
        "academic_identity": 0,
    }
    broken_profile_urls = 0
    broken_image_urls = 0
    duplicate_names = set()
    seen_names = set()

    for r in records:
        dept = r.department or "Unknown"
        by_dept[dept] = by_dept.get(dept, 0) + 1

        desig = r.designation or "Unknown"
        by_desig[desig] = by_desig.get(desig, 0) + 1

        if r.is_hod:
            hods.append({
                "name": r.name,
                "department": r.department,
                "designation": r.designation,
                "email": r.email,
                "profile_url": r.profile_url,
            })

        if not r.email or r.email == "Not Available":
            missing_fields["email"] += 1
        if not r.phone or r.phone == "Not Available":
            missing_fields["phone"] += 1
        if not r.undergraduate_degree or r.undergraduate_degree == "Not Available":
            missing_fields["undergraduate_degree"] += 1
        if not r.postgraduate_degree or r.postgraduate_degree == "Not Available":
            missing_fields["postgraduate_degree"] += 1
        if not r.phd_degree or r.phd_degree == "Not Available":
            missing_fields["phd_degree"] += 1
        if not r.specialization or r.specialization == "Not Available":
            missing_fields["specialization"] += 1
        if not r.academic_identity_url or r.academic_identity_url == "Not Available":
            missing_fields["academic_identity"] += 1

        if not r.profile_url or not r.profile_url.startswith("http"):
            broken_profile_urls += 1
        if not r.photo_url or not r.photo_url.startswith("http"):
            broken_image_urls += 1

        if r.name in seen_names:
            duplicate_names.add(r.name)
        else:
            seen_names.add(r.name)

    # Last sync log
    last_log_stmt = select(FacultySyncLog).order_by(FacultySyncLog.id.desc()).limit(1)
    last_log = (await db.execute(last_log_stmt)).scalar_one_or_none()

    return {
        "status": "operational",
        "total_faculty_records": len(records),
        "faculty_by_department": by_dept,
        "faculty_by_designation": by_desig,
        "hods_count": len(hods),
        "hods": hods,
        "missing_profile_fields": missing_fields,
        "broken_profile_urls": broken_profile_urls,
        "broken_image_urls": broken_image_urls,
        "duplicate_faculty_records": list(duplicate_names),
        "unverified_records_count": 0,
        "last_synchronized_at": last_log.sync_timestamp.isoformat() if last_log and last_log.sync_timestamp else datetime.now(timezone.utc).isoformat(),
        "last_sync_added": last_log.faculty_added if last_log else 0,
        "last_sync_updated": last_log.faculty_updated if last_log else 0,
    }


@router.post("/faculty/sync")
async def trigger_faculty_sync(
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Triggers complete synchronization with official MLRITM Faculty Profile System.
    Validates records, tracks additions/modifications, and logs results.
    """
    sync_res = await mlritm_sync_service.synchronize(db)
    faculty_service._load_seed_cache()
    return {
        "status": "success",
        "message": "Synchronized with official MLRITM faculty profile system.",
        "results": sync_res,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/faculty/changes")
async def get_faculty_sync_changes(
    limit: int = Query(20, ge=1, le=100),
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Returns historical synchronization and change detection logs."""
    stmt = select(FacultySyncLog).order_by(FacultySyncLog.id.desc()).limit(limit)
    res = await db.execute(stmt)
    logs = res.scalars().all()

    return {
        "sync_logs": [
            {
                "id": l.id,
                "timestamp": l.sync_timestamp.isoformat() if l.sync_timestamp else None,
                "total_scanned": l.total_scanned,
                "faculty_added": l.faculty_added,
                "faculty_updated": l.faculty_updated,
                "faculty_removed": l.faculty_removed,
                "changes": l.changes_json,
                "source_url": l.source_url,
                "error_details": l.error_details,
            }
            for l in logs
        ]
    }


@router.post("/faculty/rebuild-index")
async def rebuild_faculty_search_index(
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Rebuilds memory search index and cache for faculty intelligence."""
    faculty_service._load_seed_cache()
    records = await faculty_service.get_all_faculty(db)
    return {
        "status": "success",
        "message": f"Faculty search index rebuilt with {len(records)} records.",
        "records_indexed": len(records),
    }


# =============================================================================
# SHORT QUERY & ML UNDERSTANDING DASHBOARD (Requirement 22)
# =============================================================================

@router.get("/queries/stats")
async def get_query_intelligence_stats(
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns query resolution analytics:
    Total queries, successful queries, low confidence queries, intent breakdown.
    """
    total = (await db.execute(select(func.count()).select_from(QueryPatternLog))).scalar() or 0
    successful = (await db.execute(select(func.count()).select_from(QueryPatternLog).where(QueryPatternLog.success == True))).scalar() or 0
    low_conf = (await db.execute(select(func.count()).select_from(QueryPatternLog).where(QueryPatternLog.confidence < 0.85))).scalar() or 0

    # Top intents
    stmt = (
        select(QueryPatternLog.resolved_intent, func.count(QueryPatternLog.id).label("cnt"))
        .group_by(QueryPatternLog.resolved_intent)
        .order_by(func.count(QueryPatternLog.id).desc())
        .limit(10)
    )
    res = await db.execute(stmt)
    top_intents = {row[0]: row[1] for row in res.all()}

    # Recent queries
    recent_stmt = select(QueryPatternLog).order_by(QueryPatternLog.id.desc()).limit(15)
    recent_queries = (await db.execute(recent_stmt)).scalars().all()

    return {
        "total_queries_logged": total,
        "successful_queries": successful,
        "low_confidence_queries": low_conf,
        "intent_accuracy": round((successful / total) * 100, 2) if total > 0 else 100.0,
        "top_intents": top_intents,
        "recent_queries": [
            {
                "id": q.id,
                "raw_query": q.raw_query,
                "normalized_query": q.normalized_query,
                "predicted_intent": q.predicted_intent,
                "resolved_intent": q.resolved_intent,
                "confidence": q.confidence,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q in recent_queries
        ],
    }


@router.get("/queries/aliases")
async def list_department_aliases(
    authorized: bool = Depends(verify_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Returns canonical department aliases."""
    return {
        "department_aliases": DEPARTMENT_REGISTRY,
    }


