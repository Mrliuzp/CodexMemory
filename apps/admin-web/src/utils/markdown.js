function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function inline(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
}

export function renderSafeMarkdown(source) {
  const lines = String(source || '').replaceAll('\r\n', '\n').split('\n')
  const output = []
  let inCode = false
  let inList = false
  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inList) { output.push('</ul>'); inList = false }
      output.push(inCode ? '</code></pre>' : '<pre><code>')
      inCode = !inCode
      continue
    }
    if (inCode) { output.push(`${escapeHtml(line)}\n`); continue }
    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      if (inList) { output.push('</ul>'); inList = false }
      const level = heading[1].length + 1
      output.push(`<h${level}>${inline(heading[2])}</h${level}>`)
      continue
    }
    const item = line.match(/^[-*]\s+(.+)$/)
    if (item) {
      if (!inList) { output.push('<ul>'); inList = true }
      output.push(`<li>${inline(item[1])}</li>`)
      continue
    }
    if (inList) { output.push('</ul>'); inList = false }
    output.push(line.trim() ? `<p>${inline(line)}</p>` : '')
  }
  if (inList) output.push('</ul>')
  if (inCode) output.push('</code></pre>')
  return output.join('')
}
