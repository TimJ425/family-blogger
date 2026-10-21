/* Family Blogger: small progressive enhancements (vanilla JS) */
(function () {
  'use strict';

  // Reactions: post with fetch, update in place. Falls back to normal form post on error.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.classList || !form.classList.contains('reaction-form') || !window.fetch) return;
    e.preventDefault();
    var btn = form.querySelector('button');
    btn.disabled = true;
    fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json' },
      credentials: 'same-origin'
    }).then(function (r) {
      if (!r.ok) throw new Error('bad status');
      return r.json();
    }).then(function (data) {
      var bar = form.closest('.reactions');
      bar.querySelectorAll('.reaction-form').forEach(function (f) {
        var b = f.querySelector('button');
        var kind = f.getAttribute('data-kind');
        if (data.counts && kind in data.counts) b.querySelector('.count').textContent = data.counts[kind];
        if (kind === data.kind) {
          b.classList.toggle('is-active', !!data.reacted);
          b.setAttribute('aria-pressed', data.reacted ? 'true' : 'false');
        }
      });
      btn.disabled = false;
    }).catch(function () {
      form.submit();
    });
  });

  // Confirm on forms flagged with data-confirm
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Image previews for file inputs with data-preview="<container id>".
  // Several inputs may share one preview box (e.g. camera + library): it shows the files of all of them.
  function renderPreview(boxId) {
    var box = document.getElementById(boxId);
    if (!box) return;
    box.innerHTML = '';
    document.querySelectorAll('input[type=file][data-preview="' + boxId + '"]').forEach(function (input) {
      Array.prototype.forEach.call(input.files, function (file) {
        if (!file.type || file.type.indexOf('image/') !== 0) return;
        var img = document.createElement('img');
        img.alt = 'Preview of ' + file.name;
        img.src = URL.createObjectURL(file);
        img.onload = function () { URL.revokeObjectURL(img.src); };
        box.appendChild(img);
      });
    });
  }
  document.querySelectorAll('input[type=file][data-preview]').forEach(function (input) {
    input.addEventListener('change', function () { renderPreview(input.getAttribute('data-preview')); });
  });

  // "Me" sheet in the phone tab bar (without JS the #me-sheet link opens it via :target)
  var sheet = document.getElementById('me-sheet');
  var meTab = document.querySelector('[data-sheet-open]');
  if (sheet && meTab) {
    var setSheet = function (open) {
      sheet.classList.toggle('is-open', open);
      meTab.classList.toggle('is-open', open);
      meTab.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { var first = sheet.querySelector('a[href], button'); if (first && first.focus) first.focus(); }
      else if (location.hash === '#me-sheet') history.replaceState(null, '', location.pathname + location.search);
    };
    meTab.addEventListener('click', function (e) { e.preventDefault(); setSheet(!sheet.classList.contains('is-open')); });
    sheet.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('[data-sheet-close]')) { e.preventDefault(); setSheet(false); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && sheet.classList.contains('is-open')) { setSheet(false); meTab.focus(); }
    });
  }

  // Pull-to-refresh on the home feed. iOS home-screen apps have no native one, so this only
  // runs when installed (standalone), on the feed (the .ptr indicator exists) and when scrolled to the top.
  var ptr = document.querySelector('[data-ptr]');
  var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
  if (ptr && standalone) {
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var THRESHOLD = 80, startY = null, pull = 0, busy = false;
    var show = function (dist) {
      var p = Math.min(dist, THRESHOLD * 1.5);
      ptr.style.opacity = Math.min(1, p / THRESHOLD);
      ptr.style.transform = calm ? 'none' : 'translateY(' + (p - 90 + 40) + 'px) rotate(' + (p * 4) + 'deg)';
      ptr.classList.toggle('is-ready', dist >= THRESHOLD);
    };
    var reset = function () {
      ptr.style.opacity = ''; ptr.style.transform = ''; ptr.classList.remove('is-ready'); pull = 0; startY = null;
    };
    document.addEventListener('touchstart', function (e) {
      if (busy || e.touches.length !== 1 || (window.pageYOffset || document.documentElement.scrollTop) > 0) { startY = null; return; }
      startY = e.touches[0].clientY; pull = 0;
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (startY === null || busy) return;
      var dy = e.touches[0].clientY - startY;
      if (dy <= 0 || (window.pageYOffset || document.documentElement.scrollTop) > 0) { reset(); return; }
      pull = dy * 0.5;
      if (e.cancelable) e.preventDefault();
      show(pull);
    }, { passive: false });
    document.addEventListener('touchend', function () {
      if (startY === null || busy) return;
      if (pull >= THRESHOLD) {
        busy = true;
        ptr.classList.add('is-loading');
        ptr.style.opacity = '1'; ptr.style.transform = calm ? 'none' : 'translateY(0)';
        window.location.reload();
      } else {
        reset();
      }
    }, { passive: true });
    document.addEventListener('touchcancel', reset, { passive: true });
  }
})();
