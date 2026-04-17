// ============================================
// JARVIS COGNITIVE ARCHITECTURE v4.0
// Enterprise-Grade JavaScript
// Built by Krish Paliwal
// ============================================

'use strict';

// ==================== GLOBAL CONFIGURATION ====================
const CONFIG = {
    version: '4.0.0',
    apiBaseUrl: window.location.origin,
    defaultModel: 'quantum',
    maxMessageLength: 10000,
    typingSpeed: 30,
    animationDuration: 300,
    debounceDelay: 300,
    maxRetries: 3,
    retryDelay: 1000,
    voiceLang: 'en-US',
    cacheVersion: 'v1',
    features: {
        webGL: window.deviceCapabilities?.supportsWebGL || false,
        speechRecognition: 'webkitSpeechRecognition' in window || 'SpeechRecognition' in window,
        speechSynthesis: 'speechSynthesis' in window,
        notifications: 'Notification' in window,
        clipboard: 'clipboard' in navigator,
        share: 'share' in navigator,
        vibration: 'vibrate' in navigator
    }
};

// ==================== STATE MANAGEMENT ====================
class JARVISState {
    constructor() {
        this.user = null;
        this.currentSession = null;
        this.sessions = [];
        this.messages = [];
        this.isProcessing = false;
        this.isTyping = false;
        this.isListening = false;
        this.currentModel = CONFIG.defaultModel;
        this.webSearchEnabled = false;
        this.streamingEnabled = true;
        this.theme = 'dark';
        this.accent = 'cyan';
        this.fontSize = 14;
        this.sidebarOpen = false;
        this.canvasOpen = false;
        this.commandCenterOpen = false;
        this.activeAgents = new Set(['researcher']);
        this.contextTags = new Map();
        this.messageCache = new Map();
        this.offlineQueue = [];
        this.metrics = {
            tokensUsed: 0,
            messagesSent: 0,
            sessionsCreated: 0,
            avgResponseTime: 0
        };
        
        // Load from localStorage
        this.loadFromStorage();
    }
    
    loadFromStorage() {
        try {
            const saved = localStorage.getItem('jarvis_state');
            if (saved) {
                const state = JSON.parse(saved);
                Object.assign(this, state);
            }
            
            this.theme = localStorage.getItem('jarvis_theme') || 'dark';
            this.accent = localStorage.getItem('jarvis_accent') || 'cyan';
            this.fontSize = parseInt(localStorage.getItem('jarvis_font_size')) || 14;
            this.currentModel = localStorage.getItem('jarvis_model') || CONFIG.defaultModel;
        } catch (e) {
            console.warn('Failed to load state:', e);
        }
    }
    
    saveToStorage() {
        try {
            const state = {
                theme: this.theme,
                accent: this.accent,
                fontSize: this.fontSize,
                currentModel: this.currentModel,
                webSearchEnabled: this.webSearchEnabled,
                streamingEnabled: this.streamingEnabled,
                metrics: this.metrics
            };
            
            localStorage.setItem('jarvis_theme', this.theme);
            localStorage.setItem('jarvis_accent', this.accent);
            localStorage.setItem('jarvis_font_size', this.fontSize);
            localStorage.setItem('jarvis_model', this.currentModel);
            localStorage.setItem('jarvis_state', JSON.stringify(state));
        } catch (e) {
            console.warn('Failed to save state:', e);
        }
    }
    
    addMessage(message) {
        this.messages.push({
            ...message,
            id: this.generateId(),
            timestamp: new Date().toISOString()
        });
        
        if (this.messages.length > 1000) {
            this.messages = this.messages.slice(-1000);
        }
        
        this.metrics.messagesSent++;
        this.saveToStorage();
    }
    
    generateId() {
        return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    }
    
    clearMessages() {
        this.messages = [];
    }
    
    updateMetrics(metric, value) {
        this.metrics[metric] = value;
        this.saveToStorage();
    }
}

// Initialize global state
const state = new JARVISState();

// ==================== UTILITY FUNCTIONS ====================
const utils = {
    // Debounce function
    debounce(func, wait = CONFIG.debounceDelay) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },
    
    // Throttle function
    throttle(func, limit) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    },
    
    // Sanitize HTML
    sanitizeHTML(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
    
    // Escape regex
    escapeRegex(str) {
        return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    },
    
    // Format bytes
    formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    },
    
    // Format time
    formatTime(date) {
        const now = new Date();
        const diff = now - new Date(date);
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(diff / 3600000);
        const days = Math.floor(diff / 86400000);
        
        if (minutes < 1) return 'Just now';
        if (minutes < 60) return `${minutes}m ago`;
        if (hours < 24) return `${hours}h ago`;
        if (days < 7) return `${days}d ago`;
        
        return new Date(date).toLocaleDateString();
    },
    
    // Format number
    formatNumber(num) {
        if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
        if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
        if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
        return num.toString();
    },
    
    // Deep clone
    deepClone(obj) {
        return JSON.parse(JSON.stringify(obj));
    },
    
    // Generate UUID
    generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    },
    
    // Detect device
    getDeviceInfo() {
        const ua = navigator.userAgent;
        return {
            isMobile: /Mobile|Android|iPhone|iPad|iPod/i.test(ua),
            isTablet: /Tablet|iPad/i.test(ua),
            isDesktop: !/Mobile|Tablet/i.test(ua),
            os: /Windows/i.test(ua) ? 'Windows' : 
                /Mac/i.test(ua) ? 'macOS' :
                /Linux/i.test(ua) ? 'Linux' :
                /Android/i.test(ua) ? 'Android' :
                /iOS|iPhone|iPad|iPod/i.test(ua) ? 'iOS' : 'Unknown',
            browser: /Chrome/i.test(ua) ? 'Chrome' :
                     /Firefox/i.test(ua) ? 'Firefox' :
                     /Safari/i.test(ua) ? 'Safari' :
                     /Edge/i.test(ua) ? 'Edge' : 'Unknown'
        };
    },
    
    // Copy to clipboard
    async copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            // Fallback
            const textarea = document.createElement('textarea');
            textarea.value = text;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
            return true;
        }
    },
    
    // Download file
    downloadFile(content, filename, type = 'text/plain') {
        const blob = new Blob([content], { type });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }
};

// ==================== API SERVICE ====================
class APIService {
    constructor() {
        this.baseURL = CONFIG.apiBaseUrl;
        this.retryCount = 0;
    }
    
