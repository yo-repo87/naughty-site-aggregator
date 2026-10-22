-- Account schema. Idempotent: applied on every start (no migration tool).
-- Columns ending in _enc hold AES-256-GCM ciphertext (see crypto.py); columns
-- ending in _idx / _hash hold HMAC-SHA256 values. No personal data is stored
-- in plain text.

CREATE TABLE IF NOT EXISTS users (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email_idx        bytea UNIQUE,                 -- HMAC of the lower-cased email
    email_enc        bytea,
    name_enc         bytea,
    password_hash    text,                         -- Argon2id; NULL = no password (OAuth/passkey only)
    totp_secret_enc  bytea,
    totp_enabled     boolean NOT NULL DEFAULT false,
    totp_last_step   bigint,                       -- blocks reuse of a code
    settings_enc     bytea,                        -- synced app settings (JSON)
    webauthn_handle  bytea NOT NULL,               -- random WebAuthn user handle
    failed_logins    integer NOT NULL DEFAULT 0,
    locked_until     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_login_at    timestamptz
);
-- Added later: the user's MeTube connection (JSON: url, username, password).
ALTER TABLE users ADD COLUMN IF NOT EXISTS metube_enc bytea;
CREATE UNIQUE INDEX IF NOT EXISTS users_webauthn_handle ON users (webauthn_handle);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   bytea PRIMARY KEY,                -- HMAC of the cookie token
    user_id      uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    verified_at  timestamptz NOT NULL DEFAULT now(), -- last time the user proved it's them (step-up)
    expires_at   timestamptz NOT NULL,
    device_enc   bytea,                            -- user agent summary
    ip_enc       bytea
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id);

-- Password accepted, second step outstanding.
CREATE TABLE IF NOT EXISTS pending_logins (
    token_hash bytea PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    credential_id  bytea NOT NULL UNIQUE,
    public_key     bytea NOT NULL,
    sign_count     bigint NOT NULL DEFAULT 0,
    kind           text NOT NULL CHECK (kind IN ('passkey', 'security_key')),
    name_enc       bytea,
    transports     text[] NOT NULL DEFAULT '{}',
    backed_up      boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_used_at   timestamptz
);
CREATE INDEX IF NOT EXISTS webauthn_credentials_user ON webauthn_credentials (user_id);

CREATE TABLE IF NOT EXISTS backup_codes (
    user_id   uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    code_hash bytea NOT NULL,
    used_at   timestamptz,
    PRIMARY KEY (user_id, code_hash)
);

CREATE TABLE IF NOT EXISTS oauth_identities (
    provider    text NOT NULL,
    subject_idx bytea NOT NULL,                    -- HMAC of the provider's user id
    user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    email_enc   bytea,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, subject_idx),
    UNIQUE (user_id, provider)
);

-- Short-lived WebAuthn challenges and OAuth state, keyed by an HMAC of a random
-- handle kept in an HttpOnly cookie.
CREATE TABLE IF NOT EXISTS auth_challenges (
    handle_hash bytea PRIMARY KEY,
    purpose     text NOT NULL,
    user_id     uuid REFERENCES users ON DELETE CASCADE,
    data_enc    bytea NOT NULL,
    expires_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS security_events (
    id         bigserial PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    event      text NOT NULL,
    detail_enc bytea,                              -- e.g. device / IP, encrypted
    at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS security_events_user ON security_events (user_id, at DESC);

-- "Send to" targets (MeTube, JDownloader, webhook, Discord, ntfy, plug-ins).
-- config_enc: JSON of the target's fields, bound to (user, row id).
CREATE TABLE IF NOT EXISTS user_targets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    kind        text NOT NULL,
    name_enc    bytea,
    config_enc  bytea NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_targets_user ON user_targets (user_id);

-- Saved-search alerts, checked in the background; notify through a target.
CREATE TABLE IF NOT EXISTS alerts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    target_id   uuid REFERENCES user_targets ON DELETE SET NULL,
    data_enc    bytea NOT NULL,                -- {name, params}
    seen_enc    bytea,                         -- video keys already reported
    last_run    timestamptz,
    last_new    integer NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_user ON alerts (user_id);

-- Settings the admin changes at runtime (override the environment).
CREATE TABLE IF NOT EXISTS app_settings (
    key   text PRIMARY KEY,
    value text NOT NULL
);

-- Download updates: where to notify when a sent video starts / finishes (Profile tab).
ALTER TABLE users ADD COLUMN IF NOT EXISTS dl_notify_target uuid REFERENCES user_targets ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS dl_notify_events text;   -- "start", "finish" or "start,finish"

-- Videos sent to a target that reports progress (MeTube), watched until done.
CREATE TABLE IF NOT EXISTS download_watches (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
    target_id   uuid NOT NULL REFERENCES user_targets ON DELETE CASCADE,
    data_enc    bytea NOT NULL,                -- {url, title}
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS download_watches_user ON download_watches (user_id);
