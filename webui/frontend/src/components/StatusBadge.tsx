const COLORS: Record<string, string> = {
  pending: '#8a8f98',
  queued: '#8a8f98',
  downloading: '#2f6fed',
  decrypting: '#2f6fed',
  uploading: '#2f6fed',
  running: '#2f6fed',
  succeeded: '#1f9254',
  skipped: '#8a8f98',
  failed: '#d13b3b',
  cancelled: '#d13b3b',
  active: '#1f9254',
  needs_reconnect: '#d13b3b',
}

export function StatusBadge({ status }: { status: string }) {
  const color = COLORS[status] ?? '#8a8f98'
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.4em',
        fontSize: '0.85em',
        fontWeight: 600,
        color,
      }}
    >
      <span
        style={{
          width: '0.55em',
          height: '0.55em',
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {status.replace('_', ' ')}
    </span>
  )
}