    async request(endpoint, options = {}) {
        const url = `${this.baseURL}${endpoint}`;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                'X-Client-Version': CONFIG.version,
                'X-Device-Info': JSON.stringify(utils.getDeviceInfo())
            },
            credentials: 'include'
        };
        
        const mergedOptions = { ...defaultOptions, ...options };
        
        // Add auth token if available
        const token = localStorage.getItem('jwt_token');
        if (token) {
            mergedOptions.headers['Authorization'] = `Bearer ${token}`;
        }
        
        try {
            const response = await fetch(url, mergedOptions);
            
            if (!response.ok) {
                if (response.status === 401) {
                    // Unauthorized - redirect to login
                    this.handleUnauthorized();
                    throw new Error('Unauthorized');
                }
                
                if (response.status === 429) {
                    // Rate limited - retry with backoff
                    if (this.retryCount < CONFIG.maxRetries) {
                        this.retryCount++;
                        await this.delay(CONFIG.retryDelay * this.retryCount);
                        return this.request(endpoint, options);
                    }
                    throw new Error('Rate limit exceeded');
                }
                
                const error = await response.json();
                throw new Error(error.error || 'Request failed');
            }
            
            this.retryCount = 0;
            const data = await response.json();
            return data;
            
        } catch (error) {
            console.error('API Error:', error);
            
            // Check if offline
            if (!navigator.onLine) {
                // Queue request for later
                this.queueOfflineRequest(endpoint, options);
                throw new Error('You are offline. Request queued.');
            }
            
            throw error;
        }
    }
    
    handleUnauthorized() {
        state.user = null;
        localStorage.removeItem('jwt_token');
        showLoginScreen();
        showNotification('Session expired. Please login again.', 'warning');
    }
    
    queueOfflineRequest(endpoint, options) {
        state.offlineQueue.push({
            endpoint,
            options,
            timestamp: Date.now()
        });
        state.saveToStorage();
    }
    
    async processOfflineQueue() {
        if (state.offlineQueue.length === 0) return;
        
        showNotification(`Processing ${state.offlineQueue.length} offline requests...`, 'info');
        
        const queue = [...state.offlineQueue];
        state.offlineQueue = [];
        
        for (const item of queue) {
            try {
                await this.request(item.endpoint, item.options);
            } catch (e) {
                console.warn('Failed to process offline request:', e);
                state.offlineQueue.push(item);
            }
        }
        
        state.saveToStorage();
    }
    
    delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
    
    // Auth endpoints
    async login() {
        window.location.href = '/login/google';
    }
    
    async logout() {
        await this.request('/logout');
        state.user = null;
        localStorage.removeItem('jwt_token');
        showLoginScreen();
    }
    
    async getCurrentUser() {
        return this.request('/api/me');
    }
    
    // Chat endpoints
    async getSessions(page = 1, perPage = 20) {
        return this.request(`/ai/sessions?page=${page}&per_page=${perPage}`);
    }
    
    async createSession(title = 'New Chat') {
        return this.request('/ai/session', {
            method: 'POST',
            body: JSON.stringify({ title })
        });
    }
    
    async getSessionMessages(sessionId, page = 1) {
        return this.request(`/ai/session/${sessionId}?page=${page}`);
    }
    
    async sendMessage(sessionId, prompt, model = state.currentModel) {
        return this.request(`/ai/session/${sessionId}/message`, {
            method: 'POST',
            body: JSON.stringify({ prompt, model })
        });
    }
    
    async deleteSession(sessionId) {
        return this.request(`/ai/session/${sessionId}`, {
            method: 'DELETE'
        });
    }
}

// Initialize API service
const api = new APIService();

// ==================== UI MANAGER ====================
class UIManager {
    constructor() {
        this.elements = {};
        this.cacheElements();
        this.setupEventListeners();
        this.initializeMarked();
        this.initializeHighlight();
    }
    
    cacheElements() {
        const ids = [
            'loginScreen', 'app', 'initialLoader', 'chatMessages', 'messageInput',
            'sendButton', 'userAvatar', 'userName', 'conversationTitle',
            'messageCount', 'tokenUsage', 'currentModel', 'contextMode',
            'commandCenter', 'contextCanvas', 'welcomeScreen', 'suggestionGrid',
            'agentList', 'toolsGrid', 'knowledgeStats', 'entityList',
            'artifactsGrid', 'notificationContainer', 'voiceModal', 'settingsModal'
        ];
        
        ids.forEach(id => {
            this.elements[id] = document.getElementById(id);
        });
    }
    
    initializeMarked() {
        if (typeof marked !== 'undefined') {
            marked.setOptions({
                highlight: (code, lang) => {
                    if (lang && hljs.getLanguage(lang)) {
                        try {
                            return hljs.highlight(code, { language: lang }).value;
                        } catch (e) {}
                    }
                    return code;
                },
                breaks: true,
                gfm: true
            });
        }
    }
    
    initializeHighlight() {
        if (typeof hljs !== 'undefined') {
            hljs.configure({
                ignoreUnescapedHTML: true
            });
        }
    }
    
