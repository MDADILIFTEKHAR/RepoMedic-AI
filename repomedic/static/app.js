// RepoMedic AI Real-World Frontend Logic

document.addEventListener("DOMContentLoaded", () => {
  // Global State
  let currentActiveJobId = null;
  let pollingInterval = null;
  let currentSourceType = "github"; // "github" | "local" | "fixture"
  let benchmarkTasksData = [];
  let userGitHubToken = localStorage.getItem("repomedic_gh_token") || null;
  let lastReportData = null;

  // Header Status Elements
  const pillGitHub = document.getElementById("pill-github");
  const lblGitHubUser = document.getElementById("lbl-github-user");
  const btnOpenGitHubModal = document.getElementById("btn-open-github-modal");

  const pillModel = document.getElementById("pill-model");
  const lblModelName = document.getElementById("lbl-model-name");
  const pillSandbox = document.getElementById("pill-sandbox");
  const lblSandboxMode = document.getElementById("lbl-sandbox-mode");
  const lblEnvMode = document.getElementById("lbl-env-mode");

  // Source Selector Elements
  const sourceTabBtns = document.querySelectorAll(".source-tab-btn");
  const sourcePanes = {
    github: document.getElementById("pane-source-github"),
    local: document.getElementById("pane-source-local"),
    fixture: document.getElementById("pane-source-fixture"),
  };

  const githubRepoSelect = document.getElementById("github-repo-select");
  const githubRepoManual = document.getElementById("github-repo-manual");
  const btnRefreshRepos = document.getElementById("btn-refresh-repos");
  const githubBranchSelect = document.getElementById("github-branch-select");
  const localRepoPath = document.getElementById("local-repo-path");
  const taskSelect = document.getElementById("task-select");
  const issueDesc = document.getElementById("issue-desc");

  // Primary Buttons & Badges
  const btnStartRepair = document.getElementById("btn-start-repair");
  const btnRunBenchmark = document.getElementById("btn-run-benchmark");
  const badgeAttempt = document.getElementById("badge-attempt");
  const badgeStatus = document.getElementById("badge-status");
  const progressText = document.getElementById("progress-text");
  const mainSpinner = document.getElementById("main-spinner");

  // Diff & Action Elements
  const diffViewer = document.getElementById("diff-viewer");
  const diffMeta = document.getElementById("diff-meta");
  const btnCopyDiff = document.getElementById("btn-copy-diff");
  const btnDownloadPatch = document.getElementById("btn-download-patch");
  const btnDownloadReport = document.getElementById("btn-download-report");
  const btnOpenPRModal = document.getElementById("btn-open-pr-modal");

  const preBaseline = document.getElementById("pre-baseline");
  const preVerification = document.getElementById("pre-verification");
  const auditList = document.getElementById("audit-history-list");

  // Workflow Stepper Elements
  const stepNodes = {
    CREATED: document.getElementById("step-created"),
    REPOSITORY_VALIDATED: document.getElementById("step-repo-validated"),
    BASELINE_TESTED: document.getElementById("step-baseline-tested"),
    ANALYZED: document.getElementById("step-analyzed"),
    PATCH_PROPOSED: document.getElementById("step-patch-proposed"),
    PATCH_VALIDATED: document.getElementById("step-patch-validated"),
    PATCH_APPLIED: document.getElementById("step-patch-applied"),
    TESTED: document.getElementById("step-tested"),
    VERIFIED: document.getElementById("step-verified"),
  };

  // Modals & Auth Elements
  const modalGitHub = document.getElementById("modal-github");
  const btnCloseGitHubModal = document.getElementById("btn-close-github-modal");
  const githubConnectedView = document.getElementById("github-connected-view");
  const githubConnectForm = document.getElementById("github-connect-form");
  const connectedUserAvatar = document.getElementById("connected-user-avatar");
  const connectedUserLogin = document.getElementById("connected-user-login");
  const connectedUserName = document.getElementById("connected-user-name");
  const connectedUserScope = document.getElementById("connected-user-scope");
  const btnDisconnectGH = document.getElementById("btn-disconnect-gh");
  const btnDoneGH = document.getElementById("btn-done-gh");
  const modalTabBtns = document.querySelectorAll(".modal-tab-btn");
  const authPanes = {
    "tab-pat": document.getElementById("auth-pane-pat"),
    "tab-oauth": document.getElementById("auth-pane-oauth"),
  };
  const btnSavePAT = document.getElementById("btn-save-pat");
  const patInput = document.getElementById("pat-input");
  const patFeedback = document.getElementById("pat-feedback");
  const oauthConfiguredBox = document.getElementById("oauth-configured-box");
  const oauthUnconfiguredBox = document.getElementById("oauth-unconfigured-box");
  const btnStartOAuth = document.getElementById("btn-start-oauth");
  const oauthClientIdInput = document.getElementById("oauth-client-id-input");
  const oauthClientSecretInput = document.getElementById("oauth-client-secret-input");
  const btnSaveOAuthConfig = document.getElementById("btn-save-oauth-config");
  const oauthFeedback = document.getElementById("oauth-feedback");
  const githubDockerWarning = document.getElementById("github-docker-warning");
  const btnSwitchFixturesLink = document.getElementById("btn-switch-fixtures-link");

  const modalPR = document.getElementById("modal-pr");
  const btnClosePRModal = document.getElementById("btn-close-pr-modal");
  const prRepoPill = document.getElementById("pr-repo-pill");
  const prTitleInput = document.getElementById("pr-title-input");
  const prBodyInput = document.getElementById("pr-body-input");
  const chkPRConfirm = document.getElementById("chk-pr-confirm");
  const btnSubmitPR = document.getElementById("btn-submit-pr");
  const prStatusMsg = document.getElementById("pr-status-msg");

  const modalBenchmark = document.getElementById("modal-benchmark");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const benchmarkRunning = document.getElementById("benchmark-running-state");
  const benchmarkResults = document.getElementById("benchmark-results-container");
  const metricsCards = document.getElementById("metrics-cards");
  const tableBenchmarkBody = document.querySelector("#table-benchmark-tasks tbody");

  // ---------------------------------------------------------------------------
  // 1. Navigation & Tabs
  // ---------------------------------------------------------------------------

  sourceTabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      sourceTabBtns.forEach((b) => b.classList.remove("active"));
      Object.values(sourcePanes).forEach((p) => p.classList.remove("active"));

      btn.classList.add("active");
      currentSourceType = btn.dataset.source;
      if (sourcePanes[currentSourceType]) {
        sourcePanes[currentSourceType].classList.add("active");
      }
    });
  });

  const tabBtns = document.querySelectorAll(".tab-btn");
  const tabPanes = document.querySelectorAll(".tab-pane");
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabBtns.forEach((b) => b.classList.remove("active"));
      tabPanes.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const targetPane = document.getElementById(btn.dataset.tab);
      if (targetPane) targetPane.classList.add("active");
    });
  });

  // ---------------------------------------------------------------------------
  // 2. Health & Status Checks
  // ---------------------------------------------------------------------------

  async function checkHealth() {
    try {
      const res = await fetch("/health");
      if (!res.ok) throw new Error("Health check failed");
      const data = await res.json();

      lblModelName.textContent = data.model_name || "Configured";
      if (data.model_health && data.model_health.available) {
        pillModel.className = "status-pill online";
      } else {
        pillModel.className = "status-pill warn";
        lblModelName.textContent += " (Offline / Mock)";
      }

      if (data.docker_available) {
      pillSandbox.className = "status-pill online";
      lblSandboxMode.textContent = "Docker Container";
      if (githubDockerWarning) githubDockerWarning.classList.add("hidden");
    } else {
      pillSandbox.className = "status-pill warn";
      lblSandboxMode.textContent = "Restricted Fixture";
      if (githubDockerWarning) githubDockerWarning.classList.remove("hidden");
    }

    lblEnvMode.textContent = data.hosted_mode ? "Hosted Mode" : "Local Development";

    if (data.github_oauth_configured) {
      if (oauthConfiguredBox) oauthConfiguredBox.classList.remove("hidden");
      if (oauthUnconfiguredBox) oauthUnconfiguredBox.classList.add("hidden");
    } else {
      if (oauthConfiguredBox) oauthConfiguredBox.classList.add("hidden");
      if (oauthUnconfiguredBox) oauthUnconfiguredBox.classList.remove("hidden");
    }
  } catch (err) {
    pillModel.className = "status-pill error";
    lblModelName.textContent = "Unreachable";
    pillSandbox.className = "status-pill error";
    lblSandboxMode.textContent = "Unknown";
  }
}

