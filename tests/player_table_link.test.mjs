import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const sourcePath = resolve(import.meta.dirname, "../frontend/src/pages/players/index.tsx")

test("players table uses a real profile link and no row middle-click workaround", () => {
  const source = readFileSync(sourcePath, "utf8")

  assert.match(source, /import \{ Link, useNavigate \} from "react-router-dom"/)
  assert.match(source, /<Link to=\{`\/players\/\$\{p\.player_id\}`\}>\s*<span>\{p\.player_last_time_name\}<\/span>\s*<\/Link>/)
  assert.match(source, /if \(e\.target instanceof Element && e\.target\.closest\("a"\)\) return/)
  assert.doesNotMatch(source, /onAuxClick=\{\(e\) => \{ if \(e\.button === 1\) window\.open\(/)
})