    setupEventListeners() {
        // Input handling
        const input = this.elements.messageInput;
        if (input) {
            input.addEventListener('input', utils.debounce(() => {
                this.updateCharCount();
            }, 100));
            
            input.addEventListener('paste', (e) => {
                this.handlePaste(e);
            });
        }
        
        // Online/Offline detection
        window.addEventListener('online', () => {
            showNotification('Back online!', 'success');
            api.processOfflineQueue();
        });
        
        window.addEventListener('offline', () => {
            showNotification('You are offline. Messages will be queued.', 'warning');
        });
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            this.handleKeyboardShortcuts(e);
        });
        
        // Resize handling
        window.addEventListener('resize', utils.throttle(() => {
            this.handleResize();
        }, 100));
        
        // Before unload
        window.addEventListener('beforeunload', (e) => {
            if (state.isProcessing) {
                e.preventDefault();
                e.returnValue = 'You have an ongoing conversation. Are you sure you want to leave?';
            }
        });
    }
    
    handleKeyboardShortcuts(e) {
        // Ctrl/Cmd + K - Command center
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            toggleCommandCenter();
        }
        
        // Ctrl/Cmd + / - Show shortcuts
        if ((e.ctrlKey || e.metaKey) && e.key === '/') {
            e.preventDefault();
            this.showKeyboardShortcuts();
        }
        
        // Ctrl/Cmd + , - Settings
        if ((e.ctrlKey || e.metaKey) && e.key === ',') {
            e.preventDefault();
            openSettings();
        }
        
        // Escape - Close modals
        if (e.key === 'Escape') {
            closeAllModals();
        }
    }
    
    handlePaste(e) {
        const items = e.clipboardData?.items;
        if (!items) return;
        
        for (const item of items) {
            if (item.type.indexOf('image') !== -1) {
                e.preventDefault();
                const file = item.getAsFile();
                this.handleImageUpload(file);
                break;
            }
        }
    }
    
    async handleImageUpload(file) {
        showNotification('Image upload coming soon!', 'info');
        // TODO: Implement image upload
    }
    
    handleResize() {
        // Update viewport height for mobile
        const vh = window.innerHeight * 0.01;
        document.documentElement.style.setProperty('--vh', `${vh}px`);
        
        // Close panels on mobile if needed
        if (window.innerWidth <= 768) {
            if (state.commandCenterOpen) toggleCommandCenter();
            if (state.canvasOpen) toggleCanvas();
        }
    }
    
    updateCharCount() {
        const input = this.elements.messageInput;
        const count = input?.value.length || 0;
        const maxLength = CONFIG.maxMessageLength;
        
        // Update UI with character count
        const footer = document.querySelector('.input-footer');
        if (footer) {
            let counter = footer.querySelector('.char-counter');
            if (!counter) {
                counter = document.createElement('span');
                counter.className = 'char-counter';
                footer.appendChild(counter);
            }
            
            counter.textContent = `${count}/${maxLength}`;
            counter.style.color = count > maxLength * 0.9 ? 'var(--color-red)' : 'var(--text-tertiary)';
        }
    }
    
    showKeyboardShortcuts() {
        const shortcuts = [
            { key: 'Ctrl/Cmd + K', action: 'Toggle Command Center' },
            { key: 'Ctrl/Cmd + ,', action: 'Open Settings' },
            { key: 'Ctrl/Cmd + /', action: 'Show Shortcuts' },
            { key: 'Ctrl/Cmd + Enter', action: 'Send Message' },
            { key: 'Shift + Enter', action: 'New Line' },
            { key: 'Escape', action: 'Close Modals' }
        ];
        
        const content = shortcuts.map(s => 
            `<div style="display: flex; justify-content: space-between; padding: 8px 0;">
                <kbd style="padding: 4px 8px; background: var(--bg-elevated); border-radius: 4px;">${s.key}</kbd>
                <span>${s.action}</span>
            </div>`
        ).join('');
        
        showModal('Keyboard Shortcuts', content);
    }
    
    renderMessage(message) {
        const isUser = message.role === 'user';
        const content = isUser ? utils.sanitizeHTML(message.content) : this.renderMarkdown(message.content);
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${isUser ? 'user' : 'assistant'}`;
        messageDiv.dataset.messageId = message.id;
        
        messageDiv.innerHTML = `
            ${!isUser ? `
                <div class="message-avatar">
                    <i class="fas fa-robot"></i>
                </div>
            ` : ''}
            <div class="message-content-wrapper">
                <div class="message-content">${content}</div>
                <div class="message-meta">
                    <span>${utils.formatTime(message.timestamp)}</span>
                    ${message.model ? `<span>${message.model}</span>` : ''}
                    ${!isUser ? `
                        <button class="message-action" onclick="copyMessage('${message.id}')">
                            <i class="fas fa-copy"></i>
                        </button>
                        <button class="message-action" onclick="regenerateResponse('${message.id}')">
                            <i class="fas fa-redo"></i>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
        
        return messageDiv;
    }
    
    renderMarkdown(content) {
        if (typeof marked !== 'undefined') {
            const html = marked.parse(content);
            return html;
        }
        return content.replace(/\n/g, '<br>');
    }
    
    showTypingIndicator() {
        const container = this.elements.chatMessages;
        const indicator = document.createElement('div');
        indicator.className = 'message assistant';
        indicator.id = 'typingIndicator';
        indicator.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content-wrapper">
                <div class="typing-indicator">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>
        `;
        
        container.appendChild(indicator);
        this.scrollToBottom();
    }
    
    hideTypingIndicator() {
        const indicator = document.getElementById('typingIndicator');
        if (indicator) indicator.remove();
    }
    
    scrollToBottom(smooth = true) {
        const container = this.elements.chatMessages;
        if (container) {
            container.scrollTo({
                top: container.scrollHeight,
                behavior: smooth ? 'smooth' : 'auto'
            });
        }
    }
    
    updateWelcomeScreen() {
        const greetingTime = document.getElementById('greetingTime');
        const userName = document.getElementById('userFirstName');
        
        if (greetingTime) {
            const hour = new Date().getHours();
            const greeting = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
            greetingTime.textContent = greeting;
        }
        
        if (userName && state.user) {
            userName.textContent = state.user.name?.split(' ')[0] || 'Visionary';
        }
        
        this.renderSuggestions();
    }
    
    renderSuggestions() {
        const grid = this.elements.suggestionGrid;
        if (!grid) return;
        
        const suggestions = [
            { icon: 'brain', text: 'Explain quantum computing in simple terms' },
            { icon: 'code', text: 'Write a Python script for data visualization' },
            { icon: 'chart-line', text: 'Analyze market trends for AI in 2026' },
            { icon: 'flask', text: 'Design an experiment for AGI testing' },
            { icon: 'globe', text: 'Summarize today\'s top technology news' },
            { icon: 'robot', text: 'Compare different AI model architectures' }
        ];
        
        grid.innerHTML = suggestions.map(s => `
            <div class="suggestion-card" onclick="useSuggestion('${utils.sanitizeHTML(s.text)}')">
                <i class="fas fa-${s.icon}"></i>
                <span>${utils.sanitizeHTML(s.text)}</span>
            </div>
        `).join('');
    }
    
    updateMetrics() {
        // Update token count
        if (this.elements.tokenUsage) {
            this.elements.tokenUsage.textContent = `${utils.formatNumber(state.metrics.tokensUsed)} tokens`;
        }
        
        // Update message count
        if (this.elements.messageCount) {
            this.elements.messageCount.textContent = `${state.messages.length} messages`;
        }
        
        // Update model indicator
        if (this.elements.currentModel) {
            const modelNames = {
                quantum: 'JARVIS Quantum',
                lightning: 'JARVIS Lightning',
                balanced: 'JARVIS Balanced',
                legacy: 'JARVIS Legacy'
            };
            this.elements.currentModel.textContent = modelNames[state.currentModel] || state.currentModel;
        }
    }
    
    renderAgents() {
        const container = this.elements.agentList;
        if (!container) return;
        
        const agents = [
            { id: 'researcher', name: 'Researcher', icon: 'database', active: state.activeAgents.has('researcher') },
            { id: 'coder', name: 'Coder', icon: 'code', active: state.activeAgents.has('coder') },
            { id: 'analyst', name: 'Analyst', icon: 'chart-line', active: state.activeAgents.has('analyst') },
            { id: 'writer', name: 'Writer', icon: 'pen', active: state.activeAgents.has('writer') }
        ];
        
        container.innerHTML = agents.map(agent => `
            <div class="agent-item ${agent.active ? 'active' : ''}" onclick="toggleAgent('${agent.id}')">
                <i class="fas fa-${agent.icon}"></i>
                <span>${agent.name}</span>
                <span class="agent-status ${agent.active ? '' : 'idle'}">${agent.active ? 'Active' : 'Idle'}</span>
            </div>
        `).join('');
    }
    
    renderTools() {
        const container = this.elements.toolsGrid;
        if (!container) return;
        
        const tools = [
            { id: 'web-search', name: 'Web Search', icon: 'globe', active: state.webSearchEnabled },
            { id: 'code-interpreter', name: 'Code', icon: 'terminal' },
            { id: 'image-gen', name: 'Image Gen', icon: 'paint-brush' },
            { id: 'data-analysis', name: 'Analysis', icon: 'chart-pie' }
        ];
        
        container.innerHTML = tools.map(tool => `
            <div class="tool-item ${tool.active ? 'active' : ''}" onclick="activateTool('${tool.id}')">
                <i class="fas fa-${tool.icon}"></i>
                <span>${tool.name}</span>
            </div>
        `).join('');
    }
}

