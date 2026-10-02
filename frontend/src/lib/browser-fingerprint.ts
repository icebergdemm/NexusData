const COOKIE_NAME = "nd_browser_fingerprint"

declare global {
  interface Window {
    ndBrowserFingerprintReady?: Promise<string | null>
  }
}

export async function readBrowserFingerprintCookie(): Promise<string | null> {
  if (typeof document === "undefined") return null
  try {
    if (typeof window !== "undefined") await window.ndBrowserFingerprintReady?.catch(() => null)
    const cookie = document.cookie.split(";").map(value => value.trim())
      .find(value => value.startsWith(`${COOKIE_NAME}=`))
    return cookie ? decodeURIComponent(cookie.slice(COOKIE_NAME.length + 1)) || null : null
  } catch {
    return null
  }
}
