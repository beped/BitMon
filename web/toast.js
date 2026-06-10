(function () {
  'use strict';

  let activeToast = null;
  let activeTimer = null;
  const VALID_POSITIONS = new Set(['top', 'mid', 'bot']);

  /*
   * Public API:
   *   toast(message)
   *   toast(message, type)
   *   toast(message, type, position)
   *
   * Supported types:
   *   success | error | warning | info
   *
   * Supported positions:
   *   top | mid | bot
   */

  function ensureContainer() {
    let container = document.getElementById('appToastContainer');
    if (!container) {
      container = document.createElement('div');
      container.id = 'appToastContainer';
      container.className = 'app-toast-container';
      document.body.appendChild(container);
    }
    return container;
  }

  function removeToast() {
    if (!activeToast) return;
    const toast = activeToast;
    activeToast = null;
    toast.classList.remove('is-visible');
    setTimeout(() => {
      toast.remove();
    }, 220);
  }

  function normalizePosition(position) {
    const value = String(position || 'top').trim().toLowerCase();
    return VALID_POSITIONS.has(value) ? value : 'top';
  }

  function showToast(message, type = 'success', position = 'top') {
    const text = String(message || '').trim();
    if (!text) return;

    if (activeTimer) {
      clearTimeout(activeTimer);
      activeTimer = null;
    }

    removeToast();

    const container = ensureContainer();
    const toast = document.createElement('div');
    toast.className = `app-toast app-toast--${type} app-toast--${normalizePosition(position)}`;
    toast.textContent = text;
    container.appendChild(toast);
    activeToast = toast;

    requestAnimationFrame(() => {
      toast.classList.add('is-visible');
    });

    activeTimer = setTimeout(() => {
      removeToast();
      activeTimer = null;
    }, 2000);
  }

  window.toast = showToast;
})();
