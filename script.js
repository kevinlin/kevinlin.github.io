document.addEventListener('DOMContentLoaded', function() {
    // Add smooth scrolling for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            
            const targetId = this.getAttribute('href');
            const targetElement = document.querySelector(targetId);
            
            if (targetElement) {
                window.scrollTo({
                    top: targetElement.offsetTop,
                    behavior: 'smooth'
                });
            }
        });
    });
    
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
    };
    
    // Run animation check on scroll
    window.addEventListener('scroll', animateOnScroll);
    
    // Run once on page load
    animateOnScroll();
    
    // Initialize the photo slider
    initPhotoSlider();
});

function initPhotoSlider() {
    const photoSlider = document.querySelector('.photo-slider');
    const sliderDots = document.querySelector('.slider-dots');
    const prevButton = document.querySelector('.prev-button');
    const nextButton = document.querySelector('.next-button');
    
    // Sample photo data - replace with your actual Flickr photos later
    const photos = [
        {
            url: 'https://source.unsplash.com/random/1200x800/?singapore',
            title: 'Singapore Skyline',
            description: 'The beautiful skyline of Singapore captured during sunset'
        },
        {
            url: 'https://source.unsplash.com/random/1200x800/?travel',
            title: 'Travel Adventures',
            description: 'Exploring new places and capturing memories'
        },
        {
            url: 'https://source.unsplash.com/random/1200x800/?nature',
            title: 'Natural Beauty',
            description: 'The stunning colors and patterns found in nature'
        },
        {
            url: 'https://source.unsplash.com/random/1200x800/?architecture',
            title: 'Urban Architecture',
            description: 'Modern and traditional architectural marvels'
        },
        {
            url: 'https://source.unsplash.com/random/1200x800/?portrait',
            title: 'Portrait Photography',
            description: 'Capturing the essence and emotions of people'
        }
    ];
    
    let currentSlide = 0;
    
    // Create slides and dots
    photos.forEach((photo, index) => {
        // Create slide
        const slide = document.createElement('div');
        slide.className = 'photo-slide';
        slide.style.backgroundImage = `url(${photo.url})`;
        
        const description = document.createElement('div');
        description.className = 'photo-description';
        description.innerHTML = `
            <h3>${photo.title}</h3>
            <p>${photo.description}</p>
        `;
        
        slide.appendChild(description);
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
}
