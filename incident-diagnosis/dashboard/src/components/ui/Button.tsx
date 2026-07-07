import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

type Variant = 'primary' | 'secondary' | 'danger'

const VARIANT_CLASSES: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-strong',
  secondary: 'bg-white text-ink border border-border hover:border-accent',
  danger: 'bg-critical-soft text-critical hover:bg-critical/10',
}

export function buttonClasses(variant: Variant = 'primary', className?: string): string {
  return cn(
    'inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
    VARIANT_CLASSES[variant],
    className,
  )
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
}

export function Button({ variant = 'primary', className, ...props }: ButtonProps) {
  return <button className={buttonClasses(variant, className)} {...props} />
}
