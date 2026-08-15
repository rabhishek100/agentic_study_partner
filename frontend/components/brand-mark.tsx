import Image from "next/image";

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
        "grid shrink-0 place-items-center",
        size === "sm" ? "size-8" : "size-14",
        className,
      )}
    >
      <Image
        src="/brand/mugensei-mark.png"
        alt=""
        width={1254}
        height={1254}
        className="size-full object-contain"
        priority
      />
    </span>
  );
}

/** Full product signature for spacious brand moments such as welcome and auth. */
export function BrandLockup({ className }: { className?: string }) {
  return (
    <span
      role="img"
      aria-label="Mugensei"
      className={cn("block w-48", className)}
    >
      <Image
        src="/brand/mugensei-lockup-light.png"
        alt=""
        width={1536}
        height={1024}
        className="h-auto w-full object-contain dark:hidden"
        priority
      />
      <Image
        src="/brand/mugensei-lockup.png"
        alt=""
        width={1536}
        height={1024}
        className="hidden h-auto w-full object-contain dark:block"
        priority
      />
    </span>
  );
}
