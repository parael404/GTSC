(function () {
    const lightbox = document.getElementById('landingLightbox');
    const lightboxImage = document.getElementById('landingLightboxImage');
    const lightboxCaption = document.getElementById('landingLightboxCaption');
    const lightboxClose = document.getElementById('landingLightboxClose');
    const lightboxTriggers = document.querySelectorAll('.js-lightbox-trigger');

    function closeLightbox() {
      if (!lightbox) return;
      lightbox.hidden = true;
      lightbox.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('lightbox-open');
      lightboxImage.src = '';
      lightboxImage.alt = '';
      lightboxCaption.textContent = '';
    }

    function openLightbox(trigger) {
      if (!lightbox || !trigger) return;
      lightboxImage.src = trigger.dataset.image || '';
      lightboxImage.alt = trigger.querySelector('img')?.alt || trigger.dataset.title || 'Landing photo';
      lightboxCaption.textContent = trigger.dataset.title || '';
      lightbox.hidden = false;
      lightbox.setAttribute('aria-hidden', 'false');
      document.body.classList.add('lightbox-open');
    }

    lightboxTriggers.forEach((trigger) => {
      trigger.addEventListener('click', () => openLightbox(trigger));
      trigger.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openLightbox(trigger);
        }
      });
    });

    if (lightboxClose) {
      lightboxClose.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        closeLightbox();
      });
    }

    if (lightbox) {
      lightbox.addEventListener('click', (event) => {
        const clickedBackdrop = event.target === lightbox;
        const clickedClose = event.target === lightboxClose;
        if (clickedBackdrop || clickedClose) {
          closeLightbox();
        }
      });
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeLightbox();
      }
    });
})();
