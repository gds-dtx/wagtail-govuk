// GOV.UK cookie banner.
//
// Consent is held in a first-party cookie so the banner can decide whether to
// show itself without a request to the server, which keeps pages cacheable.
// Other scripts can check consent with window.govukCookieConsent().

const CONSENT_COOKIE_NAME = 'cookie_policy'
const CONSENT_COOKIE_MAX_AGE_DAYS = 365

function readConsent() {
  const match = document.cookie.match(
    new RegExp('(?:^|; )' + CONSENT_COOKIE_NAME + '=([^;]*)')
  )
  if (!match) {
    return null
  }

  try {
    const policy = JSON.parse(decodeURIComponent(match[1]))
    return typeof policy.analytics === 'boolean' ? policy : null
  } catch (error) {
    // A cookie we cannot read is the same as no choice having been made.
    return null
  }
}

function writeConsent(analyticsAccepted) {
  const value = encodeURIComponent(JSON.stringify({ analytics: analyticsAccepted }))
  const maxAge = CONSENT_COOKIE_MAX_AGE_DAYS * 24 * 60 * 60
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie =
    CONSENT_COOKIE_NAME + '=' + value + '; Path=/; Max-Age=' + maxAge + '; SameSite=Lax' + secure
}

function initCookieBanner() {
  const banner = document.querySelector('.js-cookie-banner')
  if (!banner) {
    return
  }

  const question = banner.querySelector('.js-cookie-banner-question')
  const confirmation = banner.querySelector('.js-cookie-banner-confirmation')
  const confirmationText = banner.querySelector('.js-cookie-banner-confirmation-text')
  const cookiesPageUrl = banner.getAttribute('data-cookies-page-url') || '/cookies'

  if (readConsent()) {
    return
  }
  banner.hidden = false

  function confirmChoice(accepted) {
    writeConsent(accepted)
    question.hidden = true
    confirmation.hidden = false
    const settingsLink = document.createElement('a')
    settingsLink.className = 'govuk-link'
    settingsLink.href = cookiesPageUrl
    settingsLink.textContent = 'change your cookie settings'

    confirmationText.textContent =
      'You have ' + (accepted ? 'accepted' : 'rejected') + ' analytics cookies. You can '
    confirmationText.appendChild(settingsLink)
    confirmationText.appendChild(document.createTextNode(' at any time.'))
  }

  banner
    .querySelector('.js-cookie-banner-accept')
    .addEventListener('click', () => confirmChoice(true))
  banner
    .querySelector('.js-cookie-banner-reject')
    .addEventListener('click', () => confirmChoice(false))
  banner.querySelector('.js-cookie-banner-hide').addEventListener('click', () => {
    banner.hidden = true
  })
}

window.govukCookieConsent = function govukCookieConsent() {
  const policy = readConsent()
  return policy ? policy.analytics : null
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCookieBanner)
} else {
  initCookieBanner()
}
