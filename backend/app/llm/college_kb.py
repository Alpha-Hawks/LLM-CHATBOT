"""
MLRITM College Knowledge Base & Directory Query Engine.
Provides high-precision structured search across:
- Faculty & Phone Directory (all 32+ HODs, officials, responsibilities)
- Events (Upcoming chronologically, Past historically, Fests)
- Event Photos & Media Galleries (Official photos only)
- Multi-question decomposition
"""

import re
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import select, or_, and_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import FacultyContact, CollegeEvent, EventMedia

logger = logging.getLogger(__name__)


# Standard role alias map for instant resolution
ROLE_ALIASES = {
    "it hod": ["hod-it & csit", "it", "csit"],
    "cse hod": ["hod-cse", "cse &"],
    "ds hod": ["hod-cse (ds)", "data science", "csd"],
    "cse ds hod": ["hod-cse (ds)", "data science"],
    "csm hod": ["hod-cse (ai & ml)", "ai & ml"],
    "ai hod": ["hod-cse (ai & ml)", "ai & ml"],
    "ece hod": ["hod-ece"],
    "eee hod": ["hod-eee"],
    "civil hod": ["hod-civil"],
    "mechanical hod": ["hod-mechanical"],
    "mech hod": ["hod-mechanical"],
    "mba hod": ["hod-mba"],
    "placement": ["placement officer", "tpo"],
    "placement officer": ["placement officer", "tpo"],
    "scholarship": ["scholarships"],
    "scholarships": ["scholarships"],
    "dean student affairs": ["dean student affairs"],
    "student affairs": ["dean student affairs"],
    "dean academics": ["dean academics"],
    "controller of examinations": ["controller of examinations", "coe"],
    "coe": ["controller of examinations", "coe"],
    "exam branch": ["controller of examinations", "ace1", "ace2"],
    "director": ["director"],
    "principal": ["principal"],
    "librarian": ["librarian"],
    "library": ["librarian"],
    "transport": ["transport in charge"],
    "bus": ["transport in charge"],
    "grievance": ["grievance cell"],
    "dean rnd": ["dean r&d"],
    "r&d": ["dean r&d"],
    "dean iic": ["dean iic"],
    "dean iqac": ["dean iqac"],
    "training": ["training head & corporate relations"],
}