// Initialize UI manager
const ui = new UIManager();

// ==================== CHAT MANAGER ====================
class ChatManager {
    constructor() {
        this.currentStream = null;
    }
    
    async sendMessage(message) {
        if (!message || state.isProcessing) return;
        
        if (!state.currentSession) {
            await this.createNewSession();
        }
        
        const userMessage = {
            role: 'user',
            content: message,
            timestamp: new Date().toISOString()
        };
        
        state.addMessage(userMessage);
        ui.elements.chatMessages.appendChild(ui.renderMessage(userMessage));
        ui.scrollToBottom();
        
        ui.elements.messageInput.value = '';
        ui.elements.welcomeScreen.style.display = 'none';
        
        state.isProcessing = true;
        ui.elements.sendButton.disabled = true;
        ui.showTypingIndicator();
        
        try {
            const startTime = Date.now();
            const response = await api.sendMessage(
                state.currentSession.id,
                message,
                state.currentModel
            );
            
            ui.hideTypingIndicator();
            
            if (response.success) {
                const assistantMessage = {
                    role: 'assistant',
                    content: response.response,
                    model: response.model,
                    timestamp: new Date().toISOString(),
                    responseTime: response.response_time
                };
                
                state.addMessage(assistantMessage);
                ui.elements.chatMessages.appendChild(ui.renderMessage(assistantMessage));
                
                // Update metrics
                state.updateMetrics('avgResponseTime', 
                    (state.metrics.avgResponseTime * (state.messages.length - 1) + response.response_time) / state.messages.length
                );
                
                // Update session title if first message
                if (response.is_first_message) {
                    await this.updateSessionTitle(message);
                }
                
                // Speak response if voice enabled
                if (state.voiceEnabled) {
                    this.speakResponse(response.response);
                }
            } else {
                this.showError(response.error || 'Failed to get response');
            }
            
        } catch (error) {
            ui.hideTypingIndicator();
            this.showError(error.message);
        } finally {
            state.isProcessing = false;
            ui.elements.sendButton.disabled = false;
            ui.elements.messageInput.focus();
            ui.scrollToBottom();
            ui.updateMetrics();
        }
    }
    
    async createNewSession() {
        try {
            const response = await api.createSession();
            if (response.success) {
                state.currentSession = { id: response.session_id, title: 'New Chat' };
                state.sessions.unshift(state.currentSession);
                ui.elements.conversationTitle.textContent = 'New Chat';
                state.clearMessages();
                ui.elements.chatMessages.innerHTML = '';
                ui.elements.welcomeScreen.style.display = 'flex';
                ui.updateWelcomeScreen();
                
                // Update sidebar
                await this.loadSessions();
            }
        } catch (error) {
            showNotification('Failed to create new session', 'error');
        }
    }
    
    async loadSessions() {
        try {
            const response = await api.getSessions();
            if (response.success) {
                state.sessions = response.sessions;
                this.renderSessionList();
            }
        } catch (error) {
            console.error('Failed to load sessions:', error);
        }
    }
    
    async loadSession(sessionId) {
        if (state.currentSession?.id === sessionId) return;
        
        try {
            const response = await api.getSessionMessages(sessionId);
            if (response.success) {
                state.currentSession = state.sessions.find(s => s.id === sessionId);
                state.messages = response.messages;
                
                ui.elements.conversationTitle.textContent = state.currentSession?.title || 'Chat';
                ui.elements.chatMessages.innerHTML = '';
                ui.elements.welcomeScreen.style.display = 'none';
                
                state.messages.forEach(msg => {
                    ui.elements.chatMessages.appendChild(ui.renderMessage(msg));
                });
                
                ui.scrollToBottom(false);
                ui.updateMetrics();
                
                // Update active state in sidebar
                this.renderSessionList();
            }
        } catch (error) {
            showNotification('Failed to load session', 'error');
        }
    }
    
    renderSessionList() {
        const container = document.getElementById('chatHistoryList');
        if (!container) return;
        
        const grouped = this.groupSessionsByDate(state.sessions);
        
        container.innerHTML = '';
        
        Object.entries(grouped).forEach(([group, sessions]) => {
            if (sessions.length === 0) return;
            
            const groupDiv = document.createElement('div');
            groupDiv.className = 'history-group';
            groupDiv.innerHTML = `<div class="group-title">${group}</div>`;
            
            sessions.forEach(session => {
                const item = document.createElement('div');
                item.className = `chat-item ${state.currentSession?.id === session.id ? 'active' : ''}`;
                item.innerHTML = `
                    <i class="fas fa-comment"></i>
                    <span>${utils.sanitizeHTML(session.title || 'New Chat')}</span>
                    <button class="delete-session" onclick="event.stopPropagation(); deleteSession(${session.id})">
                        <i class="fas fa-trash"></i>
                    </button>
                `;
                item.onclick = () => this.loadSession(session.id);
                groupDiv.appendChild(item);
            });
            
            container.appendChild(groupDiv);
        });
    }
    
    groupSessionsByDate(sessions) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const lastWeek = new Date(today);
        lastWeek.setDate(lastWeek.getDate() - 7);
        
        const groups = {
            'Today': [],
            'Yesterday': [],
            'Last 7 Days': [],
            'Older': []
        };
        
        sessions.forEach(session => {
            const date = new Date(session.updated_at);
            if (date >= today) groups['Today'].push(session);
            else if (date >= yesterday) groups['Yesterday'].push(session);
            else if (date >= lastWeek) groups['Last 7 Days'].push(session);
            else groups['Older'].push(session);
        });
        
        return groups;
    }
    
    async updateSessionTitle(firstMessage) {
        const title = firstMessage.length > 50 ? firstMessage.substring(0, 47) + '...' : firstMessage;
        state.currentSession.title = title;
        ui.elements.conversationTitle.textContent = title;
    }
    
    speakResponse(text) {
        if (!CONFIG.features.speechSynthesis) return;
        
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = CONFIG.voiceLang;
        utterance.rate = 1.0;
        utterance.pitch = 1.0;
        
        window.speechSynthesis.speak(utterance);
    }
    
    showError(message) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'message system';
        errorDiv.innerHTML = `
            <div class="message-content">
                <i class="fas fa-exclamation-triangle"></i>
                ${utils.sanitizeHTML(message)}
            </div>
        `;
        
        ui.elements.chatMessages.appendChild(errorDiv);
        ui.scrollToBottom();
    }
}

