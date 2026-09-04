import { useMemo, useState } from "react"
import { toast } from "sonner"
import {
  AlertTriangle,
  ArchiveRestore,
  Bot,
  Check,
  Clock3,
  Download,
  ExternalLink,
  File,
  FileArchive,
  FileImage,
  Forward,
  ImageOff,
  Link2,
  MessageSquareReply,
  ShieldCheck,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { ImageLightbox, type LightboxImage } from "@/components/ui/image-lightbox"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { formatBrowserDate } from "@/lib/format-date"
import { cn } from "@/lib/utils"
import type {
  TicketAttachment,
  TicketEmbed,
  TicketMentionProfile,
  TicketMessageRow,
  TicketParticipant,
  TicketReferenceSnapshot,
} from "../types"
import { PersonAvatar } from "./ticket-shared"
import { formatBytes, identityName, participantName } from "./ticket-utils"

const IMAGE_EXTENSIONS = /\.(avif|bmp|gif|jpe?g|png|webp)$/i
const VIDEO_EXTENSIONS = /\.(m4v|mov|mp4|webm)$/i
const AUDIO_EXTENSIONS = /\.(aac|flac|m4a|mp3|ogg|opus|wav)$/i

const MEDIA_TYPES_BY_EXTENSION: Record<string, string> = {
  aac: "audio/aac",
  flac: "audio/flac",
  m4a: "audio/mp4",
  mov: "video/quicktime",
  mp3: "audio/mpeg",
  mp4: "video/mp4",
  m4v: "video/mp4",
  ogg: "audio/ogg",
  opus: "audio/ogg",
  wav: "audio/wav",
  webm: "video/webm",
}

function downloadUrl(attachment: TicketAttachment): string {
  return attachment.download_url || attachment.url || ""
}

function previewUrl(attachment: TicketAttachment): string {
  return attachment.url || ""
}

function isImage(attachment: TicketAttachment): boolean {
  const contentType = attachment.content_type?.toLowerCase()
  // SVG stays a download: embedding active XML into the authenticated admin
  // origin is not worth the evidence-preview convenience.
  if (contentType === "image/svg+xml") return false
  return Boolean(contentType?.startsWith("image/")) || IMAGE_EXTENSIONS.test(attachment.filename)
}

function isVideo(attachment: TicketAttachment): boolean {
  return attachment.content_type?.startsWith("video/") || VIDEO_EXTENSIONS.test(attachment.filename)
}

function isAudio(attachment: TicketAttachment): boolean {
  return attachment.content_type?.startsWith("audio/") || AUDIO_EXTENSIONS.test(attachment.filename)
}

function mediaType(attachment: TicketAttachment, kind: "audio" | "video"): string | undefined {
  const declared = attachment.content_type?.split(";", 1)[0].trim().toLowerCase()
  if (declared?.startsWith(`${kind}/`)) return declared
  const extension = attachment.filename.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1]
  return extension ? MEDIA_TYPES_BY_EXTENSION[extension] : undefined
}

function AttachmentArchiveState({ attachment }: { attachment: TicketAttachment }) {
  const state = attachment.archive_status
  if (attachment.archived || state === "archived" || state === "complete") {
    return <span className="inline-flex items-center gap-1 text-[10px] text-success" title="Файл сохранён в архиве"><Check className="size-3" />Архив</span>
  }
  if (["failed", "missing", "too_large", "quota_exceeded"].includes(state || "")) {
    const reason = attachment.archive_error ? `: ${attachment.archive_error}` : ""
    return <span className="inline-flex items-center gap-1 text-[10px] text-destructive" title={`Не удалось сохранить файл${reason}`}><AlertTriangle className="size-3" />Не сохранён</span>
  }
  if (state === "pending") {
    return <span className="inline-flex items-center gap-1 text-[10px] text-warning" title="Файл сохраняется"><Clock3 className="size-3" />Сохраняется</span>
  }
  return <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground" title="Ссылка ведёт на исходный файл"><Link2 className="size-3" />Источник</span>
}

