var App = {
  currentRoute: 'dashboard',
  state: {},
};

App.init = function() {
  var self = this;
  document.querySelectorAll('.nav-item').forEach(function(el) {
    el.addEventListener('click', function(e) {
      e.preventDefault();
      var route = this.getAttribute('data-route');
      self.navigate(route);
    });
  });
  this.refresh();
};

App.navigate = function(route) {
  this.currentRoute = route;
  document.querySelectorAll('.nav-item').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-route') === route);
  });
  var names = {
    'dashboard': '概览',
    'projects': '项目管理',
    'jobs': '作业监控',
    'candidates': '记忆审查',
    'flags': '功能开关',
    'profiles': '嵌入配置',
    'memories': '记忆浏览',
    'logs': 'L0 原始日志',
    'token-usage': '令牌用量',
    'audit-logs': '审计日志',
  };
  document.getElementById('breadcrumb').textContent = names[route] || route;
  this.render(route);
};

App.render = function(route) {
  UI.showLoading();
  var self = this;
  setTimeout(function() {
    var renderers = {
      'dashboard': Pages.dashboard,
      'projects': Pages.projects,
      'jobs': Pages.jobs,
      'candidates': Pages.candidates,
      'flags': Pages.flags,
      'profiles': Pages.profiles,
      'memories': Pages.memories,
      'logs': Pages.logs,
      'token-usage': Pages.tokenUsage,
      'audit-logs': Pages.auditLogs,
    };
    var renderFn = renderers[route] || Pages.dashboard;
    renderFn.call(Pages, self.state);
  }, 50);
};

App.refresh = function() {
  this.render(this.currentRoute);
};

App.showProject = function(projectId) {
  this.state.selectedProjectId = projectId;
  this.navigate('projects');
};

window.onload = function() { App.init(); };