// Initialize chat manager
const chat = new ChatManager();

// ==================== VOICE ASSISTANT ====================
class VoiceAssistant {
    constructor() {
        this.recognition = null;
        this.isListening = false;
        this.transcript = '';
        this.initializeRecognition();
    }
    
    initializeRecognition() {
        if (!CONFIG.features.speechRecognition) return;
        
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = CONFIG.voiceLang;
        
        this.recognition.onstart = () => {
            this.isListening = true;
            this.updateUI();
        };
        
        this.recognition.onend = () => {
            this.isListening = false;
            this.updateUI();
        };
        
        this.recognition.onresult = (event) => {
            let interim = '';
            let final = '';
            
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    final += transcript;
                } else {
                    interim += transcript;
                }
            }
            
            this.transcript = final || interim;
            this.updateTranscript();
            
            if (final) {
                this.handleFinalTranscript(final);
            }
        };
        
        this.recognition.onerror = (event) => {
            console.error('Recognition error:', event.error);
            this.isListening = false;
            this.updateUI();
            showNotification('Voice recognition error', 'error');
        };
    }
    
    start() {
        if (!this.recognition) {
            showNotification('Speech recognition not supported', 'error');
            return;
        }
        
        try {
            this.recognition.start();
            ui.elements.voiceModal.classList.add('active');
        } catch (e) {
            console.error('Failed to start recognition:', e);
        }
    }
    
    stop() {
        if (this.recognition) {
            this.recognition.stop();
        }
        ui.elements.voiceModal.classList.remove('active');
    }
    
    toggle() {
        if (this.isListening) {
            this.stop();
        } else {
            this.start();
        }
    }
    
    updateUI() {
        const status = document.getElementById('voiceStatus');
        const visualizer = document.getElementById('voiceVisualizer');
        
        if (status) {
            status.textContent = this.isListening ? 'Listening...' : 'Stopped';
        }
        
        if (visualizer) {
            if (this.isListening) {
                this.startVisualizer(visualizer);
            } else {
                this.stopVisualizer();
            }
        }
    }
    
    updateTranscript() {
        const transcriptEl = document.getElementById('voiceTranscript');
        if (transcriptEl) {
            transcriptEl.textContent = this.transcript;
        }
    }
    
    handleFinalTranscript(text) {
        ui.elements.messageInput.value = text;
        this.stop();
        
        // Auto-send if enabled
        if (state.autoSendVoice) {
            setTimeout(() => sendMessage(), 500);
        }
    }
    
    startVisualizer(container) {
        container.innerHTML = '<div class="visualizer-bars"></div>';
        const bars = container.querySelector('.visualizer-bars');
        
        for (let i = 0; i < 20; i++) {
            const bar = document.createElement('div');
            bar.className = 'visualizer-bar';
            bar.style.animationDelay = `${i * 0.05}s`;
            bars.appendChild(bar);
        }
    }
    
    stopVisualizer() {
        const visualizer = document.getElementById('voiceVisualizer');
        if (visualizer) {
            visualizer.innerHTML = '';
        }
    }
}

// Initialize voice assistant
const voice = new VoiceAssistant();

// ==================== THREE.JS VISUALIZATIONS ====================
class Visualizer3D {
    constructor() {
        this.scenes = new Map();
        this.isInitialized = false;
        
        if (CONFIG.features.webGL && !window.deviceCapabilities?.isLowEnd) {
            this.initialize();
        }
    }
    
    initialize() {
        this.isInitialized = true;
        this.createLoginVisualization();
        this.createLoaderVisualization();
        this.createOrbVisualization();
    }
    
    createLoginVisualization() {
        const container = document.getElementById('neuralNetworkViz');
        if (!container) return;
        
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
        
        renderer.setSize(container.clientWidth, container.clientHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        container.appendChild(renderer.domElement);
        
        // Create neural network visualization
        const particles = new THREE.BufferGeometry();
        const particleCount = 200;
        const positions = new Float32Array(particleCount * 3);
        const colors = new Float32Array(particleCount * 3);
        
        for (let i = 0; i < particleCount; i++) {
            // Sphere distribution
            const radius = 2;
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.acos((Math.random() * 2) - 1);
            
            positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
            positions[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
            positions[i * 3 + 2] = radius * Math.cos(phi);
            
            // Color based on position
            colors[i * 3] = 0.0;
            colors[i * 3 + 1] = 1.0;
            colors[i * 3 + 2] = 1.0;
        }
        
        particles.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        particles.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        
        const particleMaterial = new THREE.PointsMaterial({
            size: 0.05,
            vertexColors: true,
            transparent: true,
            blending: THREE.AdditiveBlending
        });
        
        const particleSystem = new THREE.Points(particles, particleMaterial);
        scene.add(particleSystem);
        
        // Add lines between particles
        const lines = this.createConnections(positions);
        scene.add(lines);
        
        camera.position.z = 5;
        
        this.scenes.set('login', { scene, camera, renderer, particleSystem, lines });
        this.animate('login');
    }
    
    createConnections(positions) {
        const lineGeometry = new THREE.BufferGeometry();
        const vertices = [];
        
        for (let i = 0; i < positions.length; i += 3) {
            for (let j = i + 3; j < positions.length; j += 3) {
                const dx = positions[i] - positions[j];
                const dy = positions[i + 1] - positions[j + 1];
                const dz = positions[i + 2] - positions[j + 2];
                const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
                
                if (distance < 1.5) {
                    vertices.push(positions[i], positions[i + 1], positions[i + 2]);
                    vertices.push(positions[j], positions[j + 1], positions[j + 2]);
                }
            }
        }
        
        lineGeometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
        
        const lineMaterial = new THREE.LineBasicMaterial({ 
            color: 0x00ffff, 
            transparent: true, 
            opacity: 0.2 
        });
        
        return new THREE.LineSegments(lineGeometry, lineMaterial);
    }
    
    createLoaderVisualization() {
        const canvas = document.getElementById('loaderCanvas');
        if (!canvas) return;
        
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ canvas, alpha: true });
        
        renderer.setSize(400, 400);
        
        // Create a rotating torus knot
        const geometry = new THREE.TorusKnotGeometry(1, 0.3, 100, 16);
        const material = new THREE.MeshBasicMaterial({ 
            color: 0x00ffff,
            wireframe: true,
            transparent: true,
            opacity: 0.8
        });
        
        const knot = new THREE.Mesh(geometry, material);
        scene.add(knot);
        
        // Add glowing particles around it
        const particles = new THREE.BufferGeometry();
        const particlePositions = [];
        
        for (let i = 0; i < 100; i++) {
            const angle = (i / 100) * Math.PI * 2;
            const radius = 1.8;
            particlePositions.push(
                Math.cos(angle) * radius,
                Math.sin(angle) * radius,
                0
            );
        }
        
        particles.setAttribute('position', new THREE.Float32BufferAttribute(particlePositions, 3));
        
        const particleMaterial = new THREE.PointsMaterial({
            color: 0xff00ff,
            size: 0.05,
            transparent: true,
            blending: THREE.AdditiveBlending
        });
        
        const particleSystem = new THREE.Points(particles, particleMaterial);
        scene.add(particleSystem);
        
        camera.position.z = 4;
        
        this.scenes.set('loader', { scene, camera, renderer, knot, particleSystem });
        this.animate('loader');
    }
    
