(function () {
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  async function request(url, options) {
    const response = await fetch(url, Object.assign(
      { headers: { 'Content-Type': 'application/json' } }, options || {}
    ));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.code || ('HTTP ' + response.status));
    return data;
  }

  function setMessage(text, isError) {
    const box = document.getElementById('dispatch-message');
    if (!box) return;
    box.textContent = text || '';
    box.className = 'dispatch-message' + (isError ? ' error' : ' ok');
  }

  function statusBadge(group) {
    if (group.can_dispatch) {
      return '<span class="dispatch-badge ready">可下发</span>';
    }
    const label = group.status === 'SOURCE_CHANGED' ? '源数据待核'
      : group.needs_recheck ? '重新核对' : '暂停下发';
    return '<span class="dispatch-badge held">' + esc(label) + '</span>';
  }

  function reasonText(group) {
    const reasons = (group.reason_codes || []).map(function (code) {
      const hold = (group.active_holds || []).find(function (item) { return item.code === code; });
      return hold && hold.reason ? hold.reason : code;
    });
    return reasons.length ? reasons.join('；') : '—';
  }

  function correctionText(group) {
    const ids = group.pending_correction_ids || [];
    return ids.length ? ids.map(esc).join('、') : '无';
  }

  function certText(group) {
    const cert = group.original_credential || group.credential;
    return cert ? '<code title="' + esc(cert.credential_sha256 || '') + '">' +
      esc((cert.credential_sha256 || '').slice(0, 12)) + '</code>' : '未签发';
  }

  function rowHtml(group, detailed) {
    const actions = detailed ? '<td>' + (group.can_dispatch
      ? '<button type="button" data-dispatch="' + esc(group.group_id) + '">确认下发</button>'
      : '<button type="button" disabled>不可下发</button>') + '</td>' : '';
    return '<tr data-group="' + esc(group.group_id) + '">' +
      '<td><strong>' + esc(group.group_id) + '</strong></td>' +
      '<td>' + statusBadge(group) + '</td>' +
      '<td>' + esc(reasonText(group)) + '</td>' +
      '<td>' + esc(correctionText(group)) + '</td>' +
      (detailed ? '<td>' + certText(group) + '</td>' : '') +
      actions +
      '</tr>';
  }

  function render(board) {
    const groups = board.groups || [];
    const mini = document.getElementById('dispatch-mini-body');
    if (mini) {
      mini.innerHTML = groups.length ? groups.map(function (g) { return rowHtml(g, false); }).join('')
        : '<tr><td colspan="4" class="empty">暂无下发组，点击“载入样例下发组”</td></tr>';
    }
    const body = document.getElementById('dispatch-body');
    if (body) {
      body.innerHTML = groups.length ? groups.map(function (g) { return rowHtml(g, true); }).join('')
        : '<tr><td colspan="6" class="empty">暂无下发组</td></tr>';
    }
    const summary = board.summary || {};
    [['dispatch-total', summary.total], ['dispatch-ready', summary.dispatchable],
     ['dispatch-held', summary.held], ['dispatch-recheck', summary.needs_recheck]].forEach(function (pair) {
      const el = document.getElementById(pair[0]);
      if (el) el.textContent = pair[1] == null ? '--' : pair[1];
    });
    const readyList = document.getElementById('dispatch-ready-list');
    if (readyList) {
      const ids = board.dispatchable_group_ids || [];
      readyList.innerHTML = ids.length ? ids.map(function (id) {
        return '<li class="check-ok">' + esc(id) + ' · 还能下发</li>';
      }).join('') : '<li class="empty">当前没有可下发组</li>';
    }
  }

  async function refresh(runId) {
    try {
      const url = runId ? '/api/runs/' + encodeURIComponent(runId) + '/dispatch' : '/api/dispatch/board';
      render(await request(url));
    } catch (error) { setMessage(error.message, true); }
  }

  async function demo() {
    try {
      setMessage('');
      render(await request('/api/dispatch/demo', { method: 'POST', body: '{}' }));
      setMessage('已载入三个理赔组；它们初始均可下发。');
    } catch (error) { setMessage(error.message, true); }
  }

  async function submitSource(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      update_id: form.update_id.value || undefined,
      group_id: form.group_id.value,
      reason: form.reason.value || undefined,
    };
    try {
      await request('/api/source-updates', { method: 'POST', body: JSON.stringify(payload) });
      setMessage('源数据更新已登记，受影响组已暂停下发。');
      form.reset();
      await refresh();
    } catch (error) { setMessage(error.message, true); }
  }

  async function submitCorrection(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      correction_id: form.correction_id.value || undefined,
      claim_id: form.claim_id.value || undefined,
      group_id: form.group_id.value || undefined,
      new_member_id: form.new_member_id.value || undefined,
      new_service_date: form.new_service_date.value || undefined,
      reason: form.reason.value || undefined,
    };
    try {
      const result = await request('/api/corrections', { method: 'POST', body: JSON.stringify(payload) });
      setMessage('补正 ' + result.correction_id + ' 已登记，相关组进入重新核对。');
      form.reset();
      await refresh();
    } catch (error) { setMessage(error.message, true); }
  }

  async function withdraw(id) {
    try {
      await request('/api/corrections/' + encodeURIComponent(id) + '/withdraw', { method: 'POST', body: '{}' });
      setMessage('补正 ' + id + ' 已撤回，原签发内容已恢复。');
      await refresh();
    } catch (error) { setMessage(error.message, true); }
  }

  window.dispatchWorkbench = {
    refresh: refresh,
    showRun: refresh,
    render: render,
  };

  document.addEventListener('DOMContentLoaded', function () {
    if (!document.getElementById('dispatch-mini-body') && !document.getElementById('dispatch-body')) return;
    refresh();
    const demoButton = document.getElementById('dispatch-demo');
    const refreshButton = document.getElementById('dispatch-refresh');
    if (demoButton) demoButton.addEventListener('click', demo);
    if (refreshButton) refreshButton.addEventListener('click', refresh);
    document.addEventListener('submit', function (event) {
      if (event.target.id === 'source-update-form') submitSource(event);
      if (event.target.id === 'correction-form') submitCorrection(event);
    });
    document.addEventListener('click', function (event) {
      const withdrawId = event.target.getAttribute && event.target.getAttribute('data-withdraw');
      if (withdrawId) withdraw(withdrawId);
      const groupId = event.target.getAttribute && event.target.getAttribute('data-dispatch');
      if (groupId) {
        request('/api/groups/' + encodeURIComponent(groupId) + '/dispatch', {
          method: 'POST', body: JSON.stringify({ dispatch_id: 'UI-' + Date.now() })
        }).then(function () { setMessage(groupId + ' 已确认下发。'); return refresh(); })
          .catch(function (error) { setMessage(error.message, true); });
      }
    });
  });
})();
