// SuperNova Code-RAG Dashboard Application Logic

let currentUserEmail = localStorage.getItem("supernova_user_email") || "";
let currentChatId = "";
let currentRepoUrl = "";

document.addEventListener("DOMContentLoaded", () => {
    fetchSystemStatus();
    checkAuthenticationState();

    const syncBtn = document.getElementById("syncBtn");
    const repoUrlInput = document.getElementById("repoUrlInput");
    const sendBtn = document.getElementById("sendBtn");
    const chatInput = document.getElementById("chatInput");
    const clearChatBtn = document.getElementById("clearChatBtn");
    const newChatBtn = document.getElementById("newChatBtn");
    const logoutBtn = document.getElementById("logoutBtn");

    // OTP Auth Listeners
    document.getElementById("sendOtpBtn").addEventListener("click", handleSendOtp);
    document.getElementById("verifyOtpBtn").addEventListener("click", handleVerifyOtp);
    document.getElementById("backToEmailBtn").addEventListener("click", () => {
        document.getElementById("otpStep2").style.display = "none";
        document.getElementById("otpStep1").style.display = "block";
    });

    logoutBtn.addEventListener("click", handleLogout);
    newChatBtn.addEventListener("click", () => createNewChat());

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

    clearChatBtn.addEventListener("click", async () => {
        if (!currentChatId) return;
        try {
            await fetch(`/api/chat-history?session_id=${encodeURIComponent(currentChatId)}`, { method: "DELETE" });
            document.getElementById("chatHistory").innerHTML = `
                <div class="message message-ai">
                    <div class="avatar"><i class="fa-solid fa-robot"></i></div>
                    <div class="message-content">
                        <p>Chat history cleared for this session! How can I assist you with your repository?</p>
                    </div>
                </div>
            `;
            showToast("Session history cleared", "info");
        } catch (e) {
            console.error("Error clearing chat history:", e);
        }
    });
});

// Authentication State Manager
function checkAuthenticationState() {
    const modal = document.getElementById("otpModal");
    const profile = document.getElementById("userProfile");
    const emailSpan = document.getElementById("userEmailSpan");

    if (currentUserEmail) {
        modal.style.display = "none";
        profile.style.display = "flex";
        emailSpan.innerText = currentUserEmail;
        fetchUserChats();
    } else {
        modal.style.display = "flex";
        profile.style.display = "none";
    }
}

// Send OTP Handler
async function handleSendOtp() {
    const emailInput = document.getElementById("userEmailInput");
    const email = emailInput.value.trim();
    const btn = document.getElementById("sendOtpBtn");
    const notice = document.getElementById("modalNotice");

    if (!email || !email.includes("@")) {
        showModalNotice("Please enter a valid Gmail or Email address.");
        return;
    }

    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Sending OTP Code...`;
    hideModalNotice();

    try {
        const res = await fetch("/api/send-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: email })
        });
        const data = await res.json();
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-paper-plane"></i> Send 6-Digit OTP Code`;

        if (data.error) {
            showModalNotice(data.error);
            return;
        }

        document.getElementById("otpStep1").style.display = "none";
        document.getElementById("otpStep2").style.display = "block";
        showToast("6-Digit OTP sent to your inbox!", "success");

        if (data.dev_otp) {
            showModalNotice(`SMTP Notice: Logged verification code: ${data.dev_otp}`);
        }
    } catch (e) {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-paper-plane"></i> Send 6-Digit OTP Code`;
        showModalNotice(`Connection error: ${e.message}`);
    }
}

// Verify OTP Handler
async function handleVerifyOtp() {
    const email = document.getElementById("userEmailInput").value.trim();
    const otp = document.getElementById("otpCodeInput").value.trim();
    const btn = document.getElementById("verifyOtpBtn");

    if (!otp || otp.length !== 6) {
        showModalNotice("Please enter the 6-digit OTP code sent to your email.");
        return;
    }

    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Verifying...`;
    hideModalNotice();

    try {
        const res = await fetch("/api/verify-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: email, otp: otp })
        });
        const data = await res.json();
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-check-double"></i> Verify & Enter Portal`;

        if (data.error) {
            showModalNotice(data.error);
            return;
        }

        currentUserEmail = data.email;
        localStorage.setItem("supernova_user_email", currentUserEmail);
        showToast("Authentication successful! Welcome to SuperNova.", "success");
        checkAuthenticationState();
    } catch (e) {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-check-double"></i> Verify & Enter Portal`;
        showModalNotice(`Verification error: ${e.message}`);
    }
}

