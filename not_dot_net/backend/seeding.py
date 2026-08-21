"""Dev data seeding — fake users, workflow requests, and resource bookings."""

import logging
import os
import random as _random
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone, timedelta
from types import SimpleNamespace

from not_dot_net.backend.permissions import SYSTEM_ACTOR

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager

logger = logging.getLogger("not_dot_net.seeding")


def _refuse_in_production() -> None:
    """Refuse to seed fake users on a real deployment.

    Dev mode is identified the same way `app.py` does it: the absence of an
    explicit `DATABASE_URL`. In production the operator must export it; on
    a developer's laptop it stays unset and we fall back to local SQLite.
    """
    if "DATABASE_URL" in os.environ:
        raise RuntimeError(
            "Refusing to seed fake users in production "
            "(DATABASE_URL is set). Seeding is a dev-only workflow — "
            "100 users with password 'dev' would be created."
        )


async def seed_fake_users() -> None:
    """Seed ~100 fake users + ~20 workflows for development."""
    _refuse_in_production()
    from not_dot_net.backend.seed_data import get_fake_users
    from fastapi_users.exceptions import UserAlreadyExists

    created_users = []
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                count = 0
                for fake in get_fake_users():
                    try:
                        user = await user_manager.create(
                            UserCreate(
                                email=fake["email"],
                                password="dev",
                                is_active=True,
                                is_superuser=False,
                            )
                        )
                        for field in ("full_name", "phone", "office", "team", "title", "employment_status"):
                            setattr(user, field, fake.get(field))
                        if fake.get("start_date"):
                            user.start_date = date.fromisoformat(fake["start_date"])
                        if fake.get("end_date"):
                            user.end_date = date.fromisoformat(fake["end_date"])
                        user.role = fake["role"]
                        session.add(user)
                        created_users.append(user)
                        count += 1
                    except UserAlreadyExists:
                        pass
                await session.commit()
                if count:
                    logger.info("Seeded %d fake users", count)

    if created_users:
        await _seed_fake_workflows(created_users)
    await _seed_resources_and_bookings(created_users)
    await _seed_pages()


def _workflow_creator_candidates(users: list) -> list:
    """Prefer regular staff as demo requesters so approvals look realistic."""
    staff = [u for u in users if u.role == "staff"]
    return staff or [u for u in users if u.role in ("staff", "director")]


def _choose_workflow_approver(rng, directors: list, creator):
    """Choose a director who is not the requester when possible."""
    distinct_directors = [u for u in directors if u.id != creator.id]
    if distinct_directors:
        return rng.choice(distinct_directors)
    if directors:
        return rng.choice(directors)
    return creator


def _seed_actor(user):
    """Use the visible actor identity while bypassing RBAC for dev seed data."""
    return SimpleNamespace(
        id=user.id,
        email=user.email,
        role=user.role,
        is_superuser=True,
    )


async def _seed_fake_workflows(users: list) -> None:
    """Seed ~20 workflow requests in various states."""
    from not_dot_net.backend.seed_data import WORKFLOW_SEEDS
    from not_dot_net.backend.workflow_service import create_request, submit_step

    rng = _random.Random(42)

    staff = _workflow_creator_candidates(users)
    directors = [u for u in users if u.role == "director"]

    if not staff:
        return

    count = 0
    for seed in WORKFLOW_SEEDS:
        creator = rng.choice(staff)
        creator_actor = _seed_actor(creator)
        try:
            req = await create_request(
                workflow_type=seed["type"],
                created_by=creator.id,
                data=seed["data"],
                actor=creator_actor,
            )

            if seed["step"] == "request" and seed["action"] is None:
                pass
            elif seed["action"] == "submit":
                await submit_step(
                    req.id,
                    creator.id,
                    "submit",
                    data={},
                    actor_user=creator_actor,
                )
            elif seed["action"] == "approve":
                req = await submit_step(
                    req.id,
                    creator.id,
                    "submit",
                    data={},
                    actor_user=creator_actor,
                )
                approver = _choose_workflow_approver(rng, directors, creator)
                approver_actor = _seed_actor(approver)
                if seed["type"] == "onboarding" and seed["step"] in ("admin_validation", "done"):
                    req = await submit_step(
                        req.id,
                        None,
                        "submit",
                        data=seed["data"],
                        actor_token=req.token,
                    )
                await submit_step(
                    req.id, approver.id, "approve",
                    comment=seed.get("comment"),
                    actor_user=approver_actor,
                    ad_creds=("seed", "seed"),
                )
            elif seed["action"] == "reject":
                req = await submit_step(
                    req.id,
                    creator.id,
                    "submit",
                    data={},
                    actor_user=creator_actor,
                )
                approver = _choose_workflow_approver(rng, directors, creator)
                approver_actor = _seed_actor(approver)
                if seed["type"] == "onboarding" and seed["step"] == "rejected":
                    req = await submit_step(
                        req.id,
                        None,
                        "submit",
                        data=seed["data"],
                        actor_token=req.token,
                    )
                await submit_step(
                    req.id, approver.id, "reject",
                    comment=seed.get("comment"),
                    actor_user=approver_actor,
                )

            await _backdate_workflow_request(req.id, rng.randint(0, 10))
            count += 1
        except Exception as e:
            logger.warning("Seed workflow failed: %s", e)

    if count:
        logger.info("Seeded %d workflow requests", count)


