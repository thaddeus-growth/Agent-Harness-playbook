// The console's only script. It writes no words of its own: what it shows
// comes in data attributes the page carries, or is copied from the console's
// own reply. It polls for change. A page nobody has touched (at the top,
// nothing typed, no row opened or closed) reloads itself; any other is left as
// it is and a bar offers the reload. The open count in the tab title follows
// every poll, hidden tab or not.
//
// A form is sent without leaving the page: the reply's result lines (or the
// notice a redirect ends on) show over the page for a few seconds, then the
// bar and the rows are swapped for the current ones, with every row's open or
// closed state and everything typed but not sent kept. While a send is on its
// way the page never reloads or polls, a second send of the same form is
// stopped, and a mouse wheel over a focused number box blurs it instead of
// changing what will be sent. If the console cannot be reached the form comes
// back as it was, with the words below. Without fetch, or with any failure
// here, a form is sent the ordinary way. If the console cannot be reached
// three times in a row a bar says so, and it goes as soon as a poll gets
// through. The answer-all button is off, with its reason beside it, while any
// row of its group is being edited.
(function () {
  "use strict";
  try {
    var body = document.body, url = body.dataset.poll, sending = 0, shown = null;
    function each(list, fn) { Array.prototype.forEach.call(list, fn); }
    function make(parent, tag, cls, text) {
      var el = document.createElement(tag);
      el.className = cls;
      el.textContent = text;
      el.setAttribute("role", "status");
      parent.appendChild(el);
      return el;
    }
    function buttons(f, off) { each(f.querySelectorAll("button"), function (b) { b.disabled = off; }); }

    document.addEventListener("submit", function (e) {
      var f = e.target;
      if (f.dataset.sent) { e.preventDefault(); return; }
      f.dataset.sent = "1";
      sending++;
      buttons(f, true);
      if (!url || typeof fetch !== "function" || typeof DOMParser !== "function") return;
      e.preventDefault();
      post(f);
    }, true);
    document.addEventListener("wheel", function (e) {
      var a = document.activeElement;
      if (a && a.type === "number" && e.target === a) a.blur();
    }, { passive: true });
    window.addEventListener("pageshow", function (e) { if (e.persisted) location.reload(); });
    if (!url) return;

    var seq = body.dataset.seq, base = document.title.replace(/^\(\d+\) /, "");
    var forms, folds, was, live = null, lost = null, failed = 0, tray = make(body, "div", "toasts", "");
    function snap(f) { return new URLSearchParams(new FormData(f)).toString(); }
    function edited(f) { return f.dataset.was !== snap(f); }
    function shape() { return folds.map(function (d) { return d.open ? 1 : 0; }).join(""); }
    function untouched() { return window.scrollY <= 0 && !forms.some(edited) && shape() === was; }

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
    function bind() {                     // once, and again for each page swapped in
      forms = Array.prototype.slice.call(document.querySelectorAll("form"));
      folds = Array.prototype.slice.call(document.querySelectorAll("details"));
      forms.forEach(function (f) { if (f.dataset.was === undefined) f.dataset.was = snap(f); });
      was = shape();
      sync();
    }
    bind();

    // -- a form sent in place --
    function drop() { if (shown) { shown.remove(); shown = null; } }
    function show(kind, fill) {
      drop();
      var t = document.createElement("div");
      t.className = "toast " + kind;
      fill(t);
      tray.appendChild(t);
      t.addEventListener("click", drop);
      shown = t;
      if (kind === "ok") setTimeout(function () { if (shown === t) drop(); }, 7000);
    }
    function said(doc) {                  // the console's own lines, as it drew them
      var lines = doc.querySelectorAll(".res, .flash");
      var bad = Array.prototype.some.call(lines, function (l) { return /\bbad\b/.test(l.className); });
      if (lines.length) show(bad ? "bad" : "ok", function (t) {
        each(lines, function (l) { t.appendChild(document.importNode(l, true)); });
      });
    }
    function key(f) { return f.getAttribute("action") + "|" + new URLSearchParams(new FormData(f)).get("id"); }
    function swap(next) {
      var main = document.querySelector("main"), fresh = next.querySelector("main"), rows = {}, drafts = {};
      each(main.querySelectorAll("details"), function (d) { var t = d.querySelector(".ttl"); if (t) rows[t.textContent] = d; });
      each(main.querySelectorAll("form"), function (f) { if (!f.dataset.sent && edited(f)) drafts[key(f)] = f; });
      each(fresh.querySelectorAll("form"), function (f) { if (drafts[key(f)]) f.replaceWith(drafts[key(f)]); });
      each(fresh.querySelectorAll("details"), function (d) {
        var t = d.querySelector(".ttl");
        if (t && rows[t.textContent]) d.open = rows[t.textContent].open;
      });
      document.querySelector("header").replaceWith(next.querySelector("header"));
      main.replaceWith(fresh);
      if (live) { live.remove(); live = null; }
      seq = body.dataset.seq = next.body.dataset.seq;
      base = next.title.replace(/^\(\d+\) /, "");
      count(+next.body.dataset.open);
      bind();
    }
    function refresh() {
      return fetch(location.href, { cache: "no-store", credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw r.status; return r.text(); })
        .then(function (text) {
          try { swap(new DOMParser().parseFromString(text, "text/html")); } catch (e) { location.reload(); }
        }, function () { /* the poll will say so */ });
    }
    function post(f) {
      function done() { sending--; }
      fetch(f.action, { method: "POST", credentials: "same-origin", body: new URLSearchParams(new FormData(f)) })
        .then(function (r) { return r.text(); })
        .then(function (text) { said(new DOMParser().parseFromString(text, "text/html")); return refresh(); }, function () {
          delete f.dataset.sent;          // never reached: nothing was saved, so it may be sent again
          buttons(f, false);
          show("bad", function (t) { t.textContent = body.dataset.lost; });
        })
        .then(done, done);
    }

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
