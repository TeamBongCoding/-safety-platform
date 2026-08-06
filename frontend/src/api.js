export const API_BASE = import.meta.env.VITE_API_BASE || ''

const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
export const WS_URL = import.meta.env.VITE_WS_BASE || `${wsProtocol}://${window.location.host}/ws`

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
