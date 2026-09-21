/* Bring-your-own-key store.
 * Keys live in this browser only. By default they are held in sessionStorage (gone when the tab closes);
 * "Remember on this device" additionally writes them to localStorage. They are sent to this app's server as
 * request headers and forwarded to the provider for that one request; the server never stores or logs them. */
const STORE = "jev-keys";
const NAMES = ["jev", "openrouter"];
let mem = {};

const clean = (keys) => Object.fromEntries(NAMES.map((n) => [n, String(keys?.[n] ?? "").trim()]).filter(([, v]) => v));

export function loadKeys() {
  try { const raw = sessionStorage.getItem(STORE) ?? localStorage.getItem(STORE); mem = raw ? clean(JSON.parse(raw)) : {}; } catch { mem = {}; }
  return mem;
}
export const currentKeys = () => mem;
export const hasAnyKey = () => NAMES.some((n) => mem[n]);
export const isRemembered = () => { try { return localStorage.getItem(STORE) !== null; } catch { return false; } };

export function saveKeys(keys, remember) {
  mem = clean(keys);
  const json = JSON.stringify(mem);
  try {
    sessionStorage.setItem(STORE, json);
    remember ? localStorage.setItem(STORE, json) : localStorage.removeItem(STORE);
  } catch { /* storage blocked: keys stay in memory for this page only */ }
}
export function clearKeys() {
  mem = {};
  try { sessionStorage.removeItem(STORE); localStorage.removeItem(STORE); } catch { /* ignore */ }
}

/** Request headers carrying the visitor's keys (only the ones they set). */
export function keyHeaders() {
  const h = {};
  if (mem.jev) h["x-jev-key"] = mem.jev;
  if (mem.openrouter) h["x-openrouter-key"] = mem.openrouter;
  return h;
}