function MediaFailure({
  attachment,
  kind,
}: {
  attachment: TicketAttachment
  kind: "image" | "video" | "audio"
}) {
  const fileUrl = downloadUrl(attachment)
  const sourceOnly = !attachment.archived
  const labels = {
    image: "Предпросмотр изображения недоступен",
    video: "Видео не удалось воспроизвести",
    audio: "Аудио не удалось воспроизвести",
  }
  return (
    <div className="flex min-h-28 flex-col items-center justify-center gap-2 border border-warning/40 bg-muted/40 p-5 text-center">
      {kind === "image" ? <ImageOff className="size-6 text-muted-foreground" /> : <AlertTriangle className="size-6 text-warning" />}
      <div>
        <p className="text-xs font-medium text-foreground">{labels[kind]}</p>
        <p className="mt-1 max-w-lg truncate font-mono text-[10px] text-foreground">{attachment.filename || "Вложение"}</p>
        <p className="mt-1 max-w-lg text-[10px] text-muted-foreground">
          {sourceOnly
            ? "Исходная ссылка Discord могла истечь. Если канал ещё существует, архиватор повторит попытку."
            : "Файл сохранён. Возможен неподдерживаемый браузером формат или кодек."}
        </p>
      </div>
      {fileUrl ? (
        <div className="flex flex-wrap items-center justify-center gap-3 text-xs">
          <a href={fileUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
            <ExternalLink className="size-3.5" />Открыть файл
          </a>
          <a href={fileUrl} download={attachment.filename} className="inline-flex items-center gap-1 text-primary hover:underline">
            <Download className="size-3.5" />Скачать
          </a>
        </div>
      ) : null}
    </div>
  )
}

function ImageAttachmentPreview({
  attachment,
  image,
  onOpen,
  single,
}: {
  attachment: TicketAttachment
  image: LightboxImage
  onOpen: () => void
  single: boolean
}) {
  const [failed, setFailed] = useState(false)
  if (failed) return <MediaFailure attachment={attachment} kind="image" />
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "group relative min-h-28 overflow-hidden border bg-muted text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        single ? "max-h-[30rem]" : "aspect-[4/3]",
      )}
      aria-label={`Открыть изображение ${image.filename}`}
    >
      <img
        src={image.url}
        alt={image.filename}
        loading="lazy"
        onError={() => setFailed(true)}
        className="size-full object-contain transition-opacity group-hover:opacity-90"
      />
      <span className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-background/85 px-2 py-1 text-[10px] backdrop-blur-sm">
        <span className="truncate"><FileImage className="mr-1 inline size-3" />{image.filename}</span>
        <AttachmentArchiveState attachment={attachment} />
      </span>
    </button>
  )
}

function VideoAttachmentPreview({ attachment }: { attachment: TicketAttachment }) {
  const [failed, setFailed] = useState(false)
  const mediaUrl = previewUrl(attachment)
  if (failed) return <MediaFailure attachment={attachment} kind="video" />
  return (
    <video controls preload="metadata" className="max-h-[30rem] w-full bg-black" onError={() => setFailed(true)}>
      <source src={mediaUrl} type={mediaType(attachment, "video")} />
      Ваш браузер не поддерживает воспроизведение видео.
    </video>
  )
}

function AudioAttachmentPreview({ attachment }: { attachment: TicketAttachment }) {
  const [failed, setFailed] = useState(false)
  const mediaUrl = previewUrl(attachment)
  if (failed) return <MediaFailure attachment={attachment} kind="audio" />
  return (
    <audio controls preload="metadata" className="h-9 w-full" onError={() => setFailed(true)}>
      <source src={mediaUrl} type={mediaType(attachment, "audio")} />
      Ваш браузер не поддерживает аудио.
    </audio>
  )
}

