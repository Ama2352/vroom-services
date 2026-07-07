import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-canvas font-sans">
      <Sidebar />
      <main className="mx-auto w-full max-w-[1400px] flex-1 px-8 py-6 max-[720px]:p-4">{children}</main>
    </div>
  )
}