function handleLogout() {
    currentUserEmail = "";
    currentChatId = "";
    currentRepoUrl = "";
    localStorage.removeItem("supernova_user_email");
    document.getElementById("chatsList").innerHTML = "";
    checkAuthenticationState();
    showToast("Logged out successfully", "info");
}

function showModalNotice(msg) {
    const el = document.getElementById("modalNotice");
    el.innerText = msg;
    el.style.display = "block";
}

function hideModalNotice() {
    const el = document.getElementById("modalNotice");
    el.style.display = "none";
}

// Fetch User Multi-Chat Sessions
async function fetchUserChats() {
    if (!currentUserEmail) return;
    try {
        const res = await fetch("/api/chats", {
            headers: { "X-User-Email": currentUserEmail }
        });
        const data = await res.json();
        const chats = data.chats || [];
        renderChatList(chats);

        if (chats.length > 0 && !currentChatId) {
            switchChat(chats[0].chat_id);
        } else if (chats.length === 0) {
            createNewChat();
        }
    } catch (e) {
        console.error("Error loading user chats:", e);
    }
}

// Render Chat Sessions in Sidebar
function renderChatList(chats) {
    const listEl = document.getElementById("chatsList");
    listEl.innerHTML = "";

    chats.forEach((chat) => {
        const item = document.createElement("div");
        item.className = `chat-session-item ${chat.chat_id === currentChatId ? 'active' : ''}`;
        item.onclick = (e) => {
            if (!e.target.closest('.chat-delete-btn')) {
                switchChat(chat.chat_id);
            }
        };

        const titleText = chat.title || "Repository Chat";
        const repoText = chat.repo_url ? chat.repo_url : "No repo assigned";

        item.innerHTML = `
            <div>
                <div class="chat-session-title"><i class="fa-solid fa-comments"></i> ${escapeHtml(titleText)}</div>
                <div class="chat-session-repo">${escapeHtml(repoText)}</div>
            </div>
            <button class="chat-delete-btn" onclick="deleteChat('${chat.chat_id}')" title="Delete chat">
                <i class="fa-solid fa-trash-can"></i>
            </button>
        `;
        listEl.appendChild(item);
    });
}

// Create New Chat Session
async function createNewChat(repoUrl = "") {
    if (!currentUserEmail) return;
    try {
        const res = await fetch("/api/chats", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-User-Email": currentUserEmail
            },
            body: JSON.stringify({ repo_url: repoUrl })
        });
        const data = await res.json();
        if (data.chat) {
            currentChatId = data.chat.chat_id;
            currentRepoUrl = repoUrl;
            document.getElementById("repoUrlInput").value = repoUrl;
            fetchUserChats();
            renderChatHistoryMessages([]);
            showToast("New chat session created!", "info");
        }
    } catch (e) {
        console.error("Error creating chat:", e);
    }
}

// Switch Active Chat Session
async function switchChat(chatId) {
    currentChatId = chatId;
    try {
        const res = await fetch(`/api/chats/${chatId}`, {
            headers: { "X-User-Email": currentUserEmail }
        });
        const data = await res.json();
        if (data.chat) {
            currentRepoUrl = data.chat.repo_url || "";
            document.getElementById("repoUrlInput").value = currentRepoUrl;
            document.getElementById("activeChatTitle").innerText = data.chat.title || "Code Assistant Chat";

            renderChatHistoryMessages(data.chat.messages || []);
            fetchUserChats();
        }
    } catch (e) {
        console.error("Error switching chat:", e);
    }
}

// Delete Chat Session
async function deleteChat(chatId) {
    if (!confirm("Are you sure you want to delete this chat session?")) return;
    try {
        await fetch(`/api/chats?chat_id=${chatId}`, {
            method: "DELETE",
            headers: { "X-User-Email": currentUserEmail }
        });
        showToast("Chat session deleted", "info");
        if (currentChatId === chatId) {
            currentChatId = "";
        }
        fetchUserChats();
    } catch (e) {
        console.error("Error deleting chat:", e);
    }
}

