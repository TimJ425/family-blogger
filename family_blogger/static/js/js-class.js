/* Marks the page as JavaScript-capable before it is drawn. Loaded
 * from a file (not inline) so the Content-Security-Policy can forbid
 * inline scripts. */
document.documentElement.classList.add('js');
