/**
 * @fileoverview Dropdown component for selecting the active OpenCode project/workspace.
 */

import { getDirectory } from './opencode.js';

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

    // The client module owns the persisted project ('oc_active_project'); this
    // used to read a differently-spelled key, so the restored selection never
    // matched and no directory was ever sent with a request.
    const storedProject = getDirectory();

    // Handle selection changes
    const handleChange = () => {
        const selectedValue = select.value;
        if (selectedValue !== activeProject) {
            activeProject = selectedValue;
            // Persistence lives in the client module (setDirectory), which also
            // rebinds the event stream to the new directory.
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
        // Map string paths to objects if necessary, or just use them as strings
        // The backend returns an array of strings: ["/path/one", "/path/two"]
        currentProjects = (projects || []).map(p => typeof p === 'string' ? {path: p} : p);

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