export function AttachmentGallery({ attachments, compact = false }: { attachments: TicketAttachment[]; compact?: boolean }) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const imageAttachments = useMemo(() => attachments.filter((item) => isImage(item) && Boolean(previewUrl(item))), [attachments])
  const images = useMemo(
    () => imageAttachments.map((item) => ({ url: previewUrl(item), filename: item.filename } satisfies LightboxImage)),
    [imageAttachments],
  )
  const otherAttachments = useMemo(
    () => attachments.filter((item) => !imageAttachments.includes(item)),
    [attachments, imageAttachments],
  )

  if (!attachments.length) return null

  return (
    <>
      <div className="mt-3 flex flex-col gap-2">
        {images.length ? (
          <div className={cn("grid gap-1.5", images.length === 1 ? "grid-cols-1" : "grid-cols-2", compact && "max-w-md")}>
            {images.map((image, index) => {
              const sourceAttachment = imageAttachments[index]
              return (
                <ImageAttachmentPreview
                  key={`${image.url}-${index}`}
                  attachment={sourceAttachment}
                  image={image}
                  single={images.length === 1}
                  onOpen={() => setLightboxIndex(index)}
                />
              )
            })}
          </div>
        ) : null}

        {otherAttachments.map((attachment, index) => {
          const mediaUrl = previewUrl(attachment)
          const fileUrl = downloadUrl(attachment)
          if (!mediaUrl) {
            return (
              <div key={attachment.id ?? `${attachment.filename}-${index}`} className="flex items-center gap-3 border border-destructive/40 bg-destructive/10 p-3 text-destructive">
                <AlertTriangle className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate text-xs">{attachment.filename || "Вложение"}</span>
                <span className="text-[10px]">Файл недоступен</span>
              </div>
            )
          }
          if (isVideo(attachment)) {
            return (
              <figure key={attachment.id ?? `${mediaUrl}-${index}`} className="overflow-hidden border bg-muted/40">
                <VideoAttachmentPreview attachment={attachment} />
                <figcaption className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
                  <span className="min-w-0 truncate font-medium">{attachment.filename}</span>
                  <span className="flex shrink-0 items-center gap-2 text-muted-foreground">
                    {formatBytes(attachment.file_size)}
                    <AttachmentArchiveState attachment={attachment} />
                    <a href={fileUrl} download={attachment.filename} aria-label={`Скачать ${attachment.filename}`} className="hover:text-foreground"><Download className="size-4" /></a>
                  </span>
                </figcaption>
              </figure>
            )
          }
          if (isAudio(attachment)) {
            return (
              <div key={attachment.id ?? `${mediaUrl}-${index}`} className="border bg-muted/40 p-3">
                <div className="mb-2 flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate font-medium">{attachment.filename}</span>
                  <AttachmentArchiveState attachment={attachment} />
                </div>
                <AudioAttachmentPreview attachment={attachment} />
              </div>
            )
          }
          return (
            <a
              key={attachment.id ?? `${fileUrl}-${index}`}
              href={fileUrl}
              download={attachment.filename}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-3 border bg-muted/30 p-3 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="flex size-9 shrink-0 items-center justify-center border bg-background"><File className="size-4" /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium">{attachment.filename}</span>
                <span className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
                  {formatBytes(attachment.file_size)}
                  <AttachmentArchiveState attachment={attachment} />
                </span>
              </span>
              <Download className="size-4 shrink-0 text-muted-foreground" />
            </a>
          )
        })}
      </div>
      {lightboxIndex !== null ? (
        <ImageLightbox images={images} index={lightboxIndex} onClose={() => setLightboxIndex(null)} onIndexChange={setLightboxIndex} />
      ) : null}
    </>
  )
}

