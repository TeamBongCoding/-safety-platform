export function formatKoreanDateTime(value) {
  if (!value) return '-'
  const timestamp = !value.endsWith('Z') && !value.includes('+') ? `${value}Z` : value
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(timestamp))
}