    createOrbVisualization() {
        const container = document.getElementById('orbCanvas');
        if (!container) return;
        
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ canvas: container, alpha: true });
        
        renderer.setSize(120, 120);
        
        // Create a glowing orb
        const geometry = new THREE.SphereGeometry(1, 32, 32);
        const material = new THREE.MeshPhongMaterial({
            color: 0x00ffff,
            emissive: 0x004444,
            transparent: true,
            opacity: 0.9,
            shininess: 30
        });
        
        const orb = new THREE.Mesh(geometry, material);
        scene.add(orb);
        
        // Add lights
        const light1 = new THREE.PointLight(0x00ffff, 1, 10);
        light1.position.set(2, 2, 2);
        scene.add(light1);
        
        const light2 = new THREE.PointLight(0xff00ff, 0.5, 10);
        light2.position.set(-2, -1, 2);
        scene.add(light2);
        
        camera.position.z = 3;
        
        this.scenes.set('orb', { scene, camera, renderer, orb });
        this.animate('orb');
    }
    
    animate(sceneName) {
        const sceneData = this.scenes.get(sceneName);
        if (!sceneData) return;
        
        const animate = () => {
            requestAnimationFrame(animate);
            
            if (sceneName === 'login' && sceneData.particleSystem) {
                sceneData.particleSystem.rotation.y += 0.001;
                sceneData.lines.rotation.y += 0.001;
            }
            
            if (sceneName === 'loader' && sceneData.knot) {
                sceneData.knot.rotation.x += 0.01;
                sceneData.knot.rotation.y += 0.01;
                sceneData.particleSystem.rotation.z += 0.005;
            }
            
            if (sceneName === 'orb' && sceneData.orb) {
                sceneData.orb.rotation.y += 0.005;
            }
            
            sceneData.renderer.render(sceneData.scene, sceneData.camera);
        };
        
        animate();
    }
    
    resize(sceneName, width, height) {
        const sceneData = this.scenes.get(sceneName);
        if (!sceneData) return;
        
        sceneData.camera.aspect = width / height;
        sceneData.camera.updateProjectionMatrix();
        sceneData.renderer.setSize(width, height);
    }
}

// Initialize 3D visualizations if WebGL is supported
const visualizer = CONFIG.features.webGL ? new Visualizer3D() : null;

// ==================== GLOBAL FUNCTIONS ====================

// Initialize application
async function initializeApp() {
    try {
        // Check login status
        const response = await api.getCurrentUser();
        
        if (response.success) {
            state.user = response.user;
            await onLoginSuccess();
        } else {
            showLoginScreen();
        }
        
    } catch (error) {
        console.error('Initialization error:', error);
        showLoginScreen();
    } finally {
        // Hide loader
        setTimeout(() => {
            document.getElementById('initialLoader')?.classList.add('hidden');
        }, 1000);
    }
}

async function onLoginSuccess() {
    // Hide login, show app
    document.getElementById('loginScreen').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    
    // Update user info
    document.getElementById('userName').textContent = state.user.name;
    document.getElementById('userAvatar').src = state.user.avatar || '';
    document.getElementById('userAvatar').alt = state.user.name.charAt(0).toUpperCase();
    
    // Load sessions
    await chat.loadSessions();
    
    // Update UI
    ui.updateWelcomeScreen();
    ui.renderAgents();
    ui.renderTools();
    ui.updateMetrics();
    
    // Apply theme
    applyTheme(state.theme);
    applyAccent(state.accent);
    
    // Request notification permission
    if (CONFIG.features.notifications && Notification.permission === 'default') {
        Notification.requestPermission();
    }
    
    // Process offline queue
    if (navigator.onLine) {
        api.processOfflineQueue();
    }
    
    showNotification(`Welcome back, ${state.user.name}!`, 'success');
}

function showLoginScreen() {
    document.getElementById('loginScreen').style.display = 'flex';
    document.getElementById('app').style.display = 'none';
}

// Authentication functions
function googleLogin() {
    api.login();
}

function continueAsGuest() {
    state.user = { name: 'Guest', email: 'guest@jarvis.ai', isGuest: true };
    onLoginSuccess();
}

async function logout() {
    if (confirm('Are you sure you want to logout?')) {
        await api.logout();
    }
}

// Chat functions
function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (message) {
        chat.sendMessage(message);
    }
}

function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

function useSuggestion(text) {
    document.getElementById('messageInput').value = text;
    sendMessage();
}

async function createNewChat() {
    await chat.createNewSession();
    if (window.innerWidth <= 768) {
        toggleCommandCenter();
    }
}

async function deleteSession(sessionId) {
    if (!confirm('Delete this conversation?')) return;
    
    try {
        await api.deleteSession(sessionId);
        state.sessions = state.sessions.filter(s => s.id !== sessionId);
        
        if (state.currentSession?.id === sessionId) {
            await chat.createNewSession();
        }
        
        chat.renderSessionList();
        showNotification('Conversation deleted', 'success');
    } catch (error) {
        showNotification('Failed to delete session', 'error');
    }
}

function copyMessage(messageId) {
    const message = state.messages.find(m => m.id === messageId);
    if (message) {
        utils.copyToClipboard(message.content);
        showNotification('Copied to clipboard!', 'success');
    }
}

async function regenerateResponse(messageId) {
    // Find the user message before this response
    const index = state.messages.findIndex(m => m.id === messageId);
    if (index > 0) {
        const userMessage = state.messages[index - 1];
        if (userMessage.role === 'user') {
            // Remove the old response
            state.messages.splice(index, 1);
            
            // Resend the user message
            await chat.sendMessage(userMessage.content);
        }
    }
}

// UI Toggle functions
function toggleCommandCenter() {
    const center = document.getElementById('commandCenter');
    state.commandCenterOpen = !state.commandCenterOpen;
    center.classList.toggle('open', state.commandCenterOpen);
}

function toggleCanvas() {
    const canvas = document.getElementById('contextCanvas');
    state.canvasOpen = !state.canvasOpen;
    canvas.classList.toggle('open', state.canvasOpen);
}

function toggleVoiceAssistant() {
    voice.toggle();
}

function toggleWebSearch() {
    state.webSearchEnabled = !state.webSearchEnabled;
    const btn = document.getElementById('webSearchBtn');
    btn.style.color = state.webSearchEnabled ? 'var(--accent-primary)' : '';
    showNotification(`Web search ${state.webSearchEnabled ? 'enabled' : 'disabled'}`, 'info');
}

