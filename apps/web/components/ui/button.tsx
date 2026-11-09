// shadcn/ui Button (new-york) with the Peil codemod (COMPONENTEN §1): every size ≥ 44 px, two-colour focus ring,
// destructive-foreground on destructive, disabled without opacity (dashed border + disabled-foreground), text ≥ 14 px.
// The hook class `btn` stays on the element (tests and existing selectors).
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  [
    "btn focus-ring inline-flex min-h-touch min-w-touch max-w-full items-center justify-center gap-2 rounded-lg border-2 px-4 py-1.5",
    "text-base font-semibold leading-snug whitespace-normal text-center no-underline transition-colors",
    "cursor-pointer [&_svg]:pointer-events-none [&_svg]:size-5 [&_svg]:shrink-0",
    "disabled:cursor-not-allowed disabled:border-dashed disabled:border-border-strong disabled:bg-muted disabled:text-disabled-foreground",
    "aria-disabled:cursor-not-allowed aria-disabled:border-dashed aria-disabled:border-border-strong aria-disabled:bg-muted aria-disabled:text-disabled-foreground",
  ].join(" "),
  {
    variants: {
      variant: {
        primair: "border-primary bg-primary text-primary-foreground hover:border-primary-hover hover:bg-primary-hover",
        secundair: "border-border-strong bg-card text-foreground hover:bg-accent",
        stil: "border-transparent bg-transparent text-foreground hover:bg-accent",
        gevaar: "border-destructive bg-card text-destructive hover:bg-accent",
        "gevaar-vol": "border-destructive bg-destructive text-destructive-foreground hover:border-destructive-hover hover:bg-destructive-hover",
        link: "border-transparent bg-transparent px-1 text-link underline underline-offset-[3px] hover:decoration-2",
      },
      size: {
        standaard: "",
        klein: "px-3 text-sm",
        icoon: "size-touch p-0",
      },
    },
    defaultVariants: { variant: "secundair", size: "standaard" },
  },
);

export type ButtonVariantProps = VariantProps<typeof buttonVariants>;

export function UiButton({ className, variant, size, asChild = false, type, ...props }: ComponentProps<"button"> & ButtonVariantProps & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "button";
  return <Comp data-slot="button" type={asChild ? undefined : (type ?? "button")} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
