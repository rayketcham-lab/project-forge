// Idea-detail engine tools: Launchpad (GTM brief) + Recruiter (build estimate).
// CSP-safe: no inline scripts, no innerHTML on response data.
(function () {
    'use strict';

    var resultEl = document.getElementById('engine-tools-result');

    function render(obj) {
        resultEl.hidden = false;
        resultEl.textContent = JSON.stringify(obj, null, 2);
    }

    function wire(btnId, url, label) {
        var btn = document.getElementById(btnId);
        if (!btn) return;
        btn.addEventListener('click', async function () {
            var id = btn.getAttribute('data-idea-id');
            if (!id) return;
            var prev = btn.textContent;
            btn.disabled = true;
            btn.textContent = 'generating ' + label + '…';
            resultEl.hidden = false;
            resultEl.textContent = 'asking the engine…';
            try {
                var headers = (typeof getAuthHeaders === 'function') ? getAuthHeaders() : {};
                var resp = await fetch(url + encodeURIComponent(id), { method: 'POST', headers: headers });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                render(await resp.json());
            } catch (e) {
                resultEl.hidden = false;
                resultEl.textContent = 'failed: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.textContent = prev;
            }
        });
    }

    wire('launchpad-btn', '/api/launchpad/', 'GTM brief');
    wire('recruiter-btn', '/api/recruiter/', 'estimate');
})();
