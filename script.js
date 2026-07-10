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
    
    let themeTransitionTimer;

    // Toggle theme when button is clicked
    themeToggle.addEventListener('click', function() {
        let theme;
        const root = document.documentElement;

        // Delight: brief whole-page color crossfade + a turn of the icon
        root.classList.add('theme-transition');
        window.clearTimeout(themeTransitionTimer);
        themeTransitionTimer = window.setTimeout(function() {
            root.classList.remove('theme-transition');
        }, 500);

        themeToggle.classList.add('switching');
        window.setTimeout(function() {
            themeToggle.classList.remove('switching');
        }, 500);

        // If the current theme is dark, switch to light
        if (root.getAttribute('data-theme') === 'dark') {
            root.removeAttribute('data-theme');
            theme = 'light';
        } else {
            root.setAttribute('data-theme', 'dark');
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
                
                const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                window.scrollTo({
                    top: targetPosition,
                    behavior: prefersReducedMotion ? 'auto' : 'smooth'
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
    
    // Add animation to elements when they come into view
    const animateOnScroll = function() {
        const elements = document.querySelectorAll('.project-card, .github-embed-item, .photo-slide');

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

    // Initialize theme toggle
    initThemeToggle();

    // Paint the generative architecture hero
    initHeroNet();

    // Build the interactive skills knowledge graph
    initSkillsGraph();

    // Load featured projects dynamically
    loadFeaturedProjects();

    // Delight: a quiet nudge after a resume download, and a hello for the curious
    initResumeNudge();
    greetInConsole();
});

/* ============================================================
   Generative architecture hero (overdrive)
   The first fold becomes a live systems diagram: edge → app →
   data tiers with an AI cluster, force-settled into place and
   drifting slowly, waking under the cursor. One monochrome blue
   carries it; content sits clean on top.

   Progressive enhancement: decorative canvas only. No-JS keeps
   the plain gradient hero. Reduced-motion renders one composed,
   static frame (no loop, no cursor). Off-screen and hidden tabs
   pause the loop; DPR is capped for mid-range devices.
   ============================================================ */
function initHeroNet() {
    const canvas = document.querySelector('.hero-net');
    const header = document.getElementById('home');
    if (!canvas || !header || !canvas.getContext) return;

    const ctx = canvas.getContext('2d');
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // --- Topology: a small, legible AI system, laid out left→right ---
    // tier x-fractions read as edge → gateway → app → AI → data.
    // id lets edges reference nodes; r is a relative size unit.
    const NODES = [
        // Edge / clients (mostly behind the portrait — kept lean)
        { id: 'c1', tier: 'edge', x: 0.075, y: 0.22, r: 1.0 },
        { id: 'c2', tier: 'edge', x: 0.055, y: 0.48, r: 1.2 },
        { id: 'c3', tier: 'edge', x: 0.085, y: 0.75, r: 1.0 },
        // Gateway
        { id: 'gw', tier: 'gateway', x: 0.25, y: 0.40, r: 1.7 },
        { id: 'cdn', tier: 'gateway', x: 0.22, y: 0.68, r: 1.0 },
        { id: 'auth', tier: 'gateway', x: 0.27, y: 0.16, r: 1.1 },
        // App / services
        { id: 'app', tier: 'app', x: 0.44, y: 0.34, r: 2.0 },
        { id: 'svc1', tier: 'app', x: 0.47, y: 0.60, r: 1.3 },
        { id: 'svc2', tier: 'app', x: 0.41, y: 0.84, r: 1.0 },
        { id: 'svc3', tier: 'app', x: 0.50, y: 0.16, r: 1.1 },
        // AI cluster (the AI-system shape) — in the open, visible band
        { id: 'orch', tier: 'ai', x: 0.635, y: 0.36, r: 2.0 },
        { id: 'llm', tier: 'ai', x: 0.72, y: 0.15, r: 1.6 },
        { id: 'agent', tier: 'ai', x: 0.71, y: 0.56, r: 1.5 },
        { id: 'guard', tier: 'ai', x: 0.585, y: 0.60, r: 1.1 },
        { id: 'embed', tier: 'ai', x: 0.665, y: 0.80, r: 1.2 },
        // Data
        { id: 'pg', tier: 'data', x: 0.885, y: 0.28, r: 1.5 },
        { id: 'redis', tier: 'data', x: 0.915, y: 0.50, r: 1.2 },
        { id: 'vec', tier: 'data', x: 0.855, y: 0.68, r: 1.4 },
        { id: 's3', tier: 'data', x: 0.905, y: 0.86, r: 1.1 },
        { id: 'queue', tier: 'data', x: 0.955, y: 0.70, r: 1.0 }
    ];
    const EDGES = [
        ['c1', 'gw'], ['c2', 'gw'], ['c3', 'gw'], ['c3', 'cdn'], ['c1', 'auth'],
        ['gw', 'app'], ['cdn', 'app'], ['auth', 'app'], ['gw', 'svc3'],
        ['app', 'svc1'], ['app', 'svc2'], ['app', 'svc3'],
        ['app', 'orch'], ['svc1', 'orch'], ['svc3', 'orch'],
        ['orch', 'llm'], ['orch', 'agent'], ['orch', 'guard'], ['orch', 'embed'],
        ['agent', 'svc1'],           // agent tools loop back to a service
        ['llm', 'guard'], ['agent', 'llm'],
        ['orch', 'vec'], ['agent', 'vec'], ['embed', 'vec'],
        ['app', 'pg'], ['svc1', 'redis'], ['app', 'redis'],
        ['svc2', 's3'], ['agent', 'queue'], ['queue', 'svc2']
    ];
    const byId = {};
    NODES.forEach(n => { byId[n.id] = n; });
    const edges = EDGES.map(([a, b]) => ({ a: byId[a], b: byId[b] })).filter(e => e.a && e.b);

    // --- Theme-aware colour: one blue, read from the design tokens ---
    let rgb = [0, 99, 163];
    function readColor() {
        const raw = getComputedStyle(document.documentElement)
            .getPropertyValue('--primary-color').trim();
        const m = raw.match(/^#([0-9a-f]{6})$/i);
        if (m) {
            rgb = [
                parseInt(m[1].slice(0, 2), 16),
                parseInt(m[1].slice(2, 4), 16),
                parseInt(m[1].slice(4, 6), 16)
            ];
        }
    }
    readColor();
    const stroke = (a) => `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${a})`;

    // --- Geometry / sizing ---
    let W = 0, H = 0, dpr = 1, unit = 4;
    function layout() {
        const cssW = canvas.clientWidth || header.clientWidth;
        const cssH = canvas.clientHeight || header.clientHeight;
        if (!cssW || !cssH) return;
        W = cssW; H = cssH;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(W * dpr);
        canvas.height = Math.round(H * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // Node radius scales gently with width; the diagram fills a middle band.
        unit = Math.max(2.4, Math.min(4.2, W / 340));
        NODES.forEach(n => {
            n.hx = n.x * W;
            n.hy = 0.14 * H + n.y * 0.72 * H;
            n.rr = n.r * unit;
            if (n.px === undefined) { n.px = n.hx; n.py = n.hy; n.vx = 0; n.vy = 0; }
        });
    }

    // --- Per-node drift signature (slow, organic breathing, ~10–18s periods) ---
    NODES.forEach((n, i) => {
        n.ph = i * 1.7;
        n.f1 = 0.38 + (i % 5) * 0.05;
        n.f2 = 0.30 + (i % 3) * 0.06;
    });

    // --- Pointer (cursor wake) ---
    const WAKE_R = 170;      // reach of the wake, in css px
    const PUSH = 16;         // how far a node leans from the cursor
    let pointer = { x: -9999, y: -9999, on: false };
    if (!prefersReduced) {
        const rectOf = () => canvas.getBoundingClientRect();
        header.addEventListener('pointermove', (e) => {
            const r = rectOf();
            pointer.x = e.clientX - r.left;
            pointer.y = e.clientY - r.top;
            pointer.on = true;
        }, { passive: true });
        header.addEventListener('pointerleave', () => { pointer.on = false; });
    }

    // --- One simulation + draw frame ---
    let t = 0;
    function frame(dt) {
        t += dt;
        ctx.clearRect(0, 0, W, H);

        // Update node positions: drift target + spring + cursor push
        for (let i = 0; i < NODES.length; i++) {
            const n = NODES[i];
            let tx = n.hx, ty = n.hy;
            if (!prefersReduced) {
                tx += Math.sin(t * n.f1 + n.ph) * (7 + n.r * 2);
                ty += Math.cos(t * n.f2 + n.ph) * (5 + n.r * 1.6);
            }
            let wake = 0;
            if (pointer.on) {
                const dx = n.px - pointer.x;
                const dy = n.py - pointer.y;
                const d = Math.hypot(dx, dy);
                if (d < WAKE_R) {
                    wake = 1 - d / WAKE_R;
                    const push = (wake * PUSH) / (d || 1);
                    tx += dx * push;
                    ty += dy * push;
                }
            }
            n.wake = wake;
            // Displayed brightness eases toward the target so the wake fades
            // out gently when the cursor moves away or leaves.
            n.wd = (n.wd || 0) + (wake - (n.wd || 0)) * 0.15;
            if (prefersReduced) {
                n.px = tx; n.py = ty; n.wd = wake;
            } else {
                n.vx = (n.vx + (tx - n.px) * 0.06) * 0.86;
                n.vy = (n.vy + (ty - n.py) * 0.06) * 0.86;
                n.px += n.vx;
                n.py += n.vy;
            }
        }

        // Edges first — hairline, brightening where the cursor wakes them
        for (let i = 0; i < edges.length; i++) {
            const e = edges[i];
            const hot = Math.max(e.a.wd || 0, e.b.wd || 0);
            ctx.strokeStyle = stroke(0.14 + hot * 0.44);
            ctx.lineWidth = 1 + hot * 1.0;
            ctx.beginPath();
            ctx.moveTo(e.a.px, e.a.py);
            ctx.lineTo(e.b.px, e.b.py);
            ctx.stroke();
        }

        // Nodes on top — filled discs; hubs carry a faint halo, wake adds light
        for (let i = 0; i < NODES.length; i++) {
            const n = NODES[i];
            const w = n.wd || 0;
            const isHub = n.r >= 1.7;
            const base = isHub ? 0.42 : 0.28;
            if (isHub || w > 0.12) {
                ctx.fillStyle = stroke((0.06 + w * 0.16) * (isHub ? 1.4 : 1));
                ctx.beginPath();
                ctx.arc(n.px, n.py, n.rr + 5 + w * 7, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.fillStyle = stroke(Math.min(0.92, base + w * 0.5));
            ctx.beginPath();
            ctx.arc(n.px, n.py, n.rr, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    // --- Loop with off-screen / hidden-tab pausing ---
    let raf = null, last = 0, visible = true;
    function loop(now) {
        const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
        last = now;
        frame(dt);
        raf = requestAnimationFrame(loop);
    }
    function start() {
        if (raf || prefersReduced || !visible) return;
        last = 0;
        raf = requestAnimationFrame(loop);
    }
    function stop() {
        if (raf) { cancelAnimationFrame(raf); raf = null; }
    }

    // --- Boot ---
    layout();
    frame(0); // paint a composed frame immediately (also the reduced-motion still)

    if (!prefersReduced) {
        if ('IntersectionObserver' in window) {
            const io = new IntersectionObserver((entries) => {
                visible = entries[0].isIntersecting;
                if (visible) start(); else stop();
            }, { threshold: 0.01 });
            io.observe(header);
        } else {
            start();
        }
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) stop(); else start();
        });
    }

    // Resize (debounced) — relayout and repaint
    let resizeTimer = null;
    window.addEventListener('resize', () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {
            layout();
            frame(0);
        }, 150);
    });

    // Repaint colour when the theme flips
    if (window.MutationObserver) {
        const mo = new MutationObserver(() => { readColor(); frame(0); });
        mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    }
}

/* ============================================================
   Skills knowledge graph (overdrive)
   Progressive enhancement: reads the semantic domain list, then
   builds an interactive SVG constellation on top. Five pinned
   domain hubs; skill leaves settle around them via a tiny
   dependency-free force solver. Hover/focus a hub to trace its
   stack (coral on contact); drag any node to explore. Falls back
   to the plain list on mobile, no-JS, and reduced-motion.
   ============================================================ */
function initSkillsGraph() {
    const mount = document.querySelector('[data-skills-graph]');
    if (!mount) return;

    const SVG_NS = 'http://www.w3.org/2000/svg';
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const isDesktop = () => window.matchMedia('(min-width: 768px)').matches;

    // --- Read data from the semantic list (single source of truth) ---
    const domainEls = Array.from(mount.querySelectorAll('.skills-domain'));
    if (domainEls.length === 0) return;

    const W = 1200;
    const H = 640;
    const cx = W / 2;
    const cy = H / 2;
    const rx = 350;
    const ry = 198;

    // Simulation constants (tuned for ~30 nodes; tight, legible clusters)
    const CHARGE = 340;      // node repulsion
    const LINK = 0.05;       // pull of a skill toward its hub
    const DAMP = 0.82;       // velocity decay
    const COLLIDE_PAD = 14;  // extra breathing room between pills
    const EDGE_PAD = 34;     // keep nodes off the frame edge

    let dragNode = null;     // set while a node is being dragged (referenced in tick)

    const el = (tag, attrs) => {
        const node = document.createElementNS(SVG_NS, tag);
        if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
        return node;
    };
    // Estimated pill dimensions before we can measure real text
    const estWidth = (label, perChar, pad) => label.length * perChar + pad;

    // --- Build node + edge model ---
    const nodes = [];
    const skills = [];
    const edges = [];

    domainEls.forEach((dEl, i) => {
        const angle = -Math.PI / 2 + (i * 2 * Math.PI) / domainEls.length;
        const key = dEl.getAttribute('data-domain') || String(i);
        const hubLabel = dEl.getAttribute('data-label') ||
            (dEl.querySelector('h3') ? dEl.querySelector('h3').textContent.trim() : key);
        const hub = {
            type: 'domain', key, label: hubLabel,
            x: cx + rx * Math.cos(angle),
            y: cy + ry * Math.sin(angle),
            vx: 0, vy: 0, pinned: true,
            hw: estWidth(hubLabel, 8.6, 40) / 2, hh: 21
        };
        hub.homeX = hub.x;
        hub.homeY = hub.y;
        nodes.push(hub);

        const items = Array.from(dEl.querySelectorAll('li'));
        const n = items.length;
        items.forEach((li, j) => {
            const label = li.textContent.trim();
            const a = angle + ((j - (n - 1) / 2) * 0.55);
            const skill = {
                type: 'skill', key, label, hub,
                x: hub.x + Math.cos(a) * 72,
                y: hub.y + Math.sin(a) * 72,
                vx: 0, vy: 0, pinned: false,
                hw: estWidth(label, 7.1, 26) / 2, hh: 16
            };
            nodes.push(skill);
            skills.push(skill);
            edges.push({ skill, hub, key });
        });
    });

    // Collision radius approximates a pill by its half-width (pills are wide)
    const radius = (node) => node.hw;

    // --- Force solver: one tick ---
    function tick(alpha) {
        for (let i = 0; i < nodes.length; i++) {
            const a = nodes[i];
            for (let k = i + 1; k < nodes.length; k++) {
                const b = nodes[k];
                let dx = a.x - b.x;
                let dy = a.y - b.y;
                let d2 = dx * dx + dy * dy;
                if (d2 < 0.01) { dx = (i - k) * 0.5 + 0.3; dy = 0.4; d2 = dx * dx + dy * dy; }
                const d = Math.sqrt(d2);
                let f = CHARGE / d2;
                const minDist = radius(a) + radius(b) + COLLIDE_PAD;
                if (d < minDist) f += ((minDist - d) * 0.5) / d; // firm anti-overlap
                const fx = dx * f;
                const fy = dy * f;
                if (!a.pinned) { a.vx += fx; a.vy += fy; }
                if (!b.pinned) { b.vx -= fx; b.vy -= fy; }
            }
        }
        for (let s = 0; s < skills.length; s++) {
            const node = skills[s];
            if (node === dragNode) continue;
            node.vx += (node.hub.x - node.x) * LINK * alpha;
            node.vy += (node.hub.y - node.y) * LINK * alpha;
        }
        for (let i = 0; i < nodes.length; i++) {
            const node = nodes[i];
            if (node.pinned || node === dragNode) continue;
            node.vx *= DAMP;
            node.vy *= DAMP;
            node.x += node.vx;
            node.y += node.vy;
            node.x = Math.max(node.hw + EDGE_PAD, Math.min(W - node.hw - EDGE_PAD, node.x));
            node.y = Math.max(node.hh + EDGE_PAD, Math.min(H - node.hh - EDGE_PAD, node.y));
        }
    }

    // Settle synchronously (no paint), then remember the resting layout
    for (let t = 0; t < 520; t++) tick(1);
    nodes.forEach(node => { node.sx = node.x; node.sy = node.y; });

    // --- Render the SVG ---
    const svg = el('svg', {
        class: 'skills-canvas',
        viewBox: `0 0 ${W} ${H}`,
        role: 'group',
        'aria-label': 'Interactive map of Kevin’s skills across five domains'
    });

    // Faint blueprint dot-grid
    const defs = el('defs');
    const pattern = el('pattern', {
        id: 'sg-dots', width: 34, height: 34, patternUnits: 'userSpaceOnUse'
    });
    pattern.appendChild(el('circle', { cx: 2, cy: 2, r: 1.4, class: 'sg-grid' }));
    defs.appendChild(pattern);
    svg.appendChild(defs);
    svg.appendChild(el('rect', { x: 0, y: 0, width: W, height: H, fill: 'url(#sg-dots)' }));

    const edgeLayer = el('g', { class: 'sg-edge-layer' });
    const nodeLayer = el('g', { class: 'sg-node-layer' });
    svg.appendChild(edgeLayer);
    svg.appendChild(nodeLayer);

    edges.forEach(edge => {
        edge.line = el('line', { class: 'sg-edge' });
        edgeLayer.appendChild(edge.line);
    });

    // Build a pill node; measure real text width when possible
    function buildNode(node) {
        const isDomain = node.type === 'domain';
        const g = el('g', {
            class: `gnode gnode--${node.type}`,
            'data-domain': node.key
        });

        const label = el('text', { class: 'gnode__label', x: 0, y: 0 });
        label.textContent = node.label;

        const rect = el('rect', { class: 'gnode__pill' });
        const ring = el('rect', { class: 'gnode__focus-ring' });

        g.appendChild(rect);
        g.appendChild(ring);
        g.appendChild(label);
        nodeLayer.appendChild(g);

        node.g = g;
        node.rect = rect;
        node.ring = ring;
        node.label_el = label;
        return { g, rect, ring, label, isDomain, node };
    }

    const built = nodes.map(buildNode);

    // Size each pill from measured text (falls back to estimate if hidden)
    function sizePills() {
        built.forEach(({ rect, ring, label, isDomain, node }) => {
            let w;
            try {
                const bb = label.getBBox();
                w = (bb.width || node.hw * 2) + (isDomain ? 40 : 28);
            } catch (e) {
                w = node.hw * 2;
            }
            const h = isDomain ? 42 : 32;
            node.hw = w / 2;
            node.hh = h / 2;
            rect.setAttribute('x', -w / 2);
            rect.setAttribute('y', -h / 2);
            rect.setAttribute('width', w);
            rect.setAttribute('height', h);
            rect.setAttribute('rx', h / 2);
            ring.setAttribute('x', -w / 2 - 4);
            ring.setAttribute('y', -h / 2 - 4);
            ring.setAttribute('width', w + 8);
            ring.setAttribute('height', h + 8);
            ring.setAttribute('rx', (h + 8) / 2);
        });
    }

    function render() {
        nodes.forEach(node => {
            node.g.setAttribute('transform', `translate(${node.x.toFixed(2)}, ${node.y.toFixed(2)})`);
        });
        edges.forEach(edge => {
            edge.line.setAttribute('x1', edge.skill.x.toFixed(2));
            edge.line.setAttribute('y1', edge.skill.y.toFixed(2));
            edge.line.setAttribute('x2', edge.hub.x.toFixed(2));
            edge.line.setAttribute('y2', edge.hub.y.toFixed(2));
        });
    }

    // Place at rest, then attach to the page
    nodes.forEach(node => { node.x = node.sx; node.y = node.sy; });
    render();
    mount.insertBefore(svg, mount.querySelector('.skills-hint'));
    mount.classList.add('is-interactive');
    if (prefersReduced) {
        const hint = mount.querySelector('.skills-hint');
        if (hint) hint.textContent = 'Hover a domain to trace its stack';
    }
    // Real measurements now that the SVG is laid out
    sizePills();
    if (isDesktop()) { for (let t = 0; t < 260; t++) tick(1); nodes.forEach(n => { n.sx = n.x; n.sy = n.y; n.x = n.sx; n.y = n.sy; }); }
    render();

    // --- Highlight a cluster (hover / focus) ---
    let activeKey = null;
    function highlight(key) {
        activeKey = key;
        built.forEach(({ g, node }) => {
            g.classList.toggle('is-hot', !!key && node.key === key);
            g.classList.toggle('is-dim', !!key && node.key !== key);
        });
        edges.forEach(edge => {
            edge.line.classList.toggle('is-hot', !!key && edge.key === key);
            edge.line.classList.toggle('is-dim', !!key && edge.key !== key);
        });
    }

    built.forEach(({ g, node }) => {
        g.addEventListener('mouseenter', () => { if (!dragNode) highlight(node.key); });
        g.addEventListener('mouseleave', () => { if (!dragNode) highlight(null); });
        if (node.type === 'domain') {
            g.setAttribute('tabindex', '0');
            g.setAttribute('role', 'button');
            const skillNames = skills.filter(s => s.key === node.key).map(s => s.label).join(', ');
            g.setAttribute('aria-label', `${node.label}: ${skillNames}`);
            g.addEventListener('focus', () => highlight(node.key));
            g.addEventListener('blur', () => highlight(null));
        } else {
            g.setAttribute('aria-hidden', 'true');
        }
    });

    // --- Drag to explore (pointer) — skipped for reduced motion ---
    let raf = null;
    let pointer = { x: 0, y: 0 };

    function toSvgPoint(evt) {
        const pt = svg.createSVGPoint();
        pt.x = evt.clientX;
        pt.y = evt.clientY;
        const ctm = svg.getScreenCTM();
        if (!ctm) return { x: 0, y: 0 };
        const p = pt.matrixTransform(ctm.inverse());
        return { x: p.x, y: p.y };
    }

    function loop() {
        tick(0.5);
        if (dragNode) {
            dragNode.x = pointer.x;
            dragNode.y = pointer.y;
            dragNode.vx = 0;
            dragNode.vy = 0;
        }
        render();
        let ke = 0;
        for (let i = 0; i < skills.length; i++) ke += skills[i].vx * skills[i].vx + skills[i].vy * skills[i].vy;
        if (dragNode || ke > 0.15) {
            raf = requestAnimationFrame(loop);
        } else {
            raf = null;
            render();
        }
    }
    function startLoop() { if (!raf) raf = requestAnimationFrame(loop); }

    if (!prefersReduced) {
        built.forEach(({ g, node }) => {
            g.addEventListener('pointerdown', (evt) => {
                if (!isDesktop()) return;
                evt.preventDefault();
                dragNode = node;
                node.pinned = false;
                pointer = toSvgPoint(evt);
                node.x = pointer.x;
                node.y = pointer.y;
                highlight(node.key);
                g.setPointerCapture(evt.pointerId);
                startLoop();
            });
            g.addEventListener('pointermove', (evt) => {
                if (dragNode !== node) return;
                pointer = toSvgPoint(evt);
            });
            const end = (evt) => {
                if (dragNode !== node) return;
                dragNode = null;
                if (node.type === 'domain') { node.pinned = true; }
                highlight(null);
                try { g.releasePointerCapture(evt.pointerId); } catch (e) { /* no-op */ }
                startLoop();
            };
            g.addEventListener('pointerup', end);
            g.addEventListener('pointercancel', end);
        });
    }

    // --- Entrance: unfold each stack from its hub when scrolled into view ---
    const easeOutQuint = (t) => 1 - Math.pow(1 - t, 5);
    let entranceDone = false;

    function runEntrance() {
        if (entranceDone) return;
        entranceDone = true;
        if (prefersReduced || !isDesktop()) {
            nodes.forEach(node => { node.x = node.sx; node.y = node.sy; });
            render();
            return;
        }
        const start = nodes.map(node => ({
            x: node.pinned ? node.sx : node.hub.x,
            y: node.pinned ? node.sy : node.hub.y
        }));
        const duration = 1000;
        let startTs = null;
        function step(ts) {
            if (startTs === null) startTs = ts;
            const p = Math.min(1, (ts - startTs) / duration);
            const e = easeOutQuint(p);
            nodes.forEach((node, i) => {
                node.x = start[i].x + (node.sx - start[i].x) * e;
                node.y = start[i].y + (node.sy - start[i].y) * e;
            });
            render();
            if (p < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    if ('IntersectionObserver' in window) {
        const io = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) { runEntrance(); io.disconnect(); }
            });
        }, { threshold: 0.25 });
        io.observe(mount);
    } else {
        runEntrance();
    }
}

// Dynamic project loading functionality
async function loadFeaturedProjects() {
    const projectGrid = document.querySelector('.project-grid');
    
    if (!projectGrid) {
        console.warn('Project grid not found');
        return;
    }
    
    try {
        // Show loading state
        projectGrid.innerHTML = '<div class="loading-projects">Loading featured projects...</div>';
        
        // Fetch featured repositories from GitHub API
        const response = await fetch('https://api.github.com/search/repositories?q=user:kevinlin+topic:featured');
        
        if (!response.ok) {
            throw new Error(`GitHub API request failed: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (!data.items || data.items.length === 0) {
            projectGrid.innerHTML = '<div class="no-projects">No featured projects found.</div>';
            return;
        }
        
        // Sort repositories alphabetically by name
        const sortedRepos = data.items.sort((a, b) => a.name.localeCompare(b.name));
        
        // Generate project cards HTML
        const projectCardsHTML = sortedRepos.map(repo => generateProjectCard(repo)).join('');
        
        // Update the project grid
        projectGrid.innerHTML = projectCardsHTML;
        
        // Re-run animations for new elements
        setTimeout(() => {
            const newElements = document.querySelectorAll('.project-card');
            newElements.forEach(element => {
                element.classList.add('visible');
            });
        }, 100);
        
    } catch (error) {
        console.error('Error loading featured projects:', error);
        projectGrid.innerHTML = '<div class="error-projects">Failed to load projects. Please try again later.</div>';
    }
}

function generateProjectCard(repo) {
    // Generate project description based on repository data
    const description = getProjectDescription(repo);
    
    // Generate tech badges based on repository language and topics
    const techBadges = generateTechBadges(repo);
    
    // Generate project links (Live Site if homepage exists, GitHub link)
    const projectLinks = generateProjectLinks(repo);
    
    return `
        <div class="project-card">
            <div class="project-card-content">
                <h3><a href="${repo.html_url}" target="_blank" class="project-title-link" aria-label="View ${formatProjectName(repo.name)} project on GitHub">${formatProjectName(repo.name)}</a></h3>
                <p>${description}</p>
                <div class="tech-badges">
                    ${techBadges}
                </div>
                <div class="project-links">
                    ${projectLinks}
                </div>
            </div>
        </div>
    `;
}

function formatProjectName(name) {
    // Convert repository name to a more readable format
    let formattedName = name
        .split('-')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
    
    // Apply all CAPS for specific terms
    formattedName = formattedName.replace(/\bUi\b/g, 'UI');
    formattedName = formattedName.replace(/\bAi Sdlc\b/g, 'AI SDLC');
    
    return formattedName;
}

function getProjectDescription(repo) {
    // Use repository description if available, otherwise generate based on name/language
    if (repo.description && repo.description.trim()) {
        return repo.description;
    }
    
    // Generate description based on repository characteristics
    const name = repo.name.toLowerCase();
    const language = repo.language || 'Code';
    
    if (name.includes('ai') || name.includes('agent')) {
        return `An AI-powered application built with ${language}, showcasing advanced artificial intelligence integration and automation capabilities.`;
    } else if (name.includes('ui') || name.includes('frontend')) {
        return `A modern ${language}-based user interface application providing an intuitive and responsive user experience.`;
    } else if (name.includes('api') || name.includes('backend')) {
        return `A robust ${language} backend application providing scalable API services and data management capabilities.`;
    } else if (name.includes('workshop') || name.includes('demo')) {
        return `An educational ${language} project designed for learning and demonstration purposes, showcasing best practices and implementation patterns.`;
    } else if (name.includes('toolkit') || name.includes('tool')) {
        return `A comprehensive ${language} toolkit providing practical utilities and resources for developers and technical professionals.`;
    } else if (name.includes('extension')) {
        return `A browser extension built with ${language}, enhancing web browsing experience with additional functionality and features.`;
    } else if (name.includes('map') || name.includes('visualization')) {
        return `An interactive ${language} application featuring data visualization and mapping capabilities for enhanced user insights.`;
    } else {
        return `A ${language} project demonstrating modern development practices and innovative solutions for technical challenges.`;
    }
}

function generateTechBadges(repo) {
    const badges = [];
    
    // Add primary language
    if (repo.language) {
        badges.push(repo.language);
    }
    
    // Add badges based on repository name and characteristics
    const name = repo.name.toLowerCase();
    
    if (name.includes('ai') || name.includes('rag') || name.includes('llm')) {
        badges.push('AI/ML');
    }
    if (name.includes('agent')) {
        badges.push('AI Agent');
    }
    if (name.includes('ui') || name.includes('frontend')) {
        badges.push('Frontend');
    }
    if (name.includes('api') || name.includes('backend')) {
        badges.push('Backend');
    }
    if (name.includes('extension')) {
        badges.push('Browser Extension');
    }
    if (name.includes('terraform') || name.includes('infrastructure')) {
        badges.push('Infrastructure as Code');
    }
    if (name.includes('auth')) {
        badges.push('Authentication');
    }
    if (name.includes('workshop') || name.includes('demo')) {
        badges.push('Educational');
    }
    if (name.includes('toolkit') || name.includes('tool')) {
        badges.push('Developer Tools');
    }
    if (name.includes('presentation')) {
        badges.push('Presentations');
    }
    if (name.includes('map') || name.includes('earthquake')) {
        badges.push('Data Visualization');
    }
    if (name.includes('wiki') || name.includes('documentation')) {
        badges.push('Documentation');
    }
    
    // Add additional badges based on topics if available
    if (repo.topics && repo.topics.length > 0) {
        repo.topics.forEach(topic => {
            if (topic !== 'featured' && !badges.some(badge => badge.toLowerCase().includes(topic.toLowerCase()))) {
                badges.push(topic.charAt(0).toUpperCase() + topic.slice(1));
            }
        });
    }
    
    // Limit to 4 badges for clean display
    const displayBadges = badges.slice(0, 4);
    
    return displayBadges.map(badge => `<span class="tech-badge">${badge}</span>`).join('');
}

function generateProjectLinks(repo) {
    const links = [];
    
    // Add Live Site link if homepage exists
    if (repo.homepage && repo.homepage.trim()) {
        links.push(`
            <a href="${repo.homepage}" target="_blank" class="project-link" aria-label="View ${formatProjectName(repo.name)} live site">
                <i class="fas fa-external-link-alt" aria-hidden="true"></i> Live Site
            </a>
        `);
    }
    
    // Add GitHub link
    const linkText = repo.homepage && repo.homepage.trim() ? 'View Code' : 'View Project';
    links.push(`
        <a href="${repo.html_url}" target="_blank" class="project-link" aria-label="View ${formatProjectName(repo.name)} project on GitHub">
            <i class="fab fa-github" aria-hidden="true"></i> ${linkText}
        </a>
    `);
    
    return links.join('');
}

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
        
        // Auto advance slides (respect reduced-motion: no autoplay)
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let slideInterval = prefersReducedMotion ? null : setInterval(nextSlide, 5000);

        // Pause auto-advance on hover
        photoSlider.addEventListener('mouseenter', () => {
            if (slideInterval) clearInterval(slideInterval);
        });

        photoSlider.addEventListener('mouseleave', () => {
            if (!prefersReducedMotion) slideInterval = setInterval(nextSlide, 5000);
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

// Delight: after downloading the resume, a quiet pointer toward a conversation
function initResumeNudge() {
    const resumeBtn = document.querySelector('.resume-btn');
    if (!resumeBtn) return;

    resumeBtn.addEventListener('click', function () {
        // Once per session, and only after a real download click
        if (sessionStorage.getItem('resume-nudged')) return;
        sessionStorage.setItem('resume-nudged', '1');
        window.setTimeout(showResumeNudge, 900);
    });
}

function showResumeNudge() {
    if (document.querySelector('.nudge-toast')) return;

    const toast = document.createElement('div');
    toast.className = 'nudge-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML =
        '<i class="fas fa-paper-plane nudge-toast__icon" aria-hidden="true"></i>' +
        '<div class="nudge-toast__body">' +
            '<p>Thanks for grabbing my resume. If something looks like a fit, I\'d genuinely enjoy the conversation.</p>' +
            '<a href="#contact">Let’s connect &rarr;</a>' +
        '</div>' +
        '<button class="nudge-toast__close" aria-label="Dismiss">&times;</button>';

    document.body.appendChild(toast);

    // Animate in on the next frame so the transition runs
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            toast.classList.add('visible');
        });
    });

    const dismiss = function () {
        toast.classList.remove('visible');
        window.setTimeout(function () {
            toast.remove();
        }, 500);
    };

    toast.querySelector('.nudge-toast__close').addEventListener('click', dismiss);
    toast.querySelector('a').addEventListener('click', dismiss);
    window.setTimeout(dismiss, 9000);
}

// Delight: a warm hello for anyone who opens the console
function greetInConsole() {
    try {
        console.log(
            '%cThanks for looking under the hood.',
            'color:#0063a3;font-size:14px;font-weight:600'
        );
        console.log(
            '%cIf you\'re hiring or building something with AI, I\'d love to talk — https://www.linkedin.com/in/kevinlinyun/',
            'color:#666;font-size:12px'
        );
    } catch (e) {
        /* no-op */
    }
}
