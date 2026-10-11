from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets-at-rest / session signing. No defaults: the app must refuse to
    # start rather than silently run with an ephemeral key that would brick
    # every encrypted row on the next restart.
    webui_master_key: str
    session_secret: str

    # Authelia OIDC Relying Party configuration. Required: this app has no
    # other login mechanism, so a missing value must fail startup, not fall
    # back to an unauthenticated app.
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_admin_group: str
    public_base_url: str

    database_url: str = "sqlite:///data/webui.db"
    work_dir: str = "data/work"
    max_concurrent_runs: int = 2

    # True in production; set to false only for local HTTP-only dev, where
    # a `secure` cookie would never be sent back by the browser at all.
    cookie_secure: bool = True


settings = Settings()