function MentionChip({ discordId, profile }: { discordId: string; profile?: TicketMentionProfile }) {
  const label = `@${identityName(profile?.platform_display_name, profile?.display_name || profile?.username, discordId)}`
  const discordAppUrl = `discord://-/users/${encodeURIComponent(discordId)}`
  const discordWebUrl = `https://discord.com/users/${encodeURIComponent(discordId)}`
  const chip = (
    <span
      className="mx-0.5 inline-flex max-w-full items-center gap-1 rounded-sm border border-primary/30 bg-primary/10 px-1.5 py-0.5 align-baseline font-medium leading-none text-primary transition-colors hover:border-primary/60 hover:bg-primary/15"
      title={profile?.player_id ? `Открыть профиль NexusData · Discord ${discordId}` : `Открыть профиль Discord · ${discordId}`}
    >
      <PersonAvatar name={profile?.display_name || discordId} url={profile?.avatar_url} size="sm" className="!size-4" />
      <span className="truncate">{label}</span>
      {profile?.is_staff ? <ShieldCheck className="size-3 shrink-0" aria-label="Команда" /> : null}
    </span>
  )

  if (profile?.player_id) {
    return <PlayerHoverLink playerId={profile.player_id} className="inline-flex max-w-full no-underline">{chip}</PlayerHoverLink>
  }
  return (
    <a
      href={discordAppUrl}
      className="inline-flex max-w-full no-underline"
      title={`Открыть профиль в приложении Discord · ${discordId}`}
      onClick={() => {
        toast.info("Открываем профиль в Discord", {
          description: "Если приложение не открылось, используйте веб-версию.",
          action: {
            label: "Веб-версия",
            onClick: () => window.open(discordWebUrl, "_blank", "noopener,noreferrer"),
          },
          duration: 6000,
        })
      }}
    >
      {chip}
    </a>
  )
}

function DiscordMessageLink({
  guildId,
  channelId,
  messageId,
}: {
  guildId: string
  channelId: string
  messageId: string
}) {
  const path = `${encodeURIComponent(guildId)}/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}`
  const discordAppUrl = `discord://-/channels/${path}`
  const discordWebUrl = `https://discord.com/channels/${path}`
  return (
    <a
      href={discordAppUrl}
      className="inline-flex items-center gap-1 font-sans text-[10px] text-primary hover:underline"
      title="Перейти к этому сообщению в Discord"
      aria-label={`Открыть сообщение ${messageId} в Discord`}
      onClick={() => {
        toast.info("Открываем сообщение в Discord", {
          description: "Если приложение не открылось, используйте веб-версию.",
          action: {
            label: "Веб-версия",
            onClick: () => window.open(discordWebUrl, "_blank", "noopener,noreferrer"),
          },
          duration: 6000,
        })
      }}
    >
      <ExternalLink className="size-3" />Discord
    </a>
  )
}

function RichText({
  content,
  className,
  mentionProfiles = {},
}: {
  content: string
  className?: string
  mentionProfiles?: Record<string, TicketMentionProfile>
}) {
  const parts = content.split(/(<@!?\d{5,25}>|https?:\/\/[^\s]+)/g)
  return (
    <div className={cn("whitespace-pre-wrap break-words", className)}>
      {parts.map((part, index) => {
        const mention = /^<@!?(\d{5,25})>$/.exec(part)
        if (mention) return <MentionChip key={`${mention[1]}-${index}`} discordId={mention[1]} profile={mentionProfiles[mention[1]]} />
        if (part.startsWith("http://") || part.startsWith("https://")) {
          return <a key={`${part}-${index}`} href={part} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">{part}</a>
        }
        return <span key={index}>{part}</span>
      })}
    </div>
  )
}

function EmbedList({ embeds }: { embeds: TicketEmbed[] }) {
  if (!embeds.length) return null
  return (
    <div className="mt-2 flex flex-col gap-2">
      {embeds.map((embed, index) => (
        <article key={`${embed.url ?? embed.title ?? "embed"}-${index}`} className="border-l-2 border-l-muted-foreground bg-muted/30 px-3 py-2 text-xs">
          {embed.author_name ? <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{embed.author_name}</p> : null}
          {embed.title ? (
            embed.url ? <a href={embed.url} target="_blank" rel="noreferrer" className="font-semibold underline-offset-2 hover:underline">{embed.title}</a> : <p className="font-semibold">{embed.title}</p>
          ) : null}
          {embed.description ? <RichText content={embed.description} className="mt-1 text-muted-foreground" /> : null}
          {embed.image_url ? <img src={embed.image_url} alt="" loading="lazy" className="mt-2 max-h-72 max-w-full border object-contain" /> : null}
          {embed.thumbnail_url && !embed.image_url ? <img src={embed.thumbnail_url} alt="" loading="lazy" className="mt-2 size-20 border object-cover" /> : null}
          {embed.footer_text ? <p className="mt-2 text-[10px] text-muted-foreground">{embed.footer_text}</p> : null}
        </article>
      ))}
    </div>
  )
}

