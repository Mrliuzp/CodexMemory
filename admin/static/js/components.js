var UI = {};

UI.showLoading = function() {
  document.getElementById('loading').style.display = 'flex';
  document.getElementById('content').querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
};

UI.hideLoading = function() {
  document.getElementById('loading').style.display = 'none';
};

UI.toast = function(message, type) {
  type = type || 'info';
  var container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  var el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(function() { el.remove(); }, 3000);
};

UI.badge = function(text, type) {
  type = type || 'dark';
  return '<span class="badge badge-' + type + '">' + escapeHtml(text) + '</span>';
};

UI.badgeForStatus = function(status) {
  var map = {
    'pending': 'warning',
    'running': 'info',
    'completed': 'success',
    'failed': 'danger',
    'dead': 'danger',
    'retry_wait': 'orange',
    'generated': 'info',
    'approved': 'success',
    'rejected': 'danger',
    'shadow': 'dark',
    'active': 'success',
    'inactive': 'warning',
    'draft': 'warning',
    'published': 'success',
  };
  return UI.badge(status, map[status] || 'dark');
};

UI.timeAgo = function(iso) {
  if (!iso) return '-';
  var now = new Date();
  var d = new Date(iso);
  var diff = Math.floor((now - d) / 1000);
  if (diff < 60) return diff + '秒前';
  if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
  return Math.floor(diff / 86400) + '天前';
};

UI.formatDate = function(iso) {
  if (!iso) return '-';
  var d = new Date(iso);
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};

UI.pagination = function(total, limit, offset, onPage) {
  var totalPages = Math.ceil(total / limit);
  var currentPage = Math.floor(offset / limit) + 1;
  if (totalPages <= 1) return '';
  var html = '<div class="pagination">';
  html += '<button class="btn btn-sm btn-outline" onclick="(' + onPage.toString() + ')(' + Math.max(0, offset - limit) + ')" ' + (offset === 0 ? 'disabled' : '') + '>&laquo; 上一页</button>';
  html += '<span>' + currentPage + ' / ' + totalPages + '</span>';
  html += '<button class="btn btn-sm btn-outline" onclick="(' + onPage.toString() + ')(' + Math.min((totalPages - 1) * limit, offset + limit) + ')" ' + (offset + limit >= total ? 'disabled' : '') + '>下一页 &raquo;</button>';
  html += '</div>';
  return html;
};

UI.confirmModal = function(title, bodyHtml, onConfirm) {
  var overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = '<div class="modal"><div class="modal-header"><h2>' + title + '</h2><button class="modal-close" onclick="this.closest(\'.modal-overlay\').remove()">&times;</button></div><div class="modal-body">' + bodyHtml + '</div><div class="modal-footer"><button class="btn btn-outline" onclick="this.closest(\'.modal-overlay\').remove()">取消</button><button class="btn btn-primary" id="modal-confirm-btn">确认</button></div></div>';
  document.body.appendChild(overlay);
  document.getElementById('modal-confirm-btn').onclick = function() {
    onConfirm();
    overlay.remove();
  };
};

UI.formModal = function(title, fieldsHtml, onSave) {
  var overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = '<div class="modal"><div class="modal-header"><h2>' + title + '</h2><button class="modal-close" onclick="this.closest(\'.modal-overlay\').remove()">&times;</button></div><div class="modal-body">' + fieldsHtml + '</div><div class="modal-footer"><button class="btn btn-outline" onclick="this.closest(\'.modal-overlay\').remove()">取消</button><button class="btn btn-primary" id="modal-save-btn">保存</button></div></div>';
  document.body.appendChild(overlay);
  document.getElementById('modal-save-btn').onclick = function() {
    var data = onSave();
    if (data !== false) overlay.remove();
  };
};

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