// ---------------------------------------------------------------------------
// 3. GitHub Authentication & Repositories
// ---------------------------------------------------------------------------

function getAuthHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (userGitHubToken) {
    headers["Authorization"] = `Bearer ${userGitHubToken}`;
  }
  return headers;
}

// Modal Tab Switcher (PAT vs OAuth)
modalTabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    modalTabBtns.forEach((b) => b.classList.remove("active"));
    Object.values(authPanes).forEach((p) => p && p.classList.remove("active"));
    btn.classList.add("active");
    const targetPane = authPanes[btn.dataset.authtab];
    if (targetPane) targetPane.classList.add("active");
  });
});

async function verifyGitHubToken() {
  if (!userGitHubToken) {
    lblGitHubUser.textContent = "Not Connected";
    pillGitHub.className = "status-pill";
    btnOpenGitHubModal.textContent = "Connect";
    if (githubConnectedView) githubConnectedView.classList.add("hidden");
    if (githubConnectForm) githubConnectForm.classList.remove("hidden");
    return;
  }

  try {
    const res = await fetch("/api/github/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: userGitHubToken }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Token invalid");
    }

    const data = await res.json();
    const user = data.user;
    lblGitHubUser.textContent = `@${user.login}`;
    pillGitHub.className = "status-pill online";
    btnOpenGitHubModal.textContent = "Connected";

    // Update modal connected card
    if (connectedUserAvatar) connectedUserAvatar.src = user.avatar_url || "https://github.com/ghost.png";
    if (connectedUserLogin) connectedUserLogin.textContent = `@${user.login}`;
    if (connectedUserName) connectedUserName.textContent = user.name || user.login;
    if (connectedUserScope) {
      const scopes = user.scopes || [];
      connectedUserScope.textContent = scopes.length ? scopes.join(", ") : (data.has_repo_scope ? "repo (fine-grained)" : "metadata only");
    }

    if (githubConnectedView) githubConnectedView.classList.remove("hidden");
    if (githubConnectForm) githubConnectForm.classList.add("hidden");

    loadGitHubRepositories();
  } catch (err) {
    console.warn("GitHub token verification failed:", err.message);
    userGitHubToken = null;
    localStorage.removeItem("repomedic_gh_token");
    lblGitHubUser.textContent = "Not Connected";
    pillGitHub.className = "status-pill";
    btnOpenGitHubModal.textContent = "Connect";
    if (githubConnectedView) githubConnectedView.classList.add("hidden");
    if (githubConnectForm) githubConnectForm.classList.remove("hidden");
  }
}

