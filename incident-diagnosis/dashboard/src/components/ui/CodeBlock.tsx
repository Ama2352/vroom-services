import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { cn } from '../../lib/cn'

interface CodeBlockProps {
  children: string
  className?: string
}

export function CodeBlock({ children, className }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(children || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className={cn('relative rounded-lg bg-ink px-3 py-2.5 font-mono text-[12px] text-slate-200', className)}>
      <button
        onClick={copy}
        className="absolute right-2 top-2 flex items-center gap-1 rounded-md border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:border-accent"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? 'Copied' : 'Copy'}
      </button>
      <div className="pr-16">{children}</div>
    </div>
  )
}
