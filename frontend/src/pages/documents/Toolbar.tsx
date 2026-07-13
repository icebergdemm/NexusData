import { useState } from "react"
import type { Editor } from "@tiptap/react"
import {
  Bold, Italic, Underline, Strikethrough, Code, Undo2, Redo2,
  List, ListOrdered, ListTodo, Quote, Code2, Link2, Image as ImageIcon,
  Table2, Minus, Superscript as SuperscriptIcon, Subscript as SubscriptIcon,
  AlignLeft, AlignCenter, AlignRight, AlignJustify, Palette, Highlighter,
  RemoveFormatting, MessageSquarePlus, ChevronDown, Rows3, Columns3, Trash,
} from "lucide-react"
import { Toggle } from "@/components/ui/toggle"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"
import { canCommentDocument, canEditDocument, type AccessLevel } from "@/pages/documents/api"

const TEXT_COLORS = ["#fafafa", "#f87171", "#fb923c", "#facc15", "#4ade80", "#4a8fe8", "#c084fc", "#f472b6"]
const HIGHLIGHT_COLORS = ["#fde68a", "#bbf7d0", "#bfdbfe", "#fbcfe8", "#e5e7eb"]
const FONT_SIZES = ["12px", "14px", "16px", "18px", "24px", "32px"]
const FONT_FAMILIES = [
  { label: "По умолчанию", value: "" },
  { label: "Serif", value: "Georgia, serif" },
  { label: "Моноширинный", value: "'JetBrains Mono', monospace" },
]

function ToolbarToggle({ pressed, onClick, disabled, title, children }: {
  pressed: boolean; onClick: () => void; disabled?: boolean; title: string; children: React.ReactNode
}) {
  return (
    <Toggle size="sm" pressed={pressed} onPressedChange={onClick} disabled={disabled} title={title} className="h-8 min-w-8 px-1.5">
      {children}
    </Toggle>
  )
}

function ToolbarButton({ onClick, disabled, title, children }: {
  onClick: () => void; disabled?: boolean; title: string; children: React.ReactNode
}) {
  return (
    <Button type="button" variant="ghost" size="icon-sm" onClick={onClick} disabled={disabled} title={title}>
      {children}
    </Button>
  )
}