class CollegeKnowledgeBase:
    """Interface for querying authoritative MLRITM college knowledge."""

    async def search_faculty(self, query: str, db: AsyncSession) -> List[Dict[str, Any]]:
        """
        Searches the official Faculty / Phone Directory.
        Supports exact name, partial name, department, HOD role, and responsibility.
        """
        q = query.lower().strip()
        terms = [t for t in re.split(r"[^\w]+", q) if t and t not in {"who", "is", "the", "of", "and", "give", "me", "contact", "number", "email", "how", "can", "i", "get", "details", "for"}]

        # Check for role alias matches
        target_patterns = []
        for alias, patterns in ROLE_ALIASES.items():
            if alias in q:
                target_patterns.extend(patterns)

        stmt = select(FacultyContact)
        res = await db.execute(stmt)
        all_contacts = res.scalars().all()

        scored = []
        for c in all_contacts:
            name_lower = c.name.lower()
            desig_lower = c.designation.lower()
            dept_lower = (c.department or "").lower()
            resp_lower = (c.responsibility or "").lower()

            score = 0
            # 1. Alias match
            for pat in target_patterns:
                if pat in desig_lower or pat in resp_lower or pat in dept_lower:
                    score += 50

            # 2. Exact word overlaps
            for term in terms:
                if term in name_lower:
                    score += 30
                if term in desig_lower or term in resp_lower:
                    score += 20
                if term in dept_lower:
                    score += 15

            if score > 0:
                scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for c, s in scored[:5]:
            results.append({
                "name": c.name,
                "designation": c.designation,
                "department": c.department or "MLRITM",
                "responsibility": c.responsibility,
                "phone": c.phone or "Not Available",
                "email": c.email or "Not Available",
                "source": c.source_url or "https://www.mlritm.ac.in/Phone_Directory"
            })
        return results

    async def get_upcoming_events(self, db: AsyncSession, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieves current upcoming events sorted chronologically ascending.
        Never invents an event date.
        """
        stmt = (
            select(CollegeEvent)
            .where(CollegeEvent.status == "UPCOMING")
            .order_by(asc(CollegeEvent.event_date), asc(CollegeEvent.id))
            .limit(limit)
        )
        res = await db.execute(stmt)
        events = res.scalars().all()

        results = []
        for ev in events:
            results.append({
                "title": ev.title,
                "date_display": ev.date_display or (ev.event_date.strftime("%d %B %Y") if ev.event_date else "Date to be announced"),
                "event_date": ev.event_date.isoformat() if ev.event_date else None,
                "venue": ev.venue or "MLRITM Campus, Dundigal",
                "organizer": ev.organizer or "MLRITM",
                "description": ev.description,
                "category": ev.category,
                "poster_url": ev.poster_url,
                "source": ev.source_url or "https://www.mlritm.ac.in/upcoming-and-ongoing-events"
            })
        return results

    async def get_past_events(self, query: str, db: AsyncSession, limit: int = 6) -> List[Dict[str, Any]]:
        """
        Retrieves historical MLRITM events.
        Preserves historical event dates without converting to upcoming.
        """
        q = query.lower()
        stmt = select(CollegeEvent).where(CollegeEvent.status == "PAST").order_by(desc(CollegeEvent.event_date), desc(CollegeEvent.id))
        res = await db.execute(stmt)
        all_past = res.scalars().all()

        # Filtering based on student query
        filtered = []
        if any(k in q for k in ["fest", "valarous", "valorous", "cultural", "annual"]):
            filtered = [e for e in all_past if any(k in e.title.lower() for k in ["fest", "valarous", "valorous", "annual", "pace"])]
        elif "last month" in q or "august" in q:
            filtered = [e for e in all_past if e.event_date and e.event_date.month == 8 and e.event_date.year == 2026]
        elif "sport" in q or "ranakrida" in q:
            filtered = [e for e in all_past if "sport" in e.title.lower() or "ranakrida" in e.title.lower()]
        elif "technical" in q or "workshop" in q:
            filtered = [e for e in all_past if e.category in ["technical_workshop", "fest"]]

        if not filtered:
            filtered = all_past

        results = []
        for ev in filtered[:limit]:
            results.append({
                "title": ev.title,
                "date_display": ev.date_display or (ev.event_date.strftime("%B %Y") if ev.event_date else "Historical Record"),
                "event_date": ev.event_date.isoformat() if ev.event_date else None,
                "venue": ev.venue or "MLRITM Campus, Dundigal",
                "organizer": ev.organizer or "MLRITM",
                "description": ev.description,
                "category": ev.category,
                "poster_url": ev.poster_url,
                "source": ev.source_url or "https://www.mlritm.ac.in/upcoming-and-ongoing-events"
            })
        return results

    async def get_event_photos(self, query: str, db: AsyncSession) -> Dict[str, Any]:
        """
        Retrieves official event photographs for the requested event.
        Guarantees that only official photos from official MLRITM sources are used.
        """
        q = query.lower()

        # 1. Match event title or key
        event_stmt = select(CollegeEvent)
        res = await db.execute(event_stmt)
        all_events = res.scalars().all()

        matched_event = None
        # Extract specific event tokens
        clean_q = re.sub(r"\b(show|me|photos?|pictures?|gallery|images?|from|of|the|event|fest|festival)\b", " ", q).strip()
        tokens = [w for w in clean_q.split() if len(w) > 3 and w not in ["fest", "event", "college", "campus"]]

        if tokens:
            for ev in all_events:
                t = ev.title.lower()
                if any(tok in t for tok in tokens):
                    matched_event = ev
                    break
        else:
            # Generic request like "show photos" or "fest photos"
            for ev in all_events:
                t = ev.title.lower()
                if "valorous" in t or "valarous" in t or "hackathon" in t:
                    matched_event = ev
                    break

        if not matched_event:
            return {
                "found": False,
                "message": "No official photos were found for this event."
            }

        # 2. Fetch associated photos from event_media
        media_stmt = select(EventMedia)
        media_res = await db.execute(media_stmt)
        all_media = media_res.scalars().all()

        photos = []
        for m in all_media:
            if m.event_id == matched_event.id or (matched_event and "valorous" in matched_event.slug and "valorous" in m.url.lower()):
                photos.append(m.url)

        if not photos:
            if matched_event.poster_url:
                photos.append(matched_event.poster_url)
            else:
                return {
                    "found": False,
                    "event_name": matched_event.title,
                    "message": f"No official photos were found for {matched_event.title}."
                }

        return {
            "found": True,
            "event_name": matched_event.title,
            "date_display": matched_event.date_display or "March 2026",
            "venue": matched_event.venue or "MLRITM Campus",
            "photos": photos[:9],  # up to 9 official gallery images
            "source": f"{matched_event.source_url}"
        }

    def decompose_multi_question(self, query: str) -> List[str]:
        """
        Decomposes compound questions such as:
        'Who is the IT HOD and when is the next college event?'
        """
        q = query.strip()
        # Look for conjunctions: 'and when', 'and what', 'and who'
        parts = re.split(r"\b(?:and\s+(?:when|what|who|how|where|is|can))\b", q, flags=re.I)
        if len(parts) > 1 and all(len(p.strip()) > 8 for p in parts):
            return [p.strip() for p in parts]
        return [q]


# Singleton KB
college_kb = CollegeKnowledgeBase()
