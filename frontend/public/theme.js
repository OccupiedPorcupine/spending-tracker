/* Applies the saved theme before the first paint (no flash of the wrong colours).
 * Kept in step with src/lib/theme.ts, which applies changes made on the settings page. */
(function () {
  var t = 'system';
  try { t = JSON.parse(localStorage.getItem('spend-theme')) || 'system'; } catch (e) { /* storage unavailable */ }
  var root = document.documentElement;
  if (t === 'light' || t === 'dark') root.setAttribute('data-theme', t); else root.removeAttribute('data-theme');
  if (t === 'light' || t === 'dark') {
    var c = t === 'dark' ? '#0e1117' : '#e9ecf1';
    var m = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < m.length; i++) m[i].setAttribute('content', c);
  }
})();
