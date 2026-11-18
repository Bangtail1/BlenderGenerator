import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition-colors",
  {
    variants: {
      variant: {
        default: "border-primary/40 bg-primary/15 text-primary",
        secondary: "border-border bg-secondary text-secondary-foreground",
        success: "border-emerald-500/35 bg-emerald-500/15 text-emerald-300",
        warning: "border-amber-500/35 bg-amber-500/15 text-amber-300",
        destructive: "border-rose-500/40 bg-rose-500/15 text-rose-300",
        info: "border-sky-500/35 bg-sky-500/15 text-sky-300",
        outline: "border-border text-muted-foreground",
      },
    },
    defaultVariants: {
      variant: "secondary",
    },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
