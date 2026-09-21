/* Theme: Light / Dark / Auto (follows the OS). Loaded as a blocking script in <head> so the right theme is
 * set before first paint. The choice is remembered in localStorage; CSS reads it from <html data-theme>. */
(function () {
  var KEY = "jev-theme", root = document.documentElement, mq = matchMedia("(prefers-color-scheme: dark)");
  function pref() { try { return localStorage.getItem(KEY) || "system"; } catch (e) { return "system"; } }
  function apply() {
    var p = pref(), dark = p === "dark" || (p === "system" && mq.matches);
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.themePref = p;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#0e1013" : "#f6f7f9");
  }
  window.jevTheme = {
    get: pref,
    set: function (mode) { try { mode === "system" ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, mode); } catch (e) {} apply(); },
  };
  (mq.addEventListener ? mq.addEventListener("change", apply) : mq.addListener(apply));
  apply();
})();
