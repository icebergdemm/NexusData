import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, ApiError } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"

interface LookupResult {
  player_id: number
  name: string | null
}

/** Non-dismissable post-login onboarding: link a game profile (item 3) and/or
 * set a forced nickname (items 2 & 3) before the admin can use the rest of
 * the site. Mounted once inside RequireAuth so it applies on every route. */
export function OnboardingModal() {
  const { data: admin } = useCurrentAdmin()
  const queryClient = useQueryClient()

  const [wizardStep, setWizardStep] = useState<"steamid" | "confirm">("steamid")
  const [steamid, setSteamid] = useState("")
  const [steamidError, setSteamidError] = useState<string | null>(null)
  const [foundPlayer, setFoundPlayer] = useState<LookupResult | null>(null)
  const [justLinkedViaSteamid, setJustLinkedViaSteamid] = useState(false)
  const [nameValue, setNameValue] = useState("")
  const [nameError, setNameError] = useState<string | null>(null)

  const lookupMutation = useMutation({
    mutationFn: (sid: string) => api.get<LookupResult>(`/api/v1/me/player-lookup?steamid=${encodeURIComponent(sid)}`),
    onSuccess: (res) => {
      setFoundPlayer(res)
      setSteamidError(null)
      setWizardStep("confirm")
    },
    onError: (e: ApiError) => setSteamidError(e.message),
  })

  const linkMutation = useMutation({
    mutationFn: () => api.post("/api/v1/me/link-player", { player_id: foundPlayer?.player_id }),
    onSuccess: () => {
      setJustLinkedViaSteamid(true)
      queryClient.invalidateQueries({ queryKey: ["me"] })
    },
    onError: (e: ApiError) => setSteamidError(e.message),
  })

  const nameMutation = useMutation({
    mutationFn: () => api.post("/api/v1/me/forced-nickname", { name: nameValue.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] })
    },
    onError: (e: ApiError) => setNameError(e.message),
  })

  if (!admin) return null

  const step: "steamid" | "confirm" | "name" | null = admin.linked_player
    ? (admin.linked_player.forced_display_name ? null : "name")
    : wizardStep

  if (step === null) return null

  const preventDismiss = (e: Event) => e.preventDefault()

  return (
    <Dialog open onOpenChange={() => {}}>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={preventDismiss}
        onPointerDownOutside={preventDismiss}
        onInteractOutside={preventDismiss}
        className="sm:max-w-md"
      >
        {step === "steamid" && (
          <>
            <DialogHeader>
              <DialogTitle>Добро пожаловать</DialogTitle>
              <DialogDescription>
                Добро пожаловать, товарищ! Похоже ты тут в первый раз (ну или был тут слишком давно). Твоя первая задача - ввести свой игровой SteamID64. Если не знаешь как его получить, то спроси у своего главы отдела!
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Input
                autoFocus
                value={steamid}
                onChange={(e) => { setSteamid(e.target.value); setSteamidError(null) }}
                placeholder="76561198000000000"
                onKeyDown={(e) => e.key === "Enter" && steamid.trim() && lookupMutation.mutate(steamid.trim())}
              />
              {steamidError && <p className="text-sm text-destructive">{steamidError}</p>}
            </div>
            <DialogFooter>
              <Button disabled={!steamid.trim() || lookupMutation.isPending} onClick={() => lookupMutation.mutate(steamid.trim())}>
                Далее
              </Button>
            </DialogFooter>
          </>
        )}

        {step === "confirm" && (
          <>
            <DialogHeader>
              <DialogTitle>Что-то нашёл</DialogTitle>
              <DialogDescription>Вы случаем не {foundPlayer?.name ?? "—"}?</DialogDescription>
            </DialogHeader>
            {steamidError && <p className="text-sm text-destructive">{steamidError}</p>}
            <DialogFooter>
              <Button
                variant="outline"
                disabled={linkMutation.isPending}
                onClick={() => { setWizardStep("steamid"); setFoundPlayer(null); setSteamid(""); setSteamidError(null) }}
              >
                Нет, вы обознались
              </Button>
              <Button disabled={linkMutation.isPending} onClick={() => linkMutation.mutate()}>
                Да, это я
              </Button>
            </DialogFooter>
          </>
        )}

        {step === "name" && (
          <>
            <DialogHeader>
              <DialogTitle>Представьтесь</DialogTitle>
              <DialogDescription>
                {justLinkedViaSteamid
                  ? "Товарищ, я страдаю эпизодической амнезией, поэтому укажите ваши имя и фамилию / позывной. Указывать звание не нужно."
                  : "Здравия желаю, товарищ! Напомните ваши имя и фамилию / позывной. Указывать звание не нужно."}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Input
                autoFocus
                value={nameValue}
                onChange={(e) => { setNameValue(e.target.value); setNameError(null) }}
                placeholder="Имя Фамилия / позывной"
                onKeyDown={(e) => e.key === "Enter" && nameValue.trim() && nameMutation.mutate()}
              />
              {nameError && <p className="text-sm text-destructive">{nameError}</p>}
            </div>
            <DialogFooter>
              <Button disabled={!nameValue.trim() || nameMutation.isPending} onClick={() => nameMutation.mutate()}>
                Применить
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
