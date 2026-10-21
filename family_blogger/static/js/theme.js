/* Live theme preview + onboarding wizard stepping */
(function () {
  'use strict';
  var root = document.documentElement;
  var picker = document.querySelector('[data-theme-picker]');

  if (picker) {
    var presets = {};
    try { presets = JSON.parse(picker.getAttribute('data-presets') || '{}'); } catch (e) {}
    var names = ['primary', 'accent', 'bg', 'card', 'text'];
    var inputs = {};
    names.forEach(function (n) { inputs[n] = picker.querySelector('#colour_' + n); });
    var custom = picker.querySelector('#custom_colours');

    function apply(vals) {
      names.forEach(function (n) { if (vals[n]) root.style.setProperty('--fb-' + n, vals[n]); });
    }
    function current() {
      var v = {};
      names.forEach(function (n) { if (inputs[n]) v[n] = inputs[n].value; });
      return v;
    }

    picker.querySelectorAll('input[name=theme_preset]').forEach(function (r) {
      r.addEventListener('change', function () {
        var p = presets[r.value];
        if (!p) return;
        names.forEach(function (n) { if (inputs[n]) inputs[n].value = p[n]; });
        if (custom) custom.checked = false;
        apply(p);
      });
    });
    names.forEach(function (n) {
      if (!inputs[n]) return;
      inputs[n].addEventListener('input', function () {
        if (custom) custom.checked = true;
        apply(current());
      });
    });
    if (custom) {
      custom.addEventListener('change', function () {
        if (custom.checked) { apply(current()); return; }
        var sel = picker.querySelector('input[name=theme_preset]:checked');
        if (sel && presets[sel.value]) {
          var p = presets[sel.value];
          names.forEach(function (n) { if (inputs[n]) inputs[n].value = p[n]; });
          apply(p);
        }
      });
    }
  }

  // Wizard
  var wiz = document.querySelector('[data-wizard]');
  if (wiz) {
    var steps = Array.prototype.slice.call(wiz.querySelectorAll('.wizard-step'));
    var dots = Array.prototype.slice.call(document.querySelectorAll('.wizard-steps li'));
    var prev = wiz.querySelector('[data-prev]');
    var next = wiz.querySelector('[data-next]');
    var i = 0;
    function show(n) {
      i = Math.max(0, Math.min(steps.length - 1, n));
      steps.forEach(function (s, k) { s.hidden = k !== i; });
      dots.forEach(function (d, k) {
        d.classList.toggle('is-current', k === i);
        d.classList.toggle('is-done', k < i);
        if (k === i) d.setAttribute('aria-current', 'step'); else d.removeAttribute('aria-current');
      });
      prev.style.visibility = i === 0 ? 'hidden' : 'visible';
      next.style.display = i === steps.length - 1 ? 'none' : '';
      var h = steps[i].querySelector('h2'); if (h) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
      window.scrollTo({ top: 0 });
    }
    prev.addEventListener('click', function () { show(i - 1); });
    next.addEventListener('click', function () { show(i + 1); });
    // If a field error exists, jump to the step containing it
    var errStep = steps.findIndex(function (s) { return s.querySelector('.errors'); });
    show(errStep > -1 ? errStep : 0);
  }
})();
