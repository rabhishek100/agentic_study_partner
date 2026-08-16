import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:bg-disabled-surface disabled:bg-disabled-surface disabled:text-disabled-foreground aria-invalid:border-destructive md:text-sm dark:bg-surface dark:disabled:bg-disabled-surface dark:aria-invalid:border-destructive",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