// Render Messages for Active Chat History
function renderChatHistoryMessages(messages) {
    const history = document.getElementById("chatHistory");
    history.innerHTML = `
        <div class="message message-ai">
            <div class="avatar"><i class="fa-solid fa-robot"></i></div>
            <div class="message-content">
                <p>Welcome to <strong>SuperNova Code-RAG</strong>! 👋</p>
                <p>Chat session active. Ask a question or request a code update for your repository.</p>
            </div>
        </div>
    `;

    messages.forEach((msg) => {
        if (msg.role === "user") {
            appendMessage("user", msg.content);
        } else if (msg.role === "assistant") {
            appendAIMessage(msg.content, null);
        }
    });
}

// Fetch System Status
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

    currentRepoUrl = repoUrl;

    const eventSource = new EventSource(`/api/stream-sync?repo=${encodeURIComponent(repoUrl)}&chat_id=${encodeURIComponent(currentChatId)}`);

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
            fetchUserChats();
            if (currentChatId) switchChat(currentChatId);
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

// Send Chat Message to Server
async function sendChatMessage() {
    const input = document.getElementById("chatInput");
    const prompt = input.value.trim();
    if (!prompt) return;

    input.value = "";

    // Append User Message UI
    appendMessage("user", prompt);

    // Append Loading Message UI
    const loadingId = "loading-" + Date.now();
    appendLoadingMessage(loadingId);

    const repoUrl = document.getElementById("repoUrlInput").value.trim() || currentRepoUrl;

    try {
        const res = await fetch("/api/query", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-User-Email": currentUserEmail
            },
            body: JSON.stringify({
                prompt: prompt,
                chat_id: currentChatId || "default_session",
                repo_url: repoUrl
            })
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

// Append Generic Message Bubble
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

// Append Loading Indicator
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

// Append AI Message with Code Update Proposal
function appendAIMessage(answerText, proposal) {
    const history = document.getElementById("chatHistory");
    const msgDiv = document.createElement("div");
    msgDiv.className = "message message-ai";

    let formattedText = formatMarkdown(answerText);
    let proposalHtml = "";

    if (proposal) {
        proposalHtml = `
            <div class="code-proposal-card">
                <div class="proposal-header">
                    <span><i class="fa-solid fa-wand-magic-sparkles"></i> Code Update Proposal Detected</span>
                    <span class="proposal-meta">${escapeHtml(proposal.file_path)} (Lines ${proposal.line_start}-${proposal.line_end})</span>
                </div>
                <div class="proposal-code-box">${escapeHtml(proposal.updated_code)}</div>
                <button class="btn btn-accent btn-sm" onclick="applyCodeUpdate('${escapeHtml(proposal.file_path)}', ${proposal.line_start}, ${proposal.line_end}, this)">
                    <i class="fa-solid fa-check"></i> Apply Code Update & Surgical Re-index
                </button>
            </div>
        `;
    }

    msgDiv.innerHTML = `
        <div class="avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-content">
            ${formattedText}
            ${proposalHtml}
        </div>
    `;
    history.appendChild(msgDiv);
    history.scrollTop = history.scrollHeight;
}

// Apply Code Update Handler
async function applyCodeUpdate(filePath, lineStart, lineEnd, btnElement) {
    const card = btnElement.closest(".code-proposal-card");
    const codeBox = card.querySelector(".proposal-code-box");
    const updatedCode = codeBox.innerText;

    btnElement.disabled = true;
    btnElement.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Applying & Re-indexing...`;

    try {
        const res = await fetch("/api/apply-update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                file_path: filePath,
                line_start: lineStart,
                line_end: lineEnd,
                updated_code: updatedCode
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            btnElement.className = "btn btn-success btn-sm";
            btnElement.innerHTML = `<i class="fa-solid fa-circle-check"></i> Code Applied & Surgically Synced!`;
            showToast(`Successfully updated '${filePath}'!`, "success");
            fetchSystemStatus();
        } else {
            btnElement.disabled = false;
            btnElement.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Error Applying Update`;
            showToast(`Error: ${data.error}`, "error");
        }
    } catch (e) {
        btnElement.disabled = false;
        btnElement.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Error Applying Update`;
        showToast(`Failed to connect: ${e.message}`, "error");
    }
}

// Markdown Formatter Utility
function formatMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);
    html = html.replace(/```([a-zA-Z]*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    return `<p>${html}</p>`;
}

function escapeHtml(str) {
    return (str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    
    let icon = "fa-info-circle";
    if (type === "success") icon = "fa-circle-check";
    if (type === "error") icon = "fa-circle-xmark";

    toast.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 4000);
}