function snapshotSourceLabel(snapshot: TicketReferenceSnapshot): string | null {
  if (!snapshot.source) return null
  if (typeof snapshot.source === "string") return snapshot.source
  return snapshot.source.channel_name ? `#${snapshot.source.channel_name}` : snapshot.source.message_id ? `Сообщение ${snapshot.source.message_id}` : null
}

function ReferenceSnapshot({ snapshot, mentionProfiles }: { snapshot: TicketReferenceSnapshot; mentionProfiles: Record<string, TicketMentionProfile> }) {
  const isForward = snapshot.kind === "forward"
  const sourceLabel = snapshotSourceLabel(snapshot)
  const jumpUrl = typeof snapshot.source === "object" && snapshot.source ? snapshot.source.jump_url : null
  const authorName = snapshot.author?.display_name || snapshot.author?.username || (isForward ? "Автор не сохранён" : "Исходное сообщение")
  return (
    <blockquote className="mb-3 border-l-2 border-l-primary bg-muted/40 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {isForward ? <Forward className="size-3.5" /> : <MessageSquareReply className="size-3.5" />}
        <span>{isForward ? "Пересланное сообщение" : "Ответ на сообщение"}</span>
        {sourceLabel ? <span>· {sourceLabel}</span> : null}
        {jumpUrl ? <a href={jumpUrl} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 normal-case hover:text-foreground"><ExternalLink className="size-3" />Источник</a> : null}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <PersonAvatar name={authorName} url={snapshot.author?.avatar_url} size="sm" />
        <span className="text-xs font-semibold">{authorName}</span>
        {snapshot.created_at ? <time dateTime={snapshot.created_at} className="ml-auto text-[10px] text-muted-foreground">{formatBrowserDate(snapshot.created_at)}</time> : null}
      </div>
      {snapshot.content ? <RichText content={snapshot.content} mentionProfiles={mentionProfiles} className="mt-2 text-xs text-muted-foreground" /> : null}
      <EmbedList embeds={snapshot.embeds ?? []} />
      <AttachmentGallery attachments={snapshot.attachments ?? []} compact />
    </blockquote>
  )
}

function dateGroupKey(value: string | null): string {
  if (!value) return "unknown"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 10)
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
}

function dateGroupLabel(value: string | null): string {
  if (!value) return "Дата неизвестна"
  return formatBrowserDate(value, { day: "numeric", month: "long", year: "numeric" })
}

function MessageDeliveryState({ status, error }: { status: TicketMessageRow["delivery_status"]; error?: string | null }) {
  if (!status || status === "received" || status === "sent") return null
  if (status === "failed") return <span className="inline-flex items-center gap-1 text-destructive" title={error || "Discord не подтвердил отправку"}><AlertTriangle className="size-3" />Не доставлено</span>
  return <span className="inline-flex items-center gap-1 text-warning"><Clock3 className="size-3" />Отправляется</span>
}

function findParticipant(message: TicketMessageRow, participants: TicketParticipant[]): TicketParticipant | undefined {
  return participants.find((participant) => participant.discord_id && participant.discord_id === message.author_discord_id)
}

function MessageAuthor({ message, participant }: { message: TicketMessageRow; participant?: TicketParticipant }) {
  const discordName = message.author_display_name || message.author_username
  const name = participantName(participant, discordName)
  const playerId = participant?.player?.player_id ?? participant?.player_id
  const nameNode = playerId ? (
    <PlayerHoverLink playerId={playerId} className="font-semibold text-foreground underline-offset-2 hover:underline">{name}</PlayerHoverLink>
  ) : <span className="font-semibold text-foreground">{name}</span>
  return (
    <div className="flex min-w-0 items-center gap-2">
      {nameNode}
      {participant?.role === "staff" || participant?.admin ? (
        <Badge variant="outline" className="px-1.5 py-0 text-[10px]"><ShieldCheck />Команда</Badge>
      ) : message.is_bot ? (
        <Badge variant="outline" className="px-1.5 py-0 text-[10px]"><Bot />Интеграция</Badge>
      ) : participant?.role === "creator" ? (
        <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">Автор</Badge>
      ) : null}
    </div>
  )
}