export function EditorToolbar({ editor, accessLevel, onAddComment, onInsertImage }: {
  editor: Editor | null
  accessLevel: AccessLevel | null | undefined
  onAddComment: () => void
  onInsertImage: () => void
}) {
  const [linkOpen, setLinkOpen] = useState(false)
  const [linkUrl, setLinkUrl] = useState("")

  if (!editor) return null

  const editable = canEditDocument(accessLevel)
  const commentable = canCommentDocument(accessLevel)
  const headingLevel = [1, 2, 3].find((l) => editor.isActive("heading", { level: l })) ?? 0

  const applyLink = () => {
    if (!linkUrl.trim()) {
      editor.chain().focus().extendMarkRange("link").unsetLink().run()
    } else {
      let href = linkUrl.trim()
      if (!/^https?:\/\//i.test(href) && !href.startsWith("/") && !href.startsWith("#")) href = `https://${href}`
      editor.chain().focus().extendMarkRange("link").setLink({ href }).run()
    }
    setLinkOpen(false)
    setLinkUrl("")
  }

  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b bg-muted/30 p-1.5">
      <ToolbarButton title="Отменить" onClick={() => editor.chain().focus().undo().run()} disabled={!editable || !editor.can().undo()}>
        <Undo2 />
      </ToolbarButton>
      <ToolbarButton title="Повторить" onClick={() => editor.chain().focus().redo().run()} disabled={!editable || !editor.can().redo()}>
        <Redo2 />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="sm" disabled={!editable} className="gap-1 px-2">
            {headingLevel === 0 ? "Текст" : `Заголовок ${headingLevel}`}
            <ChevronDown className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onClick={() => editor.chain().focus().setParagraph().run()}>Обычный текст</DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>Заголовок 1</DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>Заголовок 2</DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>Заголовок 3</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="sm" disabled={!editable} className="gap-1 px-2">
            Шрифт <ChevronDown className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {FONT_FAMILIES.map((f) => (
            <DropdownMenuItem key={f.label} onClick={() => (f.value ? editor.chain().focus().setFontFamily(f.value).run() : editor.chain().focus().unsetFontFamily().run())}>
              {f.label}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          {FONT_SIZES.map((s) => (
            <DropdownMenuItem key={s} onClick={() => editor.chain().focus().setFontSize(s).run()}>{s}</DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <ToolbarToggle title="Жирный" pressed={editor.isActive("bold")} onClick={() => editor.chain().focus().toggleBold().run()} disabled={!editable}>
        <Bold className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Курсив" pressed={editor.isActive("italic")} onClick={() => editor.chain().focus().toggleItalic().run()} disabled={!editable}>
        <Italic className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Подчёркнутый" pressed={editor.isActive("underline")} onClick={() => editor.chain().focus().toggleUnderline().run()} disabled={!editable}>
        <Underline className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Зачёркнутый" pressed={editor.isActive("strike")} onClick={() => editor.chain().focus().toggleStrike().run()} disabled={!editable}>
        <Strikethrough className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Код" pressed={editor.isActive("code")} onClick={() => editor.chain().focus().toggleCode().run()} disabled={!editable}>
        <Code className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Верхний индекс" pressed={editor.isActive("superscript")} onClick={() => editor.chain().focus().toggleSuperscript().run()} disabled={!editable}>
        <SuperscriptIcon className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Нижний индекс" pressed={editor.isActive("subscript")} onClick={() => editor.chain().focus().toggleSubscript().run()} disabled={!editable}>
        <SubscriptIcon className="size-4" />
      </ToolbarToggle>

      <Popover>
        <PopoverTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" disabled={!editable} title="Цвет текста">
            <Palette className="size-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-2">
          <div className="flex gap-1">
            {TEXT_COLORS.map((c) => (
              <button key={c} className="size-6 rounded-full border" style={{ backgroundColor: c }} onClick={() => editor.chain().focus().setColor(c).run()} />
            ))}
            <button className="flex size-6 items-center justify-center rounded-full border text-[10px]" onClick={() => editor.chain().focus().unsetColor().run()}>✕</button>
          </div>
        </PopoverContent>
      </Popover>

      <Popover>
        <PopoverTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" disabled={!editable} title="Цвет выделения">
            <Highlighter className="size-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-2">
          <div className="flex gap-1">
            {HIGHLIGHT_COLORS.map((c) => (
              <button key={c} className="size-6 rounded-full border" style={{ backgroundColor: c }} onClick={() => editor.chain().focus().toggleHighlight({ color: c }).run()} />
            ))}
            <button className="flex size-6 items-center justify-center rounded-full border text-[10px]" onClick={() => editor.chain().focus().unsetHighlight().run()}>✕</button>
          </div>
        </PopoverContent>
      </Popover>

      <ToolbarButton title="Очистить форматирование" onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()} disabled={!editable}>
        <RemoveFormatting />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <ToolbarToggle title="По левому краю" pressed={editor.isActive({ textAlign: "left" })} onClick={() => editor.chain().focus().setTextAlign("left").run()} disabled={!editable}>
        <AlignLeft className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="По центру" pressed={editor.isActive({ textAlign: "center" })} onClick={() => editor.chain().focus().setTextAlign("center").run()} disabled={!editable}>
        <AlignCenter className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="По правому краю" pressed={editor.isActive({ textAlign: "right" })} onClick={() => editor.chain().focus().setTextAlign("right").run()} disabled={!editable}>
        <AlignRight className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="По ширине" pressed={editor.isActive({ textAlign: "justify" })} onClick={() => editor.chain().focus().setTextAlign("justify").run()} disabled={!editable}>
        <AlignJustify className="size-4" />
      </ToolbarToggle>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <ToolbarToggle title="Маркированный список" pressed={editor.isActive("bulletList")} onClick={() => editor.chain().focus().toggleBulletList().run()} disabled={!editable}>
        <List className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Нумерованный список" pressed={editor.isActive("orderedList")} onClick={() => editor.chain().focus().toggleOrderedList().run()} disabled={!editable}>
        <ListOrdered className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Список задач" pressed={editor.isActive("taskList")} onClick={() => editor.chain().focus().toggleTaskList().run()} disabled={!editable}>
        <ListTodo className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Цитата" pressed={editor.isActive("blockquote")} onClick={() => editor.chain().focus().toggleBlockquote().run()} disabled={!editable}>
        <Quote className="size-4" />
      </ToolbarToggle>
      <ToolbarToggle title="Блок кода" pressed={editor.isActive("codeBlock")} onClick={() => editor.chain().focus().toggleCodeBlock().run()} disabled={!editable}>
        <Code2 className="size-4" />
      </ToolbarToggle>
      <ToolbarButton title="Горизонтальная линия" onClick={() => editor.chain().focus().setHorizontalRule().run()} disabled={!editable}>
        <Minus />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-6" />

      <Popover open={linkOpen} onOpenChange={(o) => { setLinkOpen(o); if (o) setLinkUrl(editor.getAttributes("link").href ?? "") }}>
        <PopoverTrigger asChild>
          <Toggle size="sm" pressed={editor.isActive("link")} disabled={!editable} title="Ссылка" className="h-8 min-w-8 px-1.5">
            <Link2 className="size-4" />
          </Toggle>
        </PopoverTrigger>
        <PopoverContent className="flex w-72 gap-2 p-2">
          <Input autoFocus placeholder="https://…" value={linkUrl} onChange={(e) => setLinkUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && applyLink()} />
          <Button size="sm" onClick={applyLink}>OK</Button>
        </PopoverContent>
      </Popover>

      <ToolbarButton title="Изображение" onClick={onInsertImage} disabled={!editable}>
        <ImageIcon />
      </ToolbarButton>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" disabled={!editable} title="Таблица">
            <Table2 className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>
            <Table2 className="mr-1 size-4" /> Вставить таблицу 3×3
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => editor.chain().focus().addRowAfter().run()}><Rows3 className="mr-1 size-4" /> Добавить строку</DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().addColumnAfter().run()}><Columns3 className="mr-1 size-4" /> Добавить столбец</DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().deleteRow().run()}>Удалить строку</DropdownMenuItem>
          <DropdownMenuItem onClick={() => editor.chain().focus().deleteColumn().run()}>Удалить столбец</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => editor.chain().focus().deleteTable().run()}><Trash className="mr-1 size-4" /> Удалить таблицу</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <div className="flex-1" />

      <ToolbarButton title="Добавить комментарий к выделению" onClick={onAddComment} disabled={!commentable || editor.state.selection.empty}>
        <MessageSquarePlus />
      </ToolbarButton>
    </div>
  )
}