function toggleAgent(agentId) {
    if (state.activeAgents.has(agentId)) {
        state.activeAgents.delete(agentId);
    } else {
        state.activeAgents.add(agentId);
    }
    ui.renderAgents();
    showNotification(`${agentId} agent ${state.activeAgents.has(agentId) ? 'activated' : 'deactivated'}`, 'info');
}

function activateTool(toolId) {
    switch(toolId) {
        case 'web-search':
            toggleWebSearch();
            break;
        case 'code-interpreter':
            showNotification('Code interpreter ready', 'info');
            break;
        default:
            showNotification(`${toolId} activated`, 'info');
    }
    ui.renderTools();
}

// Settings functions
function openSettings() {
    renderSettingsModal();
    document.getElementById('settingsModal').classList.add('active');
}

function closeSettings() {
    document.getElementById('settingsModal').classList.remove('active');
}

function renderSettingsModal() {
    const body = document.querySelector('#settingsModal .modal-body');
    if (!body) return;
    
    body.innerHTML = `
        <div class="settings-container">
            <div class="settings-section">
                <h3>Appearance</h3>
                <div class="theme-grid">
                    <div class="theme-option ${state.theme === 'dark' ? 'active' : ''}" onclick="setTheme('dark')">
                        <div class="theme-preview dark"></div>
                        <span>Dark</span>
                    </div>
                    <div class="theme-option ${state.theme === 'light' ? 'active' : ''}" onclick="setTheme('light')">
                        <div class="theme-preview light"></div>
                        <span>Light</span>
                    </div>
                    <div class="theme-option ${state.theme === 'oled' ? 'active' : ''}" onclick="setTheme('oled')">
                        <div class="theme-preview oled"></div>
                        <span>OLED</span>
                    </div>
                </div>
            </div>
            
            <div class="settings-section">
                <h3>Accent Color</h3>
                <div class="color-palette" id="colorPalette"></div>
            </div>
            
            <div class="settings-section">
                <h3>AI Model</h3>
                <select id="modelSelect" class="model-selector" onchange="setModel(this.value)">
                    <option value="quantum" ${state.currentModel === 'quantum' ? 'selected' : ''}>JARVIS Quantum (Most Capable)</option>
                    <option value="lightning" ${state.currentModel === 'lightning' ? 'selected' : ''}>JARVIS Lightning (Fastest)</option>
                    <option value="balanced" ${state.currentModel === 'balanced' ? 'selected' : ''}>JARVIS Balanced (Recommended)</option>
                    <option value="legacy" ${state.currentModel === 'legacy' ? 'selected' : ''}>JARVIS Legacy (Stable)</option>
                </select>
            </div>
            
            <div class="settings-section">
                <h3>Advanced</h3>
                <div class="setting-item">
                    <label>
                        <input type="checkbox" id="streamingMode" ${state.streamingEnabled ? 'checked' : ''} onchange="toggleStreaming()">
                        Enable Response Streaming
                    </label>
                </div>
                <div class="setting-item">
                    <label>
                        <input type="checkbox" id="autoSendVoice" ${state.autoSendVoice ? 'checked' : ''} onchange="toggleAutoSendVoice()">
                        Auto-send voice messages
                    </label>
                </div>
                <div class="setting-item">
                    <label>
                        <input type="checkbox" id="webSearchDefault" ${state.webSearchEnabled ? 'checked' : ''} onchange="toggleWebSearch()">
                        Web Search by default
                    </label>
                </div>
            </div>
            
            <div class="settings-section">
                <h3>Data Management</h3>
                <button class="upload-knowledge-btn" onclick="exportAllData()">
                    <i class="fas fa-download"></i>
                    Export All Data
                </button>
                <button class="upload-knowledge-btn danger" onclick="clearAllData()" style="margin-top: 12px; background: var(--color-red);">
                    <i class="fas fa-trash"></i>
                    Clear All Data
                </button>
            </div>
            
            <div class="settings-section">
                <h3>About</h3>
                <div class="knowledge-stats">
                    <div class="stat-row">
                        <span>Version</span>
                        <span>${CONFIG.version}</span>
                    </div>
                    <div class="stat-row">
                        <span>Built by</span>
                        <span>Krish Paliwal</span>
                    </div>
                    <div class="stat-row">
                        <span>Messages sent</span>
                        <span>${utils.formatNumber(state.metrics.messagesSent)}</span>
                    </div>
                    <div class="stat-row">
                        <span>Avg response time</span>
                        <span>${state.metrics.avgResponseTime.toFixed(2)}s</span>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    renderColorPalette();
}

function renderColorPalette() {
    const palette = document.getElementById('colorPalette');
    if (!palette) return;
    
    const colors = [
        'cyan', 'magenta', 'orange', 'green', 'pink', 
        'purple', 'yellow', 'blue', 'red', 'teal', 'indigo', 'gold'
    ];
    
    palette.innerHTML = colors.map(color => `
        <div class="color-option ${state.accent === color ? 'active' : ''}" 
             style="background: var(--color-${color});" 
             onclick="setAccent('${color}')">
        </div>
    `).join('');
}

function setTheme(theme) {
    state.theme = theme;
    applyTheme(theme);
    state.saveToStorage();
    renderSettingsModal();
}

function applyTheme(theme) {
    document.body.setAttribute('data-theme', theme);
}

function setAccent(color) {
    state.accent = color;
    applyAccent(color);
    state.saveToStorage();
    renderSettingsModal();
}

function applyAccent(color) {
    document.body.setAttribute('data-accent', color);
}

function setModel(model) {
    state.currentModel = model;
    state.saveToStorage();
    ui.updateMetrics();
    showNotification(`Switched to ${model} model`, 'success');
}

function toggleStreaming() {
    state.streamingEnabled = !state.streamingEnabled;
    state.saveToStorage();
}

function toggleAutoSendVoice() {
    state.autoSendVoice = !state.autoSendVoice;
    state.saveToStorage();
}

// Data management
function exportAllData() {
    const data = {
        version: CONFIG.version,
        exportDate: new Date().toISOString(),
        user: state.user,
        sessions: state.sessions,
        messages: state.messages,
        metrics: state.metrics,
        settings: {
            theme: state.theme,
            accent: state.accent,
            model: state.currentModel
        }
    };
    
    utils.downloadFile(
        JSON.stringify(data, null, 2),
        `jarvis-export-${Date.now()}.json`,
        'application/json'
    );
    
    showNotification('Data exported successfully!', 'success');
}

async function clearAllData() {
    if (!confirm('⚠️ This will permanently delete all your data. Are you absolutely sure?')) return;
    
    try {
        // Delete all sessions
        for (const session of state.sessions) {
            await api.deleteSession(session.id);
        }
        
        // Clear local state
        state.sessions = [];
        state.messages = [];
        state.metrics = {
            tokensUsed: 0,
            messagesSent: 0,
            sessionsCreated: 0,
            avgResponseTime: 0
        };
        
        // Create new session
        await chat.createNewSession();
        
        showNotification('All data cleared', 'success');
    } catch (error) {
        showNotification('Failed to clear all data', 'error');
    }
}

// Notification system
function showNotification(message, type = 'info') {
    const container = document.getElementById('notificationContainer');
    if (!container) return;
    
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    
    const icons = {
        success: 'check-circle',
        error: 'exclamation-circle',
        warning: 'exclamation-triangle',
        info: 'info-circle'
    };
    
    notification.innerHTML = `
        <i class="fas fa-${icons[type]}"></i>
        <span class="notification-message">${utils.sanitizeHTML(message)}</span>
    `;
    
    container.appendChild(notification);
    
    // Animate in
    setTimeout(() => notification.classList.add('show'), 10);
    
    // Remove after delay
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transform = 'translateY(20px)';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

// Modal functions
function showModal(title, content, actions = []) {
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h2>${utils.sanitizeHTML(title)}</h2>
                <button class="close-modal" onclick="this.closest('.modal').remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                ${content}
            </div>
            ${actions.length ? `
                <div class="modal-footer">
                    ${actions.map(action => `
                        <button class="modal-action" onclick="${action.onclick}">${action.text}</button>
                    `).join('')}
                </div>
            ` : ''}
        </div>
    `;
    
    document.body.appendChild(modal);
}

