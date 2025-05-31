// Navigation functionality
function initNavigation() {
    const navToggle = document.querySelector('.nav-toggle');
    const navMenu = document.querySelector('.nav-menu');
    const navLinks = document.querySelectorAll('.nav-menu a');
    
    // Mobile menu toggle
    navToggle.addEventListener('click', function() {
        const isExpanded = navToggle.getAttribute('aria-expanded') === 'true';
        
        navMenu.classList.toggle('active');
        navToggle.classList.toggle('active');
        navToggle.setAttribute('aria-expanded', !isExpanded);
    });
    
    // Close mobile menu when clicking outside
    document.addEventListener('click', function(e) {
        if (!navToggle.contains(e.target) && !navMenu.contains(e.target)) {
            navMenu.classList.remove('active');
            navToggle.classList.remove('active');
            navToggle.setAttribute('aria-expanded', 'false');
        }
    });
    
    // Highlight active section in navigation
    function highlightActiveSection() {
        const sections = document.querySelectorAll('section[id], header[id]');
        const navHeight = document.querySelector('.main-nav').offsetHeight;
        
        let current = '';
        sections.forEach(section => {
            const sectionTop = section.offsetTop - navHeight - 100;
            const sectionHeight = section.offsetHeight;
            
            if (window.scrollY >= sectionTop && window.scrollY < sectionTop + sectionHeight) {
                current = section.getAttribute('id');
            }
        });
        
        // Remove active class from all nav links
        navLinks.forEach(link => {
            link.classList.remove('active');
        });
        
        // Add active class to current section link
        if (current) {
            const activeLink = document.querySelector(`.nav-menu a[href="#${current}"]`);
            if (activeLink) {
                activeLink.classList.add('active');
            }
        }
    }
    
    // Run on scroll
    window.addEventListener('scroll', highlightActiveSection);
    
    // Run once on load
    highlightActiveSection();
}

// Theme toggle functionality
function initThemeToggle() {
    const themeToggle = document.getElementById('theme-toggle');
    const prefersDarkScheme = window.matchMedia('(prefers-color-scheme: dark)');
    
    // Check for saved theme preference or use the system preference
    const currentTheme = localStorage.getItem('theme') || 
                        (prefersDarkScheme.matches ? 'dark' : 'light');
    
    // Apply the theme on initial load
    if (currentTheme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
    
    // Toggle theme when button is clicked
    themeToggle.addEventListener('click', function() {
        let theme;
        
        // If the current theme is dark, switch to light
        if (document.documentElement.getAttribute('data-theme') === 'dark') {
            document.documentElement.removeAttribute('data-theme');
            theme = 'light';
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
            theme = 'dark';
        }
        
        // Save the preference
        localStorage.setItem('theme', theme);
    });
}

document.addEventListener('DOMContentLoaded', function() {
    // Initialize navigation functionality
    initNavigation();
    
    // Add smooth scrolling for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            
            const targetId = this.getAttribute('href');
            const targetElement = document.querySelector(targetId);
            
            if (targetElement) {
                const navHeight = document.querySelector('.main-nav').offsetHeight;
                const targetPosition = targetElement.offsetTop - navHeight - 20;
                
                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
                
                // Close mobile menu if open
                const navMenu = document.querySelector('.nav-menu');
                const navToggle = document.querySelector('.nav-toggle');
                navMenu.classList.remove('active');
                navToggle.classList.remove('active');
                navToggle.setAttribute('aria-expanded', 'false');
            }
        });
    });
    
    // Function to animate progress bars
    const animateProgressBars = function() {
        const progressBars = document.querySelectorAll('.progress-bar');
        
        progressBars.forEach(bar => {
            const parent = bar.closest('.skill-item');
            const percentage = parent.querySelector('.skill-percentage').innerText;
            const valueWidth = percentage.replace('%', '');
            const barPosition = bar.getBoundingClientRect().top;
            const windowHeight = window.innerHeight;
            
            if (barPosition < windowHeight - 50) {
                // Set width based on percentage value
                bar.style.width = percentage;
            }
        });
    };
    
    // Add animation to elements when they come into view
    const animateOnScroll = function() {
        const elements = document.querySelectorAll('.project-card, .skill-category, .github-embed-item, .photo-slide');
        
        elements.forEach(element => {
            const elementPosition = element.getBoundingClientRect().top;
            const windowHeight = window.innerHeight;
            
            if (elementPosition < windowHeight - 100) {
                element.classList.add('visible');
            }
        });
        
        // Also animate progress bars
        animateProgressBars();
    };
    
    // Run animation check on scroll
    window.addEventListener('scroll', animateOnScroll);
    
    // Run once on page load
    animateOnScroll();
    
    // Initialize the photo slider
    initPhotoSlider();
    
    // Initialize theme toggle
    initThemeToggle();
});

