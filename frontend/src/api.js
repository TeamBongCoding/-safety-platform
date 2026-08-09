function withoutTrailingSlash(value) {
  return value === '/' ? '' : value.replace(/\/+$/, '')
}

export function detectJupyterProxyBase(pathname) {
  const match = pathname.match(/^(.*\/proxy\/(?:absolute\/)?\d+)(?:\/|$)/)
  return match ? withoutTrailingSlash(match[1]) : ''
}

const configuredApiBase = import.meta.env.VITE_API_BASE
export const API_BASE = withoutTrailingSlash(
  configuredApiBase || detectJupyterProxyBase(window.location.pathname),
)

const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'

// In JupyterHub, bypass Vite's WS proxy by pointing WebSocket directly at
// the backend port — JupyterHub proxies that port independently and more reliably.
function detectJupyterWsBase(pathname, protocol, origin) {
  const match = pathname.match(/^(.*\/proxy\/(?:absolute\/)?)(\d+)(\/|$)/)
  if (!match) return null
  const backendPort = import.meta.env.VITE_BACKEND_PORT || '8000'
  const u = new URL(`${match[1]}${backendPort}/ws`, origin)
  u.protocol = `${protocol}:`
  return withoutTrailingSlash(u.toString())
}

export const WS_URL = import.meta.env.VITE_WS_BASE ||
  detectJupyterWsBase(window.location.pathname, wsProtocol, window.location.origin) ||
  withoutTrailingSlash((() => {
    const u = new URL(`${API_BASE || ''}/ws`, window.location.origin)
    u.protocol = `${wsProtocol}:`
    return u.toString()
  })())

export function cameraUploadUrl(cameraId) {
  const wsOrigin = WS_URL.replace(/\/ws\/?$/, '')
  return `${wsOrigin}/ws/camera-upload/${cameraId}`
}

export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  if (response.status === 204) return null
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const error = new Error(data?.detail || '요청을 처리하지 못했습니다.')
    error.status = response.status
    throw error
  }
  return data
}
