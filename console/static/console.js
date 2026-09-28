// The console's only script. It writes no words of its own: what it shows
// comes in data attributes the page carries. It polls for change. A page
// nobody has touched (at the top, nothing typed, no row opened or closed)
// reloads itself; any other is left as it is and a bar offers the reload. The
// open count in the tab title follows every poll, hidden tab or not. Once a
// form is sent the page never reloads or polls again, a second send is
// stopped, and a mouse wheel over a focused number box blurs it instead of
// changing what will be sent. If the console cannot be reached three times in
// a row a bar says so, and it goes as soon as a poll gets through. The
// answer-all button is off, with its reason beside it, while any row of its
// group is being edited. If anything here fails the page is simply an
// ordinary page.
(function () {
  "use strict";
  try {
    var body = document.body, url = body.dataset.poll, sending = false;
    function each(list, fn) { Array.prototype.forEach.call(list, fn); }
    function make(parent, tag, cls, text) {
      var el = document.createElement(tag);
      el.className = cls;
      el.textContent = text;
      el.setAttribute("role", "status");
      parent.appendChild(el);
      return el;
    }

    document.addEventListener("submit", function (e) {
      var f = e.target;
      if (f.dataset.sent) { e.preventDefault(); return; }
      f.dataset.sent = "1";
      sending = true;
      each(f.querySelectorAll("button"), function (b) { b.disabled = true; });
    }, true);
    document.addEventListener("wheel", function (e) {
      var a = document.activeElement;
      if (a && a.type === "number" && e.target === a) a.blur();
    }, { passive: true });
    window.addEventListener("pageshow", function (e) { if (e.persisted) location.reload(); });
    if (!url) return;

    var seq = body.dataset.seq, base = document.title.replace(/^\(\d+\) /, "");
    var forms = Array.prototype.slice.call(document.querySelectorAll("form"));
    var folds = Array.prototype.slice.call(document.querySelectorAll("details"));
    var live = null, lost = null, failed = 0;
    function snap(f) { return new URLSearchParams(new FormData(f)).toString(); }
    function edited(f) { return f.dataset.was !== snap(f); }
    function shape() { return folds.map(function (d) { return d.open ? 1 : 0; }).join(""); }
    var was = shape();
    function untouched() { return window.scrollY <= 0 && !forms.some(edited) && shape() === was; }
    forms.forEach(function (f) { f.dataset.was = snap(f); });

    function count(n) { document.title = n > 0 ? "(" + n + ") " + base : base; }
    count(+body.dataset.open);

    function sync() {
      each(document.querySelectorAll("form.all"), function (all) {
        var off = Array.prototype.some.call(all.closest("section").querySelectorAll("form.answer"), edited);
        var why = all.querySelector(".busy");
        all.querySelector("button").disabled = off;
        if (off && !why) make(all, "p", "hint busy", all.dataset.busy);
        else if (!off && why) why.remove();
      });
    }
    ["input", "change"].forEach(function (ev) { document.addEventListener(ev, sync); });

    function polled(d) {
      failed = 0;
      if (lost) { lost.remove(); lost = null; }
      count(d.open);
      if (document.hidden || sending || String(d.seq) === seq) return;
      if (untouched()) location.reload();
      else if (!live) { live = make(body, "a", "banner live", body.dataset.new); live.href = location.href; }
    }
    function missed() {
      if (++failed >= 3 && !lost) lost = make(body, "p", "banner lost", body.dataset.lost);
    }
    function tick() {
      if (sending) return;
      fetch(url, { cache: "no-store", credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw r.status; return r.json(); })
        .then(polled, missed);
    }
    setInterval(tick, 4000);
    document.addEventListener("visibilitychange", tick);
  } catch (e) { /* no script is fine */ }
})();