function initPhotoSlider() {
    const photoSlider = document.querySelector('.photo-slider');
    const sliderDots = document.querySelector('.slider-dots');
    const prevButton = document.querySelector('.prev-button');
    const nextButton = document.querySelector('.next-button');
    
    let currentSlide = 0;
    let photos = [];
    
    // Flickr API configuration
    const FLICKR_USER_ID = '91923312@N00'; // Your Flickr user ID (you may need to find this)
    const FLICKR_API_KEY = '2883b23b15ab0136845cb7007ef2f2ec'; // You'll need to get this from Flickr
    
    // Function to load photos from Flickr with randomization
    async function loadFlickrPhotos() {
        try {
            // First, get the total number of photos to calculate random page
            const totalResponse = await fetch(`https://api.flickr.com/services/rest/?method=flickr.people.getPublicPhotos&api_key=${FLICKR_API_KEY}&user_id=${FLICKR_USER_ID}&format=json&nojsoncallback=1&per_page=1`);
            
            if (!totalResponse.ok) {
                throw new Error('Flickr API request failed');
            }
            
            const totalData = await totalResponse.json();
            
            if (totalData.stat !== 'ok' || totalData.photos.total === 0) {
                throw new Error('No public photos found');
            }
            
            const totalPhotos = parseInt(totalData.photos.total);
            const photosPerPage = 20;
            const maxPages = Math.ceil(totalPhotos / photosPerPage);
            
            // Generate a random page number
            const randomPage = Math.floor(Math.random() * maxPages) + 1;
            
            // Fetch photos from the random page
            const response = await fetch(`https://api.flickr.com/services/rest/?method=flickr.people.getPublicPhotos&api_key=${FLICKR_API_KEY}&user_id=${FLICKR_USER_ID}&format=json&nojsoncallback=1&per_page=${photosPerPage}&page=${randomPage}&extras=url_l,description`);
            
            if (!response.ok) {
                throw new Error('Flickr API request failed');
            }
            
            const data = await response.json();
            
            if (data.stat === 'ok' && data.photos.photo.length > 0) {
                // Shuffle the photos for additional randomization
                const shuffledPhotos = data.photos.photo.sort(() => Math.random() - 0.5);
                
                return shuffledPhotos.map(photo => ({
                    url: photo.url_l || `https://live.staticflickr.com/${photo.server}/${photo.id}_${photo.secret}_b.jpg`,
                    title: photo.title,
                    description: photo.description._content || ''
                }));
            } else {
                throw new Error('No photos found on selected page');
            }
        } catch (error) {
            console.log('Flickr photos not available:', error.message);
            return [];
        }
    }
    
    // Initialize the slider
    async function initializeSlider() {
        // Load randomized Flickr photos
        photos = await loadFlickrPhotos();
        
        // If no photos are available, hide the photography section
        if (photos.length === 0) {
            const photographySection = document.querySelector('.photography');
            if (photographySection) {
                photographySection.style.display = 'none';
            }
            return;
        }
        
        // Create slides and dots
        photos.forEach((photo, index) => {
            // Create slide
            const slide = document.createElement('div');
            slide.className = 'photo-slide';
            slide.style.backgroundImage = `url(${photo.url})`;
            
            // Only add description if it exists and is not empty
            if (photo.description && photo.description.trim() !== '') {
                const description = document.createElement('div');
                description.className = 'photo-description';
                description.innerHTML = `<p>${photo.description}</p>`;
                slide.appendChild(description);
            }
            
            photoSlider.appendChild(slide);
            
            // Create dot
            const dot = document.createElement('div');
            dot.className = 'slider-dot';
            if (index === 0) dot.classList.add('active');
            
            dot.addEventListener('click', () => {
                goToSlide(index);
            });
            
            sliderDots.appendChild(dot);
        });
        
        // Set initial position
        updateSliderPosition();
        
        // Add event listeners for buttons
        prevButton.addEventListener('click', previousSlide);
        nextButton.addEventListener('click', nextSlide);
        
        // Add keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') previousSlide();
            if (e.key === 'ArrowRight') nextSlide();
        });
        
        // Add touch swipe support
        let touchStartX = 0;
        let touchEndX = 0;
        
        photoSlider.addEventListener('touchstart', (e) => {
            touchStartX = e.changedTouches[0].screenX;
        });
        
        photoSlider.addEventListener('touchend', (e) => {
            touchEndX = e.changedTouches[0].screenX;
            handleSwipe();
        });
        
        function handleSwipe() {
            if (touchEndX < touchStartX - 50) nextSlide();
            if (touchEndX > touchStartX + 50) previousSlide();
        }
        
        // Auto advance slides
        let slideInterval = setInterval(nextSlide, 5000);
        
        // Pause auto-advance on hover
        photoSlider.addEventListener('mouseenter', () => {
            clearInterval(slideInterval);
        });
        
        photoSlider.addEventListener('mouseleave', () => {
            slideInterval = setInterval(nextSlide, 5000);
        });
    }
    
    function previousSlide() {
        currentSlide--;
        if (currentSlide < 0) {
            currentSlide = photos.length - 1;
        }
        updateSliderPosition();
    }
    
    function nextSlide() {
        currentSlide++;
        if (currentSlide >= photos.length) {
            currentSlide = 0;
        }
        updateSliderPosition();
    }
    
    function goToSlide(index) {
        currentSlide = index;
        updateSliderPosition();
    }
    
    function updateSliderPosition() {
        photoSlider.style.transform = `translateX(-${currentSlide * 100}%)`;
        
        // Update active dot
        document.querySelectorAll('.slider-dot').forEach((dot, index) => {
            if (index === currentSlide) {
                dot.classList.add('active');
            } else {
                dot.classList.remove('active');
            }
        });
    }
    
    // Start the initialization
    initializeSlider();
}
