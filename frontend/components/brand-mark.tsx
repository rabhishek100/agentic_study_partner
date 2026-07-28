import { cn } from "@/lib/utils";

/** The product mark. Decorative wherever the product name is already present. */
export function BrandMark({
  className,
  size = "md",
}: {
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center rounded-xl bg-primary font-heading font-semibold text-primary-foreground",
        size === "sm" ? "size-7 text-xs" : "size-11 text-sm",
        className,
      )}
    >
      ASP
    </span>
  );
}
