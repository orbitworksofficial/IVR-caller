"""
Phase 4 Voice Admin API — manages phone numbers, voice profiles, per-bot IVR
configuration, call history (turns, transcripts, recordings), consent records,
DNC list, and outbound call campaigns.

Every endpoint resolves `tenant_id` from `get_current_admin` and scopes all
database reads and writes to that tenant.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from ai.prompts.voice.script_config import validate_voice_script
from apps.api.admin_auth import get_current_admin, verify_bot_ownership
from apps.api.dependencies import get_supabase_admin_client
from apps.api.permissions import require
from apps.voice_gateway import twilio_utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/voice", tags=["Voice Admin"])

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _tenant(admin: Dict[str, Any]) -> str:
    return admin["tenant_id"]


def _require_e164(phone: str) -> str:
    cleaned = (phone or "").strip()
    if not _E164_RE.match(cleaned):
        raise HTTPException(
            status_code=400,
            detail="Phone number must be in E.164 format (e.g. +14435550123).",
        )
    return cleaned


# ── Schemas ──────────────────────────────────────────────────────────────────


class PhoneNumberCreateRequest(BaseModel):
    phone_number: str
    bot_id: Optional[str] = None
    friendly_name: Optional[str] = None
    direction: str = Field(default="both", pattern="^(inbound|outbound|both)$")
    configure_twilio_webhooks: bool = True


class VoiceProfileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    voice_id: str = Field(..., min_length=1, max_length=120)
    provider: str = "elevenlabs"
    model_id: str = "eleven_flash_v2_5"
    stability: float = Field(default=0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(default=0.8, ge=0.0, le=1.0)
    style: float = Field(default=0.0, ge=0.0, le=1.0)
    is_default: bool = False


class BotVoiceConfigUpdateRequest(BaseModel):
    phone_number_id: Optional[str] = None
    voice_profile_id: Optional[str] = None
    voice_id: Optional[str] = None
    voice_model_id: Optional[str] = None
    voice_stability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    voice_similarity_boost: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    voice_style: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    stt_model: Optional[str] = None
    stt_endpointing_ms: Optional[int] = Field(default=None, ge=50, le=3000)
    stt_utterance_end_ms: Optional[int] = Field(default=None, ge=200, le=5000)
    call_recording_enabled: Optional[bool] = None
    recording_disclosure_text: Optional[str] = None
    recording_retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    outbound_caller_id: Optional[str] = None
    calling_window_start: Optional[str] = None
    calling_window_end: Optional[str] = None
    calling_timezone: Optional[str] = None
    max_call_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    retry_interval_hours: Optional[int] = Field(default=None, ge=1, le=720)
    voicemail_message: Optional[str] = None
    abstain_phrase: Optional[str] = None
    filler_phrase: Optional[str] = None
    voice_script: Optional[Dict[str, Any]] = None


class ConsentGrantRequest(BaseModel):
    phone_number: str
    consent_type: str = Field(default="call", pattern="^(call|sms|email|recording)$")
    source: str = Field(default="crm_import")
    evidence_text: Optional[str] = None
    contact_id: Optional[str] = None


class DncAddRequest(BaseModel):
    phone_number: str
    source: str = "internal"
    reason: Optional[str] = None


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    bot_id: str
    campaign_type: str = Field(default="follow_up")
    call_reason: Optional[str] = None
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_concurrent: int = Field(default=2, ge=1, le=20)
    phone_numbers: Optional[List[str]] = None


class CampaignUpdateRequest(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(draft|active|paused|completed)$")
    name: Optional[str] = None
    call_reason: Optional[str] = None
    max_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=20)


class CampaignAddContactsRequest(BaseModel):
    phone_numbers: List[str]


# ── Phone numbers ────────────────────────────────────────────────────────────


@router.get("/numbers")
async def list_phone_numbers(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = (
        supabase.table("phone_numbers")
        .select("*")
        .eq("tenant_id", _tenant(admin))
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


@router.post("/numbers", status_code=201)
async def add_phone_number(
    payload: PhoneNumberCreateRequest,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    number = _require_e164(payload.phone_number)

    if payload.bot_id:
        await verify_bot_ownership(payload.bot_id, tenant_id, supabase)

    taken = (
        supabase.table("phone_numbers")
        .select("tenant_id")
        .eq("phone_number", number)
        .eq("is_active", True)
        .execute()
    )
    if taken.data and str(taken.data[0]["tenant_id"]) != tenant_id:
        raise HTTPException(
            status_code=409,
            detail="This phone number is already registered to another tenant.",
        )

    row = {
        "tenant_id": tenant_id,
        "bot_id": payload.bot_id,
        "phone_number": number,
        "friendly_name": payload.friendly_name,
        "direction": payload.direction,
        "is_active": True,
    }
    resp = (
        supabase.table("phone_numbers")
        .upsert(row, on_conflict="tenant_id,phone_number")
        .execute()
    )
    saved = resp.data[0] if resp.data else row

    if payload.bot_id and saved.get("id"):
        supabase.table("ivr_bot_configs").upsert(
            {
                "tenant_id": tenant_id,
                "bot_id": payload.bot_id,
                "phone_number_id": saved["id"],
            },
            on_conflict="tenant_id,bot_id",
        ).execute()

    webhook_info = None
    if payload.configure_twilio_webhooks:
        webhook_info = await twilio_utils.configure_number_webhooks(number)

    return {**saved, "twilio_webhooks": webhook_info}


@router.delete("/numbers/{number_id}", status_code=204)
async def delete_phone_number(
    number_id: str,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    supabase.table("phone_numbers").delete().eq("id", number_id).eq(
        "tenant_id", _tenant(admin)
    ).execute()


# ── Voice profiles ───────────────────────────────────────────────────────────


@router.get("/profiles")
async def list_voice_profiles(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = (
        supabase.table("voice_profiles")
        .select("*")
        .eq("tenant_id", _tenant(admin))
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


@router.post("/profiles", status_code=201)
async def create_voice_profile(
    payload: VoiceProfileCreateRequest,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    record = payload.model_dump()
    record["tenant_id"] = tenant_id
    try:
        resp = (
            supabase.table("voice_profiles")
            .upsert(record, on_conflict="tenant_id,name")
            .execute()
        )
    except Exception as e:
        logger.error(f"Voice profile upsert failed: {e}")
        raise HTTPException(status_code=400, detail="Could not save voice profile.")
    return resp.data[0] if resp.data else record


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_voice_profile(
    profile_id: str,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    supabase.table("voice_profiles").delete().eq("id", profile_id).eq(
        "tenant_id", _tenant(admin)
    ).execute()


# ── Per-bot IVR configuration ────────────────────────────────────────────────


@router.get("/bots/{bot_id}/config")
async def get_bot_voice_config(
    bot_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    await verify_bot_ownership(bot_id, tenant_id, supabase)

    resp = (
        supabase.table("ivr_bot_configs")
        .select("*, voice_profiles(*)")
        .eq("tenant_id", tenant_id)
        .eq("bot_id", bot_id)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]

    # Return defaults if not yet persisted
    return {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "voice_model_id": "eleven_flash_v2_5",
        "voice_stability": 0.5,
        "voice_similarity_boost": 0.8,
        "voice_style": 0.0,
        "stt_model": "nova-2-phonecall",
        "stt_endpointing_ms": 300,
        "stt_utterance_end_ms": 1000,
        "call_recording_enabled": True,
        "recording_disclosure_text": "This call may be recorded for quality assurance purposes.",
        "recording_retention_days": 90,
        "calling_window_start": "09:00:00",
        "calling_window_end": "18:00:00",
        "calling_timezone": "America/New_York",
        "max_call_attempts": 3,
        "retry_interval_hours": 24,
        "voice_script": {},
    }


@router.put("/bots/{bot_id}/config")
async def update_bot_voice_config(
    bot_id: str,
    payload: BotVoiceConfigUpdateRequest,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    await verify_bot_ownership(bot_id, tenant_id, supabase)

    updates = payload.model_dump(exclude_unset=True)
    if "voice_script" in updates and updates["voice_script"] is not None:
        ok, problems = validate_voice_script(updates["voice_script"])
        if not ok or problems:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid voice_script: {'; '.join(problems)}",
            )

    record = {"tenant_id": tenant_id, "bot_id": bot_id, **updates}
    try:
        resp = (
            supabase.table("ivr_bot_configs")
            .upsert(record, on_conflict="tenant_id,bot_id")
            .execute()
        )
    except Exception as e:
        logger.error(f"ivr_bot_configs update failed for bot {bot_id}: {e}")
        raise HTTPException(status_code=400, detail="Could not save voice configuration.")
    return resp.data[0] if resp.data else record


# ── Calls, transcripts & recordings ──────────────────────────────────────────


@router.get("/calls")
async def list_calls(
    limit: int = 50,
    direction: Optional[str] = None,
    outcome: Optional[str] = None,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    query = (
        supabase.table("calls")
        .select("*")
        .eq("tenant_id", _tenant(admin))
    )
    if direction:
        query = query.eq("direction", direction)
    if outcome:
        query = query.eq("outcome", outcome)
    resp = query.order("started_at", desc=True).limit(min(limit, 200)).execute()
    return resp.data or []


@router.get("/calls/{call_id}")
async def get_call_detail(
    call_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    call_resp = (
        supabase.table("calls")
        .select("*")
        .eq("id", call_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not call_resp.data:
        raise HTTPException(status_code=404, detail="Call not found.")

    turns_resp = (
        supabase.table("call_turns")
        .select("*")
        .eq("call_id", call_id)
        .eq("tenant_id", tenant_id)
        .order("turn_index")
        .execute()
    )
    transcript_resp = (
        supabase.table("call_transcripts")
        .select("*")
        .eq("call_id", call_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    recordings_resp = (
        supabase.table("call_recordings")
        .select("*")
        .eq("call_id", call_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )

    return {
        "call": call_resp.data[0],
        "turns": turns_resp.data or [],
        "transcript": transcript_resp.data[0] if transcript_resp.data else None,
        "recordings": recordings_resp.data or [],
    }


# ── Consent & DNC ────────────────────────────────────────────────────────────


@router.get("/consent")
async def list_consent_records(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = (
        supabase.table("consent_records")
        .select("*")
        .eq("tenant_id", _tenant(admin))
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    return resp.data or []


@router.post("/consent", status_code=201)
async def grant_consent(
    payload: ConsentGrantRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    number = _require_e164(payload.phone_number)
    row = {
        "tenant_id": tenant_id,
        "phone_number": number,
        "consent_type": payload.consent_type,
        "source": payload.source,
        "evidence_text": payload.evidence_text,
        "contact_id": payload.contact_id,
        "granted_at": datetime.now(timezone.utc).isoformat(),
        "revoked_at": None,
    }
    resp = (
        supabase.table("consent_records")
        .upsert(row, on_conflict="tenant_id,phone_number,consent_type")
        .execute()
    )
    return resp.data[0] if resp.data else row


@router.delete("/consent/{record_id}", status_code=204)
async def revoke_consent(
    record_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    supabase.table("consent_records").update(
        {"revoked_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", record_id).eq("tenant_id", _tenant(admin)).execute()


@router.get("/dnc")
async def list_dnc(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = (
        supabase.table("dnc_list")
        .select("*")
        .eq("tenant_id", _tenant(admin))
        .order("added_at", desc=True)
        .limit(200)
        .execute()
    )
    return resp.data or []


@router.post("/dnc", status_code=201)
async def add_to_dnc(
    payload: DncAddRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    number = _require_e164(payload.phone_number)
    row = {
        "tenant_id": tenant_id,
        "phone_number": number,
        "source": payload.source,
        "reason": payload.reason,
    }
    resp = (
        supabase.table("dnc_list")
        .upsert(row, on_conflict="tenant_id,phone_number")
        .execute()
    )
    # Also revoke active call consent immediately
    supabase.table("consent_records").update(
        {"revoked_at": datetime.now(timezone.utc).isoformat()}
    ).eq("tenant_id", tenant_id).eq("phone_number", number).eq(
        "consent_type", "call"
    ).is_("revoked_at", "null").execute()

    return resp.data[0] if resp.data else row


@router.delete("/dnc/{dnc_id}", status_code=204)
async def remove_from_dnc(
    dnc_id: str,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    supabase.table("dnc_list").delete().eq("id", dnc_id).eq(
        "tenant_id", _tenant(admin)
    ).execute()


# ── Outbound Campaigns ───────────────────────────────────────────────────────


@router.get("/campaigns")
async def list_campaigns(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    resp = (
        supabase.table("call_campaigns")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    campaigns = resp.data or []
    for camp in campaigns:
        cid = str(camp["id"])
        contacts_resp = (
            supabase.table("campaign_contacts")
            .select("status")
            .eq("campaign_id", cid)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        counts: Dict[str, int] = {}
        for c in contacts_resp.data or []:
            st = c.get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
        camp["contact_counts"] = counts
    return campaigns


@router.post("/campaigns", status_code=201)
async def create_campaign(
    payload: CampaignCreateRequest,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    await verify_bot_ownership(payload.bot_id, tenant_id, supabase)

    row = {
        "tenant_id": tenant_id,
        "bot_id": payload.bot_id,
        "name": payload.name,
        "campaign_type": payload.campaign_type,
        "call_reason": payload.call_reason,
        "max_attempts": payload.max_attempts,
        "max_concurrent": payload.max_concurrent,
        "status": "draft",
    }
    resp = supabase.table("call_campaigns").insert(row).execute()
    campaign = resp.data[0] if resp.data else row

    if payload.phone_numbers and campaign.get("id"):
        contacts = [
            {
                "tenant_id": tenant_id,
                "campaign_id": campaign["id"],
                "phone_number": _require_e164(num),
                "status": "pending",
            }
            for num in payload.phone_numbers
        ]
        supabase.table("campaign_contacts").upsert(
            contacts, on_conflict="campaign_id,phone_number"
        ).execute()

    return campaign


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    payload: CampaignUpdateRequest,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    resp = (
        supabase.table("call_campaigns")
        .update(updates)
        .eq("id", campaign_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return resp.data[0]


@router.post("/campaigns/{campaign_id}/contacts", status_code=201)
async def add_campaign_contacts(
    campaign_id: str,
    payload: CampaignAddContactsRequest,
    admin: Dict[str, Any] = Depends(require("manage_voice")),
    supabase: Client = Depends(get_supabase_admin_client),
):
    tenant_id = _tenant(admin)
    camp = (
        supabase.table("call_campaigns")
        .select("id")
        .eq("id", campaign_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not camp.data:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    rows = [
        {
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "phone_number": _require_e164(n),
            "status": "pending",
        }
        for n in payload.phone_numbers
    ]
    resp = (
        supabase.table("campaign_contacts")
        .upsert(rows, on_conflict="campaign_id,phone_number")
        .execute()
    )
    return {"added": len(resp.data or rows)}
