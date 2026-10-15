// Loaded synchronously in <head> (CSP forbids inline scripts).
  // Pick the layout before first paint: phones, tablets (touch without a mouse) and
  // narrow windows get the mobile layout; desktops keep the sidebar layout.
  (function () {
    var mq = matchMedia("(max-width: 860px), (hover: none) and (pointer: coarse)");
    var set = function () { document.documentElement.classList.toggle("mobile", mq.matches); };
    set();
    mq.addEventListener("change", function () { set(); window.dispatchEvent(new Event("layoutchange")); });
  })();
