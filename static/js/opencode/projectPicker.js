/**
 * @fileoverview Dropdown component for selecting the active OpenCode project/workspace.
 */

/**
 * Creates a project picker component.
 * @param {HTMLElement} container - The DOM element to render the picker into.
 * @param {Object} options - Configuration options.
 * @param {Function} options.onProjectChange - Callback when a project is selected, receives the directory path.
 * @returns {Object} Picker API (setProjects, getActive, destroy).
 */
export function createProjectPicker(container, { onProjectChange }) {
    let currentProjects = [];
    let activeProject = null;

    // Create UI elements
    const wrapper = document.createElement('div');
    wrapper.className = 'oc-project-picker';

    const select = document.createElement('select');
    select.className = 'oc-project-select';
    
    const message = document.createElement('div');
    message.className = 'oc-project-message';
    message.style.display = 'none';
    message.innerHTML = 'No projects configured. <a href="#settings">Go to Settings</a>';

    wrapper.appendChild(select);
    wrapper.appendChild(message);
    container.appendChild(wrapper);

    // Load from local storage
    const storedProject = localStorage.getItem('oc-active-project');

    // Handle selection changes
    const handleChange = () => {
        const selectedValue = select.value;
        if (selectedValue !== activeProject) {
            activeProject = selectedValue;
            localStorage.setItem('oc-active-project', activeProject);
            if (onProjectChange) {
                onProjectChange(activeProject);
            }
        }
    };

    select.addEventListener('change', handleChange);

    /**
     * Renders the project list into the select element.
     * @param {Array<{path: string}>} projects 
     */
    const render = (projects) => {
        select.innerHTML = '';
        currentProjects = projects || [];

        if (currentProjects.length === 0) {
            select.style.display = 'none';
            message.style.display = 'block';
            return;
        }

        select.style.display = 'block';
        message.style.display = 'none';

        currentProjects.forEach(proj => {
            const option = document.createElement('option');
            option.className = 'oc-project-option';
            option.value = proj.path;
            
            // Extract basename
            const parts = proj.path.replace(/\\/g, '/').split('/');
            const basename = parts.pop() || parts.pop(); // handle trailing slash
            
            option.textContent = basename;
            option.title = proj.path;
            select.appendChild(option);
        });

        // Set initial selection
        if (storedProject && currentProjects.find(p => p.path === storedProject)) {
            select.value = storedProject;
            activeProject = storedProject;
        } else if (currentProjects.length > 0) {
            select.value = currentProjects[0].path;
            activeProject = currentProjects[0].path;
        }

        // Trigger initial callback
        if (activeProject && onProjectChange) {
            onProjectChange(activeProject);
        }
    };

    /**
     * Fetches configuration and sets the projects.
     */
    const fetchConfig = async () => {
        try {
            const res = await fetch('/api/opencode-config');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            const data = await res.json();
            render(data.projects || []);
        } catch (err) {
            console.error('Failed to fetch OpenCode config:', err);
            render([]);
        }
    };

    // Initialize
    fetchConfig();

    return {
        /**
         * Update the list of projects manually.
         * @param {Array} list 
         */
        setProjects(list) {
            render(list);
        },
        /**
         * Get the currently active project path.
         * @returns {string|null}
         */
        getActive() {
            return activeProject;
        },
        /**
         * Clean up DOM and event listeners.
         */
        destroy() {
            select.removeEventListener('change', handleChange);
            wrapper.remove();
        }
    };
}
