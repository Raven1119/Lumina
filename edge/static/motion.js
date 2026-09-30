/* Lumina chat's application-local V9 motion adapter.
   Selected mechanisms are from lumina-interface-language 1.0.0:
   assets/motion-reference.html, script ids ril-engine and lumina-application.
   Third-party notices: lumina-language/THIRD_PARTY_NOTICES.txt.
   This file contains only the mechanisms used by the chat surface. */
(function () {
  "use strict";

  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let enabled = true, ambient = false, writing = false;
  const animations = new Set();
  const canMove = () => enabled && !reduced.matches;

  // RB-35 Animated Content: original V9 WAAPI timing and 24px / 600ms entry.
  function wa(el, frames, options = {}) {
    if (!canMove() || !el?.isConnected) return null;
    const a = el.animate(frames, {duration: 400, easing: 'cubic-bezier(.215,.61,.355,1)', ...options});
    animations.add(a); a.finished.catch(() => {}).finally(() => animations.delete(a)); return a;
  }
  function reconcile() { document.dispatchEvent(new Event('motion:state')); }
  function setEnabled(v) {
    enabled = !!v;
    if (!canMove()) animations.forEach(a => a.finish());
    document.documentElement.dataset.motion = canMove() ? 'on' : 'off';
    reconcile();
  }
  function setAmbient(v) { ambient = !!v; reconcile(); }
  function setWriting(v) { writing = !!v; reconcile(); }
  function enter(el, {distance = 24, duration = 600, delay = 0, axis = 'Y'} = {}) {
    return wa(el, [{opacity: 0, transform: `translate${axis}(${distance}px)`},
                   {opacity: 1, transform: 'none'}], {duration, delay, fill: 'backwards'});
  }
  document.addEventListener('visibilitychange', reconcile);
  reduced.addEventListener('change', () => { setEnabled(enabled); reconcile(); });

  const Motion = {canMove, setEnabled, setAmbient, setWriting, wa, enter,
    get state() { return {enabled, ambient, writing, reduced: reduced.matches}; }};
  window.LuminaMotion = Motion;

  // RB-29 Target Cursor: original V9 corner geometry and 200/300ms capture.
  // It augments fine-pointer controls and never hides the operating-system cursor.
  const cursor = document.getElementById('target-cursor');
  const corners = [...cursor.children];
  let cursorTarget = null, hideTimer = 0, lastPointer = {x: 0, y: 0};
  function targetCursor(el, point) {
    if (!Motion.canMove() || !matchMedia('(hover:hover) and (pointer:fine)').matches || document.querySelector('dialog[open]')) return;
    clearTimeout(hideTimer);
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    const size = 10, border = 3,
      pos = [{x:r.left-border,y:r.top-border},{x:r.right+border-size,y:r.top-border},
             {x:r.right+border-size,y:r.bottom+border-size},{x:r.left-border,y:r.bottom+border-size}];
    const initial = cursor.dataset.active !== 'true';
    cursor.dataset.active = 'true'; cursorTarget = el;
    corners.forEach((c, i) => {
      const p = pos[i], from = initial
        ? `translate(${point.x+(i===1||i===2?5:-15)}px,${point.y+(i>1?5:-15)}px)`
        : getComputedStyle(c).transform;
      c.getAnimations().forEach(a => a.cancel());
      c.style.transform = `translate(${p.x}px,${p.y}px)`;
      Motion.wa(c, [{transform:from},{transform:c.style.transform}], {duration:200});
    });
  }
  function hideCursor() {
    cursorTarget = null; clearTimeout(hideTimer);
    if (cursor.dataset.active !== 'true') return;
    const positions = [[-15,-15],[5,-15],[5,5],[-15,5]];
    corners.forEach((c, i) => {
      const from = getComputedStyle(c).transform;
      c.getAnimations().forEach(a => a.cancel());
      c.style.transform = `translate(${lastPointer.x+positions[i][0]}px,${lastPointer.y+positions[i][1]}px)`;
      Motion.wa(c, [{transform:from},{transform:c.style.transform}], {duration:300});
    });
    hideTimer = setTimeout(() => cursor.dataset.active = 'false', 300);
  }
  document.addEventListener('pointermove', e => lastPointer = {x:e.clientX,y:e.clientY}, {passive:true});
  document.addEventListener('pointerover', e => {
    const t = e.target.closest('.cursor-target:not(:disabled)');
    if (t && t !== cursorTarget) targetCursor(t, {x:e.clientX,y:e.clientY});
    else if (!t && cursorTarget) hideCursor();
  }, {passive:true});
  document.addEventListener('pointerout', e => { if (!e.relatedTarget) hideCursor(); });
  document.addEventListener('focusin', e => { if (e.target.matches('textarea,input')) hideCursor(); });
  window.addEventListener('scroll', () => { cursor.dataset.active = 'false'; cursorTarget = null; }, {passive:true});
  document.addEventListener('motion:state', () => {
    if (!Motion.canMove()) { cursor.dataset.active = 'false'; cursorTarget = null; }
  });
  window.addEventListener('pagehide', () => { Motion.setEnabled(false); Motion.setAmbient(false); });
  Motion.setAmbient(false);
  Motion.setEnabled(true);
})();