export function TicketConversation({
  messages,
  participants,
  mentionProfiles = {},
  discordGuildId,
  discordChannelId,
}: {
  messages: TicketMessageRow[]
  participants: TicketParticipant[]
  mentionProfiles?: Record<string, TicketMentionProfile>
  discordGuildId?: string | null
  discordChannelId?: string | null
}) {
  if (!messages.length) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
        <MessageSquareReply className="size-8" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium text-foreground">В тикете пока нет сообщений</p>
          <p className="mt-1 text-xs">История общения появится здесь.</p>
        </div>
      </div>
    )
  }

  let previousDay = ""
  return (
    <div role="log" aria-label="История сообщений" aria-live="polite" className="mx-auto w-full max-w-4xl">
      {messages.map((message) => {
        const day = dateGroupKey(message.created_at)
        const showDay = day !== previousDay
        previousDay = day
        const participant = findParticipant(message, participants)
        const authorName = participantName(participant, message.author_display_name || message.author_username)
        return (
          <div key={message.id}>
            {showDay ? (
              <div className="sticky top-0 z-10 flex items-center gap-3 bg-background/90 py-3 backdrop-blur-sm" role="separator">
                <span className="h-px flex-1 bg-border" />
                <time className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground" dateTime={message.created_at ?? undefined}>{dateGroupLabel(message.created_at)}</time>
                <span className="h-px flex-1 bg-border" />
              </div>
            ) : null}
            <article className={cn("group flex gap-3 border-b px-1 py-4", message.delivery_status === "failed" && "border-l-2 border-l-destructive pl-3")}>
              <PersonAvatar name={authorName} url={message.author_avatar_url || participant?.avatar_url || participant?.admin?.avatar_url || participant?.player?.avatar_url} size="lg" status={participant?.player?.online ? "in_game" : participant?.status} />
              <div className="min-w-0 flex-1">
                <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
                  <MessageAuthor message={message} participant={participant} />
                  <time className="font-mono text-[10px] text-muted-foreground" dateTime={message.created_at ?? undefined}>{formatBrowserDate(message.created_at)}</time>
                  {message.edited_at ? <span className="text-[10px] text-muted-foreground">изменено</span> : null}
                  <span className="ml-auto text-[10px]"><MessageDeliveryState status={message.delivery_status} error={message.delivery_error} /></span>
                </header>
                <div className="mt-2 text-sm leading-relaxed">
                  {message.reference_snapshot ? <ReferenceSnapshot snapshot={message.reference_snapshot} mentionProfiles={mentionProfiles} /> : null}
                  {message.content ? <RichText content={message.content} mentionProfiles={mentionProfiles} /> : null}
                  <EmbedList embeds={message.embeds ?? []} />
                  <AttachmentGallery attachments={message.attachments ?? []} />
                  {!message.content && !message.reference_snapshot && !(message.attachments?.length) && !(message.embeds?.length) ? (
                    <p className="italic text-muted-foreground">Содержимое сообщения недоступно.</p>
                  ) : null}
                </div>
                <div className="mt-2 flex items-center gap-2 font-mono text-[9px] text-muted-foreground opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
                  <span>MSG {message.discord_message_id || message.id}</span>
                  {discordGuildId && discordChannelId && message.discord_message_id ? (
                    <DiscordMessageLink
                      guildId={discordGuildId}
                      channelId={discordChannelId}
                      messageId={message.discord_message_id}
                    />
                  ) : null}
                  {message.attachments?.some((item) => item.archived) ? <span className="inline-flex items-center gap-1"><ArchiveRestore className="size-3" />Материалы в архиве</span> : null}
                </div>
              </div>
            </article>
          </div>
        )
      })}
    </div>
  )
}

export function ArchiveLegend() {
  return (
    <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
      <FileArchive className="size-3.5" />Вложения с отметкой «Архив» доступны независимо от Discord.
    </div>
  )
}
