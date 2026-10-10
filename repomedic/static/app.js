// RepoMedic AI Frontend Logic

document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const pillModel = document.getElementById("pill-model");
  const pillSandbox = document.getElementById("pill-sandbox");
  const lblModelName = document.getElementById("lbl-model-name");
  const lblSandboxMode = document.getElementById("lbl-sandbox-mode");
  const lblEnvMode = document.getElementById("lbl-env-mode");

  const taskSelect = document.getElementById("task-select");
  const issueDesc = document.getElementById("issue-desc");
  const btnStartRepair = document.getElementById("btn-start-repair");
  const btnRunBenchmark = document.getElementById("btn-run-benchmark");

  const badgeAttempt = document.getElementById("badge-attempt");
  const badgeStatus = document.getElementById("badge-status");
  const progressBox = document.getElementById("progress-box");
  const progressText = document.getElementById("progress-text");
  const mainSpinner = document.getElementById("main-spinner");

  const diffViewer = document.getElementById("diff-viewer");
  const diffMeta = document.getElementById("diff-meta");
  const btnCopyDiff = document.getElementById("btn-copy-diff");

  const preBaseline = document.getElementById("pre-baseline");
  const preVerification = document.getElementById("pre-verification");
  const auditList = document.getElementById("audit-history-list");

  // Stepper Elements
  const stepNodes = {
    INITIALIZING: document.getElementById("step-inspect"),
    COLLECTING_CONTEXT: document.getElementById("step-inspect"),
    RUNNING_BASELINE_TESTS: document.getElementById("step-baseline"),
    GENERATING_PATCH: document.getElementById("step-generate"),
    VALIDATING_PATCH_GATE: document.getElementById("step-gate"),
    APPLYING_PATCH: document.getElementById("step-verify"),
    RUNNING_VERIFICATION_TESTS: document.getElementById("step-verify"),
    COMPLETED_VERIFIED: document.getElementById("step-outcome"),
  };

  // Tabs
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

  // Benchmark Modal Elements
  const modalBenchmark = document.getElementById("modal-benchmark");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const benchmarkRunning = document.getElementById("benchmark-running-state");
  const benchmarkResults = document.getElementById("benchmark-results-container");
  const metricsCards = document.getElementById("metrics-cards");
  const tableBenchmarkBody = document.querySelector("#table-benchmark-tasks tbody");

  let benchmarkTasksData = [];
  let currentActiveJobId = null;
  let pollingInterval = null;

  // 1. Fetch Health Status
  async function fetchHealth() {
    try {
      const res = await fetch("/health");
      if (!res.ok) throw new Error("Health check failed");
      const data = await res.json();

      lblModelName.textContent = data.model_name || "Unknown";
      if (data.model_health && data.model_health.available) {
        pillModel.className = "status-pill online";
      } else {
        pillModel.className = "status-pill warn";
        lblModelName.textContent += " (Offline/Mock)";
      }

      if (data.docker_available) {
        pillSandbox.className = "status-pill online";
        lblSandboxMode.textContent = "Docker Container";
      } else {
        pillSandbox.className = "status-pill warn";
        lblSandboxMode.textContent = "Restricted Fixture";
      }

      lblEnvMode.textContent = data.hosted_mode ? "Hosted (Fixtures Only)" : "Local Development";
    } catch (err) {
      pillModel.className = "status-pill error";
      lblModelName.textContent = "Unreachable";
      pillSandbox.className = "status-pill error";
      lblSandboxMode.textContent = "Unknown";
    }
  }

  // 2. Fetch Benchmark Tasks for selector
  async function fetchTasks() {
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
      console.error("Failed to load tasks:", err);
    }
  }

  taskSelect.addEventListener("change", () => {
    const selected = benchmarkTasksData.find((t) => t.task_id === taskSelect.value);
    if (selected) {
      issueDesc.value = selected.issue_description;
    }
  });

  // 3. Start Repair Workflow
  btnStartRepair.addEventListener("click", async () => {
    const fixtureId = taskSelect.value;
    const description = issueDesc.value.trim();

    if (!fixtureId && !description) {
      alert("Please select a benchmark task or enter a bug description.");
      return;
    }

    resetWorkflowUI();
    setLoadingState(true, "Submitting repair request to harness...");

    try {
      const payload = {
        fixture_id: fixtureId || null,
        issue_description: description,
      };

      const res = await fetch("/api/repair/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

    if (data.current_state === "COMPLETED_VERIFIED") {
      badgeStatus.className = "badge badge-verified";
      badgeStatus.textContent = "Verified";
    } else if (data.current_state === "PATCH_REJECTED") {
      badgeStatus.className = "badge badge-rejected";
      badgeStatus.textContent = "Gate Rejected";
    } else if (data.current_state === "FAILED_MAX_ATTEMPTS") {
      badgeStatus.className = "badge badge-failed";
      badgeStatus.textContent = "Max Retries Failed";
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

    // Mark prior steps done
    const stepsInOrder = [
      stepNodes.INITIALIZING,
      stepNodes.RUNNING_BASELINE_TESTS,
      stepNodes.GENERATING_PATCH,
      stepNodes.VALIDATING_PATCH_GATE,
      stepNodes.RUNNING_VERIFICATION_TESTS,
      stepNodes.COMPLETED_VERIFIED,
    ];

    const idx = stepsInOrder.indexOf(activeNode);
    if (idx > 0) {
      for (let i = 0; i < idx; i++) {
        if (stepsInOrder[i]) stepsInOrder[i].classList.add("done");
      }
    }
  }

  function renderFinalReport(report) {
    // 1. Render Diff
    if (report.verified_patch) {
      diffMeta.textContent = `Verified unified diff applied to ${report.changed_files.join(", ")}`;
      diffViewer.innerHTML = formatDiffSyntax(report.verified_patch);
      btnCopyDiff.disabled = false;
    } else {
      diffMeta.textContent = "No verified patch generated.";
      diffViewer.innerHTML = "<code>/* No verified unified diff available */</code>";
      btnCopyDiff.disabled = true;
    }

    // 2. Render Baseline & Verification Output
    preBaseline.textContent =
      report.baseline_stdout || report.baseline_stderr || "No baseline test output captured.";

    const lastAudit = report.audit_history && report.audit_history[report.audit_history.length - 1];
    if (lastAudit) {
      preVerification.textContent =
        lastAudit.test_stdout || lastAudit.test_stderr || "No verification test output.";
    } else {
      preVerification.textContent = "No verification executed.";
    }

    // 3. Render Audit History
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

  btnCopyDiff.addEventListener("click", () => {
    const raw = diffViewer.textContent;
    if (raw) {
      navigator.clipboard.writeText(raw).then(() => {
        btnCopyDiff.textContent = "Copied!";
        setTimeout(() => (btnCopyDiff.textContent = "Copy Diff"), 2000);
      });
    }
  });

  function resetWorkflowUI() {
    badgeAttempt.textContent = "Attempt: 0 / 3";
    badgeStatus.className = "badge badge-idle";
    badgeStatus.textContent = "Running";
    diffViewer.innerHTML = "<code>/* Running repair workflow... */</code>";
    diffMeta.textContent = "Generating patch...";
    btnCopyDiff.disabled = true;
    preBaseline.textContent = "Running...";
    preVerification.textContent = "Pending...";
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

  // 4. Benchmark Modal
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

  // Initial load
  fetchHealth();
  fetchTasks();
});
