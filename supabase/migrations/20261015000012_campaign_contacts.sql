-- Campaign runner (Phase 2, Kashif): the call list a campaign works through.
--
-- call_campaigns had no membership table — dial_attempts records what happened,
-- not who is still to be called. campaign_contacts is the queue:
--
--   pending ──dial──► dialing ──call ends──► done        (a person answered)
--      ▲                 │                ► pending     (voicemail/no answer/busy → retry later)
--      │                 │                ► exhausted   (attempt limit reached)
--      └──(window/retry/transient error: try later)
--   pending ──policy──► blocked                          (DNC / no consent / invalid number)
--
-- Compliance is NOT decided here: every dial still goes through the dial
-- policy at dial time. A contact being in this table grants nothing.

CREATE TABLE IF NOT EXISTS campaign_contacts (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id      UUID NOT NULL REFERENCES call_campaigns(id) ON DELETE CASCADE,
    lead_id          UUID REFERENCES leads(id) ON DELETE SET NULL,
    phone_number     TEXT NOT NULL,                       -- E.164
    status           TEXT NOT NULL DEFAULT 'pending',     -- pending | dialing | done | blocked | exhausted
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_call_id  UUID REFERENCES calls(id) ON DELETE SET NULL,
    last_outcome     TEXT,                                -- last dial/call outcome or block reason
    claimed_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(campaign_id, phone_number)
);

-- Max simultaneous live calls per campaign (protects agents/handoff capacity
-- and Twilio concurrency).
ALTER TABLE call_campaigns
    ADD COLUMN IF NOT EXISTS max_concurrent INTEGER NOT NULL DEFAULT 2;

CREATE INDEX IF NOT EXISTS idx_campaign_contacts_due
    ON campaign_contacts(campaign_id, status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_tenant ON campaign_contacts(tenant_id);

ALTER TABLE campaign_contacts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON campaign_contacts;
CREATE POLICY tenant_isolation ON campaign_contacts
    USING (tenant_id::text = current_setting('app.current_tenant_id', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', TRUE));

DROP TRIGGER IF EXISTS trg_campaign_contacts_updated_at ON campaign_contacts;
CREATE TRIGGER trg_campaign_contacts_updated_at
    BEFORE UPDATE ON campaign_contacts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
