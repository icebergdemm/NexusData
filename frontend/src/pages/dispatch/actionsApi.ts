import { api } from "@/lib/api"

/** Queues a ServerAction the game server's existing poll loop already
 * consumes (GET /api/v1/actions — see routes/moderation.py) the same way it
 * does for kick/ban/dispatch. This site only ever queues a correctly
 * structured action (and, for cargo, deducts warehouse stock) — actual
 * execution in-game is the game server/plugin's job. */
export const dispatchActionsApi = {
  shuttle: (serverName: string, command: "send" | "recall") =>
    api.post<{ status: string; action_id: number }>("/api/v1/admin/dispatch/actions/shuttle", {
      server_name: serverName, command,
    }),
  torpedo: (serverName: string, x: number, y: number) =>
    api.post<{ status: string; action_id: number }>("/api/v1/admin/dispatch/actions/torpedo", {
      server_name: serverName, x, y,
    }),
  cargo: (serverName: string, items: { identifier: string; quantity: number }[]) =>
    api.post<{ status: string; action_id: number }>("/api/v1/admin/dispatch/actions/cargo", {
      server_name: serverName, items,
    }),
}
