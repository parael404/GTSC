(function () {
  const STORAGE_KEY = 'codexmbs-theme';
  const root = document.documentElement;
  const toggle = document.getElementById('themeToggle');
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
  const icons = {
    dark: '<svg class="theme-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.5a1 1 0 0 1 1 1V7a1 1 0 1 1-2 0V5.5a1 1 0 0 1 1-1m0 11.5a4 4 0 1 0 0-8 4 4 0 0 0 0 8m6.4-9.8 1.1-1.1a1 1 0 0 1 1.4 1.4l-1.1 1.1a1 1 0 1 1-1.4-1.4M20.5 11a1 1 0 1 1 0 2H19a1 1 0 1 1 0-2zm-2.1 6.8a1 1 0 0 1 1.4 0l1.1 1.1a1 1 0 0 1-1.4 1.4l-1.1-1.1a1 1 0 0 1 0-1.4M12 16a1 1 0 0 1 1 1v1.5a1 1 0 1 1-2 0V17a1 1 0 0 1 1-1m-7.5 2.9 1.1-1.1A1 1 0 1 1 7 19.2l-1.1 1.1a1 1 0 0 1-1.4-1.4M5 11a1 1 0 1 1 0 2H3.5a1 1 0 1 1 0-2zm.6-5.9A1 1 0 0 1 7 6.5L5.9 7.6a1 1 0 1 1-1.4-1.4z"/></svg>',
    light: '<svg class="theme-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 14.2A8.5 8.5 0 0 1 9.8 3a7 7 0 1 0 11.2 11.2"/></svg>'
  };

  function setTheme(mode) {
    root.setAttribute('data-theme', mode);
    localStorage.setItem(STORAGE_KEY, mode);
    if (toggle) {
      const iconTarget = toggle.querySelector('.theme-menu-icon') || toggle;
      iconTarget.innerHTML = mode === 'dark' ? icons.dark : icons.light;
      toggle.setAttribute('aria-pressed', mode === 'dark' ? 'true' : 'false');
      toggle.setAttribute('aria-label', mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
      toggle.setAttribute('title', mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    }
  }

  const savedTheme = localStorage.getItem(STORAGE_KEY);
  if (savedTheme === 'dark' || savedTheme === 'light') {
    setTheme(savedTheme);
  } else {
    setTheme(mediaQuery.matches ? 'dark' : 'light');
  }

  if (toggle) {
    toggle.addEventListener('click', () => {
      const current = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      setTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  document.querySelectorAll('[data-confirm]').forEach((element) => {
    element.addEventListener('click', (event) => {
      if (!window.confirm(element.dataset.confirm || 'Continue?')) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll('select[data-auto-submit]').forEach((select) => {
    select.addEventListener('change', () => {
      if (select.form) {
        select.form.submit();
      }
    });
  });
})();
