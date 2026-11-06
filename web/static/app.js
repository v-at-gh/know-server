(function() {
  'use strict';

  let currentTaskId = null;
  let currentPollTimer = null;

  function el(id) { return document.getElementById(id); }

  function setStatus(status, cls) {
    const s = el('task-status');
    if (!s) return;
    s.textContent = status;
    s.className = 'chip ' + (cls || '');
  }

  function appendLog(text) {
    const log = el('task-log');
    if (!log) return;
    log.textContent += text;
    log.scrollTop = log.scrollHeight;
  }

  function setLog(text) {
    const log = el('task-log');
    if (!log) return;
    log.textContent = text;
    log.scrollTop = log.scrollHeight;
  }

  function showModal() {
    const m = el('task-modal');
    if (m) m.style.display = 'flex';
  }

  function hideModal() {
    const m = el('task-modal');
    if (m) m.style.display = 'none';
  }

  window.closeTaskModal = function() {
    // Hide overlay; polling continues in background if task is running
    hideModal();
  };

  window.openTaskModal = function() {
    // Re-open if task is still running (user closed earlier)
    if (currentTaskId) showModal();
  };

  async function pollTask(taskId) {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      const data = await res.json();

      setLog(data.log || '');
      const statusCls = data.status === 'done' ? 'status-ok'
                     : data.status === 'error' || data.status === 'killed' || data.status === 'interrupted' ? 'status-incomplete'
                     : '';
      setStatus(data.status, statusCls);

      if (data.status === 'queued' || data.status === 'running') {
        currentPollTimer = setTimeout(() => pollTask(taskId), 2000);
      } else {
        currentPollTimer = null;
        // Task done/error/killed/interrupted — stay in current state, user can Hide
      }
    } catch (e) {
      appendLog('\n[ERROR] Lost connection to server: ' + e.message);
      currentPollTimer = setTimeout(() => pollTask(taskId), 5000);
    }
  }

  window.startTask = async function(domain, lang, action) {
    const isTrans = action === 'translate';
    const msg = isTrans
      ? `Start translation for ${domain} -> ${lang.toUpperCase()}?`
      : `Start recovery for ${domain}?`;
    if (!confirm(msg + "\n\nThis runs in the background. You can safely close this page or the browser.")) return;

    showModal();
    setStatus('starting...', '');
    setLog('Initializing...\n');
    const fullLink = el('task-full-link');
    if (fullLink) fullLink.style.display = 'none';

    const fd = new FormData();
    fd.append('domain', domain);
    fd.append('lang', lang);
    fd.append('action', action);

    try {
      const res = await fetch('/api/tasks/start', {
        method: 'POST', body: fd,
        headers: { 'Accept': 'application/json' },
      });
      const data = await res.json();

      if (data.task_id) {
        currentTaskId = data.task_id;
        if (fullLink) {
          fullLink.href = `/tasks/${data.task_id}`;
          fullLink.style.display = 'inline-block';
        }
        pollTask(data.task_id);
      } else {
        appendLog('Error starting task. Check backend logs.');
        setStatus('error', 'status-incomplete');
      }
    } catch (e) {
      appendLog('Network error: ' + e.message);
      setStatus('error', 'status-incomplete');
    }
  };

  // Backdrop click closes modal (does not stop polling)
  document.addEventListener('click', function(e) {
    const m = el('task-modal');
    if (m && e.target === m) closeTaskModal();
  });

  // Escape key closes modal
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      const m = el('task-modal');
      if (m && m.style.display === 'flex') closeTaskModal();
    }
  });

  window.addEventListener('beforeunload', function(e) {
    if (currentPollTimer) {
      // Task still running — warn before leaving
      e.preventDefault();
      e.returnValue = 'Task is still running. Leave anyway?';
    }
  });
})();
