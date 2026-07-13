var Pages = {};

// ===== 仪表盘 =====
Pages.dashboard = function(state) {
  UI.showLoading();
  Promise.all([API.health(), API.stats()]).then(function(results) {
    var health = results[0];
    var stats = results[1];
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });

    var dbStatus = health.status;
    var dbEl = document.getElementById('db-status');
    dbEl.className = 'status-dot ' + dbStatus;
    dbEl.title = '数据库：' + dbStatus;

    var section = document.createElement('div');
    section.className = 'page-section';
    section.innerHTML = '<div class="stats-grid">' +
      '<div class="stat-card"><span class="stat-label">系统状态</span><span class="stat-value ' + (dbStatus === 'ok' ? 'success' : 'danger') + '">' + dbStatus + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">数据库</span><span class="stat-value accent">' + (health.dialect || '-') + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">向量支持</span><span class="stat-value ' + (health.vector === 'ok' || health.vector === 'not-applicable' ? 'success' : 'warning') + '">' + health.vector + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">项目数</span><span class="stat-value purple">' + health.projects + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">消息数</span><span class="stat-value accent">' + health.messages + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">记忆数</span><span class="stat-value success">' + health.memories + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">待处理作业</span><span class="stat-value ' + (health.jobs_pending > 0 ? 'warning' : 'success') + '">' + health.jobs_pending + '</span></div>' +
      '<div class="stat-card"><span class="stat-label">待审查候选</span><span class="stat-value ' + (health.candidates_pending > 0 ? 'orange' : 'success') + '">' + health.candidates_pending + '</span></div>' +
      '</div>';

    if (stats.memory_by_level) {
      var levels = stats.memory_by_level;
      var levelNames = { L1: '工作记忆', L2: '知识库', L3: '错误记忆' };
      var levelColors = { L1: 'accent', L2: 'success', L3: 'danger' };
      var levelHtml = Object.keys(levels).map(function(k) {
        return '<div class="stat-card"><span class="stat-label">' + (levelNames[k] || k) + '</span><span class="stat-value ' + (levelColors[k] || 'purple') + '">' + levels[k] + '</span></div>';
      }).join('');
      section.innerHTML += '<h3 style="margin-bottom:12px;font-size:14px;font-weight:600;">记忆分布</h3><div class="stats-grid">' + levelHtml + '</div>';
    }

    if (stats.projects && stats.projects.length > 0) {
      section.innerHTML += '<div class="card"><div class="card-header"><h2>项目概览</h2></div><div class="table-wrapper"><table><thead><tr><th>项目</th><th>状态</th><th>消息</th><th>记忆</th><th>候选</th><th>作业</th><th>启用的功能</th><th>创建时间</th></tr></thead><tbody>';
      stats.projects.forEach(function(p) {
        var jobHtml = '';
        if (p.jobs && Object.keys(p.jobs).length) {
          jobHtml = Object.keys(p.jobs).map(function(s) { return s + ':' + p.jobs[s]; }).join(' ');
        }
        section.innerHTML += '<tr style="cursor:pointer" onclick="App.showProject(' + p.id + ')"><td><strong>' + escapeHtml(p.project_key) + '</strong>' + (p.name ? '<br><small>' + escapeHtml(p.name) + '</small>' : '') + '</td><td>' + UI.badgeForStatus(p.status) + '</td><td>' + p.messages + '</td><td>' + p.memories + '</td><td>' + p.candidates + '</td><td>' + jobHtml + '</td><td>' + p.flags_enabled + '/7</td><td>' + UI.timeAgo(p.created_at) + '</td></tr>';
      });
      section.innerHTML += '</tbody></table></div></div>';
    }

    if (stats.recent_jobs && stats.recent_jobs.length > 0) {
      section.innerHTML += '<div class="card" style="margin-top:16px"><div class="card-header"><h2>最近作业（7天）</h2></div><div class="table-wrapper"><table><thead><tr><th>ID</th><th>类型</th><th>项目</th><th>状态</th><th>创建时间</th></tr></thead><tbody>';
      stats.recent_jobs.forEach(function(j) {
        section.innerHTML += '<tr><td>' + j.id + '</td><td>' + escapeHtml(j.job_type) + '</td><td>' + escapeHtml(j.project_key) + '</td><td>' + UI.badgeForStatus(j.status) + '</td><td>' + UI.timeAgo(j.created_at) + '</td></tr>';
      });
      section.innerHTML += '</tbody></table></div></div>';
    }

    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

// ===== 项目 =====
Pages.projects = function(state) {
  UI.showLoading();
  var selectedId = state.selectedProjectId;
  Promise.all([API.projects(), selectedId ? API.project(selectedId) : null]).then(function(results) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';
    var projectsData = results[0];
    var projectDetail = results[1] || null;

    if (projectDetail) {
      var p = projectDetail.project;
      section.innerHTML = '<div style="margin-bottom:16px"><button class="btn btn-sm btn-outline" onclick="App.navigate(\'projects\')">&larr; 返回项目列表</button></div>';
      section.innerHTML += '<div class="card"><div class="card-header"><h2>' + escapeHtml(p.project_key) + '</h2>' + UI.badgeForStatus(p.status) + '</div>';
      section.innerHTML += '<div class="detail-grid">';
      section.innerHTML += '<span class="detail-label">名称</span><span class="detail-value">' + escapeHtml(p.name || '-') + '</span>';
      section.innerHTML += '<span class="detail-label">仓库</span><span class="detail-value">' + escapeHtml(p.repository || '-') + '</span>';
      section.innerHTML += '<span class="detail-label">描述</span><span class="detail-value">' + escapeHtml(p.description || '-') + '</span>';
      section.innerHTML += '<span class="detail-label">消息数</span><span class="detail-value">' + projectDetail.messages + '</span>';
      section.innerHTML += '<span class="detail-label">记忆数</span><span class="detail-value">' + projectDetail.memories + '</span>';
      section.innerHTML += '<span class="detail-label">候选数</span><span class="detail-value">' + projectDetail.candidates + '</span>';
      section.innerHTML += '<span class="detail-label">创建时间</span><span class="detail-value">' + UI.formatDate(p.created_at) + '</span>';
      section.innerHTML += '</div></div>';

      if (projectDetail.memories_by_level && Object.keys(projectDetail.memories_by_level).length) {
        section.innerHTML += '<div class="stats-grid" style="margin-top:16px">';
        Object.keys(projectDetail.memories_by_level).forEach(function(k) {
          section.innerHTML += '<div class="stat-card"><span class="stat-label">' + k + ' 记忆</span><span class="stat-value accent">' + projectDetail.memories_by_level[k] + '</span></div>';
        });
        section.innerHTML += '</div>';
      }

      if (projectDetail.jobs_by_status && Object.keys(projectDetail.jobs_by_status).length) {
        section.innerHTML += '<div class="card" style="margin-top:16px"><div class="card-header"><h3>作业状态</h3></div><div class="stats-grid">';
        Object.keys(projectDetail.jobs_by_status).forEach(function(s) {
          section.innerHTML += '<div class="stat-card"><span class="stat-label">' + s + '</span><span class="stat-value">' + projectDetail.jobs_by_status[s] + '</span></div>';
        });
        section.innerHTML += '</div></div>';
      }

      section.innerHTML += '<div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">';
      section.innerHTML += '<button class="btn btn-sm" onclick="App.navigate(\'jobs\')">查看作业 &#8594;</button>';
      section.innerHTML += '<button class="btn btn-sm" onclick="App.navigate(\'candidates\')">查看候选 &#8594;</button>';
      section.innerHTML += '<button class="btn btn-sm" onclick="\n        var pid = ' + p.id + ';\n        App.state.flagsProjectId = pid;\n        App.navigate(\'flags\');\n      ">功能开关 &#8594;</button>';
      section.innerHTML += '</div>';
    } else {
      section.innerHTML = '<div class="section"><h3 style="margin-bottom:12px">所有项目</h3><div class="two-col">';
      (projectsData.projects || []).forEach(function(p) {
        section.innerHTML += '<div class="project-card" onclick="App.showProject(' + p.id + ')"><strong>' + escapeHtml(p.project_key) + '</strong>' + (p.name ? '<br><small>' + escapeHtml(p.name) + '</small>' : '') + '<br><span class="badge badge-' + (p.status === 'active' ? 'success' : 'dark') + '">' + p.status + '</span><br><small style="color:var(--text-secondary)">' + UI.timeAgo(p.created_at) + '</small></div>';
      });
      section.innerHTML += '</div></div>';
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

// ===== 任务 =====
Pages.jobs = function(state) {
  UI.showLoading();
  var params = {};
  if (state.jobsFilter) Object.assign(params, state.jobsFilter);
  API.jobs(params).then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';

    var statusOptions = ['', 'pending', 'running', 'completed', 'failed', 'retry_wait', 'dead'];
    var statusLabels = ['全部状态', '待处理', '运行中', '已完成', '失败', '等待重试', '终止'];
    var selStr = statusOptions.map(function(s, i) {
      return '<option value="' + s + '"' + (state.jobsFilter && state.jobsFilter.status === s ? ' selected' : '') + '>' + statusLabels[i] + '</option>';
    }).join('');

    section.innerHTML = '<div class="filters">' +
      '<select id="job-status-filter">' + selStr + '</select>' +
      '<button class="btn btn-sm" onclick="\n        App.state.jobsFilter = {};\n        var sf = document.getElementById(\'job-status-filter\').value;\n        if (sf) App.state.jobsFilter.status = sf;\n        App.refresh();\n      ">筛选</button>' +
      '</div>';
    section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>类型</th><th>项目</th><th>状态</th><th>尝试</th><th>错误</th><th>创建时间</th><th>操作</th></tr></thead><tbody>';
    (data.jobs || []).forEach(function(j) {
      var errMsg = j.last_error_message || j.last_error_code || '';
      if (errMsg.length > 60) errMsg = errMsg.substring(0, 60) + '...';
      section.innerHTML += '<tr><td>' + j.id + '</td><td><small>' + escapeHtml(j.job_type) + '</small></td><td>' + escapeHtml(j.project_key || '') + '</td><td>' + UI.badgeForStatus(j.status) + '</td><td>' + j.attempt_count + '/' + j.max_attempts + '</td><td><small title="' + escapeHtml(j.last_error_message || '') + '" style="color:var(--danger)">' + escapeHtml(errMsg) + '</small></td><td><small>' + UI.timeAgo(j.created_at) + '</small></td><td>' +
        (j.status === 'dead' || j.status === 'retry_wait' ? '<button class="btn btn-sm btn-warning" onclick="Pages.retryJob(' + j.id + ')">重试</button>' : '') +
        '</td></tr>';
    });
    section.innerHTML += '</tbody></table></div>';
    if (data.jobs && data.jobs.length >= 50) {
      section.innerHTML += '<p style="text-align:center;color:var(--text-secondary);margin-top:8px">显示最多50条记录</p>';
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

Pages.retryJob = function(jobId) {
  UI.confirmModal('重试作业', '<p>确定要重试作业 #' + jobId + ' 吗？</p>', function() {
    API.retryJob(jobId).then(function() {
      UI.toast('作业已重新排入队列', 'success');
      App.refresh();
    }).catch(function(err) {
      UI.toast('重试失败: ' + err.message, 'error');
    });
  });
};

// ===== 候选记忆 =====
Pages.candidates = function(state) {
  UI.showLoading();
  var params = {};
  if (state.candidatesFilter) Object.assign(params, state.candidatesFilter);
  API.candidates(params).then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';

    var statusOptions = ['', 'generated', 'approved', 'rejected', 'shadow'];
    var statusLabels = ['全部(非shadow)', '已生成', '已批准', '已拒绝', 'Shadow'];
    var selStr = statusOptions.map(function(s, i) {
      return '<option value="' + s + '"' + (state.candidatesFilter && state.candidatesFilter.status === s ? ' selected' : '') + '>' + statusLabels[i] + '</option>';
    }).join('');

    section.innerHTML = '<div class="filters">' +
      '<select id="candidate-status-filter">' + selStr + '</select>' +
      '<button class="btn btn-sm" onclick="\n        App.state.candidatesFilter = {};\n        var sf = document.getElementById(\'candidate-status-filter\').value;\n        if (sf) App.state.candidatesFilter.status = sf;\n        App.refresh();\n      ">筛选</button>' +
      '</div>';

    section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>标题</th><th>层级</th><th>类型</th><th>项目</th><th>状态</th><th>置信度</th><th>弃权</th><th>发布时间</th><th>操作</th></tr></thead><tbody>';
    (data.candidates || []).forEach(function(c) {
      section.innerHTML += '<tr><td>' + c.id + '</td><td><strong>' + escapeHtml((c.title || '').substring(0, 50)) + '</strong></td><td>' + UI.badgeForStatus(c.level) + '</td><td>' + escapeHtml(c.memory_type || '') + '</td><td>' + escapeHtml(c.project_key || '') + '</td><td>' + UI.badgeForStatus(c.status) + '</td><td>' + (c.model_confidence ? (c.model_confidence * 100).toFixed(0) + '%' : '-') + '</td><td>' + (c.abstain ? '&#10003;' : '') + '</td><td>' + UI.timeAgo(c.created_at) + '</td><td>' +
        (c.status === 'generated' ? '<button class="btn btn-sm btn-success" onclick="Pages.approveCandidate(' + c.id + ')">批准</button> <button class="btn btn-sm btn-danger" onclick="Pages.rejectCandidate(' + c.id + ')">拒绝</button>' : '') +
        '</td></tr>';
      if (c.content && Object.keys(c.content).length) {
        section.innerHTML += '<tr style="background:var(--accent-light)"><td colspan="10"><details><summary>查看内容</summary><pre class="json-pre">' + escapeHtml(JSON.stringify(c.content, null, 2)) + '</pre></details></td></tr>';
      }
    });
    section.innerHTML += '</tbody></table></div>';
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

Pages.approveCandidate = function(candidateId) {
  UI.confirmModal('批准候选', '<p>确定要批准候选 #' + candidateId + ' 吗？</p><div class="form-group"><label>审查者（可选）</label><input id="approve-reviewer" class="form-control" placeholder="审查者名称"></div><div class="form-group"><label>理由（可选）</label><textarea id="approve-reason" class="form-control" placeholder="批准理由"></textarea></div>', function() {
    var reviewer = document.getElementById('approve-reviewer').value;
    var reason = document.getElementById('approve-reason').value;
    API.reviewCandidate(candidateId, 'approve', reviewer, reason).then(function() {
      UI.toast('候选已批准', 'success');
      App.refresh();
    }).catch(function(err) {
      UI.toast('操作失败: ' + err.message, 'error');
    });
  });
};

Pages.rejectCandidate = function(candidateId) {
  UI.confirmModal('拒绝候选', '<p>确定要拒绝候选 #' + candidateId + ' 吗？</p><div class="form-group"><label>审查者（可选）</label><input id="reject-reviewer" class="form-control" placeholder="审查者名称"></div><div class="form-group"><label>理由（可选）</label><textarea id="reject-reason" class="form-control" placeholder="拒绝理由"></textarea></div>', function() {
    var reviewer = document.getElementById('reject-reviewer').value;
    var reason = document.getElementById('reject-reason').value;
    API.reviewCandidate(candidateId, 'reject', reviewer, reason).then(function() {
      UI.toast('候选已拒绝', 'success');
      App.refresh();
    }).catch(function(err) {
      UI.toast('操作失败: ' + err.message, 'error');
    });
  });
};

// ===== 功能开关 =====
Pages.flags = function(state) {
  UI.showLoading();
  var pid = state.flagsProjectId;
  if (!pid) {
    API.projects().then(function(data) {
      UI.hideLoading();
      var c = document.getElementById('content');
      c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
      var section = document.createElement('div');
      section.className = 'page-section';
      section.innerHTML = '<div class="section">选择一个项目：<div class="two-col" style="margin-top:12px">';
      (data.projects || []).forEach(function(p) {
        section.innerHTML += '<div class="project-card" onclick="App.state.flagsProjectId = ' + p.id + '; App.refresh()"><strong>' + escapeHtml(p.project_key) + '</strong></div>';
      });
      section.innerHTML += '</div></div>';
      c.appendChild(section);
    }).catch(function(err) {
      UI.hideLoading();
      document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
    });
    return;
  }
  API.flags(pid).then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';
    section.innerHTML = '<div style="margin-bottom:16px"><button class="btn btn-sm btn-outline" onclick="App.state.flagsProjectId = null; App.refresh()">&larr; 切换项目</button></div>';

    var flags = data.feature_flags || {};
    var flagLabels = {
      memory_v11_enabled: 'V1.1 记忆系统',
      server_outbox_enabled: 'Server Outbox',
      lexical_retrieval_enabled: '关键词检索',
      dense_retrieval_enabled: '稠密向量检索',
      embedding_profile_v2_enabled: 'V2 嵌入配置',
      llm_shadow_enabled: 'LLM 影子模式',
      candidate_publish_enabled: '候选发布',
    };
    var flagDescs = {
      memory_v11_enabled: '启用 V1.1 内存写入通道和附加端点',
      server_outbox_enabled: '启用出站事件盒进行异步处理',
      lexical_retrieval_enabled: '启用基于关键词的全文检索',
      dense_retrieval_enabled: '启用向量嵌入的语义检索',
      embedding_profile_v2_enabled: '启用 V2 嵌入配置管理',
      llm_shadow_enabled: '启用 LLM 阴影提取器（仅记录，不影响输出）',
      candidate_publish_enabled: '允许将已批准的候选发布为正式记忆',
    };

    section.innerHTML += '<div class="card"><div class="card-header"><h2>功能开关 - 项目 #' + pid + '</h2></div>';
    Object.keys(flagLabels).forEach(function(k) {
      var checked = flags[k] ? 'checked' : '';
      section.innerHTML += '<div class="flag-item" style="margin-bottom:12px;display:flex;align-items:center;gap:12px">' +
        '<label class="toggle-switch"><input type="checkbox" ' + checked + ' onchange="Pages.toggleFlag(' + pid + ',\'' + k + '\',this.checked)"><span class="toggle-slider"></span></label>' +
        '<div><strong>' + flagLabels[k] + '</strong><br><small style="color:var(--text-secondary)">' + flagDescs[k] + '</small></div>' +
        '</div>';
    });
    section.innerHTML += '</div>';

    if (data.retrieval_profile) {
      var rp = data.retrieval_profile;
      section.innerHTML += '<div class="card" style="margin-top:16px"><div class="card-header"><h3>检索配置</h3></div><div class="detail-grid">' +
        '<span class="detail-label">活动嵌入配置</span><span class="detail-value">' + (rp.active_embedding_profile_id || '-') + '</span>' +
        '<span class="detail-label">金丝雀嵌入配置</span><span class="detail-value">' + (rp.canary_embedding_profile_id || '-') + '</span>' +
        '<span class="detail-label">金丝雀百分比</span><span class="detail-value">' + rp.canary_percent + '%</span>' +
        '<span class="detail-label">混合检索</span><span class="detail-value">' + (rp.hybrid_search_enabled ? '已启用' : '已禁用') + '</span>' +
        '<span class="detail-label">降级模式</span><span class="detail-value">' + rp.fallback_mode + '</span>' +
        '<span class="detail-label">全局结果限制</span><span class="detail-value">' + rp.global_result_limit + '</span>' +
        '</div></div>';
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

Pages.toggleFlag = function(projectId, flagName, checked) {
  var payload = {};
  payload[flagName] = checked;
  API.updateFlags(projectId, payload).then(function() {
    UI.toast(flagName + ' 已' + (checked ? '启用' : '禁用'), 'success');
  }).catch(function(err) {
    UI.toast('更新失败: ' + err.message, 'error');
    App.refresh();
  });
};

// ===== 嵌入配置 =====
Pages.profiles = function() {
  UI.showLoading();
  API.profiles().then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';
    section.innerHTML = '<div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:center"><h3 style="font-size:15px;font-weight:600">嵌入配置</h3><button class="btn btn-sm btn-primary" onclick="Pages.showCreateProfile()">+ 新建配置</button></div>';
    section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>名称</th><th>提供商</th><th>模型</th><th>维度</th><th>相似度</th><th>归一化</th><th>状态</th><th>分块器</th><th>创建时间</th></tr></thead><tbody>';
    (data.profiles || []).forEach(function(p) {
      section.innerHTML += '<tr><td>' + p.id + '</td><td><strong>' + escapeHtml(p.name) + '</strong></td><td>' + escapeHtml(p.provider) + '</td><td><small>' + escapeHtml(p.model) + '</small></td><td>' + p.dimension + '</td><td>' + (p.similarity_metric || '-') + '</td><td>' + (p.normalization || '-') + '</td><td>' + UI.badgeForStatus(p.status) + '</td><td><small>' + (p.chunker_version || '-') + '</small></td><td><small>' + UI.timeAgo(p.created_at) + '</small></td></tr>';
    });
    section.innerHTML += '</tbody></table></div>';
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

Pages.showCreateProfile = function() {
  var html = '<div class="form-group"><label>名称</label><input id="pf-name" placeholder="配置名称"></div>' +
    '<div class="form-group"><label>提供商</label><input id="pf-provider" placeholder="如 openai, local"></div>' +
    '<div class="form-group"><label>模型</label><input id="pf-model" placeholder="如 text-embedding-3-small"></div>' +
    '<div class="form-group"><label>维度</label><input id="pf-dimension" type="number" value="1536"></div>' +
    '<div class="form-group"><label>分块器版本</label><input id="pf-chunker" value="v1"></div>' +
    '<div class="form-group"><label>内容归一化版本</label><input id="pf-norm" value="v1"></div>';
  UI.formModal('新建嵌入配置', html, function() {
    var data = {
      name: document.getElementById('pf-name').value,
      provider: document.getElementById('pf-provider').value,
      model: document.getElementById('pf-model').value,
      dimension: parseInt(document.getElementById('pf-dimension').value) || 1536,
      chunker_version: document.getElementById('pf-chunker').value || 'v1',
      content_normalization_version: document.getElementById('pf-norm').value || 'v1',
    };
    if (!data.name || !data.provider || !data.model) {
      UI.toast('请填写名称、提供商和模型', 'warning');
      return false;
    }
    API.createProfile(data).then(function() {
      UI.toast('配置已创建', 'success');
      App.refresh();
    }).catch(function(err) {
      UI.toast('创建失败: ' + err.message, 'error');
    });
    return true;
  });
};

// ===== 记忆 =====
Pages.memories = function(state) {
  UI.showLoading();
  var params = { limit: 100 };
  if (state.memoriesFilter) Object.assign(params, state.memoriesFilter);
  API.memories(params).then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';

    var levelOptions = ['', 'L1', 'L2', 'L3'];
    var levelLabels = ['全部层级', 'L1 工作记忆', 'L2 知识库', 'L3 错误记忆'];
    var scopeOptions = ['', 'project', 'global'];
    var scopeLabels = ['全部范围', '项目级', '全局'];
    var lvSel = levelOptions.map(function(v, i) { return '<option value="' + v + '"' + (state.memoriesFilter && state.memoriesFilter.level === v ? ' selected' : '') + '>' + levelLabels[i] + '</option>'; }).join('');
    var scSel = scopeOptions.map(function(v, i) { return '<option value="' + v + '"' + (state.memoriesFilter && state.memoriesFilter.scope === v ? ' selected' : '') + '>' + scopeLabels[i] + '</option>'; }).join('');

    section.innerHTML = '<div class="filters">' +
      '<input id="mem-search" placeholder="搜索标题..." value="' + (state.memoriesFilter && state.memoriesFilter.search || '') + '">' +
      '<select id="mem-level">' + lvSel + '</select>' +
      '<select id="mem-scope">' + scSel + '</select>' +
      '<button class="btn btn-sm" onclick="\n        App.state.memoriesFilter = {};\n        var s = document.getElementById(\'mem-search\').value;\n        var l = document.getElementById(\'mem-level\').value;\n        var sc = document.getElementById(\'mem-scope\').value;\n        if (s) App.state.memoriesFilter.search = s;\n        if (l) App.state.memoriesFilter.level = l;\n        if (sc) App.state.memoriesFilter.scope = sc;\n        App.refresh();\n      ">搜索</button>' +
      '</div>';

    section.innerHTML += '<p style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">共 ' + (data.total || 0) + ' 条记忆</p>';
    section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>标题</th><th>层级</th><th>类型</th><th>范围</th><th>项目</th><th>状态</th><th>置信度</th><th>使用次数</th><th>更新于</th></tr></thead><tbody>';
    (data.memories || []).forEach(function(m) {
      section.innerHTML += '<tr><td>' + m.id + '</td><td><strong>' + escapeHtml((m.title || '').substring(0, 60)) + '</strong></td><td>' + UI.badgeForStatus(m.level) + '</td><td><small>' + escapeHtml(m.memory_type || '') + '</small></td><td>' + (m.scope === 'global' ? UI.badge('全局', 'purple') : '项目') + '</td><td>' + (m.project_id || '-') + '</td><td>' + UI.badgeForStatus(m.status) + '</td><td>' + (m.confidence ? (m.confidence * 100).toFixed(0) + '%' : '-') + '</td><td>' + m.usage_count + '</td><td><small>' + UI.timeAgo(m.updated_at) + '</small></td></tr>';
      if (m.content && Object.keys(m.content).length) {
        section.innerHTML += '<tr style="background:var(--accent-light)"><td colspan="10"><details><summary>查看详细内容</summary><pre class="json-pre">' + escapeHtml(JSON.stringify(m.content, null, 2)) + '</pre></details></td></tr>';
      }
    });
    section.innerHTML += '</tbody></table></div>';
    if (data.total > data.limit) {
      section.innerHTML += UI.pagination(data.total, data.limit, data.offset || 0, function(newOffset) {
        App.state.memoriesFilter = App.state.memoriesFilter || {};
        App.state.memoriesFilter.offset = newOffset;
        App.refresh();
      });
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

// ===== L0 原始日志 =====
Pages.logs = function(state) {
  UI.showLoading();
  var params = { limit: 100 };
  if (state.logsFilter) Object.assign(params, state.logsFilter);
  API.logs(params).then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';

    var roleOptions = ['', 'user', 'assistant', 'system'];
    var roleLabels = ['全部角色', '用户', '助手', '系统'];
    var roleSel = roleOptions.map(function(v, i) { return '<option value="' + v + '"' + (state.logsFilter && state.logsFilter.role === v ? ' selected' : '') + '>' + roleLabels[i] + '</option>'; }).join('');

    section.innerHTML = '<div class="filters">' +
      '<select id="log-role">' + roleSel + '</select>' +
      '<button class="btn btn-sm" onclick="\n        App.state.logsFilter = {};\n        var r = document.getElementById(\'log-role\').value;\n        if (r) App.state.logsFilter.role = r;\n        App.refresh();\n      ">筛选</button>' +
      '</div>';

    section.innerHTML += '<p style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">共 ' + (data.total || 0) + ' 条原始日志</p>';
    section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>项目</th><th>会话</th><th>角色</th><th>内容</th><th>来源</th><th>时间</th></tr></thead><tbody>';
    (data.logs || []).forEach(function(l) {
      var contentPreview = (l.content || '').substring(0, 120);
      section.innerHTML += '<tr><td>' + l.id + '</td><td><small>' + escapeHtml(l.project_key || '') + '</small></td><td><small>' + escapeHtml((l.session_key || '').substring(0, 16)) + '</small></td><td>' + UI.badge(l.role, l.role === 'user' ? 'info' : l.role === 'assistant' ? 'success' : 'dark') + '</td><td><div class="expandable-content" onclick="this.classList.toggle(\'expanded\')"><small>' + escapeHtml(contentPreview) + '</small></div></td><td><small>' + escapeHtml(l.source || '') + '</small></td><td><small>' + UI.timeAgo(l.created_at) + '</small></td></tr>';
    });
    section.innerHTML += '</tbody></table></div>';
    if (data.total > data.limit) {
      section.innerHTML += UI.pagination(data.total, data.limit, data.offset || 0, function(newOffset) {
        App.state.logsFilter = App.state.logsFilter || {};
        App.state.logsFilter.offset = newOffset;
        App.refresh();
      });
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

// ===== 令牌用量 =====
Pages.tokenUsage = function(state) {
  UI.showLoading();
  API.tokenUsage().then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';

    var totalTokens = 0;
    var usageByType = {};
    (data.usage || []).forEach(function(u) {
      totalTokens += u.tokens_used || 0;
      usageByType[u.token_type] = (usageByType[u.token_type] || 0) + (u.tokens_used || 0);
    });

    section.innerHTML = '<div class="stats-grid">' +
      '<div class="stat-card"><span class="stat-label">总令牌消耗</span><span class="stat-value accent">' + totalTokens.toLocaleString() + '</span></div>';
    Object.keys(usageByType).forEach(function(t) {
      section.innerHTML += '<div class="stat-card"><span class="stat-label">' + escapeHtml(t) + '</span><span class="stat-value purple">' + usageByType[t].toLocaleString() + '</span></div>';
    });
    section.innerHTML += '</div>';

    if (data.usage && data.usage.length > 0) {
      section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>项目</th><th>日期</th><th>类型</th><th>用量</th></tr></thead><tbody>';
      data.usage.forEach(function(u) {
        section.innerHTML += '<tr><td>' + u.id + '</td><td>' + u.project_id + '</td><td>' + u.usage_date + '</td><td>' + UI.badge(u.token_type, 'info') + '</td><td>' + (u.tokens_used || 0).toLocaleString() + '</td></tr>';
      });
      section.innerHTML += '</tbody></table></div>';
    } else {
      section.innerHTML += '<div class="empty-state"><p>暂无令牌使用记录</p></div>';
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

// ===== 审计日志 =====
Pages.auditLogs = function(state) {
  UI.showLoading();
  var params = {};
  if (state.auditFilter) Object.assign(params, state.auditFilter);
  API.auditLogs(params).then(function(data) {
    UI.hideLoading();
    var c = document.getElementById('content');
    c.querySelectorAll(':scope > .page-section').forEach(function(el) { el.remove(); });
    var section = document.createElement('div');
    section.className = 'page-section';
    section.innerHTML += '<p style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">共 ' + (data.total || 0) + ' 条审计日志</p>';
    section.innerHTML += '<div class="table-wrapper"><table><thead><tr><th>ID</th><th>事件类型</th><th>项目</th><th>主体类型</th><th>主体ID</th><th>原因</th><th>元数据</th></tr></thead><tbody>';
    (data.audit_logs || []).forEach(function(a) {
      var metaStr = a.metadata_json ? JSON.stringify(a.metadata_json) : '';
      section.innerHTML += '<tr><td>' + a.id + '</td><td>' + UI.badge(a.event_type, 'info') + '</td><td>' + (a.project_id || '-') + '</td><td><small>' + escapeHtml(a.subject_type || '') + '</small></td><td><small>' + escapeHtml(a.subject_id || '') + '</small></td><td><small>' + escapeHtml(a.reason_code || '') + '</small></td><td><small title="' + escapeHtml(metaStr) + '">' + (metaStr.length > 40 ? escapeHtml(metaStr.substring(0, 40)) + '...' : escapeHtml(metaStr)) + '</small></td></tr>';
    });
    section.innerHTML += '</tbody></table></div>';
    if (data.total > data.limit) {
      section.innerHTML += UI.pagination(data.total, data.limit, data.offset || 0, function(newOffset) {
        App.state.auditFilter = App.state.auditFilter || {};
        App.state.auditFilter.offset = newOffset;
        App.refresh();
      });
    }
    c.appendChild(section);
  }).catch(function(err) {
    UI.hideLoading();
    document.getElementById('content').innerHTML = '<div class="empty-state"><p style="color:var(--danger)">加载失败: ' + escapeHtml(err.message) + '</p></div>';
  });
};

