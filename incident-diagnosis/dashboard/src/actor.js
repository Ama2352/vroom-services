export const ACTORS = ['Alice', 'Bob', 'Charlie']

const STORAGE_KEY = 'incident-dashboard-actor'

export function getActor() {
  return localStorage.getItem(STORAGE_KEY) || ACTORS[0]
}

export function setActor(name) {
  localStorage.setItem(STORAGE_KEY, name)
}
