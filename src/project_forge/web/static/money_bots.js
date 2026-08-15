// Money-bots board interactions. Loaded from /money-bots.
// CSP-compliant: no inline scripts, every node built with DOM APIs.
(function () {
    'use strict';

    function authHeaders() {
        return Object.assign(
            { 'Content-Type': 'application/json' },
            (typeof getAuthHeaders === 'function' ? getAuthHeaders() : {})
        );
    }

    // === Churn Now — runs ONE grounded probe cycle ===
    var btn = document.getElementById('churn-btn');
    var statusEl = document.getElementById('churn-status');

    function renderStrategy(idea) {
        statusEl.textContent = '';
        var check = document.createElement('span');
        check.textContent = '✓ ';
        statusEl.appendChild(check);
        var strong = document.createElement('strong');
        strong.textContent = idea.name;
        statusEl.appendChild(strong);
        var edge = (typeof idea.bot_edge_score === 'number' ? idea.bot_edge_score : 0).toFixed(2);
        var meta = document.createElement('span');
        meta.textContent = ' — edge ' + edge + ' · ' + (idea.venue || 'unknown venue');
        statusEl.appendChild(meta);
        statusEl.className = 'churn-status churn-status-success';
    }

    if (btn && statusEl) {
        btn.addEventListener('click', async function () {
            btn.disabled = true;
            btn.classList.add('is-loading');
            // The probe sweeps venues, generates, red-teams and gates — slow
            // on purpose, so say so rather than looking hung.
            statusEl.textContent = 'probing venues, working one program…';
            statusEl.className = 'churn-status churn-status-loading';
            try {
                var resp = await fetch('/api/churn', {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify({ lab: 'money' })
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                if (data.idea) {
                    renderStrategy(data.idea);
                    setTimeout(function () { window.location.reload(); }, 1500);
                } else {
                    // A quiet cycle is the designed outcome, not a failure.
                    statusEl.textContent = 'stored nothing: ' + (data.message || 'no reason given');
                    statusEl.className = 'churn-status';
                }
            } catch (e) {
                statusEl.textContent = 'failed: ' + e.message;
                statusEl.className = 'churn-status churn-status-error';
            } finally {
                btn.disabled = false;
                btn.classList.remove('is-loading');
            }
        });
    }

    // === Live run tail ===
    // A cycle takes 5-12 minutes. Without this the page shows a spinner and
    // the operator cannot tell a working engine from a hung one.
    var tailBlock = document.getElementById('run-tail');
    var tailList = document.getElementById('run-events');
    var tailStatus = document.getElementById('run-tail-status');
    var pollTimer = null;

    function renderTail(data) {
        if (!tailList || !tailStatus) return;

        tailStatus.textContent = data.running
            ? 'running · ' + Math.round(data.elapsed_seconds) + 's'
            : (data.outcome || 'idle');

        tailList.textContent = '';
        (data.events || []).forEach(function (ev) {
            var li = document.createElement('li');
            li.className = 'run-event run-event-' + (ev.stage || 'step');
            var stage = document.createElement('span');
            stage.className = 'run-stage';
            stage.textContent = ev.stage;
            li.appendChild(stage);
            var detail = document.createElement('span');
            detail.className = 'run-detail';
            detail.textContent = ev.detail || '';
            li.appendChild(detail);
            tailList.appendChild(li);
        });
        // Newest work is at the bottom; keep it in view while open.
        if (tailBlock && tailBlock.open) {
            tailList.scrollTop = tailList.scrollHeight;
        }
    }

    async function pollProgress() {
        try {
            var resp = await fetch('/api/money-bots/progress');
            if (!resp.ok) return false;
            var data = await resp.json();
            renderTail(data);
            return data.running;
        } catch (e) {
            return false;
        }
    }

    function startPolling() {
        if (pollTimer) return;
        if (tailBlock) tailBlock.open = true;
        pollProgress();
        pollTimer = setInterval(async function () {
            var stillRunning = await pollProgress();
            if (!stillRunning) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
        }, 3000);
    }

    // Show the last run's tail on load, and resume polling if one is live.
    pollProgress().then(function (running) { if (running) startPolling(); });

    // === Per-card Scaffold bot ===
    var scaffoldStatus = document.getElementById('scaffold-status');

    function reportScaffold(data) {
        if (!scaffoldStatus) return;
        scaffoldStatus.textContent = '';
        var strong = document.createElement('strong');
        strong.textContent = '✓ ' + data.repo_name;
        scaffoldStatus.appendChild(strong);
        var where = document.createElement('span');
        where.textContent = ' scaffolded to ' + data.path + ' (' + data.files.length + ' files). ';
        scaffoldStatus.appendChild(where);
        var next = document.createElement('em');
        next.textContent = data.next_step || '';
        scaffoldStatus.appendChild(next);
        scaffoldStatus.className = 'churn-status churn-status-success';
    }

    document.querySelectorAll('.scaffold-bot-btn').forEach(function (scaffoldBtn) {
        scaffoldBtn.addEventListener('click', async function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var ideaId = scaffoldBtn.getAttribute('data-idea-id');
            if (!ideaId) return;
            scaffoldBtn.disabled = true;
            var prev = scaffoldBtn.textContent;
            scaffoldBtn.textContent = 'scaffolding…';
            try {
                var resp = await fetch('/api/scaffold-bot/' + encodeURIComponent(ideaId), {
                    method: 'POST',
                    headers: authHeaders()
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                reportScaffold(await resp.json());
                scaffoldBtn.textContent = '✓ scaffolded';
            } catch (e) {
                scaffoldBtn.textContent = 'failed: ' + e.message;
                setTimeout(function () {
                    scaffoldBtn.textContent = prev;
                    scaffoldBtn.disabled = false;
                }, 2500);
            }
        });
    });
})();
