// Mirrors audible.localization.LOCALE_TEMPLATES / AVAILABLE_MARKETPLACES in
// the audible-cli repo (src/audible_cli/constants.py).
export const MARKETPLACES: { code: string; label: string }[] = [
  { code: 'us', label: 'United States (.com)' },
  { code: 'uk', label: 'United Kingdom' },
  { code: 'de', label: 'Germany' },
  { code: 'fr', label: 'France' },
  { code: 'ca', label: 'Canada' },
  { code: 'it', label: 'Italy' },
  { code: 'au', label: 'Australia' },
  { code: 'in', label: 'India' },
  { code: 'jp', label: 'Japan' },
  { code: 'es', label: 'Spain' },
  { code: 'br', label: 'Brazil' },
]

// build_oauth_url() in the installed `audible` package only allows
// with_username=True when the resolved domain is de/com/co.uk.
export const WITH_USERNAME_MARKETPLACES = new Set(['us', 'uk', 'de'])
