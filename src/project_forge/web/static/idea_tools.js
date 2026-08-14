// Idea-detail engine tools: Launchpad (GTM brief) + Recruiter (build estimate).
// CSP-safe: no inline scripts, every node built with DOM APIs — never markup strings.
(function () {
    'use strict';

    var resultEl = document.getElementById('engine-tools-result');
    if (!resultEl) return;

    var BRIEF_KEYS = [
        'positioning', 'icp', 'first_ten_customers', 'channels',
        'pricing', 'landing_headline', 'cold_open', 'launch_checklist'
    ];
    var ESTIMATE_KEYS = [
        'roles', 'total_person_weeks', 'skills',
        'cost_band', 'complexity', 'timeline_weeks'
    ];

    // ----------------------------------------------------------- DOM helpers

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined && text !== null && text !== '') node.textContent = String(text);
        return node;
    }

    function clear() {
        while (resultEl.firstChild) resultEl.removeChild(resultEl.firstChild);
    }

    function status(text, cls) {
        clear();
        resultEl.hidden = false;
        resultEl.appendChild(el('p', cls || 'et-status', text));
    }

    function append(node) {
        if (node) resultEl.appendChild(node);
    }

    function humanize(key) {
        return key.replace(/^_+/, '').replace(/_/g, ' ').replace(/^./, function (c) {
            return c.toUpperCase();
        });
    }

    // -------------------------------------------------------------- sections

    function head(title, badge) {
        var wrap = el('div', 'et-head');
        wrap.appendChild(el('h3', 'et-title', title));
        if (badge) wrap.appendChild(el('span', 'et-badge', badge));
        return wrap;
    }

    function section(title, cls) {
        var sec = el('section', cls ? 'et-section ' + cls : 'et-section');
        sec.appendChild(el('h4', 'et-label', title));
        return sec;
    }

    function paraSection(title, text) {
        if (text === undefined || text === null || text === '') return null;
        var sec = section(title);
        sec.appendChild(el('p', 'et-para', text));
        return sec;
    }

    function listSection(title, items, ordered, cls) {
        if (!Array.isArray(items) || !items.length) return null;
        var sec = section(title);
        var list = el(ordered ? 'ol' : 'ul', cls ? 'et-list ' + cls : 'et-list');
        items.forEach(function (item) {
            list.appendChild(el('li', 'et-item', item));
        });
        sec.appendChild(list);
        return sec;
    }

    function copySection(title, text) {
        if (!text) return null;
        var sec = section(title);
        var box = el('div', 'et-quote');
        box.appendChild(el('p', 'et-para', text));
        var btn = el('button', 'et-copy', 'Copy');
        btn.type = 'button';
        btn.addEventListener('click', function () {
            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                btn.textContent = 'Copy unavailable';
                return;
            }
            navigator.clipboard.writeText(text).then(function () {
                btn.textContent = 'Copied';
                setTimeout(function () { btn.textContent = 'Copy'; }, 1600);
            }, function () {
                btn.textContent = 'Copy failed';
            });
        });
        box.appendChild(btn);
        sec.appendChild(box);
        return sec;
    }

    function statRow(pairs) {
        var row = el('div', 'et-stats');
        var filled = 0;
        pairs.forEach(function (pair) {
            var value = pair[1];
            if (value === undefined || value === null || value === '') return;
            var tile = el('div', 'et-stat');
            tile.appendChild(el('span', 'et-stat-value', value));
            tile.appendChild(el('span', 'et-stat-label', pair[0]));
            row.appendChild(tile);
            filled += 1;
        });
        return filled ? row : null;
    }

    function roleTable(roles) {
        if (!Array.isArray(roles) || !roles.length) return null;
        var sec = section('Team');
        var table = el('table', 'et-table');
        var thead = document.createElement('thead');
        var headRow = document.createElement('tr');
        ['Role', 'Count', 'Weeks'].forEach(function (label) {
            headRow.appendChild(el('th', null, label));
        });
        thead.appendChild(headRow);
        table.appendChild(thead);
        var tbody = document.createElement('tbody');
        roles.forEach(function (role) {
            var tr = document.createElement('tr');
            tr.appendChild(el('td', null, role.role));
            tr.appendChild(el('td', 'et-num', role.count));
            tr.appendChild(el('td', 'et-num', role.weeks));
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        sec.appendChild(table);
        return sec;
    }

    function tagSection(title, items) {
        if (!Array.isArray(items) || !items.length) return null;
        var sec = section(title);
        var tags = el('div', 'et-tags');
        items.forEach(function (item) {
            tags.appendChild(el('span', 'et-tag', item));
        });
        sec.appendChild(tags);
        return sec;
    }

    // Anything the engine adds later still shows up rather than vanishing.
    function appendExtras(obj, known) {
        Object.keys(obj).forEach(function (key) {
            if (known.indexOf(key) !== -1) return;
            var value = obj[key];
            append(Array.isArray(value)
                ? listSection(humanize(key), value)
                : paraSection(humanize(key), value));
        });
    }

    // ------------------------------------------------------------- renderers

    function renderBrief(brief) {
        clear();
        resultEl.hidden = false;
        append(head('🚀 Go-to-Market Brief', brief._backend));
        if (brief.landing_headline) {
            append(el('p', 'et-hero', '“' + brief.landing_headline + '”'));
        }
        append(paraSection('Positioning', brief.positioning));
        append(paraSection('Ideal customer profile', brief.icp));
        append(paraSection('Pricing', brief.pricing));
        append(listSection('Channels', brief.channels));
        append(listSection('First 10 customers', brief.first_ten_customers, true, 'et-steps'));
        append(listSection('Launch checklist', brief.launch_checklist, false, 'et-checks'));
        append(copySection('Cold open', brief.cold_open));
        appendExtras(brief, BRIEF_KEYS.concat(['_backend']));
    }

    function renderEstimate(est) {
        clear();
        resultEl.hidden = false;
        append(head('🤖 Build Estimate', est._backend));
        append(statRow([
            ['Complexity', est.complexity === undefined ? '' : est.complexity + '/5'],
            ['Timeline', est.timeline_weeks === undefined ? '' : est.timeline_weeks + ' wks'],
            ['Person-weeks', est.total_person_weeks],
            ['Cost band', est.cost_band]
        ]));
        append(roleTable(est.roles));
        append(tagSection('Skills needed', est.skills));
        appendExtras(est, ESTIMATE_KEYS.concat(['_backend']));
    }

    // ------------------------------------------------------------------ wire

    function wire(btnId, url, label, render) {
        var btn = document.getElementById(btnId);
        if (!btn) return;
        btn.addEventListener('click', async function () {
            var id = btn.getAttribute('data-idea-id');
            if (!id) return;
            var prev = btn.textContent;
            btn.disabled = true;
            btn.textContent = 'generating ' + label + '…';
            status('asking the engine…');
            try {
                var headers = (typeof getAuthHeaders === 'function') ? getAuthHeaders() : {};
                var resp = await fetch(url + encodeURIComponent(id), { method: 'POST', headers: headers });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                render(await resp.json());
            } catch (e) {
                status('failed: ' + e.message, 'et-error');
            } finally {
                btn.disabled = false;
                btn.textContent = prev;
            }
        });
    }

    wire('launchpad-btn', '/api/launchpad/', 'GTM brief', renderBrief);
    wire('recruiter-btn', '/api/recruiter/', 'estimate', renderEstimate);
})();
