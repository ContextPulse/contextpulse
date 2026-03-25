/* ============================================
   ContextPulse — Marketing Site Scripts
   ============================================ */

(function () {
  'use strict';

  // --- Intersection Observer for fade-in animations ---
  const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -40px 0px'
  };

  const fadeObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        fadeObserver.unobserve(entry.target);
      }
    });
  }, observerOptions);

  // Only observe fade-in elements that are NOT inside staggered grids
  const staggeredGrids = document.querySelectorAll('.problem-grid, .modules-grid, .features-grid, .pricing-grid');
  const staggeredChildren = new Set();
  staggeredGrids.forEach((grid) => {
    grid.querySelectorAll('.fade-in').forEach((child) => staggeredChildren.add(child));
  });

  document.querySelectorAll('.fade-in').forEach((el) => {
    if (!staggeredChildren.has(el)) {
      fadeObserver.observe(el);
    }
  });

  // --- Staggered animations for grid children ---
  const staggerObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        const children = entry.target.querySelectorAll('.fade-in');
        children.forEach((child, index) => {
          child.style.transitionDelay = `${index * 100}ms`;
          child.classList.add('visible');
        });
        staggerObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.05 });

  document.querySelectorAll('.problem-grid, .modules-grid, .features-grid, .pricing-grid').forEach((grid) => {
    staggerObserver.observe(grid);
  });

  // --- Mobile navigation toggle ---
  const mobileToggle = document.querySelector('.nav-mobile-toggle');
  const navLinks = document.querySelector('.nav-links');

  if (mobileToggle && navLinks) {
    mobileToggle.addEventListener('click', () => {
      navLinks.classList.toggle('open');
      mobileToggle.classList.toggle('active');
    });

    // Close mobile nav on link click
    navLinks.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', () => {
        navLinks.classList.remove('open');
        mobileToggle.classList.remove('active');
      });
    });
  }

  // --- Smooth scroll for anchor links ---
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (targetId === '#') return;

      const target = document.querySelector(targetId);
      if (target) {
        e.preventDefault();
        const navHeight = document.querySelector('.nav').offsetHeight;
        const targetPosition = target.getBoundingClientRect().top + window.pageYOffset - navHeight - 20;
        window.scrollTo({ top: targetPosition, behavior: 'smooth' });
      }
    });
  });

  // --- Navbar background on scroll ---
  const nav = document.querySelector('.nav');
  let lastScrollY = 0;

  function updateNav() {
    const scrollY = window.scrollY;
    if (scrollY > 50) {
      nav.style.borderBottomColor = 'rgba(30, 41, 59, 0.8)';
    } else {
      nav.style.borderBottomColor = 'rgba(30, 41, 59, 0.3)';
    }
    lastScrollY = scrollY;
  }

  window.addEventListener('scroll', updateNav, { passive: true });
  updateNav();

  // --- Waitlist form ---
  const waitlistForm = document.getElementById('waitlist-form');
  if (waitlistForm) {
    waitlistForm.addEventListener('submit', function (e) {
      e.preventDefault();
      const email = waitlistForm.querySelector('.waitlist-input').value;
      // Store locally until backend is wired
      const waitlist = JSON.parse(localStorage.getItem('cp-waitlist') || '[]');
      waitlist.push({ email, ts: new Date().toISOString() });
      localStorage.setItem('cp-waitlist', JSON.stringify(waitlist));
      waitlistForm.innerHTML = '<div class="waitlist-success">You\'re on the list! We\'ll email you when Pro launches.</div>';
    });
  }

})();
