// SuperNova Code-RAG Dashboard Application Logic

document.addEventListener("DOMContentLoaded", () => {
    fetchSystemStatus();

    const syncBtn = document.getElementById("syncBtn");
    const repoUrlInput = document.getElementById("repoUrlInput");
    const sendBtn = document.getElementById("sendBtn");
    const chatInput = document.getElementById("chatInput");
    const clearChatBtn = document.getElementById("clearChatBtn");

    syncBtn.addEventListener("click", () => {
        const repoUrl = repoUrlInput.value.trim();
        if (repoUrl) {
            startRepoSync(repoUrl);
        } else {
            showToast("Please enter a valid Git repository URL or path", "error");
        }
    });

    sendBtn.addEventListener("click", () => {
        sendChatMessage();
    });

    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    clearChatBtn.addEventListener("click", () => {
        document.getElementById("chatHistory").innerHTML = `
            <div class="message message-ai">
                <div class="avatar"><i class="fa-solid fa-robot"></i></div>
                <div class="message-content">
                    <p>Chat cleared! How can I assist you with your repository?</p>
                </div>
            </div>
        `;
    });
});

// Fetch system status stats
async function fetchSystemStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        if (data.status === "online") {
            document.getElementById("statVectorChunks").innerText = data.vector_chunks_count || 0;
            const triples = data.graph_stats?.in_memory_triples || 
                            (data.graph_stats?.relationships ? Object.values(data.graph_stats.relationships).reduce((a,b)=>a+b,0) : 0);
            document.getElementById("statTriples").innerText = triples;
        }
    } catch (e) {
        console.warn("Notice checking system status:", e);
    }
}

// Server-Sent Events (SSE) Live Progress Streaming
function startRepoSync(repoUrl) {
    const wrapper = document.getElementById("progressWrapper");
    const barFill = document.getElementById("progressBarFill");
    const msg = document.getElementById("progressMessage");
    const percent = document.getElementById("progressPercent");
    const syncBtn = document.getElementById("syncBtn");
    const summaryPills = document.getElementById("summaryPills");

    wrapper.style.display = "flex";
    barFill.style.width = "5%";
    msg.innerText = "Initializing connection...";
    percent.innerText = "5%";
    syncBtn.disabled = true;
    syncBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Ingesting...`;

    const eventSource = new EventSource(`/api/stream-sync?repo=${encodeURIComponent(repoUrl)}`);

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        barFill.style.width = `${data.progress}%`;
        percent.innerText = `${data.progress}%`;
        msg.innerText = data.message;

        if (data.status === "complete") {
            eventSource.close();
            syncBtn.disabled = false;
            syncBtn.innerHTML = `<i class="fa-solid fa-bolt"></i> Ingest & Index`;
            showToast("Repository ingestion & indexing complete!", "success");

            if (data.summary) {
                summaryPills.style.display = "flex";
                document.getElementById("sumFiles").innerText = data.summary.processed_files || 0;
                document.getElementById("sumChunks").innerText = data.summary.total_vector_chunks || 0;
                const triples = data.summary.graph_stats?.in_memory_triples || 0;
                document.getElementById("sumTriples").innerText = triples;
            }
            fetchSystemStatus();
        } else if (data.status === "error") {
            eventSource.close();
            syncBtn.disabled = false;
            syncBtn.innerHTML = `<i class="fa-solid fa-bolt"></i> Ingest & Index`;
            showToast(`Ingestion error: ${data.message}`, "error");
        }
    };

    eventSource.onerror = (err) => {
        console.error("SSE Connection error:", err);
        eventSource.close();
        syncBtn.disabled = false;
        syncBtn.innerHTML = `<i class="fa-solid fa-bolt"></i> Ingest & Index`;
    };
}

// Quick prompt click handler
function useQuickPrompt(promptText) {
    const input = document.getElementById("chatInput");
    input.value = promptText;
    sendChatMessage();
}

// Send chat query to Flask API
async function sendChatMessage() {
    const input = document.getElementById("chatInput");
    const prompt = input.value.trim();
    if (!prompt) return;

    input.value = "";
    const history = document.getElementById("chatHistory");

    // Append User Message
    appendMessage("user", prompt);

    // Append AI Loading Message
    const loadingId = "loading-" + Date.now();
    appendLoadingMessage(loadingId);

    try {
        const res = await fetch("/api/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: prompt })
        });
        const data = await res.json();

        // Remove Loading Message
        document.getElementById(loadingId)?.remove();

        if (data.error) {
            appendMessage("ai", `⚠️ Error: ${data.error}`);
            return;
        }

        // Render AI Answer Message
        appendAIMessage(data.answer, data.code_update_proposal);

    } catch (e) {
        document.getElementById(loadingId)?.remove();
        appendMessage("ai", `❌ Failed to connect to server: ${e.message}`);
    }
}

// Append generic message bubble
function appendMessage(sender, text) {
    const history = document.getElementById("chatHistory");
    const msgDiv = document.createElement("div");
    msgDiv.className = `message message-${sender}`;
    
    const icon = sender === "user" ? "fa-user" : "fa-robot";
    msgDiv.innerHTML = `
        <div class="avatar"><i class="fa-solid ${icon}"></i></div>
        <div class="message-content"><p>${escapeHtml(text)}</p></div>
    `;
    history.appendChild(msgDiv);
    history.scrollTop = history.scrollHeight;
}

// Append loading message indicator
function appendLoadingMessage(id) {
    const history = document.getElementById("chatHistory");
    const msgDiv = document.createElement("div");
    msgDiv.className = "message message-ai";
    msgDiv.id = id;
    msgDiv.innerHTML = `
        <div class="avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-content">
            <p><i class="fa-solid fa-circle-notch fa-spin"></i> Searching vector chunks (BGE) & traversing OKF call graph...</p>
        </div>
    `;
    history.appendChild(msgDiv);
    history.scrollTop = history.scrollHeight;
}

// Append AI Message with markdown code blocks and Proposal Card
function appendAIMessage(answerText, proposal) {
    const history = document.getElementById("chatHistory");
    const msgDiv = document.createElement("div");
    msgDiv.className = "message message-ai";

    let formattedText = formatMarkdown(answerText);

    let proposalHtml = "";
    if (proposal && proposal.file_path && proposal.updated_code) {
        const codeId = "code-" + Date.now();
        window[codeId] = proposal.updated_code;

        proposalHtml = `
            <div class="code-proposal-card">
                <div class="proposal-header">
                    <span><i class="fa-solid fa-code-commit"></i> Code Update Proposal</span>
                    <span class="proposal-meta">Lines ${proposal.line_start} - ${proposal.line_end}</span>
                </div>
                <div class="proposal-meta" style="margin-bottom: 0.5rem;">Target File: <code>${escapeHtml(proposal.file_path)}</code></div>
                <div class="proposal-code-box">${escapeHtml(proposal.updated_code)}</div>
                <button class="btn btn-accent btn-sm" onclick="applyUpdate('${escapeHtml(proposal.file_path)}', ${proposal.line_start}, ${proposal.line_end}, '${codeId}', this)">
                    <i class="fa-solid fa-bolt"></i> ⚡ Apply Update & Surgically Re-Index
                </button>
            </div>
        `;
    }

    msgDiv.innerHTML = `
        <div class="avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-content">
            <div>${formattedText}</div>
            ${proposalHtml}
        </div>
    `;

    history.appendChild(msgDiv);
    history.scrollTop = history.scrollHeight;
}

// Apply code update via Flask API and trigger surgical re-indexing
async function applyUpdate(filePath, startLine, endLine, codeId, btnElement) {
    const updatedCode = window[codeId] || "";
    if (!updatedCode) {
        showToast("Error retrieving updated code snippet", "error");
        return;
    }

    btnElement.disabled = true;
    btnElement.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Applying & Re-indexing...`;

    try {
        const res = await fetch("/api/apply-update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                file_path: filePath,
                line_start: startLine,
                line_end: endLine,
                updated_code: updatedCode
            })
        });

        const data = await res.json();
        if (data.status === "success") {
            btnElement.className = "btn btn-secondary btn-sm";
            btnElement.style.borderColor = "#10b981";
            btnElement.style.color = "#10b981";
            btnElement.innerHTML = `<i class="fa-solid fa-check"></i> ✓ Patch Applied & Surgically Re-Indexed!`;
            showToast(`Successfully updated '${filePath}' and surgically re-indexed ChromaDB & OKF graph!`, "success");
            fetchSystemStatus();
        } else {
            btnElement.disabled = false;
            btnElement.innerHTML = `<i class="fa-solid fa-bolt"></i> Retry Apply Update`;
            showToast(`Failed: ${data.error}`, "error");
        }
    } catch (e) {
        btnElement.disabled = false;
        btnElement.innerHTML = `<i class="fa-solid fa-bolt"></i> Retry Apply Update`;
        showToast(`Server error: ${e.message}`, "error");
    }
}

