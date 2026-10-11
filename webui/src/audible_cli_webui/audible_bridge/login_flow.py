import httpx
from audible.localization import Locale
from audible.login import build_oauth_url, create_code_verifier, extract_code_from_url
from audible.register import register


class AudibleConnectError(Exception):
    """A user-facing failure in the connect-Audible-account flow."""


def start_login(marketplace: str, with_username: bool) -> tuple[str, dict]:
    """Build the Amazon sign-in URL and the state needed to finish the login later.

    Splits what `audible.auth.Authenticator.from_login_external` does as a
    single blocking call into two steps, since in a web app "generate the
    URL" and "the user pastes back the result" are two separate HTTP
    requests, potentially minutes apart.
    """
    locale = Locale(country_code=marketplace)
    code_verifier = create_code_verifier()
    oauth_url, serial = build_oauth_url(
        country_code=locale.country_code,
        domain=locale.domain,
        market_place_id=locale.market_place_id,
        code_verifier=code_verifier,
        with_username=with_username,
    )

    pending_state = {
        "code_verifier": code_verifier.decode("ascii"),
        "serial": serial,
        "domain": locale.domain,
        "marketplace": marketplace,
        "with_username": with_username,
    }
    return oauth_url, pending_state


def complete_login(pending_state: dict, pasted_url: str) -> dict:
    """Finish the login using the URL the user pasted back.

    Returns a dict shaped exactly like `Authenticator.to_dict()` (minus
    `activation_bytes`, which is never fetched — this app's pipeline is
    AAXC-only and never needs it), ready for audible_bridge.auth_store to
    encrypt and persist.
    """
    try:
        authorization_code = extract_code_from_url(httpx.URL(pasted_url))
    except (KeyError, IndexError) as exc:
        raise AudibleConnectError(
            "That URL doesn't contain a login result. Make sure you copied the "
            "full address bar contents of the page you landed on after signing in."
        ) from exc

    try:
        register_device = register(
            authorization_code=authorization_code,
            code_verifier=pending_state["code_verifier"].encode("ascii"),
            domain=pending_state["domain"],
            serial=pending_state["serial"],
            with_username=pending_state["with_username"],
        )
    except Exception as exc:
        raise AudibleConnectError(
            "Amazon rejected the login attempt. The link may have expired — "
            "try generating a new one."
        ) from exc

    return {
        **register_device,
        "with_username": pending_state["with_username"],
        "locale_code": pending_state["marketplace"],
        "activation_bytes": None,
    }
