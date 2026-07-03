// Missions page interactions (v0.18, #84). Loaded from /missions.
// CSP-compliant: no inline scripts, no innerHTML on LLM data.
(function() {
    'use strict';

    function headers() {
        return Object.assign(
            {'Content-Type': 'application/json'},
            (typeof getAuthHeaders === 'function' ? getAuthHeaders() : {})
        );
    }

    // ── Create form ─────────────────────────────────────────────────
    var createBtn = document.getElementById('mission-create-btn');
    var createStatus = document.getElementById('mission-create-status');

    if (createBtn && createStatus) {
        createBtn.addEventListener('click', async function() {
            var title = (document.getElementById('mission-title').value || '').trim();
            var brief = (document.getElementById('mission-brief').value || '').trim();
            var urlsRaw = (document.getElementById('mission-urls').value || '').trim();
            var category = document.getElementById('mission-category').value || null;
            var urls = urlsRaw ? urlsRaw.split('\n').map(function(u) { return u.trim(); }).filter(Boolean) : [];

            if (!title) {
                createStatus.textContent = 'give the mission a title';
                createStatus.className = 'churn-status churn-status-error';
                return;
            }
            if (brief.length < 10) {
                createStatus.textContent = 'brief is too short — say what actually matters';
                createStatus.className = 'churn-status churn-status-error';
                return;
            }
            if (urls.length > 3) {
                createStatus.textContent = 'at most 3 grounding URLs';
                createStatus.className = 'churn-status churn-status-error';
                return;
            }

            createBtn.disabled = true;
            createStatus.textContent = 'creating mission...';
            createStatus.className = 'churn-status churn-status-loading';
            try {
                var resp = await fetch('/api/missions', {
                    method: 'POST',
                    headers: headers(),
                    body: JSON.stringify({ title: title, brief: brief, urls: urls, category: category })
                });
                if (!resp.ok) {
                    var err = await (typeof safeJson === 'function' ? safeJson(resp) : resp.json());
                    var detail = (err && err.detail) ? JSON.stringify(err.detail) : ('HTTP ' + resp.status);
                    throw new Error(detail);
                }
                createStatus.textContent = 'mission created — reloading...';
                createStatus.className = 'churn-status churn-status-success';
                setTimeout(function() { window.location.reload(); }, 600);
            } catch (e) {
                createStatus.textContent = 'failed: ' + e.message;
                createStatus.className = 'churn-status churn-status-error';
                createBtn.disabled = false;
            }
        });
    }

    // ── Per-mission actions (generate / pause / resume / archive) ───
    function cardStatusEl(btn) {
        var card = btn.closest('.mission-card');
        return card ? card.querySelector('.mission-card-status') : null;
    }

    async function generateFor(btn, missionId) {
        var statusEl = cardStatusEl(btn);
        btn.disabled = true;
        if (statusEl) {
            statusEl.textContent = 'anchoring a generation to this brief...';
            statusEl.className = 'churn-status mission-card-status churn-status-loading';
        }
        try {
            var resp = await fetch('/api/missions/' + missionId + '/generate', {
                method: 'POST',
                headers: headers()
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            if (statusEl) {
                statusEl.textContent = '';
                if (data.idea) {
                    var strong = document.createElement('strong');
                    strong.textContent = data.idea.name;
                    statusEl.appendChild(strong);
                    var tail = document.createElement('span');
                    tail.textContent = data.saved ? ' — saved' : (' — rejected: ' + (data.reason || 'duplicate'));
                    statusEl.appendChild(tail);
                    statusEl.className = 'churn-status mission-card-status ' +
                        (data.saved ? 'churn-status-success' : 'churn-status-error');
                    if (data.saved) {
                        setTimeout(function() { window.location.reload(); }, 1500);
                    }
                } else {
                    statusEl.textContent = data.message || 'no idea produced';
                    statusEl.className = 'churn-status mission-card-status churn-status-error';
                }
            }
        } catch (e) {
            if (statusEl) {
                statusEl.textContent = 'failed: ' + e.message;
                statusEl.className = 'churn-status mission-card-status churn-status-error';
            }
        } finally {
            btn.disabled = false;
        }
    }

    async function setStatus(btn, missionId, status) {
        btn.disabled = true;
        try {
            var resp = await fetch('/api/missions/' + missionId + '/status', {
                method: 'POST',
                headers: headers(),
                body: JSON.stringify({ status: status })
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            window.location.reload();
        } catch (e) {
            var statusEl = cardStatusEl(btn);
            if (statusEl) {
                statusEl.textContent = 'failed: ' + e.message;
                statusEl.className = 'churn-status mission-card-status churn-status-error';
            }
            btn.disabled = false;
        }
    }

    document.addEventListener('click', function(ev) {
        var btn = ev.target.closest('button[data-action][data-mission-id]');
        if (!btn) return;
        var action = btn.dataset.action;
        var missionId = btn.dataset.missionId;
        if (action === 'generate') {
            generateFor(btn, missionId);
        } else {
            setStatus(btn, missionId, action);
        }
    });
})();
