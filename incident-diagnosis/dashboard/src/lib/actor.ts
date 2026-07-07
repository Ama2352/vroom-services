export const ACTORS = ['Alice', 'Bob', 'Charlie']

const STORAGE_KEY = 'incident-dashboard-actor'

export function getActor(): string {
  return localStorage.getItem(STORAGE_KEY) || ACTORS[0]
}

export function setActor(name: string): void {
  localStorage.setItem(STORAGE_KEY, name)
}
