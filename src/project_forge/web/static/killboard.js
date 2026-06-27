// Kill Board page interactions. Loaded from /killboard.
// CSP-compliant: no inline scripts, no innerHTML on LLM data.
(function () {
    'use strict';

    function getToken() {
        var meta = document.querySelector('meta[name="forge-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function renderPremortem(resultArea, pm) {
        var inner = resultArea.querySelector('.killboard-result-inner');
        if (!inner) return;
        inner.textContent = '';

        function addLine(label, text) {
            if (!text) return;
            var p = document.createElement('p');
            var strong = document.createElement('strong');
            strong.textContent = label + ': ';
            p.appendChild(strong);
            p.appendChild(document.createTextNode(text));
            inner.appendChild(p);
        }

        function addList(label, items) {
            if (!items || !items.length) return;
            var heading = document.createElement('p');
            var strong = document.createElement('strong');
            strong.textContent = label + ':';
            heading.appendChild(strong);
            inner.appendChild(heading);
            var ul = document.createElement('ul');
            items.forEach(function (item) {
                var li = document.createElement('li');
                li.textContent = item;
                ul.appendChild(li);
            });
            inner.appendChild(ul);
        }

        var odds = typeof pm.survival_odds === 'number' ? pm.survival_odds : 0;
        var pct = Math.round(odds * 100);
        addLine('Survival odds', pct + '%');
        addLine('Case against', pm.case_against);
        addLine('Why now is wrong', pm.why_now_wrong);
        addList('Fatal risks', pm.fatal_risks);
        addList('Already doing it', pm.whos_already_doing_it);
    }

    document.querySelectorAll('.killboard-run-btn').forEach(function (btn) {
        btn.addEventListener('click', async function () {
            var ideaId = btn.dataset.ideaId;
            if (!ideaId) return;

            var resultArea = document.getElementById('pm-result-' + ideaId);
            if (!resultArea) return;

            btn.disabled = true;
            btn.textContent = 'Running...';
            resultArea.style.display = 'block';

            var inner = resultArea.querySelector('.killboard-result-inner');
            if (inner) inner.textContent = 'Analysing...';

            try {
                var headers = { 'Content-Type': 'application/json' };
                var token = getToken();
                if (token) headers['X-Forge-Token'] = token;

                var resp = await fetch('/api/premortem/' + ideaId, {
                    method: 'POST',
                    headers: headers
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                renderPremortem(resultArea, data);
            } catch (e) {
                var errEl = resultArea.querySelector('.killboard-result-inner');
                if (errEl) errEl.textContent = 'Error: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.textContent = '☠ Re-run Pre-Mortem';
            }
        });
    });
})();