// Advanced markdown and code block formatting
function formatMarkdown(text) {
    if (!text) return "";
    
    // Unescape literal \n and \t if present in raw string
    let raw = text.replace(/\\n/g, "\n").replace(/\\t/g, "    ");
    
    // Protect code blocks during HTML escaping
    const codeBlocks = [];
    raw = raw.replace(/```(?:[a-zA-Z0-9_\-]+)?\s*\n([\s\S]*?)```/g, (match, code) => {
        const placeholder = `__CODE_BLOCK_${codeBlocks.length}__`;
        codeBlocks.push(code.trim());
        return placeholder;
    });

    let html = escapeHtml(raw);

    // Markdown styling
    html = html.replace(/^### (.*$)/gim, '<h3 style="margin: 0.5rem 0; color: var(--accent-cyan);">$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h2 style="margin: 0.75rem 0; color: var(--accent-indigo);">$1</h2>');
    html = html.replace(/^# (.*$)/gim, '<h1 style="margin: 1rem 0;">$1</h1>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code style="background: rgba(15, 23, 42, 0.9); padding: 0.2rem 0.4rem; border-radius: 4px; color: var(--accent-cyan); font-family: var(--font-code);">$1</code>');

    // Replace line breaks outside code blocks
    html = html.replace(/\n/g, '<br>');

    // Restore formatted code blocks
    codeBlocks.forEach((code, idx) => {
        const blockHtml = `<pre class="proposal-code-box"><code>${escapeHtml(code)}</code></pre>`;
        html = html.replace(`__CODE_BLOCK_${idx}__`, blockHtml);
    });

    return html;
}

// Helper to escape HTML characters
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Toast notification helper
function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    const icon = type === "success" ? "fa-circle-check" : "fa-triangle-exclamation";
    toast.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.remove();
    }, 4500);
}
