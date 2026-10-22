/* Mood line chart (Chart.js) with an HTML table fallback */
(function () {
  'use strict';
  var canvas = document.getElementById('mood-chart');
  if (!canvas) return;
  var fallback = document.getElementById('mood-fallback');
  // Mood labels come from a JSON data block (not an inline script),
  // so the Content-Security-Policy can forbid inline scripts.
  var moods = {};
  var moodData = document.getElementById('mood-data');
  try { moods = JSON.parse(moodData ? moodData.textContent : '{}') || {}; } catch (e) { moods = {}; }
  var byScore = {};
  Object.keys(moods).forEach(function (k) { byScore[moods[k].score] = moods[k]; });

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function showTable(data) {
    var wrap = canvas.closest('.chart-wrap');
    if (wrap) wrap.hidden = true;
    fallback.hidden = false;
    fallback.innerHTML = '';
    if (!data.length) { fallback.textContent = 'No moods recorded yet.'; return; }
    var t = document.createElement('table');
    t.innerHTML = '<thead><tr><th>Date</th><th>Mood</th></tr></thead>';
    var tb = document.createElement('tbody');
    data.forEach(function (row) {
      var tr = document.createElement('tr');
      var m = moods[row[1]];
      [row[0], (m ? m.emoji + ' ' + m.label : row[1])].forEach(function (txt) {
        var td = document.createElement('td'); td.textContent = txt; tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    fallback.appendChild(t);
  }

  fetch(canvas.getAttribute('data-url'), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
    .then(function (r) { if (!r.ok) throw new Error('status'); return r.json(); })
    .then(function (data) {
      if (!data.length) {
        var wrap = canvas.closest('.chart-wrap'); if (wrap) wrap.hidden = true;
        fallback.hidden = false; fallback.textContent = 'No moods recorded yet. Pick a mood when you write a post!';
        return;
      }
      if (typeof window.Chart === 'undefined') { showTable(data); return; }
      var primary = css('--fb-primary'), text = css('--fb-text'), grid = css('--fb-border');
      // One label per day from the first mood to today, with null for
      // days without a mood, so gaps show as gaps (no date adapter is
      // vendored, so this stands in for a time scale).
      var scores = {};
      data.forEach(function (row) { scores[row[0]] = row[2]; });
      var labels = [], values = [];
      var DAY = 86400000;
      function utcDay(iso) { var p = iso.split('-'); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
      var now = new Date();
      var last = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
      var first = utcDay(data[0][0]);
      for (var t = first; t <= last; t += DAY) {
        var iso = new Date(t).toISOString().slice(0, 10);
        labels.push(iso);
        values.push(Object.prototype.hasOwnProperty.call(scores, iso) ? scores[iso] : null);
      }
      new window.Chart(canvas, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Mood', data: values,
            borderColor: primary, backgroundColor: primary, pointRadius: 5, pointHoverRadius: 7,
            tension: 0.3, spanGaps: false
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (c) { var m = byScore[c.parsed.y]; return m ? m.emoji + ' ' + m.label : c.parsed.y; } } }
          },
          scales: {
            y: {
              min: 1, max: 5,
              ticks: { stepSize: 1, color: text, callback: function (v) { var m = byScore[v]; return m ? m.emoji + ' ' + m.label : v; } },
              grid: { color: grid }
            },
            x: { ticks: { color: text, maxRotation: 45, autoSkip: true }, grid: { color: grid } }
          }
        }
      });
    })
    .catch(function () {
      // Could not load data: use server-rendered calendar as the fallback source
      fallback.hidden = false;
      var wrap = canvas.closest('.chart-wrap'); if (wrap) wrap.hidden = true;
      fallback.textContent = 'The mood chart could not be loaded.';
    });

  // If Chart.js is slow/blocked (deferred script failed), table fallback is handled above.
})();