async function loadGitHubRepositories() {
  if (!userGitHubToken) return;

  try {
    const res = await fetch("/api/github/repos", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const repos = await res.json();

    githubRepoSelect.innerHTML = '<option value="">-- Select Your Accessible Repository --</option>';
    repos.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.full_name;
      opt.textContent = `${r.full_name} (${r.default_branch})${r.private ? " 🔒" : ""}`;
      githubRepoSelect.appendChild(opt);
    });
  } catch (err) {
    console.error("Failed to list repos:", err);
  }
}

async function loadBranchesForRepo(fullName) {
  if (!fullName) return;
  const parts = fullName.trim().split("/");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return;
  const [owner, repo] = parts;

  try {
    const res = await fetch(`/api/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/branches`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) return;
    const branches = await res.json();

    githubBranchSelect.innerHTML = "";
    branches.forEach((b) => {
      const opt = document.createElement("option");
      opt.value = b.name;
      opt.textContent = b.name + (b.protected ? " (protected)" : "");
      githubBranchSelect.appendChild(opt);
    });
  } catch (err) {
    console.error("Failed to load branches:", err);
  }
}

githubRepoSelect.addEventListener("change", () => {
  if (githubRepoSelect.value) {
    githubRepoManual.value = githubRepoSelect.value;
    loadBranchesForRepo(githubRepoSelect.value);
  }
});

