// Forge Mechanic review panel. Loaded from /mechanic.
// The operator's gate: approve (merge) or reject (close) autonomous PRs.
// CSP-compliant: no inline scripts, no innerHTML on server data.
(function () {
    'use strict';

    function headers() {
        return Object.assign(
            { 'Content-Type': 'application/json' },
            typeof getAuthHeaders === 'function' ? getAuthHeaders() : {}
        );
    }

    var statusEl = document.getElementById('mechanic-run-status');

    function setStatus(msg, kind) {
        if (!statusEl) return;
        statusEl.textContent = msg;
        statusEl.className = 'churn-status churn-status-' + kind;
    }

    async function act(number, action, btn, verb) {
        var prev = btn.textContent;
        btn.disabled = true;
        btn.textContent = verb + '...';
        setStatus('PR #' + number + ': ' + verb + '...', 'loading');
        try {
            var resp = await fetch('/api/mechanic/prs/' + encodeURIComponent(number) + '/' + action, {
                method: 'POST',
                headers: headers(),
            });
            if (!resp.ok) {
                var err = await resp.json().catch(function () { return {}; });
                throw new Error(err.detail || ('HTTP ' + resp.status));
            }
            btn.textContent = '✓ ' + verb + 'd';
            setStatus('PR #' + number + ' ' + verb + 'd — reloading...', 'success');
            var card = btn.closest('.moneybot-card');
            if (card) { card.style.opacity = '0.5'; }
            setTimeout(function () { window.location.reload(); }, 1000);
        } catch (e) {
            // Persist the full reason in the status line (not the tiny button)
            // so it's readable and stays put — you asked for real detail.
            btn.textContent = prev;
            btn.disabled = false;
            setStatus('PR #' + number + ' ' + verb + ' failed: ' + e.message, 'error');
        }
    }

    // === Run mechanic now + LIVE animated status (a run takes minutes) ===
    var runBtn = document.getElementById('mechanic-run-btn');
    var pollTimer = null, animTimer = null, animFrame = 0, lastData = null, startedAt = 0;
    var SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

    function stopTimers() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        if (animTimer) { clearInterval(animTimer); animTimer = null; }
    }

    function elapsed() {
        if (!startedAt) return '';
        var s = Math.round((Date.now() - startedAt) / 1000);
        return ' · ' + (s < 60 ? s + 's' : Math.floor(s / 60) + 'm' + ('0' + (s % 60)).slice(-2) + 's');
    }

    function renderStatus(data, spinning) {
        if (!statusEl) return;
        var spin = spinning ? SPINNER[animFrame % SPINNER.length] + ' ' : '';
        var tail = '';
        if (data.terminal && data.detail && data.stage !== 'pr_opened') { tail = ' — ' + data.detail; }
        else if (!data.terminal) { tail = elapsed(); }
        statusEl.textContent = spin + (data.message || data.stage || '') + tail;
        var kind = data.stage === 'pr_opened' ? 'success'
            : (data.terminal && data.stage !== 'idle') ? 'error' : 'loading';
        statusEl.className = 'churn-status churn-status-' + kind;
    }

    async function pollOnce() {
        try {
            var resp = await fetch('/api/mechanic/status', { headers: headers() });
            if (!resp.ok) return;
            lastData = await resp.json();
            renderStatus(lastData, !lastData.terminal);
            if (lastData.terminal) {
                stopTimers();
                if (runBtn) runBtn.disabled = false;
                if (lastData.stage === 'pr_opened') {
                    setTimeout(function () { window.location.reload(); }, 1800);
                }
            }
        } catch (e) { /* transient — keep polling */ }
    }

    function startPolling() {
        stopTimers();
        if (!startedAt) startedAt = Date.now();
        // Spin between polls so it always looks alive; the 2.5s poll swaps
        // the message at each real stage change.
        animTimer = setInterval(function () {
            animFrame++;
            if (lastData && !lastData.terminal) renderStatus(lastData, true);
        }, 350);
        pollTimer = setInterval(pollOnce, 2500);
        pollOnce();
    }

    if (runBtn && statusEl) {
        runBtn.addEventListener('click', async function () {
            if (!confirm('Run one mechanic cycle now? It implements the top Think Tank item on your subscription and opens a PR here. Takes several minutes — leave the page open.')) return;
            runBtn.disabled = true;
            startedAt = Date.now();
            setStatus('⏱ starting — this takes a few minutes (clone → Claude implements → full test suite → PR). Leave this open; the PR appears below when done.', 'loading');
            try {
                var resp = await fetch('/api/mechanic/run', { method: 'POST', headers: headers() });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                startPolling();
            } catch (e) {
                setStatus('failed to start: ' + e.message, 'error');
                runBtn.disabled = false;
            }
        });
    }

    // Resume the live status if a run is already in progress on page load.
    (async function () {
        await pollOnce();
        if (lastData && !lastData.terminal && lastData.stage !== 'idle') {
            if (runBtn) runBtn.disabled = true;
            startedAt = Date.now();
            startPolling();
        }
    })();

    document.querySelectorAll('.mechanic-approve').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var n = btn.getAttribute('data-pr-number');
            if (!n) return;
            if (!confirm('Squash-merge PR #' + n + ' to main? This ships the change.')) return;
            act(n, 'approve', btn, 'merge');
        });
    });

    document.querySelectorAll('.mechanic-reject').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var n = btn.getAttribute('data-pr-number');
            if (!n) return;
            if (!confirm('Reject and close PR #' + n + '?')) return;
            act(n, 'reject', btn, 'reject');
        });
    });
})();