function closeAllModals() {
    document.querySelectorAll('.modal').forEach(modal => {
        modal.classList.remove('active');
    });
    document.getElementById('voiceModal')?.classList.remove('active');
}

// FAB menu
function toggleFabMenu() {
    document.querySelector('.fab-container').classList.toggle('active');
}

// Quick actions
function quickResearch() {
    useSuggestion('Research the latest advancements in AI and summarize key findings');
}

function generateReport() {
    useSuggestion('Generate a comprehensive report on the current state of artificial intelligence');
}

function summarizePage() {
    showNotification('Page summarization coming soon!', 'info');
}

function uploadKnowledge() {
    showNotification('Knowledge upload coming soon!', 'info');
}

function openFileUpload() {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.txt,.pdf,.doc,.docx,.jpg,.png,.gif';
    input.onchange = (e) => {
        const files = Array.from(e.target.files);
        showNotification(`Selected ${files.length} file(s)`, 'info');
        // TODO: Implement file upload
    };
    input.click();
}

function shareConversation() {
    if (!state.currentSession) return;
    
    const url = `${window.location.origin}/share/${state.currentSession.id}`;
    
    if (CONFIG.features.share) {
        navigator.share({
            title: 'JARVIS Conversation',
            text: 'Check out my conversation with JARVIS!',
            url: url
        }).catch(() => {
            utils.copyToClipboard(url);
            showNotification('Share link copied to clipboard!', 'success');
        });
    } else {
        utils.copyToClipboard(url);
        showNotification('Share link copied to clipboard!', 'success');
    }
}

function bookmarkConversation() {
    if (!state.currentSession) return;
    
    state.currentSession.bookmarked = !state.currentSession.bookmarked;
    showNotification(
        state.currentSession.bookmarked ? 'Conversation bookmarked!' : 'Bookmark removed',
        'success'
    );
}

// Progress loader
function updateLoaderProgress(progress, status) {
    const progressBar = document.getElementById('loaderProgress');
    const statusEl = document.getElementById('loaderStatus');
    
    if (progressBar) {
        progressBar.style.width = `${progress}%`;
    }
    
    if (statusEl) {
        statusEl.textContent = status;
    }
}

// Simulate loading progress
let loadProgress = 0;
const loadInterval = setInterval(() => {
    loadProgress += Math.random() * 15;
    if (loadProgress >= 100) {
        loadProgress = 100;
        updateLoaderProgress(100, 'JARVIS Core Online');
        clearInterval(loadInterval);
    } else {
        const statuses = [
            'Initializing Neural Networks...',
            'Loading Language Models...',
            'Calibrating Quantum Circuits...',
            'Establishing Secure Connection...',
            'Almost Ready...'
        ];
        const status = statuses[Math.floor(loadProgress / 25)];
        updateLoaderProgress(loadProgress, status);
    }
}, 200);

// Initialize on load
window.addEventListener('DOMContentLoaded', () => {
    initializeApp();
    
    // Set greeting time
    const hour = new Date().getHours();
    const greeting = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
    const greetingEl = document.getElementById('greetingTime');
    if (greetingEl) greetingEl.textContent = greeting;
    
    // Check device capabilities
    if (window.deviceCapabilities?.isLowEnd) {
        document.body.classList.add('low-end-device');
    }
    
    if (window.deviceCapabilities?.isMobile) {
        document.body.classList.add('mobile-device');
    }
});

// Handle visibility change
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        // Pause animations/updates
    } else {
        // Resume animations/updates
        if (state.user) {
            ui.updateMetrics();
        }
    }
});

// Handle page unload
window.addEventListener('beforeunload', () => {
    state.saveToStorage();
});

// Service Worker registration (if supported)
if ('serviceWorker' in navigator && window.location.protocol === 'https:') {
    navigator.serviceWorker.register('/sw.js').catch(console.warn);
}

// Export for global access
window.sendMessage = sendMessage;
window.handleKeyDown = handleKeyDown;
window.autoResize = autoResize;
window.useSuggestion = useSuggestion;
window.createNewChat = createNewChat;
window.deleteSession = deleteSession;
window.copyMessage = copyMessage;
window.regenerateResponse = regenerateResponse;
window.toggleCommandCenter = toggleCommandCenter;
window.toggleCanvas = toggleCanvas;
window.toggleVoiceAssistant = toggleVoiceAssistant;
window.toggleWebSearch = toggleWebSearch;
window.toggleAgent = toggleAgent;
window.activateTool = activateTool;
window.openSettings = openSettings;
window.closeSettings = closeSettings;
window.setTheme = setTheme;
window.setAccent = setAccent;
window.setModel = setModel;
window.toggleStreaming = toggleStreaming;
window.toggleAutoSendVoice = toggleAutoSendVoice;
window.exportAllData = exportAllData;
window.clearAllData = clearAllData;
window.toggleFabMenu = toggleFabMenu;
window.quickResearch = quickResearch;
window.generateReport = generateReport;
window.summarizePage = summarizePage;
window.uploadKnowledge = uploadKnowledge;
window.openFileUpload = openFileUpload;
window.shareConversation = shareConversation;
window.bookmarkConversation = bookmarkConversation;
window.googleLogin = googleLogin;
window.continueAsGuest = continueAsGuest;
window.logout = logout;

console.log('🚀 JARVIS Cognitive Architecture v4.0 - Initialized');
console.log('Built by Krish Paliwal - The Future of AI');
