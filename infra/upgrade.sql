-- ===========================================
-- JOHKA DATABASE UPGRADE
-- Extra kolommen voor profiel en wallet
-- ===========================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS gallery_json TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance INTEGER DEFAULT 100;
