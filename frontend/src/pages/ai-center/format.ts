const sourceLabels: Record<string, string> = {
  stats: "игровая статистика",
  session: "сессия",
  punishment: "наказание",
  note: "заметка",
  label: "метка",
  trap: "ловушка",
  log: "лог",
  profile: "профиль",
}

export function formatDossierRow(row: Record<string, unknown>) {
  const text = typeof row.text === "string" ? row.text : Object.values(row).map(String).join(" · ")
  const metadata = [
    typeof row.source === "string" ? `Источник: ${sourceLabels[row.source] || row.source}` : "",
    typeof row.reason === "string" ? `Почему: ${row.reason}` : "",
  ].filter(Boolean).join(" · ")
  return { text, metadata }
}
