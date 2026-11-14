-- Phase 3 & 4 Platform Hardening & Voice Admin Support (Kashif)
--
-- 1. recording_retention_days on ivr_bot_configs (Phase 3 recording retention policy)
-- 2. SQL RPC get_campaign_contact_counts(p_campaign_id UUID) for fast campaign status counts

ALTER TABLE ivr_bot_configs
    ADD COLUMN IF NOT EXISTS recording_retention_days INTEGER DEFAULT 90;

COMMENT ON COLUMN ivr_bot_configs.recording_retention_days IS 'Number of days to retain call recordings before automatic expiry/purge.';

CREATE OR REPLACE FUNCTION get_campaign_contact_counts(p_campaign_id UUID)
RETURNS TABLE(status TEXT, count BIGINT)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT status, COUNT(*)::BIGINT AS count
    FROM campaign_contacts
    WHERE campaign_id = p_campaign_id
    GROUP BY status;
$$;
