interface TabsProps<T extends string> {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}

export function Tabs<T extends string>({ value, options, onChange }: TabsProps<T>) {
  return (
    <div className="mb-4 inline-flex gap-1 rounded-lg border border-border bg-white p-1">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={
            opt.value === value
              ? 'rounded-md bg-accent-soft px-3 py-1.5 text-sm font-semibold text-accent'
              : 'rounded-md px-3 py-1.5 text-sm font-medium text-ink-soft hover:bg-slate-50'
          }
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