async def _backdate_workflow_request(request_id, days: int) -> None:
    """Move a demo workflow request and its events back by a few days."""
    if days <= 0:
        return

    from sqlalchemy import select

    from not_dot_net.backend.workflow_models import WorkflowEvent, WorkflowRequest

    timestamp = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            return
        req.created_at = timestamp
        req.updated_at = timestamp
        result = await session.execute(
            select(WorkflowEvent).where(WorkflowEvent.request_id == request_id)
        )
        for event in result.scalars().all():
            event.created_at = timestamp
        await session.commit()


async def _seed_resources_and_bookings(users: list) -> None:
    """Seed resources and a few sample bookings."""
    from not_dot_net.backend.seed_data import SEED_RESOURCES
    from not_dot_net.backend.booking_service import (
        create_resource,
        create_booking,
        list_resources,
    )
    from not_dot_net.config import bookings_config

    existing = await list_resources(active_only=False)
    if existing:
        return

    rng = _random.Random(42)
    resources = []
    for seed in SEED_RESOURCES:
        res = await create_resource(
            name=seed["name"],
            resource_type=seed["type"],
            description=seed.get("description", ""),
            location=seed.get("location", ""),
            specs=seed.get("specs"),
            actor=SYSTEM_ACTOR,
        )
        resources.append(res)

    if not users:
        logger.info("Seeded %d resources", len(resources))
        return

    today = date.today()
    booking_cfg = await bookings_config.get()
    count = 0
    for res in resources[:5]:
        booker = rng.choice(users)
        start = today + timedelta(days=rng.randint(booking_cfg.minimum_lead_days, 30))
        end = start + timedelta(days=rng.randint(1, 14))
        try:
            await create_booking(res.id, booker.id, start, end, note="Dev seed booking",
                                 actor=_seed_actor(booker))
            count += 1
        except Exception:
            pass

    logger.info("Seeded %d resources, %d bookings", len(resources), count)


SEED_PAGES = [
    {
        "title": "Welcome to LPP Intranet",
        "slug": "welcome",
        "sort_order": 1,
        "published": True,
        "content": """\
# Welcome to the LPP Intranet

This is the internal portal for **Laboratoire de Physique des Plasmas**.

## Quick Links

- **People** — find colleagues, offices, and phone numbers
- **Bookings** — reserve desktops and laptops
- **Pages** — documentation and guides

## Getting Started

If you are new to the lab, please check the [onboarding guide](/pages/onboarding)
and make sure your VPN access request has been submitted.

## Contact

For technical issues with this intranet, contact the IT team at `it@lpp.fr`.
""",
    },
    {
        "title": "Onboarding Guide",
        "slug": "onboarding",
        "sort_order": 2,
        "published": True,
        "content": """\
# Onboarding Guide

Welcome to LPP! Here's what you need to do in your first week.

## Day 1

1. Get your badge from the reception desk (Building A, ground floor)
2. Set up your workstation — request one via the **Bookings** tab
3. Submit a **VPN access request** via the Dashboard

## First Week

- [ ] Complete mandatory safety training
- [ ] Join the lab mailing lists (ask your team lead)
- [ ] Set up your email signature

## IT Resources

| Resource | How to Access |
|----------|--------------|
| WiFi | Network: `LPP-Staff`, credentials from IT |
| VPN | Submit request via Dashboard |
| Printers | Auto-discovered on the network |
| GitLab | `https://gitlab.lpp.fr` — use LDAP credentials |

## Useful Contacts

- **IT Support**: it@lpp.fr
- **HR**: rh@lpp.fr
- **Facility Manager**: services@lpp.fr
""",
    },
    {
        "title": "Meeting Rooms",
        "slug": "meeting-rooms",
        "sort_order": 3,
        "published": True,
        "content": """\
# Meeting Rooms

## Building A

| Room | Capacity | Equipment |
|------|----------|-----------|
| A101 — Salle Arago | 20 | Projector, videoconference |
| A204 — Salle Coulomb | 8 | Screen, whiteboard |
| A310 — Salle Maxwell | 6 | Whiteboard only |

## Building B

| Room | Capacity | Equipment |
|------|----------|-----------|
| B102 — Salle Ampere | 40 | Projector, microphone, recording |
| B205 — Salle Faraday | 12 | Projector, videoconference |

## Booking

Meeting rooms are managed via the shared Nextcloud calendar.
Contact your team assistant for access.
""",
    },
    {
        "title": "Network & VPN Configuration",
        "slug": "network-vpn",
        "sort_order": 4,
        "published": False,
        "content": """\
# Network & VPN Configuration

> **Draft** — this page is being updated.

## VPN Setup

### Linux

```bash
sudo apt install openconnect
sudo openconnect vpn.lpp.fr --user=your.name
```

### macOS

Install Cisco AnyConnect from Self Service, then connect to `vpn.lpp.fr`.

### Windows

Download the AnyConnect client from `https://vpn.lpp.fr` and follow the prompts.

## Proxy Settings

Internal services do not require a proxy. For external access from lab machines:

```
http_proxy=http://proxy.lpp.fr:3128
https_proxy=http://proxy.lpp.fr:3128
```
""",
    },
]


async def _seed_pages() -> None:
    """Seed demo markdown pages."""
    from not_dot_net.backend.page_service import list_pages, create_page

    existing = await list_pages(published_only=False)
    if existing:
        return

    count = 0
    for seed in SEED_PAGES:
        await create_page(
            title=seed["title"],
            slug=seed["slug"],
            content=seed["content"],
            author_id=None,
            sort_order=seed["sort_order"],
            published=seed["published"],
            actor=SYSTEM_ACTOR,
        )
        count += 1

    logger.info("Seeded %d pages", count)
