const COOKIE_NAME = "nd_browser_fingerprint"
const COOKIE_AGE = 60 * 60 * 24 * 14
const secure = location.protocol === "https:" ? "; Secure" : ""

function clearFingerprintCookie() {
  document.cookie = `${COOKIE_NAME}=; Max-Age=0; Path=/; SameSite=Lax${secure}`
}

function setFingerprintCookie(visitorId) {
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(visitorId)}; Max-Age=${COOKIE_AGE}; Path=/; SameSite=Lax${secure}`
}

clearFingerprintCookie()
// ponytail: OSS IDs are client-spoofable; use server-verified Fingerprint Pro if spoof resistance is required.
window.ndBrowserFingerprintReady = Promise.resolve()
  .then(() => window.FingerprintJS ? window.FingerprintJS.load() : null)
  .then(agent => agent ? agent.get() : null)
  .then(result => {
    const visitorId = result?.visitorId
    if (typeof visitorId !== "string" || !visitorId || visitorId.length > 256) return null
    setFingerprintCookie(visitorId)
    return visitorId
  })
  .catch(() => null)

const discordLink = document.querySelector(".discord-btn")
discordLink?.addEventListener("click", async event => {
  event.preventDefault()
  await window.ndBrowserFingerprintReady
  location.assign(discordLink.href)
})

const loginForm = document.querySelector('form[action="/slogin"]')
loginForm?.addEventListener("submit", async event => {
  event.preventDefault()
  await window.ndBrowserFingerprintReady
  loginForm.submit()
})
