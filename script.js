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
        const elements = document.querySelectorAll('.project-card, .skill-category, .github-stat-card');
        
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
    
    // GitHub username
    const username = 'kevinlin';
    
    // Fetch GitHub stats
    fetchGitHubStats(username);
    
    // Fetch contribution graph
    fetchContributionGraph(username);
    
    // Fetch recent activity
    fetchRecentActivity(username);
});

async function fetchGitHubStats(username) {
    try {
        // Fetch user data
        const userResponse = await fetch(`https://api.github.com/users/${username}`);
        const userData = await userResponse.json();
        
        // Update repositories count
        document.getElementById('repositories-count').textContent = userData.public_repos;
        
        // Update followers count
        document.getElementById('followers-count').textContent = userData.followers;
        
        // For contributions, we'll use a placeholder since GitHub API doesn't provide this directly
        // In a real implementation, you might need to use GitHub GraphQL API or scrape the contributions page
        document.getElementById('contributions-count').textContent = '500+'; // Placeholder
    } catch (error) {
        console.error('Error fetching GitHub stats:', error);
    }
}

function fetchContributionGraph(username) {
    // Set the contribution graph image
    // This uses a service that generates an image of your GitHub contributions
    const graphUrl = `https://ghchart.rshah.org/${username}`;
    document.getElementById('contribution-graph').src = graphUrl;
}

async function fetchRecentActivity(username) {
    try {
        // Fetch user events
        const eventsResponse = await fetch(`https://api.github.com/users/${username}/events/public?per_page=10`);
        const events = await eventsResponse.json();
        
        const activityList = document.getElementById('activity-list');
        activityList.innerHTML = ''; // Clear loading placeholder
        
        if (events.length === 0) {
            activityList.innerHTML = '<li class="activity-placeholder">No recent activity found</li>';
            return;
        }
        
        // Process and display events
        events.slice(0, 5).forEach(event => {
            const activityItem = createActivityItem(event);
            if (activityItem) {
                activityList.appendChild(activityItem);
            }
        });
    } catch (error) {
        console.error('Error fetching GitHub activity:', error);
        document.getElementById('activity-list').innerHTML = 
            '<li class="activity-placeholder">Error loading activity. Please try again later.</li>';
    }
}

function createActivityItem(event) {
    const item = document.createElement('li');
    item.className = 'activity-item';
    
    let icon, title, details;
    const repo = event.repo.name;
    const time = new Date(event.created_at).toLocaleDateString();
    
    switch(event.type) {
        case 'PushEvent':
            icon = 'fa-code-commit';
            title = `Pushed to ${repo}`;
            const commits = event.payload.commits;
            details = commits ? 
                `${commits.length} commit${commits.length > 1 ? 's' : ''}` : 
                'Commits';
            break;
            
        case 'CreateEvent':
            icon = 'fa-plus-circle';
            title = `Created ${event.payload.ref_type} in ${repo}`;
            details = event.payload.ref || '';
            break;
            
        case 'PullRequestEvent':
            icon = 'fa-code-pull-request';
            title = `${event.payload.action} pull request in ${repo}`;
            details = `#${event.payload.number}: ${event.payload.pull_request?.title || ''}`;
            break;
            
        case 'IssuesEvent':
            icon = 'fa-exclamation-circle';
            title = `${event.payload.action} issue in ${repo}`;
            details = `#${event.payload.issue.number}: ${event.payload.issue.title}`;
            break;
            
        case 'IssueCommentEvent':
            icon = 'fa-comment';
            title = `Commented on issue in ${repo}`;
            details = `#${event.payload.issue.number}: ${event.payload.issue.title}`;
            break;
            
        case 'WatchEvent':
            icon = 'fa-star';
            title = `Starred ${repo}`;
            details = '';
            break;
            
        case 'ForkEvent':
            icon = 'fa-code-branch';
            title = `Forked ${repo}`;
            details = '';
            break;
            
        default:
            return null; // Skip unknown event types
    }
    
    item.innerHTML = `
        <div class="activity-icon">
            <i class="fas ${icon}"></i>
        </div>
        <div class="activity-content">
            <div class="activity-title">${title}</div>
            <div class="activity-details">${details}</div>
        </div>
        <div class="activity-time">${time}</div>
    `;
    
    return item;
}
