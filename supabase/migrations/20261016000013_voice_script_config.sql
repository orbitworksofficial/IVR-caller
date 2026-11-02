-- Phase 4: Tenant voice scripts & configuration override support (Hammad)
--
-- Adds voice_script JSONB column to ivr_bot_configs.
-- Stores custom tenant greetings, filler phrases, abstain phrases,
-- custom objection handling overrides, warm transfer destination numbers,
-- extra business rules, and specific call models.
--
-- DB values win over application defaults, allowing client onboarding
-- purely as a configuration exercise without code deployments.

ALTER TABLE ivr_bot_configs
    ADD COLUMN IF NOT EXISTS voice_script JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN ivr_bot_configs.voice_script IS 'Tenant-specific voice script overrides (greetings, objections, abstentions, fillers, extra rules).';
