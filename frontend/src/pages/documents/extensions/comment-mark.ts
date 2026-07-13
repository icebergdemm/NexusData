import { Mark, mergeAttributes } from "@tiptap/core"

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    comment: {
      setComment: (commentId: string) => ReturnType
      unsetComment: (commentId: string) => ReturnType
    }
  }
}

/** Highlights the text a comment is anchored to. Removed once its comment is resolved/deleted. */
export const CommentMark = Mark.create({
  name: "comment",

  addOptions() {
    return { HTMLAttributes: {} }
  },

  addAttributes() {
    return {
      commentId: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-comment-id"),
        renderHTML: (attributes) => (attributes.commentId ? { "data-comment-id": attributes.commentId } : {}),
      },
    }
  },

  parseHTML() {
    return [{ tag: "span[data-comment-id]" }]
  },

  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, { class: "doc-comment-mark" }), 0]
  },

  addCommands() {
    return {
      setComment:
        (commentId: string) =>
        ({ commands }) =>
          commands.setMark(this.name, { commentId }),
      unsetComment:
        (commentId: string) =>
        ({ tr, state, dispatch }) => {
          const { doc } = state
          let found = false
          doc.descendants((node, pos) => {
            node.marks.forEach((mark) => {
              if (mark.type.name === "comment" && mark.attrs.commentId === commentId) {
                tr.removeMark(pos, pos + node.nodeSize, mark.type)
                found = true
              }
            })
          })
          if (found && dispatch) dispatch(tr)
          return found
        },
    }
  },
})