let repoInputTimer = null;
githubRepoManual.addEventListener("input", () => {
  clearTimeout(repoInputTimer);
  repoInputTimer = setTimeout(() => {
    const val = githubRepoManual.value.trim();
    if (val.includes("/") && val.split("/").length === 2) {
      loadBranchesForRepo(val);
    }
  }, 600);
});

btnRefreshRepos.addEventListener("click", () => {
  loadGitHubRepositories();
});

// Modal Handlers
btnOpenGitHubModal.addEventListener("click", () => {
  if (patFeedback) patFeedback.className = "auth-feedback";
  if (oauthFeedback) oauthFeedback.className = "auth-feedback";
  modalGitHub.classList.remove("hidden");
});

btnCloseGitHubModal.addEventListener("click", () => {
  modalGitHub.classList.add("hidden");
});

if (btnDoneGH) {
  btnDoneGH.addEventListener("click", () => {
    modalGitHub.classList.add("hidden");
  });
}

if (btnDisconnectGH) {
  btnDisconnectGH.addEventListener("click", () => {
    userGitHubToken = null;
    localStorage.removeItem("repomedic_gh_token");
    verifyGitHubToken();
  });
}

if (btnSwitchFixturesLink) {
  btnSwitchFixturesLink.addEventListener("click", (e) => {
    e.preventDefault();
    const fixtureBtn = document.querySelector('.source-tab-btn[data-source="fixture"]');
    if (fixtureBtn) fixtureBtn.click();
  });
}

// PAT Token Connect
btnSavePAT.addEventListener("click", async () => {
  const token = patInput.value.trim();
  if (!token) {
    showAuthFeedback(patFeedback, "error", "Please paste your GitHub Personal Access Token.");
    return;
  }

  showAuthFeedback(patFeedback, "info", "Verifying token with GitHub API...");
  btnSavePAT.disabled = true;

  try {
    const res = await fetch("/api/github/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Authentication failed.");
    }

    userGitHubToken = token;
    localStorage.setItem("repomedic_gh_token", token);
    patInput.value = "";

    const user = data.user;
    let successMsg = `✓ Connected successfully as @${user.login}!`;
    if (data.warning) {
      successMsg += `<br><small style="color: #fde68a;">⚠️ ${data.warning}</small>`;
    }
    showAuthFeedback(patFeedback, "success", successMsg);

    await verifyGitHubToken();

    setTimeout(() => {
      modalGitHub.classList.add("hidden");
    }, 1200);
  } catch (err) {
    showAuthFeedback(patFeedback, "error", `Authentication Failed: ${err.message}`);
  } finally {
    btnSavePAT.disabled = false;
  }
});

patInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    btnSavePAT.click();
  }
});

// OAuth Initiate
btnStartOAuth.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/github/oauth/url");
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Could not initiate OAuth");
    }
    window.location.href = data.authorization_url;
  } catch (err) {
    showAuthFeedback(oauthFeedback, "error", `OAuth Error: ${err.message}`);
  }
});

// Dynamic OAuth Credentials Configuration
if (btnSaveOAuthConfig) {
  btnSaveOAuthConfig.addEventListener("click", async () => {
    const cid = oauthClientIdInput.value.trim();
    const csecret = oauthClientSecretInput.value.trim();
    if (!cid || !csecret) {
      showAuthFeedback(oauthFeedback, "error", "Please enter both Client ID and Client Secret.");
      return;
    }

    showAuthFeedback(oauthFeedback, "info", "Saving OAuth configuration...");
    try {
      const res = await fetch("/api/github/oauth/configure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: cid, client_secret: csecret }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Configuration failed");

      // Now initiate OAuth immediately
      btnStartOAuth.click();
    } catch (err) {
      showAuthFeedback(oauthFeedback, "error", `Failed to configure OAuth: ${err.message}`);
    }
  });
}

