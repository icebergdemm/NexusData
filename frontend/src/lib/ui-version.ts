export type UiVersion = "classic" | "v2"

const V2_PATH = /^\/v2(?:\/|$)/

export const UI_VERSION: UiVersion = V2_PATH.test(window.location.pathname) ? "v2" : "classic"
export const UI_BASENAME = UI_VERSION === "v2" ? "/v2" : "/admin"
export const IS_V2 = UI_VERSION === "v2"

export function installUiVersionAttribute() {
  document.documentElement.dataset.uiVersion = UI_VERSION
}

export function buildUiVersionHref(
  target: UiVersion,
  location: { pathname: string; search?: string; hash?: string },
) {
  const base = target === "v2" ? "/v2" : "/admin"
  const pathname = location.pathname === "/" ? "" : `/${location.pathname.replace(/^\/+/, "")}`
  return `${base}${pathname}${location.search ?? ""}${location.hash ?? ""}`
}
