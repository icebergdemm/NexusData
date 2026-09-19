export function securityDisplayName(
  discordDisplayName: string | null | undefined,
  nexusName: string | null | undefined,
  fallback: string,
): string {
  return discordDisplayName?.trim() || nexusName?.trim() || fallback
}

export function securitySecondaryName(
  discordDisplayName: string | null | undefined,
  nexusName: string | null | undefined,
): string | null {
  if (!nexusName?.trim() || nexusName.trim() === discordDisplayName?.trim()) return null
  return nexusName.trim()
}
