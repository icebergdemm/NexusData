/* Generates the deployable vanilla-creature reference used by Spawn Director. */
const fs = require("fs")
const path = require("path")

const content = process.argv[2]
const output = process.argv[3]
const creatures = []

function attrs(value) {
  return Object.fromEntries([...value.matchAll(/([\w]+)="([^"]*)"/g)].map((match) => [match[1].toLowerCase(), match[2]]))
}

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name)
    if (entry.isDirectory()) { walk(file); continue }
    if (!entry.name.endsWith(".xml") || entry.name.toLowerCase().includes("ragdoll")) continue
    try {
      const xml = fs.readFileSync(file, "utf8")
      const characterMatch = xml.match(/<Character\b([^>]*)>/i)
      if (!characterMatch) continue
      const character = attrs(characterMatch[1])
      if (character.humanoid === "True") continue
      const healthMatch = xml.match(/<health\b([^>]*)/i)
      if (!healthMatch) continue
      const aiMatch = xml.match(/<ai\b([^>]*)/i)
      const health = attrs(healthMatch[1])
      const ai = aiMatch ? attrs(aiMatch[1]) : {}
      const targets = [...xml.matchAll(/<target\b([^>]*)/gi)].map((match) => {
        const target = attrs(match[1])
        return { tag: target.tag || "?", state: target.state || "Observe", priority: target.priority || "0" }
      })
      creatures.push({
        id: character.speciesname || path.basename(entry.name, ".xml"), group: character.group || "",
        base_hp: Number(health.vitality || 0), needs_water: character.needswater === "True", needs_air: character.needsair === "True",
        combat_strength: Number(ai.combatstrength || 0), sight: Number(ai.sight || 0), hearing: Number(ai.hearing || 0),
        aggression_hurt: Number(ai.aggressionhurt || 0), aggression_greed: Number(ai.aggressiongreed || 0),
        flee_health_threshold: Number(ai.fleehealththreshold || 0), aggressive_boarding: ai.aggressiveboarding === "True", targets,
      })
    } catch { /* Skip malformed non-character XML. */ }
  }
}

walk(path.join(content, "Characters"))
const unique = [...new Map(creatures.map((creature) => [creature.id.toLowerCase(), creature])).values()]
unique.sort((a, b) => a.id.localeCompare(b.id))
fs.writeFileSync(output, JSON.stringify({ source: "Barotrauma vanilla Content/Characters", creatures: unique }, null, 2) + "\n")
console.log(`Wrote ${unique.length} creatures to ${output}`)
