const REASON_MESSAGES = {
  coordinator_not_configured: '认证协调器未配置，请先补齐协调器地址后再重试。',
  coordinator_token_not_configured: '认证协调器控制令牌未配置，请先补齐配置后再重试。',
  coordinator_invalid: '认证协调器地址无效，请检查配置后再重试。',
  coordinator_unauthorized: '认证协调器未接受控制令牌，请检查配置后再重试。',
  coordinator_unreachable: '认证协调器不可达，请确认协调器正在运行后再重试。',
  coordinator_invalid_response: '认证协调器返回了无法识别的状态，请检查协调器版本后再重试。',
  auth_status_error: 'Codex CLI 登录状态异常，请点击“重新检查”；若持续失败，请检查协调器和 CLI 登录进程。',
}

const STATUS_MESSAGES = {
  ready: 'Codex CLI 已登录，可点击“更换账号”启动新的登录流程。',
  not_logged_in: 'Codex CLI 尚未登录，可点击“开始登录”。',
  login_in_progress: '登录正在进行中，可等待完成或点击“取消”。',
  error: '认证状态检查失败，请点击“重新检查”。',
}

export function getCodexAuthStatusMessage(status, reason, message) {
  return message || REASON_MESSAGES[reason] || STATUS_MESSAGES[status] || REASON_MESSAGES.auth_status_error
}