function showAuthFeedback(el, type, message) {
  if (!el) return;
  el.className = `auth-feedback ${type} active`;
  el.innerHTML = message;
}

  // ---------------------------------------------------------------------------
  // 4. Predefined Benchmark Tasks
  // ---------------------------------------------------------------------------

  async function loadBenchmarkTasks() {
    try {
      const res = await fetch("/api/benchmark/tasks");
      if (!res.ok) return;
      benchmarkTasksData = await res.json();

      taskSelect.innerHTML = '<option value="">-- Choose a Deterministic Bug Task --</option>';
      benchmarkTasksData.forEach((task) => {
        const opt = document.createElement("option");
        opt.value = task.task_id;
        opt.textContent = `[${task.category}] ${task.name}`;
        taskSelect.appendChild(opt);
      });
    } catch (err) {
      console.error("Failed to load benchmark tasks:", err);
    }
  }

  taskSelect.addEventListener("change", () => {
    const selected = benchmarkTasksData.find((t) => t.task_id === taskSelect.value);
    if (selected) {
      issueDesc.value = selected.issue_description;
    }
  });

  // ---------------------------------------------------------------------------
  // 5. Run Evidence-Gated Repair
  // ---------------------------------------------------------------------------

  btnStartRepair.addEventListener("click", async () => {
    const desc = issueDesc.value.trim();
    if (!desc) {
      alert("Please provide a bug description.");
      return;
    }

    const payload = {
      source_type: currentSourceType,
      issue_description: desc,
      github_token: userGitHubToken || null,
    };

    if (currentSourceType === "github") {
      const repo = githubRepoManual.value.trim() || githubRepoSelect.value;
      if (!repo || !repo.includes("/")) {
        alert("Please select or enter a valid GitHub repository (owner/repo).");
        return;
      }
      payload.github_repo = repo;
      payload.github_branch = githubBranchSelect.value || "main";
    } else if (currentSourceType === "local") {
      const path = localRepoPath.value.trim();
      if (!path) {
        alert("Please enter a local repository path.");
        return;
      }
      payload.local_repo_path = path;
    } else if (currentSourceType === "fixture") {
      const fixtureId = taskSelect.value;
      if (!fixtureId) {
        alert("Please select a benchmark fixture.");
        return;
      }
      payload.fixture_id = fixtureId;
    }

    resetWorkflowUI();
    setLoadingState(true, "Submitting repair task to harness...");

    try {
      const res = await fetch("/api/repair/start", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to start repair job");
      }

      const jobData = await res.json();
      currentActiveJobId = jobData.job_id;
      pollJobStatus(currentActiveJobId);
    } catch (err) {
      setLoadingState(false, `Error: ${err.message}`);
      badgeStatus.className = "badge badge-failed";
      badgeStatus.textContent = "Error";
    }
  });

  function pollJobStatus(jobId) {
    if (pollingInterval) clearInterval(pollingInterval);

    pollingInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/repair/status/${jobId}`);
        if (!res.ok) throw new Error("Status query failed");
        const data = await res.json();

        updateUIFromStatus(data);

        if (data.status === "completed" || data.status === "failed") {
          clearInterval(pollingInterval);
          pollingInterval = null;
          setLoadingState(false, data.progress_message);
          if (data.report) {
            lastReportData = data.report;
            renderFinalReport(data.report);
          }
        }
      } catch (err) {
        clearInterval(pollingInterval);
        setLoadingState(false, `Polling error: ${err.message}`);
      }
    }, 800);
  }

  function updateUIFromStatus(data) {
    progressText.textContent = data.progress_message || "Processing...";
    badgeAttempt.textContent = `Attempt: ${data.attempts_used} / ${data.max_attempts}`;

    updateStepper(data.current_state);

    if (data.current_state === "VERIFIED" || data.current_state === "COMPLETED_VERIFIED") {
      badgeStatus.className = "badge badge-verified";
      badgeStatus.textContent = "Verified";
    } else if (data.current_state === "PATCH_REJECTED") {
      badgeStatus.className = "badge badge-rejected";
      badgeStatus.textContent = "Policy Rejected";
    } else if (data.current_state === "FAILED" || data.current_state === "FAILED_MAX_ATTEMPTS") {
      badgeStatus.className = "badge badge-failed";
      badgeStatus.textContent = "Failed (Max Retries)";
    } else if (data.current_state === "INFRASTRUCTURE_ERROR" || data.current_state === "SANDBOX_BLOCKED") {
      badgeStatus.className = "badge badge-infra";
      badgeStatus.textContent = "Infra Blocked";
    } else {
      badgeStatus.className = "badge";
      badgeStatus.textContent = data.current_state;
    }
  }

  function updateStepper(state) {
    Object.values(stepNodes).forEach((node) => {
      if (node) {
        node.classList.remove("active");
        node.classList.remove("done");
      }
    });

    const activeNode = stepNodes[state];
    if (activeNode) {
      activeNode.classList.add("active");
    }

    const stepsOrder = [
      stepNodes.CREATED,
      stepNodes.REPOSITORY_VALIDATED,
      stepNodes.BASELINE_TESTED,
      stepNodes.ANALYZED,
      stepNodes.PATCH_PROPOSED,
      stepNodes.PATCH_VALIDATED,
      stepNodes.PATCH_APPLIED,
      stepNodes.TESTED,
      stepNodes.VERIFIED,
    ];

    const idx = stepsOrder.indexOf(activeNode);
    if (idx > 0) {
      for (let i = 0; i < idx; i++) {
        if (stepsOrder[i]) stepsOrder[i].classList.add("done");
      }
    }
  }

  function renderFinalReport(report) {
    // 1. Diff Rendering
    if (report.verified_patch) {
      diffMeta.textContent = `Verified unified diff applied to ${report.changed_files.join(", ")}`;
      diffViewer.innerHTML = formatDiffSyntax(report.verified_patch);
      btnCopyDiff.disabled = false;
      btnDownloadPatch.disabled = false;

      // Enable PR creation if this was a remote GitHub repo
      if (report.remote_clone_url || currentSourceType === "github") {
        btnOpenPRModal.disabled = false;
      }
    } else {
      diffMeta.textContent = "No verified patch generated.";
      diffViewer.innerHTML = "<code>/* No verified unified diff available */</code>";
      btnCopyDiff.disabled = true;
      btnDownloadPatch.disabled = true;
      btnOpenPRModal.disabled = true;
    }

    btnDownloadReport.disabled = false;

    // 2. Test Evidence Rendering
    preBaseline.textContent =
      report.baseline_stdout || report.baseline_stderr || "No baseline test output captured.";

    const lastAudit = report.audit_history && report.audit_history[report.audit_history.length - 1];
    if (lastAudit) {
      preVerification.textContent =
        lastAudit.test_stdout || lastAudit.test_stderr || "No verification test output.";
    } else {
      preVerification.textContent = "No verification executed.";
    }

    // 3. Audit History
    auditList.innerHTML = "";
    if (report.audit_history && report.audit_history.length > 0) {
      report.audit_history.forEach((a) => {
        const card = document.createElement("div");
        card.className = "audit-card";
        const testBadge = a.test_passed ? "🟢 Tests Passed" : "🔴 Tests Failed";
        const gateBadge = a.gate_valid ? "🛡️ Gate Passed" : `⚠️ Gate Rejected (${a.gate_rejection_reason || ""})`;

        card.innerHTML = `
          <div class="audit-card-header">
            <span>Attempt ${a.attempt_number} (Latency: ${a.model_latency_seconds}s)</span>
            <span>+${a.lines_added} / -${a.lines_deleted} lines</span>
          </div>
          <div style="margin-bottom: 0.35rem;">${gateBadge} | ${testBadge}</div>
          <pre style="background: rgba(0,0,0,0.3); padding: 0.5rem; border-radius: 4px; font-size: 0.75rem; max-height: 120px; overflow-y: auto;"><code>${escapeHtml(a.extracted_diff || "No diff generated")}</code></pre>
        `;
        auditList.appendChild(card);
      });
    } else {
      auditList.innerHTML = '<p class="empty-state">No audit entries.</p>';
    }
  }

  // ---------------------------------------------------------------------------
  // 6. Downloads & Actions
  // ---------------------------------------------------------------------------

  btnCopyDiff.addEventListener("click", () => {
    const raw = diffViewer.textContent;
    if (raw) {
      navigator.clipboard.writeText(raw).then(() => {
        btnCopyDiff.textContent = "Copied!";
        setTimeout(() => (btnCopyDiff.textContent = "📋 Copy Diff"), 2000);
      });
    }
  });

  btnDownloadPatch.addEventListener("click", () => {
    if (currentActiveJobId) {
      window.location.href = `/api/repair/${currentActiveJobId}/download/patch`;
    }
  });

  btnDownloadReport.addEventListener("click", () => {
    if (currentActiveJobId) {
      window.location.href = `/api/repair/${currentActiveJobId}/download/report`;
    }
  });

  // ---------------------------------------------------------------------------
  // 7. Explicit Pull Request Approval & Creation
  // ---------------------------------------------------------------------------

  btnOpenPRModal.addEventListener("click", () => {
    if (!lastReportData || !lastReportData.verified_patch) return;

    const repoName = lastReportData.repository_path || githubRepoManual.value || "repository";
    const branchName = lastReportData.base_branch || githubBranchSelect.value || "main";
    prRepoPill.textContent = `${repoName} (base: ${branchName})`;

    prTitleInput.value = `RepoMedic AI Fix: ${lastReportData.issue_description.substring(0, 60)}`;
    prBodyInput.value =
      `## 🩺 Autonomous Bug Repair by RepoMedic AI\n\n` +
      `**Issue Description**:\n> ${lastReportData.issue_description}\n\n` +
      `### 🛡️ Evidence-Gated Verification Details\n` +
      `- **Test Outcome**: All verification tests passed cleanly in isolated sandbox.\n` +
      `- **Attempts Used**: ${lastReportData.attempts_count} / ${lastReportData.max_attempts}\n` +
      `- **Files Changed**: \`${lastReportData.changed_files.join(", ")}\`\n`;

    chkPRConfirm.checked = false;
    btnSubmitPR.disabled = true;
    prStatusMsg.className = "pr-status-msg";
    prStatusMsg.textContent = "";

    modalPR.classList.remove("hidden");
  });

  btnClosePRModal.addEventListener("click", () => {
    modalPR.classList.add("hidden");
  });

  chkPRConfirm.addEventListener("change", () => {
    btnSubmitPR.disabled = !chkPRConfirm.checked;
  });

  btnSubmitPR.addEventListener("click", async () => {
    if (!currentActiveJobId || !chkPRConfirm.checked) return;

    btnSubmitPR.disabled = true;
    prStatusMsg.className = "pr-status-msg active";
    prStatusMsg.textContent = "Pushing disposable branch and opening Pull Request on GitHub...";

    try {
      const payload = {
        confirm: true,
        pr_title: prTitleInput.value.trim(),
        pr_body: prBodyInput.value.trim(),
        target_branch: lastReportData.base_branch || "main",
        github_token: userGitHubToken || null,
      };

      const res = await fetch(`/api/repair/${currentActiveJobId}/publish-pr`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to create Pull Request");
      }

      const result = await res.json();
      prStatusMsg.className = "pr-status-msg active success";
      prStatusMsg.innerHTML = `🎉 Pull Request #${result.pr_number} created successfully! <br><a href="${result.pr_url}" target="_blank" style="color: var(--accent-cyan); text-decoration: underline;">View Pull Request on GitHub ↗</a>`;
      btnSubmitPR.textContent = "Pull Request Created";
    } catch (err) {
      prStatusMsg.className = "pr-status-msg active error";
      prStatusMsg.textContent = `Error: ${err.message}`;
      btnSubmitPR.disabled = false;
    }
  });

  // ---------------------------------------------------------------------------
  // 8. Deterministic Benchmark Suite Modal
  // ---------------------------------------------------------------------------

  btnRunBenchmark.addEventListener("click", async () => {
    modalBenchmark.classList.remove("hidden");
    benchmarkRunning.classList.remove("hidden");
    benchmarkResults.classList.add("hidden");
    tableBenchmarkBody.innerHTML = "";

    try {
      const res = await fetch("/api/benchmark/run?mode=bounded_repair_loop", { method: "POST" });
      if (!res.ok) throw new Error("Benchmark run failed");
      const data = await res.json();

      benchmarkRunning.classList.add("hidden");
      benchmarkResults.classList.remove("hidden");

      renderBenchmarkResults(data);
    } catch (err) {
      benchmarkRunning.innerHTML = `<span style="color: var(--accent-rose);">Error running benchmark: ${err.message}</span>`;
    }
  });

  btnCloseModal.addEventListener("click", () => {
    modalBenchmark.classList.add("hidden");
  });

  function renderBenchmarkResults(data) {
    const m = data.metrics;
    metricsCards.innerHTML = `
      <div class="metric-card">
        <div class="val">${m.final_pass_rate_pct}%</div>
        <div class="lbl">Final Pass Rate (${m.final_passed_count}/${m.tasks_eligible})</div>
      </div>
      <div class="metric-card">
        <div class="val">${m.one_shot_pass_rate_pct}%</div>
        <div class="lbl">One-Shot Pass Rate</div>
      </div>
      <div class="metric-card">
        <div class="val">+${m.loop_improvement_delta_pct}%</div>
        <div class="lbl">Loop Improvement</div>
      </div>
      <div class="metric-card">
        <div class="val">${m.patch_rejection_rate_pct}%</div>
        <div class="lbl">Policy Rejection Rate</div>
      </div>
      <div class="metric-card">
        <div class="val">${m.latency_mean_seconds}s</div>
        <div class="lbl">Mean Latency</div>
      </div>
    `;

    tableBenchmarkBody.innerHTML = "";
    data.task_reports.forEach((t) => {
      const row = document.createElement("tr");
      const statusHtml = t.success
        ? '<span style="color: #34D399; font-weight: 600;">✅ VERIFIED</span>'
        : `<span style="color: #F87171; font-weight: 600;">❌ ${t.terminal_state}</span>`;

      row.innerHTML = `
        <td><code>${t.task_id}</code></td>
        <td>${t.category}</td>
        <td>${statusHtml}</td>
        <td>${t.attempts_used} / ${t.max_attempts}</td>
        <td>${t.duration_seconds}s</td>
      `;
      tableBenchmarkBody.appendChild(row);
    });
  }

  // ---------------------------------------------------------------------------
  // Helper Utilities
  // ---------------------------------------------------------------------------

  function formatDiffSyntax(rawDiff) {
    const lines = rawDiff.split("\n");
    return lines
      .map((line) => {
        const escaped = escapeHtml(line);
        if (line.startsWith("---") || line.startsWith("+++")) {
          return `<span class="diff-header">${escaped}</span>`;
        } else if (line.startsWith("@@")) {
          return `<span class="diff-hunk">${escaped}</span>`;
        } else if (line.startsWith("+")) {
          return `<span class="diff-add">${escaped}</span>`;
        } else if (line.startsWith("-")) {
          return `<span class="diff-del">${escaped}</span>`;
        }
        return escaped;
      })
      .join("\n");
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function resetWorkflowUI() {
    badgeAttempt.textContent = "Attempt: 0 / 3";
    badgeStatus.className = "badge badge-idle";
    badgeStatus.textContent = "Running";
    diffViewer.innerHTML = "<code>/* Running evidence-gated repair harness... */</code>";
    diffMeta.textContent = "Generating patch...";
    btnCopyDiff.disabled = true;
    btnDownloadPatch.disabled = true;
    btnDownloadReport.disabled = true;
    btnOpenPRModal.disabled = true;
    preBaseline.textContent = "Running baseline tests...";
    preVerification.textContent = "Pending verification...";
    auditList.innerHTML = '<p class="empty-state">Repair running...</p>';
    Object.values(stepNodes).forEach((n) => n && n.classList.remove("active", "done"));
  }

  function setLoadingState(isLoading, msg) {
    btnStartRepair.disabled = isLoading;
    btnRunBenchmark.disabled = isLoading;
    if (isLoading) {
      mainSpinner.classList.add("active");
    } else {
      mainSpinner.classList.remove("active");
    }
    progressText.textContent = msg;
  }

  // Initial Load
  checkHealth();
  verifyGitHubToken();
  loadBenchmarkTasks();
});
